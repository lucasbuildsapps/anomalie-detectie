"""Annotaties bij bevindingen — notities en status van de analist.

Een finding_key is een stabiele hash van (datum, locatie, categorie). Zo
overleeft de annotatie nieuwe analyses op dezelfde dataset. De daadwerkelijke
opslag loopt via core.storage (SQLite of Postgres).
"""
from __future__ import annotations

import hashlib

from core import storage

VALID_STATUSES = ("open", "onderzocht", "vals_alarm", "bevestigd",
                  "geescaleerd")
STATUS_LABELS = {
    "open": "Open",
    "onderzocht": "Onderzocht",
    "vals_alarm": "Vals alarm",
    "bevestigd": "Bevestigd",
    "geescaleerd": "Geëscaleerd",
}

# Statussen die tellen als 'behandeld' (de analist heeft ernaar gekeken).
HANDLED_STATUSES = ("onderzocht", "vals_alarm", "bevestigd", "geescaleerd")


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


def triage_counts(dataset_id: int, alerts: list[dict]) -> dict:
    """Triage-stand van recente afwijkingen: hoeveel zijn er (on)behandeld?

    `alerts` is de lijst van detect_recent_alerts (dicts met 'datum' en
    'locatie'). Een afwijking telt als behandeld zodra er een annotatie
    met een HANDLED_STATUS op ligt. Dit is het wapen tegen alert-moeheid:
    het overzicht toont wat nog aandacht nodig heeft, niet alles opnieuw.
    """
    existing = list_annotations(dataset_id)
    total = len(alerts)
    handled = 0
    escalated = 0
    for a in alerts:
        key = finding_key(a["datum"], str(a.get("locatie") or ""), None)
        status = (existing.get(key) or {}).get("status")
        if status in HANDLED_STATUSES:
            handled += 1
        if status == "geescaleerd":
            escalated += 1
    return {
        "total": total,
        "handled": handled,
        "unhandled": total - handled,
        "escalated": escalated,
    }
