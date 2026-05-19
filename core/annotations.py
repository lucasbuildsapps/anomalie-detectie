"""Annotaties bij bevindingen — notities en status van de analist.

Een finding_key is een stabiele hash van (datum, locatie, categorie). Zo
overleeft de annotatie nieuwe analyses op dezelfde dataset.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from core.storage import _conn


VALID_STATUSES = ("open", "onderzocht", "vals_alarm", "bevestigd")
STATUS_LABELS = {
    "open": "Open",
    "onderzocht": "Onderzocht",
    "vals_alarm": "Vals alarm",
    "bevestigd": "Bevestigd",
}


def finding_key(date_iso: str, location: str, category: str | None = None) -> str:
    raw = f"{date_iso}|{location or ''}|{category or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_annotation(dataset_id: int, key: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT note, status, updated_at FROM annotations "
            "WHERE dataset_id = ? AND finding_key = ?",
            (dataset_id, key),
        ).fetchone()
    if row is None:
        return None
    return {"note": row[0], "status": row[1], "updated_at": row[2]}


def save_annotation(
    dataset_id: int, key: str, note: str | None, status: str | None
) -> None:
    if status not in VALID_STATUSES:
        status = "open"
    with _conn() as con:
        con.execute(
            "INSERT INTO annotations "
            "(dataset_id, finding_key, note, status, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dataset_id, finding_key) DO UPDATE SET "
            "  note = excluded.note, "
            "  status = excluded.status, "
            "  updated_at = excluded.updated_at",
            (dataset_id, key, note or "", status, _now()),
        )


def list_annotations(dataset_id: int) -> dict:
    """Mapping finding_key -> {note, status, updated_at}."""
    with _conn() as con:
        rows = con.execute(
            "SELECT finding_key, note, status, updated_at "
            "FROM annotations WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchall()
    return {
        r[0]: {"note": r[1], "status": r[2], "updated_at": r[3]}
        for r in rows
    }
