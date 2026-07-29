"""GDELT-connector: dagelijkse nieuwsvolume-reeks per gebied van interesse.

GDELT monitort wereldwijd nieuws en is gratis zonder API-key. Deze
connector levert per dag het **aantal artikelen** dat aan een zoekopdracht
voldoet — een bruikbare proxy voor "hoeveel gebeurt er in dit gebied".

Wat dit WEL is: een activiteits-indicator op basis van berichtgeving.
Wat dit NIET is: een telling van gebeurtenissen. Meer artikelen kan ook
betekenen dat er meer journalisten kijken. Gebruik het naast harde
event-data (bv. ACLED), niet in plaats daarvan. Deze kanttekening staat
ook in de dataset-omschrijving die de tool aanmaakt.

Gebieden aanpassen: bewerk WATCHLIST hieronder. Elke regel wordt een
'regio' in de tool, zodat je ze in Vergelijken naast elkaar kunt zetten.
"""
from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta

import pandas as pd

from connectors.base import Connector, ConnectorError, http_json

API = "https://api.gdeltproject.org/api/v2/doc/doc"

#: regio-label -> GDELT-zoekopdracht.
#: Syntax: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
WATCHLIST = {
    "Oekraïne":       'sourcecountry:UP (drone OR missile OR strike OR shelling)',
    "Rusland":        'sourcecountry:RS (drone OR missile OR strike OR explosion)',
    "Midden-Oosten":  '(Israel OR Gaza OR Lebanon) (strike OR rocket OR airstrike)',
    "Sahel":          '(Mali OR "Burkina Faso" OR Niger) (attack OR militant OR ambush)',
    "Taiwan-straat":  '(Taiwan OR "South China Sea") (incursion OR military OR patrol)',
}

MAX_DAYS = 365  # GDELT DOC 2.0 dekt ruwweg het laatste jaar


class GdeltNewsVolumeConnector(Connector):
    name = "gdelt-nieuwsvolume"
    dataset_name = "GDELT — nieuwsvolume per gebied"
    description = (
        "Dagelijks aantal nieuwsartikelen per gebied van interesse (GDELT "
        "DOC 2.0). Proxy voor activiteitsniveau, geen gebeurtenissen-telling."
    )
    schedule_minutes = 60 * 12  # 2x per dag; GDELT ververst continu
    enabled = False             # zet op True zodra je 'm getest hebt

    def _fetch_one(self, label: str, query: str,
                   start: datetime, end: datetime) -> pd.DataFrame:
        params = {
            "query": query,
            "mode": "timelinevolraw",   # ruwe artikel-aantallen, niet genormaliseerd
            "format": "json",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        }
        payload = http_json(f"{API}?{urllib.parse.urlencode(params)}")
        timeline = (payload or {}).get("timeline") or []
        if not timeline:
            return pd.DataFrame()
        rows = []
        for point in timeline[0].get("data", []):
            ts = pd.to_datetime(point.get("date"), errors="coerce", utc=True)
            if pd.isna(ts):
                continue
            rows.append({
                "timestamp": ts.tz_convert(None),
                "value": float(point.get("value") or 0.0),
                "location_name": label,
                "category": "nieuwsvolume",
            })
        return pd.DataFrame(rows)

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        end = datetime.now(UTC).replace(tzinfo=None)
        start = since or (end - timedelta(days=MAX_DAYS))
        start = max(start, end - timedelta(days=MAX_DAYS))

        frames, errors = [], []
        for label, query in WATCHLIST.items():
            try:
                frames.append(self._fetch_one(label, query, start, end))
            except Exception as e:
                # Eén onbereikbaar gebied mag de hele run niet slopen;
                # we falen alleen als er niets binnenkwam.
                errors.append(f"{label}: {e}")
        frames = [f for f in frames if not f.empty]
        if not frames:
            raise ConnectorError(
                "Geen data uit GDELT. " + ("; ".join(errors) if errors else "")
            )
        return pd.concat(frames, ignore_index=True)
