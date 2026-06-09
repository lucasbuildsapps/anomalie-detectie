"""SQLite storage layer. One file: data/store.db."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "store.db"

STANDARD_FIELDS = {
    "timestamp", "value", "category", "location_name", "lat", "lon",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL,
                column_mapping TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                value REAL,
                category TEXT,
                location_name TEXT,
                lat REAL,
                lon REAL,
                extras TEXT,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                UNIQUE (dataset_id, row_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_obs_dataset_time
                ON observations(dataset_id, timestamp);
            CREATE TABLE IF NOT EXISTS annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL,
                finding_key TEXT NOT NULL,
                note TEXT,
                status TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                UNIQUE (dataset_id, finding_key)
            );
            """
        )


def dataset_data_hash(dataset_id: int) -> str:
    """Goedkope signatuur die wijzigt zodra rijen worden toegevoegd/verwijderd."""
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*), MAX(timestamp), MAX(id) "
            "FROM observations WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
    return f"{row[0]}|{row[1]}|{row[2]}"


def list_datasets() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, name, description, created_at, column_mapping "
            "FROM datasets ORDER BY name"
        ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "description": r[2],
            "created_at": r[3],
            "column_mapping": json.loads(r[4]),
        }
        for r in rows
    ]


def create_dataset(name: str, description: str, column_mapping: dict) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO datasets (name, description, created_at, column_mapping) "
            "VALUES (?, ?, ?, ?)",
            (name, description, _now_iso(), json.dumps(column_mapping)),
        )
        return cur.lastrowid


def delete_dataset(dataset_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))


def clear_observations(dataset_id: int) -> None:
    """Verwijder alle observaties van een dataset (dataset zelf blijft)."""
    with _conn() as con:
        con.execute("DELETE FROM observations WHERE dataset_id = ?", (dataset_id,))


def _safe(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v


def insert_observations(dataset_id: int, df: pd.DataFrame) -> int:
    """Insert rows in één batch; dedupe via INSERT OR IGNORE op row_hash.
    Returns count daadwerkelijk nieuw ingevoegd."""
    rows: list[tuple] = []
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
            ["timestamp", "value", "category",
             "location_name", "lat", "lon"]
        ) + "|" + extras_json
        row_hash = hashlib.sha256(key_str.encode()).hexdigest()

        rows.append((
            dataset_id,
            ts,
            None if pd.isna(row.get("value")) else float(row["value"]),
            _safe(row.get("category")),
            _safe(row.get("location_name")),
            None if pd.isna(row.get("lat")) else float(row["lat"]),
            None if pd.isna(row.get("lon")) else float(row["lon"]),
            extras_json,
            row_hash,
        ))

    if not rows:
        return 0

    with _conn() as con:
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO observations "
            "(dataset_id, timestamp, value, category, "
            " location_name, lat, lon, extras, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return con.total_changes - before


def load_observations(dataset_id: int) -> pd.DataFrame:
    with _conn() as con:
        df = pd.read_sql_query(
            "SELECT timestamp, value, category, location_name, lat, lon, extras "
            "FROM observations WHERE dataset_id = ? ORDER BY timestamp",
            con,
            params=(dataset_id,),
        )
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    extras_series = df["extras"].apply(lambda s: json.loads(s) if s else {})
    extras_df = pd.json_normalize(extras_series)
    df = pd.concat([df.drop(columns=["extras"]), extras_df], axis=1)
    return df
