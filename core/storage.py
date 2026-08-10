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
    Boolean,
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
    # Wanneer wíj deze rij kregen (naïef UTC). `timestamp` zegt wanneer iets
    # gebeurde; deze kolom zegt wanneer het bekend werd. Zonder dat onderscheid
    # is niet te reconstrueren wat de tool op een gegeven dag had kunnen zeggen
    # — laat binnengekomen rapportage zit dan stilzwijgend in de historie.
    # Zie migrations/versions/0007_ingested_at.py en sentinel/core/time/.
    Column("ingested_at", DateTime),
    # True = `ingested_at` is een schatting (backfill), niet waargenomen.
    Column("ingest_estimated", Boolean),
    UniqueConstraint("dataset_id", "row_hash", name="uq_obs_dataset_hash"),
    Index("ix_obs_dataset_ts", "dataset_id", "timestamp"),
    Index("ix_obs_dataset_ingested", "dataset_id", "ingested_at"),
)

# Entity-engine output: getypeerde gebeurtenissen (loiter, ais_gap, ...) met
# dezelfde point-in-time kolommen als `observations`. Bewust een eigen tabel
# en niet `events_t`: die laatste is een analisten-annotatie (datum + label)
# en draagt geen herkomst, geen entiteit en geen aankomsttijd.
#
# Dit is de tabel waar niet-onderhandelbare eis 7 op rust: entiteit-regio's en
# count-regio's delen dezelfde indicator-machinerie, en vanaf hier ook dezelfde
# opslagvorm. Zie ARCHITECTURE_V2.md §3.1.
entity_events_t = Table(
    "entity_events", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("region_key", String(64), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("event_time", DateTime, nullable=False),
    # Wanneer wíj hem kregen. Voor afgeleide events is dat het moment waarop
    # de detector draaide, niet het moment van het gedrag zelf.
    Column("ingested_at", DateTime, nullable=False),
    Column("ingest_estimated", Boolean),
    Column("entity_key", String(128)),
    Column("entity_kind", String(32)),
    Column("area_key", String(64)),
    Column("lat", Float),
    Column("lon", Float),
    Column("magnitude", Float),
    Column("unit", String(32)),
    # Herkomst: welke bronnen, welke methode, welke laag. Zonder dit is een
    # afgeleid event niet te reproduceren en dus niet te weerleggen.
    Column("producer", String(32)),
    Column("method", String(128)),
    Column("source_keys", Text),
    Column("attrs", Text),
    Column("row_hash", String(64), nullable=False),
    UniqueConstraint("dataset_id", "row_hash", name="uq_evt_dataset_hash"),
    Index("ix_evt_region_time", "dataset_id", "region_key", "event_time"),
    Index("ix_evt_region_ingested", "dataset_id", "region_key", "ingested_at"),
)

# Ruwe positieberichten (AIS en soortgelijk). Dit is de bron waaruit de
# entity-primitieven events afleiden, én — belangrijker — de plek waar de
# *waargenomen populatie* vandaan komt: elk vaartuig dat iets uitzond, ook de
# stille meerderheid die niets deed. Zonder die noemer is zeldzaamheid niet
# toetsbaar en kan de peer-baseline alleen op magnitude oordelen.
#
# Bewust géén PostGIS. Het plan noemde het, maar niets in deze codebase doet
# een echte ruimtelijke query: `sentinel/entity/geo.py` rekent haversine zonder
# geometrie-stack, en de noemer hierboven is een SELECT DISTINCT. Een harde
# PostGIS-afhankelijkheid zou de SQLite-testweg breken waar de hele suite op
# draait, in ruil voor niets dat vandaag gebruikt wordt. Waar het wél gaat
# lonen: zodra een indicator "binnen dit polygoon" vraagt in plaats van "binnen
# deze bounding box" — kabelcorridors zijn lijnen, geen rechthoeken.
positions_t = Table(
    "positions", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("entity_key", String(128), nullable=False),
    Column("entity_kind", String(32)),
    Column("timestamp", DateTime, nullable=False),
    Column("ingested_at", DateTime, nullable=False),
    Column("ingest_estimated", Boolean),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("sog", Float),          # speed over ground, knopen
    Column("cog", Float),          # course over ground, graden
    Column("heading", Float),
    # De klasse waarop peers gegroepeerd worden. Op de positie en niet op een
    # aparte vaartuigtabel, omdat een schip van klasse kan wisselen en de
    # vergelijking hoort te gebeuren met wat het op dát moment was.
    Column("vessel_class", String(64)),
    Column("source_key", String(64)),
    Column("row_hash", String(64), nullable=False),
    UniqueConstraint("dataset_id", "row_hash", name="uq_pos_dataset_hash"),
    Index("ix_pos_entity_ts", "dataset_id", "entity_key", "timestamp"),
    Index("ix_pos_dataset_ts", "dataset_id", "timestamp"),
    Index("ix_pos_dataset_ingested", "dataset_id", "ingested_at"),
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

# Watchboard-indicatoren: vooraf vastgelegd wát ertoe doet. Bewust
# opgeslagen en niet in code, zodat een analist ze zelf kan beheren én er
# achteraf een datum bij staat — "we hadden dit vooraf opgeschreven" is
# alleen navolgbaar als het ergens vastligt.
indicators_t = Table(
    "indicators", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("condition", String(32), nullable=False),
    Column("location", Text),
    Column("category", Text),
    Column("threshold", Float),
    Column("periods", Integer, nullable=False, server_default="1"),
    Column("meaning", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False),
    Column("created_by", String(128)),
    Index("ix_indicator_dataset", "dataset_id"),
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


def add_indicator(dataset_id: int, name: str, condition: str,
                  location: str | None = None, category: str | None = None,
                  threshold: float | None = None, periods: int = 1,
                  meaning: str = "") -> int:
    with _engine().begin() as con:
        res = con.execute(insert(indicators_t).values(
            dataset_id=dataset_id, name=name, condition=condition,
            location=location or None, category=category or None,
            threshold=threshold, periods=max(1, int(periods)),
            meaning=meaning or "", enabled=1,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            created_by=current_user(),
        ))
        new_id = int(res.inserted_primary_key[0])
    record_audit("indicator_toegevoegd", "indicator", new_id,
                 {"dataset_id": dataset_id, "name": name,
                  "condition": condition})
    return new_id


def list_indicators(dataset_id: int | None = None) -> list[dict]:
    stmt = select(indicators_t).order_by(indicators_t.c.name)
    if dataset_id is not None:
        stmt = stmt.where(indicators_t.c.dataset_id == dataset_id)
    with _engine().connect() as con:
        rows = con.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def set_indicator_enabled(indicator_id: int, enabled: bool) -> None:
    with _engine().begin() as con:
        con.execute(indicators_t.update()
                    .where(indicators_t.c.id == indicator_id)
                    .values(enabled=1 if enabled else 0))
    record_audit("indicator_gewijzigd", "indicator", indicator_id,
                 {"enabled": bool(enabled)})


def delete_indicator(indicator_id: int) -> None:
    with _engine().begin() as con:
        con.execute(delete(indicators_t).where(
            indicators_t.c.id == indicator_id))
    record_audit("indicator_verwijderd", "indicator", indicator_id)


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


def rename_dataset(dataset_id: int, new_name: str) -> None:
    """Hernoem een dataset.

    De naam is uniek in de database; een botsing geeft een nette fout in
    plaats van een IntegrityError uit de diepte. De oude naam gaat mee in
    de audit-trail, want een naamswijziging maakt oudere verwijzingen
    (rapporten, meldingen) anders onnavolgbaar.
    """
    name = (new_name or "").strip()
    if not name:
        raise ValueError("Naam mag niet leeg zijn.")

    with _engine().connect() as con:
        row = con.execute(
            select(datasets.c.name).where(datasets.c.id == dataset_id)
        ).first()
        clash = con.execute(
            select(datasets.c.id)
            .where(datasets.c.name == name, datasets.c.id != dataset_id)
        ).first()
    if row is None:
        raise ValueError("Dataset bestaat niet.")
    if clash is not None:
        raise ValueError(f"Er bestaat al een dataset met de naam '{name}'.")

    old_name = row[0]
    if old_name == name:
        return
    with _engine().begin() as con:
        con.execute(datasets.update()
                    .where(datasets.c.id == dataset_id)
                    .values(name=name))
    record_audit("dataset_hernoemd", "dataset", dataset_id,
                 {"van": old_name, "naar": name})


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


#: Aankomst-beleid voor `insert_observations`.
#:
#: - "now": deze rijen komen nú binnen (connector-inwinning). `ingested_at` is
#:   waargenomen, niet geschat.
#: - "event_time": bulk-import van historie. Wanneer die rijen destijds
#:   beschikbaar waren, weten we niet; we nemen aan "meteen" en markeren dat
#:   als schatting. Zonder deze aanname is replay over historie onmogelijk —
#:   mét de aanname is replay optimistisch, en dat moet zichtbaar zijn.
ARRIVAL_POLICIES = ("now", "event_time")


def insert_observations(dataset_id: int, df: pd.DataFrame,
                        arrival: str = "now") -> int:
    """Insert rijen; dedupe via de unique constraint op (dataset_id, row_hash)
    met ON CONFLICT DO NOTHING. Returnt het aantal daadwerkelijk nieuwe rijen.

    `arrival` bepaalt hoe `ingested_at` wordt gevuld (zie ARRIVAL_POLICIES).
    Een expliciete `ingested_at`-kolom in `df` wint altijd: sommige bronnen
    melden zelf wanneer een bericht is ontvangen, en dat is de beste waarheid
    die we kunnen krijgen.

    Let op: `row_hash` verandert niet mee. Een her-import van dezelfde rij
    behoudt dus de oorspronkelijke `ingested_at` — precies goed, want de
    eerste keer dat we hem zagen ís het moment waarop we het wisten.
    """
    if arrival not in ARRIVAL_POLICIES:
        raise ValueError(
            f"onbekend arrival-beleid {arrival!r}; kies uit {ARRIVAL_POLICIES}"
        )
    now = datetime.now(UTC).replace(tzinfo=None)
    has_explicit_arrival = "ingested_at" in df.columns
    extra_cols = [c for c in df.columns
                  if c not in STANDARD_FIELDS and c != "ingested_at"]

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
        ts = _to_naive_utc(ts_raw)

        explicit = row.get("ingested_at") if has_explicit_arrival else None
        if explicit is not None and not pd.isna(explicit):
            ingested_at, estimated = _to_naive_utc(explicit), False
        elif arrival == "event_time":
            ingested_at, estimated = ts, True
        else:
            ingested_at, estimated = now, False

        rows.append({
            "dataset_id": dataset_id,
            "timestamp": ts,
            "value": None if val is None or pd.isna(val) else float(val),
            "category": _safe(row.get("category")),
            "location_name": _safe(row.get("location_name")),
            "lat": None if lat is None or pd.isna(lat) else float(lat),
            "lon": None if lon is None or pd.isna(lon) else float(lon),
            "extras": extras_json,
            "row_hash": row_hash,
            "ingested_at": ingested_at,
            "ingest_estimated": estimated,
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


def _normalize_observations(df: pd.DataFrame) -> pd.DataFrame:
    """Defensief normaliseren: data uit oudere imports of andere DB-backends
    kan afwijkende types bevatten (strings, Decimals, gemengde formaten)."""
    if df.empty:
        return df
    for col in ("timestamp", "ingested_at"):
        if col in df.columns and not pd.api.types.is_datetime64_any_dtype(
                df[col]):
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    for col in ("lat", "lon"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("category", "location_name"):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: str(v) if v is not None and not pd.isna(v) else None
            )
    if "ingest_estimated" in df.columns:
        # NULL = onbekend = behandel als schatting. Een rij waarvan we de
        # herkomst niet kennen mag geen exacte reconstructie suggereren.
        df["ingest_estimated"] = (
            df["ingest_estimated"].fillna(True).astype(bool)
        )
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
    extras_series = df["extras"].apply(lambda s: json.loads(s) if s else {})
    extras_df = pd.json_normalize(extras_series)
    return pd.concat([df.drop(columns=["extras"]), extras_df], axis=1)


_OBS_COLUMNS = (
    observations.c.timestamp, observations.c.value,
    observations.c.category, observations.c.location_name,
    observations.c.lat, observations.c.lon, observations.c.extras,
)


def load_observations(dataset_id: int) -> pd.DataFrame:
    """Alle waarnemingen van een dataset, ongeacht wanneer ze binnenkwamen.

    Voor productie-uitvoer hoort `load_observations_as_of` gebruikt te worden;
    deze functie is voor beheer, export en de v1-paden.
    """
    stmt = select(*_OBS_COLUMNS).where(
        observations.c.dataset_id == dataset_id
    ).order_by(observations.c.timestamp)
    with _engine().connect() as con:
        df = pd.read_sql_query(stmt, con)
    return _normalize_observations(df)


def load_observations_as_of(dataset_id: int, as_of: datetime) -> pd.DataFrame:
    """Waarnemingen zoals ze op `as_of` bekend waren.

    Twee filters, allebei nodig:

    - `ingested_at <= as_of` — we konden niets gebruiken wat nog niet binnen
      was. Dit is wat laat binnengekomen rapportage uit een replay houdt.
    - `timestamp <= as_of` — een waarschuwingssysteem op tijdstip t hoort geen
      gebeurtenissen te kennen die ná t plaatsvinden, ook niet als een bron ze
      vooruit heeft gemeld.

    Rijen zonder `ingested_at` (pre-migratie, nooit gebackfilled) worden
    behandeld alsof ze bekend waren op hun `timestamp`; ze dragen dan
    `ingest_estimated = True`, zodat de aanname zichtbaar blijft.

    Deze functie is het enige pad waarlangs `sentinel.core.time.AsOfView`
    data leest. Zie ARCHITECTURE_V2.md §2.1.
    """
    as_of = _to_naive_utc(as_of)
    known = observations.c.ingested_at
    stmt = select(
        *_OBS_COLUMNS,
        func.coalesce(known, observations.c.timestamp).label("ingested_at"),
        observations.c.ingest_estimated,
    ).where(
        (observations.c.dataset_id == dataset_id)
        & (observations.c.timestamp <= as_of)
        & (func.coalesce(known, observations.c.timestamp) <= as_of)
    ).order_by(observations.c.timestamp)
    with _engine().connect() as con:
        df = pd.read_sql_query(stmt, con)
    return _normalize_observations(df)


# ---------------------------------------------------------------------------
# Posities (AIS en soortgelijk)
# ---------------------------------------------------------------------------
POSITION_FIELDS = ("entity_key", "entity_kind", "timestamp", "lat", "lon",
                   "sog", "cog", "heading", "vessel_class", "source_key")


def insert_positions(dataset_id: int, df: pd.DataFrame,
                     arrival: str = "now") -> int:
    """Sla positieberichten op; dedupe op (entiteit, tijd, plaats).

    Hetzelfde aankomstbeleid als bij observaties: `"now"` voor live inwinning,
    `"event_time"` voor bulk-historie (die rijen worden dan als geschat
    gemarkeerd). Een expliciete `ingested_at`-kolom wint altijd.

    De hash dekt entiteit, tijdstip en positie. Twee berichten van hetzelfde
    schip op hetzelfde moment vanaf dezelfde plek zijn hetzelfde bericht, ook
    als ze via twee ontvangers binnenkwamen — en dubbele ontvangst is bij AIS
    eerder regel dan uitzondering.
    """
    if arrival not in ARRIVAL_POLICIES:
        raise ValueError(
            f"onbekend arrival-beleid {arrival!r}; kies uit {ARRIVAL_POLICIES}")
    _ensure_table(positions_t)
    if df is None or df.empty:
        return 0

    for required in ("entity_key", "timestamp", "lat", "lon"):
        if required not in df.columns:
            raise ValueError(
                f"positiekolom {required!r} ontbreekt; zonder identiteit, tijd "
                f"en plaats is een positie geen positie")

    now = datetime.now(UTC).replace(tzinfo=None)
    has_explicit_arrival = "ingested_at" in df.columns

    rows: list[dict] = []
    for row in df.to_dict("records"):
        ts_raw, lat, lon = row.get("timestamp"), row.get("lat"), row.get("lon")
        if pd.isna(ts_raw) or pd.isna(lat) or pd.isna(lon):
            continue
        ts = _to_naive_utc(ts_raw)

        explicit = row.get("ingested_at") if has_explicit_arrival else None
        if explicit is not None and not pd.isna(explicit):
            ingested_at, estimated = _to_naive_utc(explicit), False
        elif arrival == "event_time":
            ingested_at, estimated = ts, True
        else:
            ingested_at, estimated = now, False

        key_str = f"{row['entity_key']}|{ts.isoformat()}|{lat:.6f}|{lon:.6f}"
        rows.append({
            "dataset_id": dataset_id,
            "entity_key": str(row["entity_key"]),
            "entity_kind": _safe(row.get("entity_kind")) or "vessel",
            "timestamp": ts,
            "ingested_at": ingested_at,
            "ingest_estimated": estimated,
            "lat": float(lat),
            "lon": float(lon),
            "sog": None if pd.isna(row.get("sog")) else _as_float(row.get("sog")),
            "cog": None if pd.isna(row.get("cog")) else _as_float(row.get("cog")),
            "heading": (None if pd.isna(row.get("heading"))
                        else _as_float(row.get("heading"))),
            "vessel_class": _safe(row.get("vessel_class")),
            "source_key": _safe(row.get("source_key")),
            "row_hash": hashlib.sha256(key_str.encode()).hexdigest(),
        })

    if not rows:
        return 0

    count_stmt = select(func.count(positions_t.c.id)).where(
        positions_t.c.dataset_id == dataset_id)
    with _engine().begin() as con:
        before = con.execute(count_stmt).scalar_one()
        _insert_ignore_conflicts(con, rows, table=positions_t)
        after = con.execute(count_stmt).scalar_one()
    return int(after - before)


def _as_float(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def load_positions_as_of(dataset_id: int, as_of: datetime,
                         since: datetime | None = None,
                         bbox: tuple | None = None) -> pd.DataFrame:
    """Posities zoals ze op `as_of` bekend waren.

    `bbox` is `(lat_min, lat_max, lon_min, lon_max)` — een rechthoek, geen
    polygoon. Dat is genoeg om een regio af te bakenen en niet genoeg voor een
    kabelcorridor; zie de opmerking bij `positions_t` over waar PostGIS gaat
    lonen.
    """
    _ensure_table(positions_t)
    as_of = _to_naive_utc(as_of)
    conditions = [
        positions_t.c.dataset_id == dataset_id,
        positions_t.c.timestamp <= as_of,
        positions_t.c.ingested_at <= as_of,
    ]
    if since is not None:
        conditions.append(positions_t.c.timestamp >= _to_naive_utc(since))
    if bbox is not None:
        lat_min, lat_max, lon_min, lon_max = bbox
        conditions += [
            positions_t.c.lat >= float(lat_min),
            positions_t.c.lat <= float(lat_max),
            positions_t.c.lon >= float(lon_min),
            positions_t.c.lon <= float(lon_max),
        ]

    stmt = select(positions_t).where(*conditions).order_by(
        positions_t.c.entity_key, positions_t.c.timestamp)
    with _engine().connect() as con:
        df = pd.read_sql_query(stmt, con)
    if df.empty:
        return df
    for col in ("timestamp", "ingested_at"):
        if not pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
    return df


# ---------------------------------------------------------------------------
# Entity-events (sentinel/entity → indicator-machinerie)
# ---------------------------------------------------------------------------
def _event_row(dataset_id: int, event) -> dict:
    """Eén `sentinel.core.contracts.Event` als databaserij.

    De hash dekt identiteit, type en tijd — niet de magnitude. Een detector
    die opnieuw draait op dezelfde posities moet hetzelfde event opleveren en
    geen duplicaat; een licht afwijkende duur door een gewijzigde parameter is
    hetzelfde voorval, niet een tweede.
    """
    lineage = event.lineage
    entity_key = event.entity.key if event.entity else None
    key_str = "|".join(str(x) for x in (
        event.region_key, event.event_type, entity_key,
        _to_naive_utc(event.event_time).isoformat(), event.area_key,
    ))
    return {
        "dataset_id": dataset_id,
        "region_key": event.region_key,
        "event_type": event.event_type,
        "event_time": _to_naive_utc(event.event_time),
        "ingested_at": _to_naive_utc(event.ingested_at),
        "ingest_estimated": bool(event.ingest_estimated),
        "entity_key": entity_key,
        "entity_kind": event.entity.kind if event.entity else None,
        "area_key": event.area_key,
        "lat": None if event.geo is None else float(event.geo.lat),
        "lon": None if event.geo is None else float(event.geo.lon),
        "magnitude": (None if event.magnitude is None
                      else float(event.magnitude)),
        "unit": event.unit,
        "producer": lineage.producer.value,
        "method": lineage.method,
        "source_keys": json.dumps(list(lineage.source_keys)),
        "attrs": json.dumps(dict(event.attrs), default=str),
        "row_hash": hashlib.sha256(key_str.encode()).hexdigest(),
    }


def insert_entity_events(dataset_id: int, events) -> int:
    """Sla entity-events op; dedupe zoals bij observaties. Returnt nieuwe rijen.

    Her-draaien van een detector over dezelfde periode voegt niets toe en laat
    de oorspronkelijke `ingested_at` staan — het eerste moment waarop we het
    gedrag zagen ís het moment waarop we het wisten.
    """
    _ensure_table(entity_events_t)
    rows = [_event_row(dataset_id, event) for event in events]
    if not rows:
        return 0

    count_stmt = select(func.count(entity_events_t.c.id)).where(
        entity_events_t.c.dataset_id == dataset_id
    )
    with _engine().begin() as con:
        before = con.execute(count_stmt).scalar_one()
        _insert_ignore_conflicts(con, rows, table=entity_events_t)
        after = con.execute(count_stmt).scalar_one()
    return int(after - before)


def load_entity_events_as_of(dataset_id: int, as_of: datetime,
                             region_key: str | None = None) -> pd.DataFrame:
    """Entity-events zoals ze op `as_of` bekend waren.

    Dezelfde twee filters als `load_observations_as_of`, en om dezelfde reden:
    een detector die op t draaide kan geen gedrag kennen van ná t, en gedrag
    dat pas later is afgeleid was op t nog niet beschikbaar.
    """
    _ensure_table(entity_events_t)
    as_of = _to_naive_utc(as_of)
    conditions = [
        entity_events_t.c.dataset_id == dataset_id,
        entity_events_t.c.event_time <= as_of,
        entity_events_t.c.ingested_at <= as_of,
    ]
    if region_key is not None:
        conditions.append(entity_events_t.c.region_key == region_key)

    stmt = select(entity_events_t).where(*conditions).order_by(
        entity_events_t.c.event_time)
    with _engine().connect() as con:
        df = pd.read_sql_query(stmt, con)
    if df.empty:
        return df
    for col in ("event_time", "ingested_at"):
        if not pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
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
