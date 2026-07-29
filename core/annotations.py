"""Annotaties bij bevindingen — notities en status van de analist.

Een finding_key is een stabiele hash van (datum, locatie, categorie). Zo
overleeft de annotatie nieuwe analyses op dezelfde dataset. De daadwerkelijke
opslag loopt via core.storage (SQLite of Postgres).
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

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


def performance(dataset_id: int, window_days: int | None = 90) -> dict:
    """Hoe nuttig waren de signalen van deze tool, volgens de analist zelf?

    Dit is de terugkoppeling die het verschil maakt tussen "de tool
    produceert bevindingen" en "we weten dat het de moeite waard is".
    Basis: de statussen die analisten zelf hebben gezet.

        precisie = bevestigd / (bevestigd + vals alarm)

    Alleen beoordeelde bevindingen tellen mee — 'open' zegt niets over
    kwaliteit. Zolang er te weinig beoordelingen zijn (< 10) geven we
    bewust géén percentage: een precisie van "100%" op twee gevallen is
    misleidender dan geen getal.

    `window_days=None` gebruikt de volledige historie.
    """
    rows = storage.list_annotation_rows(dataset_id) or {}
    cutoff = None
    if window_days:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)

    counts = dict.fromkeys(VALID_STATUSES, 0)
    for entry in rows.values():
        status = (entry or {}).get("status")
        if status not in counts:
            continue
        if cutoff is not None:
            updated = _parse_ts(entry.get("updated_at"))
            if updated is not None and updated < cutoff:
                continue
        counts[status] += 1

    confirmed = counts["bevestigd"] + counts["geescaleerd"]
    false_alarms = counts["vals_alarm"]
    judged = confirmed + false_alarms
    reviewed = judged + counts["onderzocht"]

    precision = (confirmed / judged) if judged else None
    return {
        "counts": counts,
        "n_judged": judged,
        "n_reviewed": reviewed,
        "confirmed": confirmed,
        "false_alarms": false_alarms,
        "precision": precision,
        # Onder deze drempel is een percentage statistisch betekenisloos.
        "reliable": judged >= 10,
        "window_days": window_days,
    }


def _parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def performance_verdict(perf: dict) -> str:
    """Eén regel die zegt wat de cijfers betekenen — inclusief het eerlijke
    'we weten het nog niet'."""
    if not perf["reliable"]:
        need = max(0, 10 - perf["n_judged"])
        return (
            f"Nog geen uitspraak mogelijk: {perf['n_judged']} beoordeelde "
            f"bevinding(en). Beoordeel er nog minstens {need} als 'bevestigd' "
            f"of 'vals alarm' om te zien of deze tool zijn plek verdient."
        )
    pct = perf["precision"] * 100
    if pct >= 70:
        oordeel = "De signalen zijn overwegend raak."
    elif pct >= 40:
        oordeel = ("Ongeveer de helft is vals alarm — overweeg een strengere "
                   "gevoeligheid of betere brondata.")
    else:
        oordeel = ("Het merendeel is vals alarm. In deze vorm kost de tool "
                   "meer tijd dan hij oplevert.")
    return (
        f"Van {perf['n_judged']} beoordeelde bevindingen was "
        f"{pct:.0f}% raak ({perf['confirmed']} bevestigd/geëscaleerd, "
        f"{perf['false_alarms']} vals alarm). {oordeel}"
    )


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
