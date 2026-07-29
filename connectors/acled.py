"""ACLED-connector: gevalideerde conflict-events met locatie en dodental.

ACLED (Armed Conflict Location & Event Data) is de referentie-dataset voor
gewapend conflict: handmatig gecodeerde gebeurtenissen met datum,
coördinaten, type en slachtoffers. Wekelijkse update.

**Toegang**: gratis voor onderzoek/non-profit, maar je moet je registreren
op https://acleddata.com/register/ en krijgt dan een API-key. Zet daarna:

    ACLED_API_KEY=...        (jouw key)
    ACLED_EMAIL=...          (het e-mailadres van de registratie)

Op Streamlit Cloud zet je die als secrets; in de compose-stack in .env.

Anders dan de GDELT-connector zijn dit **echte gebeurtenissen**, geen
berichtgevings-proxy. Dit is de bron die je wilt voor serieuze analyse;
GDELT is aanvullend (sneller, ruwer).
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import UTC, datetime, timedelta

import pandas as pd

from connectors.base import Connector, ConnectorError, http_json

API = "https://api.acleddata.com/acled/read"

#: Landen die standaard worden opgehaald. Namen exact zoals ACLED ze
#: schrijft (zie hun codebook). Pas dit aan naar je eigen aandachtsgebieden.
COUNTRIES = ["Ukraine", "Russia"]

DEFAULT_LOOKBACK_DAYS = 180
PAGE_LIMIT = 5000


class AcledEventsConnector(Connector):
    name = "acled-events"
    dataset_name = "ACLED — conflict-events"
    description = (
        "Gevalideerde conflict-gebeurtenissen met coördinaten, type en "
        "dodental (ACLED). Wekelijks bijgewerkt; vereist gratis API-key."
    )
    schedule_minutes = 60 * 24  # dagelijks; ACLED zelf ververst wekelijks
    enabled = False
    requires_env = ("ACLED_API_KEY", "ACLED_EMAIL")

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        missing = self.missing_config()
        if missing:
            raise ConnectorError(
                f"ACLED vereist {', '.join(missing)} — registreer gratis op "
                f"https://acleddata.com/register/"
            )
        key = os.environ["ACLED_API_KEY"]
        email = os.environ["ACLED_EMAIL"]

        start = since or (datetime.now(UTC).replace(tzinfo=None)
                          - timedelta(days=DEFAULT_LOOKBACK_DAYS))
        params = {
            "key": key,
            "email": email,
            "event_date": start.strftime("%Y-%m-%d"),
            "event_date_where": ">=",
            "country": "|".join(COUNTRIES),
            "limit": PAGE_LIMIT,
        }
        payload = http_json(f"{API}?{urllib.parse.urlencode(params)}")

        if isinstance(payload, dict) and not payload.get("success", True):
            raise ConnectorError(f"ACLED weigerde de aanvraag: {payload}")
        records = (payload or {}).get("data") or []
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        out = pd.DataFrame({
            "timestamp": pd.to_datetime(df.get("event_date"), errors="coerce"),
            # 1 rij = 1 gebeurtenis; de tool telt ze per periode op.
            "value": 1.0,
            "location_name": df.get("admin1", df.get("country")),
            "category": df.get("event_type"),
            "lat": pd.to_numeric(df.get("latitude"), errors="coerce"),
            "lon": pd.to_numeric(df.get("longitude"), errors="coerce"),
        })
        for extra in ("sub_event_type", "fatalities", "actor1", "notes"):
            if extra in df.columns:
                out[extra] = df[extra]
        return out.dropna(subset=["timestamp"])
