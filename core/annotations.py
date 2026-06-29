"""Annotaties bij bevindingen — notities en status van de analist.

Een finding_key is een stabiele hash van (datum, locatie, categorie). Zo
overleeft de annotatie nieuwe analyses op dezelfde dataset. De daadwerkelijke
opslag loopt via core.storage (SQLite of Postgres).
"""
from __future__ import annotations

import hashlib

from core import storage

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


def get_annotation(dataset_id: int, key: str) -> dict | None:
    return storage.get_annotation_row(dataset_id, key)


def save_annotation(
    dataset_id: int, key: str, note: str | None, status: str | None
) -> None:
    if status not in VALID_STATUSES:
        status = "open"
    storage.upsert_annotation(dataset_id, key, note, status)


def list_annotations(dataset_id: int) -> dict:
    """Mapping finding_key -> {note, status, updated_at}."""
    return storage.list_annotation_rows(dataset_id)
