"""NASA FIRMS: satelliet-warmtedetecties (branden, explosies, fakkels).

Waarom deze bron waardevol is voor dit gereedschap:
- **Vers**: satellieten leveren meerdere keren per dag, met enkele uren
  vertraging. Geen wekelijkse of maandelijkse cyclus zoals bij
  handmatig gecodeerde bronnen.
- **Geografisch van nature**: elke detectie heeft coördinaten, dus de
  kaart werkt zonder gazetteer.
- **Onafhankelijk van berichtgeving**: een satelliet ziet een brand ook
  als niemand erover schrijft — de zwakte van nieuws-gebaseerde bronnen.

**Wat het NIET is**: geen aanvals-detectie. FIRMS ziet *warmte*. Dat is
een brand, maar net zo goed landbouw-afbranden, industrie, gasfakkels of
bosbrand. In de zomer domineert landbouw het beeld volledig. Gebruik dit
als activiteits-indicator naast andere bronnen, nooit als bewijs. Deze
waarschuwing staat ook in de dataset-omschrijving.

**Toegang**: gratis MAP_KEY aanvragen (direct, per e-mail) op
https://firms.modaps.eosdis.nasa.gov/api/area/ en zetten als:

    FIRMS_MAP_KEY=...
"""
from __future__ import annotations

import csv
import io
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import pandas as pd

from connectors.base import USER_AGENT, Connector, ConnectorError

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

#: Satelliet-bron. VIIRS_SNPP_NRT heeft 375 m resolutie (fijner dan MODIS)
#: en is near-real-time.
SOURCE = "VIIRS_SNPP_NRT"

#: Gebieden: label -> bounding box (west, zuid, oost, noord).
AREAS = {
    "Oekraïne":      (22.0, 44.0, 40.5, 52.5),
    "West-Rusland":  (30.0, 44.0, 45.0, 56.0),
    "Midden-Oosten": (34.0, 29.0, 42.5, 37.5),
}

#: FIRMS staat maximaal 10 dagen per aanvraag toe.
MAX_DAYS_PER_CALL = 10

#: Detecties onder deze betrouwbaarheid worden weggelaten (ruis).
MIN_CONFIDENCE = {"n", "nominal", "h", "high"}


def _http_text(url: str, timeout: int = 90) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise ConnectorError(f"HTTP {e.code} van FIRMS") from e
    except Exception as e:
        raise ConnectorError(f"FIRMS onbereikbaar: {e}") from e


class FirmsThermalConnector(Connector):
    name = "nasa-firms"
    dataset_name = "NASA FIRMS — warmtedetecties"
    description = (
        "Satelliet-warmtedetecties per gebied (VIIRS, near-real-time). "
        "Activiteits-indicator: ziet ook landbouw en industrie, geen "
        "aanvals-detectie."
    )
    schedule_minutes = 60 * 6      # 4x per dag; satellieten leveren continu
    enabled = False
    requires_env = ("FIRMS_MAP_KEY",)

    def _fetch_area(self, label: str, bbox: tuple, days: int,
                    start: datetime | None) -> pd.DataFrame:
        key = os.environ["FIRMS_MAP_KEY"]
        west, south, east, north = bbox
        area = f"{west},{south},{east},{north}"
        path = f"{API}/{key}/{SOURCE}/{area}/{days}"
        if start is not None:
            path += f"/{start.strftime('%Y-%m-%d')}"
        text = _http_text(path)

        head = text.lstrip()[:200].lower()
        if head.startswith(("invalid", "error", "<!doctype", "<html")):
            raise ConnectorError(f"FIRMS weigerde de aanvraag: {text[:120]}")

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        needed = {"acq_date", "latitude", "longitude"}
        if not needed.issubset(df.columns):
            raise ConnectorError(
                f"Onverwacht FIRMS-formaat, kolommen: {list(df.columns)[:8]}")

        if "confidence" in df.columns:
            conf = df["confidence"].astype(str).str.lower()
            keep = conf.isin(MIN_CONFIDENCE) | conf.str.isdigit()
            numeric = pd.to_numeric(df["confidence"], errors="coerce")
            keep = keep & (numeric.isna() | (numeric >= 50))
            df = df[keep]
        if df.empty:
            return pd.DataFrame()

        ts = pd.to_datetime(
            df["acq_date"].astype(str) + " "
            + df.get("acq_time", "0000").astype(str).str.zfill(4),
            format="%Y-%m-%d %H%M", errors="coerce",
        )
        out = pd.DataFrame({
            "timestamp": ts,
            "value": 1.0,               # 1 rij = 1 detectie; de tool telt op
            "location_name": label,
            "category": df.get("daynight", pd.Series("?", index=df.index))
                          .map({"D": "dag", "N": "nacht"}).fillna("?"),
            "lat": pd.to_numeric(df["latitude"], errors="coerce"),
            "lon": pd.to_numeric(df["longitude"], errors="coerce"),
        })
        for extra in ("bright_ti4", "frp", "confidence"):
            if extra in df.columns:
                out[extra] = pd.to_numeric(df[extra], errors="coerce")
        return out.dropna(subset=["timestamp", "lat", "lon"])

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        missing = self.missing_config()
        if missing:
            raise ConnectorError(
                f"FIRMS vereist {', '.join(missing)} — gratis aan te vragen op "
                f"https://firms.modaps.eosdis.nasa.gov/api/area/"
            )
        now = datetime.now(UTC).replace(tzinfo=None)
        if since is None:
            days, start = MAX_DAYS_PER_CALL, None
        else:
            days = int(min(MAX_DAYS_PER_CALL, max(1, (now - since).days + 1)))
            start = since

        frames, errors = [], []
        for label, bbox in AREAS.items():
            try:
                frames.append(self._fetch_area(label, bbox, days, start))
            except Exception as e:
                errors.append(f"{label}: {e}")
        frames = [f for f in frames if not f.empty]
        if not frames:
            if errors:
                raise ConnectorError("; ".join(errors))
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
