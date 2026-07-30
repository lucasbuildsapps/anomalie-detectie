"""Opslaglaag via SQLAlchemy Core.

Werkt op twee backends met dezelfde code:
- Lokaal / standaard: SQLite-bestand (data/store.db).
- Productie: externe Postgres (bv. Supabase) als DATABASE_URL is gezet
  (env-var) of `database_url` in .streamlit/secrets.toml staat.

Dedupe gebeurt in de database zelf: de unique constraint op
(dataset_id, row_hash) plus ON CONFLICT DO NOTHING (native op zowel SQLite
als Postgres). Schema-wijzigingen lopen via Alembic (migrations/).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
)

from core.logging_setup import get_logger

_logger = get_logger("storage")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "store.db"

STANDARD_FIELDS = {
    "timestamp", "value", "category", "location_name", "lat", "lon",
}

_metadata = MetaData()

datasets = Table(
    "datasets", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("description", Text),
    Column("created_at", String(64), nullable=False),
    Column("column_mapping", Text, nullable=False),
    # Compartimentering (need-to-know): alleen wie in deze groep zit — of
    # een beheerder — ziet deze dataset. NULL = zichtbaar voor iedereen
    # met leesrecht.
    Column("required_group", String(128)),
)

observations = Table(
    "observations", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    # Echte DateTime (UTC, naief opgeslagen): maakt range-queries en
    # DB-side aggregatie mogelijk. Migratie van oudere string-kolommen:
    # zie migrations/versions/0002_timestamp_datetime.py.
    Column("timestamp", DateTime, nullable=False),
    Column("value", Float),
    Column("category", Text),
    Column("location_name", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("extras", Text),
    Column("row_hash", String(64), nullable=False),
    UniqueConstraint("dataset_id", "row_hash", name="uq_obs_dataset_hash"),
    Index("ix_obs_dataset_ts", "dataset_id", "timestamp"),
)

annotations_t = Table(
    "annotations", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("finding_key", String(64), nullable=False),
    Column("note", Text),
    Column("status", String(32)),
    Column("updated_at", String(64), nullable=False),
    UniqueConstraint("dataset_id", "finding_key", name="uq_anno_dataset_key"),
)

# Globale, door de analist beheerde markeringen (bv. staakt-het-vuren-datum).
# Bewust niet aan één dataset gebonden: een gebeurtenis in de echte wereld is
# relevant voor elke reeks, ook bij cross-dataset vergelijken.
events_t = Table(
    "events", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_date", String(32), nullable=False),
    Column("label", Text, nullable=False),
    Column("created_at", String(64), nullable=False),
)

# Opgeslagen weergaves: de complete selectie van een analist (dataset, regio,
# categorieën, methode-preset, horizon, tijdschaal) als herlaadbare workflow.
saved_views_t = Table(
    "saved_views", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(64), nullable=False),
)

# Audit-trail: wie deed wat, wanneer. Voor operationeel gebruik is dit een
# harde eis — elke muterende actie en elke login-poging wordt vastgelegd.
# `username` komt uit de reverse-proxy header (X-Forwarded-User) zodra SSO
# voor de app staat; tot die tijd 'onbekend'.
audit_log_t = Table(
    "audit_log", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, nullable=False),
    Column("username", String(128)),
    Column("action", String(64), nullable=False),
    Column("object_type", String(32)),
    Column("object_id", String(64)),
    Column("detail", Text),
    Column("client", String(128)),
    Index("ix_audit_ts", "ts"),
)

# Analyse-momentopnames: wát zei de tool op welk moment. Ruwe data groeit
# en normbeelden verschuiven mee; zonder snapshot is achteraf niet meer te
# reconstrueren waarop een beoordeling was gebaseerd. Voor operationeel
# gebruik is dat een harde eis (herleidbaarheid van een oordeel).
snapshots_t = Table(
    "analysis_snapshots", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("created_by", String(128)),
    Column("label", Text),
    Column("aggregation", String(16)),
    Column("horizon", Integer),
    Column("n_rows", Integer),
    Column("n_alerts", Integer),
    Column("payload", Text, nullable=False),   # JSON: alerts + normbeeld-samenvatting
    Index("ix_snap_dataset_ts", "dataset_id", "created_at"),
)

# Verstuurde meldingen: welke afwijking is al gemeld. Zonder dit stuurt
# elke run dezelfde waarschuwing opnieuw, en dan wordt het kanaal binnen
# twee weken genegeerd — gevaarlijker dan geen kanaal, want je denkt dat
# je gewaarschuwd wordt.
notifications_t = Table(
    "notifications", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("finding_key", String(64), nullable=False),
    Column("sent_at", DateTime, nullable=False),
    UniqueConstraint("dataset_id", "finding_key", name="uq_notif_dataset_key"),
)

# Ingest-runs: één regel per connector-run (geautomatiseerde inwinning).
# Basis voor bron-gezondheid ("is mijn data actueel?") en alerting.
ingest_runs_t = Table(
    "ingest_runs", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(128), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("status", String(16), nullable=False),  # 'ok' / 'error'
    Column("rows_offered", Integer),
    Column("rows_added", Integer),
    Column("error", Text),
    Index("ix_ingest_source_ts", "source", "started_at"),
)


# ---------------------------------------------------------------------------
# Engine (per URL gecachet zodat tests die DB_PATH monkeypatchen werken)
# ---------------------------------------------------------------------------
_engines: dict = {}


def _database_url() -> str:
    """Bepaal de connectie-URL. Postgres als geconfigureerd, anders SQLite."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            import streamlit as st
            url = st.secrets.get("database_url")
        except Exception:
            url = None
    if url:
        # Supabase/Heroku geven soms 'postgres://'; SQLAlchemy wil de driver.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DB_PATH}"


def is_persistent() -> bool:
    """True wanneer een externe (persistente) database is geconfigureerd."""
    return not _database_url().startswith("sqlite")


def _engine():
    url = _database_url()
    eng = _engines.get(url)
    if eng is None:
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        eng = create_engine(url, connect_args=connect_args, future=True,
                            pool_pre_ping=not url.startswith("sqlite"))
        _engines[url] = eng
    return eng


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def init_db() -> None:
    _metadata.create_all(_engine())


# ---------------------------------------------------------------------------
# Audit-trail
# ---------------------------------------------------------------------------
def current_user() -> str:
    """Naam van de huidige gebruiker voor de audit-trail.

    De bepaling zelf zit in core/authz.py (SSO-header → env-var →
    'onbekend'); hier alleen de naam, zodat storage niet afhankelijk is
    van de rollen-logica.
    """
    try:
        from core.authz import current_identity
        return current_identity().username
    except Exception:  # authz niet beschikbaar (bv. losse migratie-run)
        return os.environ.get("SENTINEL_USER") or "onbekend"


def _client_info() -> str | None:
    try:
        import streamlit as st
        fwd = st.context.headers.get("X-Forwarded-For")
        return str(fwd) if fwd else None
    except Exception:
        return None


def record_audit(action: str, object_type: str | None = None,
                 object_id: int | str | None = None,
                 detail: dict | None = None,
                 username: str | None = None) -> None:
    """Schrijf één audit-regel. Mag NOOIT de hoofdoperatie laten falen:
    fouten worden gelogd, niet doorgegooid."""
    try:
        # Rol en herkomst meeschrijven: bij een audit wil je niet alleen
        # weten wíé iets deed, maar ook met welke rechten en of die
        # identiteit van de SSO-proxy kwam of alleen uit een env-var.
        payload = dict(detail or {})
        try:
            from core.authz import current_identity
            ident = current_identity()
            payload.setdefault("_role", ident.role)
            payload.setdefault("_identity_source", ident.source)
            actor = username or ident.username
        except Exception:
            actor = username or current_user()

        with _engine().begin() as con:
            con.execute(insert(audit_log_t).values(
                ts=datetime.now(UTC).replace(tzinfo=None),
                username=actor,
                action=action,
                object_type=object_type,
                object_id=str(object_id) if object_id is not None else None,
                detail=json.dumps(payload, default=str) if payload else None,
                client=_client_info(),
            ))
    except Exception:
        _logger.exception("audit-regel schrijven faalde",
                          extra={"ctx": {"action": action}})


def list_audit(limit: int = 200) -> list[dict]:
    """Recentste audit-regels, nieuwste eerst (voor de beheer-weergave)."""
    with _engine().connect() as con:
        rows = con.execute(
            select(audit_log_t).order_by(audit_log_t.c.ts.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Ingest-runs (geautomatiseerde inwinning)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Analyse-momentopnames
# ---------------------------------------------------------------------------
def save_snapshot(dataset_id: int, alerts: list, normbeelds: dict,
                  aggregation: str, horizon: int, n_rows: int,
                  label: str | None = None) -> int:
    """Leg vast wat de analyse op dit moment zei.

    Bewaart de alerts plus een compacte samenvatting per regio (verwacht
    niveau, bandgrenzen, band-model, aantal recente afwijkingen) — niet de
    volledige reeksen: die zijn reproduceerbaar uit de ruwe data, de
    beoordeling van het moment niet.
    """
    summary = {}
    for loc, nb in (normbeelds or {}).items():
        summary[str(loc)] = {
            "expected": round(float(nb.expected_value), 3),
            "lower": round(float(nb.lower_band), 3),
            "upper": round(float(nb.upper_band), 3),
            "band_model": getattr(nb, "band_model", None),
            "band_coverage": (round(float(nb.band_coverage), 3)
                              if nb.band_coverage is not None else None),
            "confidence": nb.confidence,
            "n_recent_deviations": int(nb.n_recent_deviations),
            "methods_used": list(nb.methods_used),
        }
    payload = json.dumps({
        "alerts": _jsonable(alerts),
        "normbeelds": summary,
    }, default=str)

    with _engine().begin() as con:
        res = con.execute(insert(snapshots_t).values(
            dataset_id=dataset_id,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            created_by=current_user(),
            label=label,
            aggregation=aggregation,
            horizon=int(horizon),
            n_rows=int(n_rows),
            n_alerts=len(alerts or []),
            payload=payload,
        ))
        snap_id = int(res.inserted_primary_key[0])
    record_audit("save_snapshot", "dataset", str(dataset_id),
                 {"snapshot_id": snap_id, "n_alerts": len(alerts or [])})
    return snap_id


def _jsonable(obj):
    """pandas/numpy-types naar iets dat json.dumps aankan."""
    if isinstance(obj, list):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "item"):          # numpy scalar
        try:
            return obj.item()
        except Exception:
            return str(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def list_snapshots(dataset_id: int | None = None, limit: int = 50) -> list[dict]:
    """Momentopnames, nieuwste eerst (zonder payload — die is groot)."""
    cols = [c for c in snapshots_t.c if c.name != "payload"]
    stmt = select(*cols).order_by(snapshots_t.c.created_at.desc())
    if dataset_id is not None:
        stmt = stmt.where(snapshots_t.c.dataset_id == dataset_id)
    with _engine().connect() as con:
        rows = con.execute(stmt.limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id: int) -> dict | None:
    with _engine().connect() as con:
        row = con.execute(
            select(snapshots_t).where(snapshots_t.c.id == snapshot_id)
        ).mappings().first()
    if row is None:
        return None
    out = dict(row)
    with contextlib.suppress(Exception):
        out["payload"] = json.loads(out["payload"])
    return out


def notified_keys(dataset_id: int) -> set:
    """Sleutels van afwijkingen die al gemeld zijn."""
    with _engine().connect() as con:
        rows = con.execute(
            select(notifications_t.c.finding_key)
            .where(notifications_t.c.dataset_id == dataset_id)
        ).scalars().all()
    return set(rows)


def mark_notified(dataset_id: int, keys: list) -> None:
    """Leg vast dat deze afwijkingen zijn gemeld (idempotent)."""
    if not keys:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [{"dataset_id": dataset_id, "finding_key": k, "sent_at": now}
            for k in keys]
    with _engine().begin() as con:
        _insert_ignore_conflicts(con, rows, table=notifications_t,
                                 conflict_cols=("dataset_id", "finding_key"))


def record_ingest_run(source: str, started_at: datetime, status: str,
                      rows_offered: int | None = None,
                      rows_added: int | None = None,
                      error: str | None = None) -> None:
    with _engine().begin() as con:
        con.execute(insert(ingest_runs_t).values(
            source=source,
            started_at=started_at,
            finished_at=datetime.now(UTC).replace(tzinfo=None),
            status=status,
            rows_offered=rows_offered,
            rows_added=rows_added,
            error=error,
        ))


def list_ingest_runs(source: str | None = None, limit: int = 100) -> list[dict]:
    stmt = select(ingest_runs_t).order_by(ingest_runs_t.c.started_at.desc())
    if source:
        stmt = stmt.where(ingest_runs_t.c.source == source)
    with _engine().connect() as con:
        rows = con.execute(stmt.limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def source_health() -> list[dict]:
    """Per bron: laatste run, status en rijen — de basis voor het
    bron-gezondheidspaneel ("werk ik met actuele data?")."""
    runs = list_ingest_runs(limit=500)
    seen: dict[str, dict] = {}
    for r in runs:  # nieuwste eerst
        src = r["source"]
        if src not in seen:
            seen[src] = {
                "source": src,
                "last_run": r["started_at"],
                "last_status": r["status"],
                "last_rows_added": r["rows_added"],
                "last_error": r["error"],
            }
        if seen[src].get("last_success") is None and r["status"] == "ok":
            seen[src]["last_success"] = r["started_at"]
    return list(seen.values())


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
def can_see_dataset(row, identity=None) -> bool:
    """Need-to-know: mag deze identiteit deze dataset zien?

    Zonder `required_group` is een dataset zichtbaar voor iedereen met
    leesrecht. Met een groep geldt: alleen leden van die groep, plus
    beheerders (die moeten de opzet kunnen beheren).
    """
    required = (row.get("required_group") if isinstance(row, dict)
                else getattr(row, "required_group", None))
    if not required:
        return True
    try:
        from core.authz import ADMIN, current_identity
        ident = identity or current_identity()
    except Exception:
        return True  # authz niet beschikbaar (migratie/CLI): niet blokkeren
    if ident.role == ADMIN:
        return True
    return required.strip().lower() in {g.strip().lower() for g in ident.groups}


def list_datasets(include_hidden: bool = False) -> list[dict]:
    """Datasets die de huidige gebruiker mag zien.

    `include_hidden=True` negeert de compartimentering — alleen voor
    beheer-weergaven en achtergrondtaken.
    """
    with _engine().connect() as con:
        rows = con.execute(
            select(datasets).order_by(datasets.c.name)
        ).mappings().all()
    out = []
    for r in rows:
        item = {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "created_at": r["created_at"],
            "column_mapping": json.loads(r["column_mapping"]),
            "required_group": r["required_group"],
        }
        if include_hidden or can_see_dataset(item):
            out.append(item)
    return out


def set_dataset_group(dataset_id: int, group: str | None) -> None:
    """Zet (of wis) de compartiment-groep van een dataset."""
    value = (group or "").strip() or None
    with _engine().begin() as con:
        con.execute(
            datasets.update()
            .where(datasets.c.id == dataset_id)
            .values(required_group=value)
        )
    record_audit("dataset_compartiment_gewijzigd", "dataset", dataset_id,
                 {"required_group": value})


def create_dataset(name: str, description: str, column_mapping: dict) -> int:
    with _engine().begin() as con:
        result = con.execute(
            insert(datasets).values(
                name=name, description=description,
                created_at=_now_iso(),
                column_mapping=json.dumps(column_mapping),
            )
        )
        new_id = int(result.inserted_primary_key[0])
    record_audit("dataset_aangemaakt", "dataset", new_id, {"name": name})
    return new_id


def update_dataset_mapping(dataset_id: int, column_mapping: dict) -> None:
    """Werk de mapping/metadata van een dataset bij (bv. gap-policy of
    bron-betrouwbaarheid). Metadata leeft in dezelfde JSON als de mapping."""
    with _engine().begin() as con:
        con.execute(
            datasets.update().where(datasets.c.id == dataset_id).values(
                column_mapping=json.dumps(column_mapping)
            )
        )
    record_audit("dataset_mapping_bijgewerkt", "dataset", dataset_id)


def delete_dataset(dataset_id: int) -> None:
    with _engine().begin() as con:
        # Expliciet kinderen verwijderen (SQLite handhaaft FK-cascade niet altijd)
        con.execute(delete(annotations_t).where(
            annotations_t.c.dataset_id == dataset_id))
        n = con.execute(delete(observations).where(
            observations.c.dataset_id == dataset_id)).rowcount
        con.execute(delete(datasets).where(datasets.c.id == dataset_id))
    record_audit("dataset_verwijderd", "dataset", dataset_id,
                 {"observaties_verwijderd": n})


def clear_observations(dataset_id: int) -> None:
    """Verwijder alle observaties van een dataset (dataset zelf blijft)."""
    with _engine().begin() as con:
        n = con.execute(delete(observations).where(
            observations.c.dataset_id == dataset_id)).rowcount
    record_audit("observaties_gewist", "dataset", dataset_id,
                 {"observaties_verwijderd": n})


def dataset_data_hash(dataset_id: int) -> str:
    """Goedkope signatuur die wijzigt zodra rijen worden toegevoegd/verwijderd."""
    with _engine().connect() as con:
        row = con.execute(
            select(
                func.count(observations.c.id),
                func.max(observations.c.timestamp),
                func.max(observations.c.id),
            ).where(observations.c.dataset_id == dataset_id)
        ).one()
    return f"{row[0]}|{row[1]}|{row[2]}"


# ---------------------------------------------------------------------------
# Observaties
# ---------------------------------------------------------------------------
def _safe(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v


def _to_naive_utc(ts) -> datetime:
    """Pandas/py-timestamp → naïeve UTC-datetime voor de DateTime-kolom."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.to_pydatetime()


def _insert_ignore_conflicts(con, rows: list[dict], table=None,
                             conflict_cols: tuple = ("dataset_id", "row_hash"),
                             ) -> None:
    """Batch-insert die botsende rijen stil overslaat.

    Gebruikt de dialect-native ON CONFLICT DO NOTHING (SQLite én Postgres)
    zodat dedupe in de database gebeurt in plaats van alle bestaande sleutels
    naar de client te halen (dat schaalde O(datasetgrootte) per import).

    Standaard op `observations`/(dataset_id, row_hash); met `table` en
    `conflict_cols` ook bruikbaar voor andere tabellen met dezelfde
    behoefte, zoals verstuurde meldingen.
    """
    target = observations if table is None else table
    dialect = con.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:  # onbekend dialect: val terug op gewone insert (kan IntegrityError geven)
        con.execute(insert(target), rows)
        return
    stmt = dialect_insert(target).on_conflict_do_nothing(
        index_elements=list(conflict_cols)
    )
    con.execute(stmt, rows)


def insert_observations(dataset_id: int, df: pd.DataFrame) -> int:
    """Insert rijen; dedupe via de unique constraint op (dataset_id, row_hash)
    met ON CONFLICT DO NOTHING. Returnt het aantal daadwerkelijk nieuwe rijen."""
    extra_cols = [c for c in df.columns if c not in STANDARD_FIELDS]

    rows: list[dict] = []
    for row in df.to_dict("records"):
        ts_raw = row.get("timestamp")
        if pd.isna(ts_raw):
            continue

        extras = {k: _safe(row.get(k)) for k in extra_cols}
        extras_json = json.dumps(extras, default=str)

        # Let op: de hash-sleutel gebruikt de RUWE veldwaarden (zoals bij de
        # oorspronkelijke implementatie), zodat bestaande databases dezelfde
        # hashes houden en her-import geen duplicaten oplevert.
        key_str = "|".join(
            str(_safe(row.get(c))) for c in
            ["timestamp", "value", "category", "location_name", "lat", "lon"]
        ) + "|" + extras_json
        row_hash = hashlib.sha256(key_str.encode()).hexdigest()

        val = row.get("value")
        lat = row.get("lat")
        lon = row.get("lon")
        rows.append({
            "dataset_id": dataset_id,
            "timestamp": _to_naive_utc(ts_raw),
            "value": None if val is None or pd.isna(val) else float(val),
            "category": _safe(row.get("category")),
            "location_name": _safe(row.get("location_name")),
            "lat": None if lat is None or pd.isna(lat) else float(lat),
            "lon": None if lon is None or pd.isna(lon) else float(lon),
            "extras": extras_json,
            "row_hash": row_hash,
        })

    if not rows:
        return 0

    count_stmt = select(func.count(observations.c.id)).where(
        observations.c.dataset_id == dataset_id
    )
    with _engine().begin() as con:
        before = con.execute(count_stmt).scalar_one()
        _insert_ignore_conflicts(con, rows)
        after = con.execute(count_stmt).scalar_one()

    n_new = int(after - before)
    record_audit("observaties_geimporteerd", "dataset", dataset_id,
                 {"aangeboden": len(rows), "nieuw": n_new})
    return n_new


def load_observations(dataset_id: int) -> pd.DataFrame:
    stmt = select(
        observations.c.timestamp, observations.c.value,
        observations.c.category, observations.c.location_name,
        observations.c.lat, observations.c.lon, observations.c.extras,
    ).where(observations.c.dataset_id == dataset_id).order_by(
        observations.c.timestamp
    )
    with _engine().connect() as con:
        df = pd.read_sql_query(stmt, con)
    if df.empty:
        return df
    # Defensief normaliseren: data uit oudere imports of andere DB-backends
    # kan afwijkende types bevatten (strings, Decimals, gemengde formaten).
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce",
                                         format="mixed")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    for col in ("lat", "lon"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("category", "location_name"):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: str(v) if v is not None and not pd.isna(v) else None
            )
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
    extras_series = df["extras"].apply(lambda s: json.loads(s) if s else {})
    extras_df = pd.json_normalize(extras_series)
    df = pd.concat([df.drop(columns=["extras"]), extras_df], axis=1)
    return df


# ---------------------------------------------------------------------------
# Annotaties (gebruikt door core/annotations.py)
# ---------------------------------------------------------------------------
def get_annotation_row(dataset_id: int, key: str) -> dict | None:
    with _engine().connect() as con:
        row = con.execute(
            select(annotations_t.c.note, annotations_t.c.status,
                   annotations_t.c.updated_at).where(
                (annotations_t.c.dataset_id == dataset_id)
                & (annotations_t.c.finding_key == key)
            )
        ).mappings().first()
    return dict(row) if row else None


def upsert_annotation(dataset_id: int, key: str, note: str | None,
                      status: str) -> None:
    with _engine().begin() as con:
        existing = con.execute(
            select(annotations_t.c.id).where(
                (annotations_t.c.dataset_id == dataset_id)
                & (annotations_t.c.finding_key == key)
            )
        ).first()
        if existing:
            con.execute(
                annotations_t.update().where(
                    (annotations_t.c.dataset_id == dataset_id)
                    & (annotations_t.c.finding_key == key)
                ).values(note=note or "", status=status, updated_at=_now_iso())
            )
        else:
            con.execute(insert(annotations_t).values(
                dataset_id=dataset_id, finding_key=key,
                note=note or "", status=status, updated_at=_now_iso(),
            ))


def list_annotation_rows(dataset_id: int) -> dict:
    with _engine().connect() as con:
        rows = con.execute(
            select(annotations_t.c.finding_key, annotations_t.c.note,
                   annotations_t.c.status, annotations_t.c.updated_at).where(
                annotations_t.c.dataset_id == dataset_id
            )
        ).mappings().all()
    return {
        r["finding_key"]: {
            "note": r["note"], "status": r["status"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


# ---------------------------------------------------------------------------
# Markeringen (handmatige gebeurtenissen op de tijdlijn)
# ---------------------------------------------------------------------------
def _ensure_table(table) -> None:
    """Maak één tabel aan als hij ontbreekt. Vangt het geval op waarin een
    oudere database (van een eerdere deploy) een nieuwere tabel mist."""
    try:
        table.create(_engine(), checkfirst=True)
    except Exception:
        _logger.exception("tabel aanmaken faalde",
                          extra={"ctx": {"table": table.name}})


def add_event(event_date: str, label: str) -> int:
    _ensure_table(events_t)
    with _engine().begin() as con:
        result = con.execute(insert(events_t).values(
            event_date=event_date, label=label, created_at=_now_iso(),
        ))
        new_id = int(result.inserted_primary_key[0])
    record_audit("markering_toegevoegd", "event", new_id,
                 {"event_date": event_date, "label": label})
    return new_id


def list_events() -> list[dict]:
    try:
        with _engine().connect() as con:
            rows = con.execute(
                select(events_t).order_by(events_t.c.event_date)
            ).mappings().all()
    except Exception:
        # Tabel bestaat mogelijk nog niet in een oudere database: aanmaken.
        _ensure_table(events_t)
        return []
    return [
        {"id": r["id"], "event_date": r["event_date"], "label": r["label"]}
        for r in rows
    ]


def delete_event(event_id: int) -> None:
    _ensure_table(events_t)
    with _engine().begin() as con:
        con.execute(delete(events_t).where(events_t.c.id == event_id))
    record_audit("markering_verwijderd", "event", event_id)


# ---------------------------------------------------------------------------
# Opgeslagen weergaves (analytische workflows)
# ---------------------------------------------------------------------------
def save_view(name: str, payload: dict) -> int:
    _ensure_table(saved_views_t)
    with _engine().begin() as con:
        result = con.execute(insert(saved_views_t).values(
            name=name, payload=json.dumps(payload), created_at=_now_iso(),
        ))
        new_id = int(result.inserted_primary_key[0])
    record_audit("weergave_opgeslagen", "view", new_id, {"name": name})
    return new_id


def list_views() -> list[dict]:
    try:
        with _engine().connect() as con:
            rows = con.execute(
                select(saved_views_t).order_by(saved_views_t.c.name)
            ).mappings().all()
    except Exception:
        _ensure_table(saved_views_t)
        return []
    return [
        {"id": r["id"], "name": r["name"],
         "payload": json.loads(r["payload"])}
        for r in rows
    ]


def delete_view(view_id: int) -> None:
    _ensure_table(saved_views_t)
    with _engine().begin() as con:
        con.execute(delete(saved_views_t).where(saved_views_t.c.id == view_id))
    record_audit("weergave_verwijderd", "view", view_id)
