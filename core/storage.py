"""Opslaglaag via SQLAlchemy Core.

Werkt op twee backends met dezelfde code:
- Lokaal / standaard: SQLite-bestand (data/store.db).
- Productie: externe Postgres (bv. Supabase) als DATABASE_URL is gezet
  (env-var) of `database_url` in .streamlit/secrets.toml staat.

De dedup-logica (query bestaande row_hashes, filter, insert) is bewust
dialect-onafhankelijk, zodat SQLite en Postgres zich identiek gedragen.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    UniqueConstraint, create_engine, delete, func, insert, select,
)

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
)

observations = Table(
    "observations", _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_id", Integer,
           ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("timestamp", String(64), nullable=False),
    Column("value", Float),
    Column("category", Text),
    Column("location_name", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("extras", Text),
    Column("row_hash", String(64), nullable=False),
    UniqueConstraint("dataset_id", "row_hash", name="uq_obs_dataset_hash"),
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
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    _metadata.create_all(_engine())


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
def list_datasets() -> list[dict]:
    with _engine().connect() as con:
        rows = con.execute(
            select(datasets).order_by(datasets.c.name)
        ).mappings().all()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "created_at": r["created_at"],
            "column_mapping": json.loads(r["column_mapping"]),
        }
        for r in rows
    ]


def create_dataset(name: str, description: str, column_mapping: dict) -> int:
    with _engine().begin() as con:
        result = con.execute(
            insert(datasets).values(
                name=name, description=description,
                created_at=_now_iso(),
                column_mapping=json.dumps(column_mapping),
            )
        )
        return int(result.inserted_primary_key[0])


def delete_dataset(dataset_id: int) -> None:
    with _engine().begin() as con:
        # Expliciet kinderen verwijderen (SQLite handhaaft FK-cascade niet altijd)
        con.execute(delete(annotations_t).where(
            annotations_t.c.dataset_id == dataset_id))
        con.execute(delete(observations).where(
            observations.c.dataset_id == dataset_id))
        con.execute(delete(datasets).where(datasets.c.id == dataset_id))


def clear_observations(dataset_id: int) -> None:
    """Verwijder alle observaties van een dataset (dataset zelf blijft)."""
    with _engine().begin() as con:
        con.execute(delete(observations).where(
            observations.c.dataset_id == dataset_id))


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


def insert_observations(dataset_id: int, df: pd.DataFrame) -> int:
    """Insert rijen; dedupe via row_hash (dialect-onafhankelijk: bestaande
    hashes worden opgehaald en de batch wordt gefilterd). Returnt nieuw aantal."""
    rows: list[dict] = []
    hashes: list[str] = []
    for _, row in df.iterrows():
        ts_raw = row.get("timestamp")
        if pd.isna(ts_raw):
            continue
        ts = pd.Timestamp(ts_raw).isoformat()

        extras = {
            k: _safe(v) for k, v in row.items() if k not in STANDARD_FIELDS
        }
        extras_json = json.dumps(extras, default=str)

        key_str = "|".join(
            str(_safe(row.get(c))) for c in
            ["timestamp", "value", "category", "location_name", "lat", "lon"]
        ) + "|" + extras_json
        row_hash = hashlib.sha256(key_str.encode()).hexdigest()

        rows.append({
            "dataset_id": dataset_id,
            "timestamp": ts,
            "value": None if pd.isna(row.get("value")) else float(row["value"]),
            "category": _safe(row.get("category")),
            "location_name": _safe(row.get("location_name")),
            "lat": None if pd.isna(row.get("lat")) else float(row["lat"]),
            "lon": None if pd.isna(row.get("lon")) else float(row["lon"]),
            "extras": extras_json,
            "row_hash": row_hash,
        })
        hashes.append(row_hash)

    if not rows:
        return 0

    with _engine().begin() as con:
        existing = set(con.execute(
            select(observations.c.row_hash).where(
                observations.c.dataset_id == dataset_id
            )
        ).scalars().all())

        # Filter dubbele rijen (zowel t.o.v. DB als binnen de batch zelf)
        fresh = []
        seen = set()
        for r in rows:
            h = r["row_hash"]
            if h in existing or h in seen:
                continue
            seen.add(h)
            fresh.append(r)

        if fresh:
            con.execute(insert(observations), fresh)
        return len(fresh)


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
    df["timestamp"] = pd.to_datetime(df["timestamp"])
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
        pass


def add_event(event_date: str, label: str) -> int:
    _ensure_table(events_t)
    with _engine().begin() as con:
        result = con.execute(insert(events_t).values(
            event_date=event_date, label=label, created_at=_now_iso(),
        ))
        return int(result.inserted_primary_key[0])


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
