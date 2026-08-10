"""Danish Maritime Authority historical AIS — daily archives into `positions`.

Chosen as the first network connector because it is the one that unblocks
something. The entity engine, its peer baselines, the population denominator
and a measured loiter floor are all built and running on a synthetic fleet;
what they lack is real tracks. DMA publishes daily AIS archives as open data,
with no API key and no redistribution restriction, which also makes it the
only declared source that can be used without a licence conversation first.

A caveat that belongs at the top
--------------------------------
**This parser has never seen a live DMA file.** The sandbox this was written in
has no outbound network access — the proxy refuses CONNECT to `web.ais.dk` —
so the column mapping below comes from the published format description, not
from a response. That is a real risk and it is handled rather than hidden:

- Column matching is by *normalised* name (case, spaces and punctuation
  folded), so cosmetic header differences do not break it.
- A missing required column raises `SchemaError` naming exactly what was
  wanted and what the file actually contained. The first real run is therefore
  a legible failure if the schema has moved, not silent garbage.
- Nothing is guessed. There is no positional fallback, because a column order
  that shifted would then be read as valid data.

Arrival time
------------
A daily archive is bulk history: we learned all of it when we downloaded it,
not when each vessel transmitted. Recording "now" would make every replay
before today empty, and recording the transmission time would claim we had the
data instantly. The archive's own date is the honest answer — the file for the
3rd became available on the 4th, so that is when the tool could have known it.
`archive_lag_days` encodes that and is applied to every row.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from sentinel.core.contracts import Credibility, Reliability, Source
from sentinel.ingest.transport import Fetcher, UrllibFetcher

__all__ = ["DMA_SOURCE", "DmaAisConnector", "SchemaError", "parse_dma_csv"]

DMA_SOURCE = Source(
    key="dma_ais",
    name="Danish Maritime Authority historical AIS",
    kind="ais",
    reliability=Reliability.B,
    credibility=Credibility.C2,
    url="https://web.ais.dk/aisdata/",
    licence="open data",
    redistribution_allowed=True,
)

#: Published column name -> our field. Keys are matched after normalising
#: case, spaces, underscores and punctuation, so "# Timestamp", "Timestamp"
#: and "timestamp" all land in the same place.
_COLUMN_MAP = {
    "timestamp": "timestamp",
    "mmsi": "entity_key",
    "latitude": "lat",
    "longitude": "lon",
    "sog": "sog",
    "cog": "cog",
    "heading": "heading",
    "shiptype": "vessel_class",
    "typeofmobile": "mobile_type",
    "navigationalstatus": "nav_status",
    "name": "vessel_name",
    "imo": "imo",
    "callsign": "callsign",
    "width": "width",
    "length": "length",
    "draught": "draught",
    "destination": "destination",
}

#: Without these a row is not a position and cannot be stored.
_REQUIRED = ("timestamp", "entity_key", "lat", "lon")

#: DMA writes day-first timestamps. Letting pandas infer would silently read
#: 03/04 as 4 March in some files and 3 April in others.
_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"


class SchemaError(ValueError):
    """The file did not carry the columns this parser needs.

    Separate from `FetchError`: this one means the format moved, and waiting
    will not help. It names what was missing and what was present, because the
    person reading it is trying to work out which.
    """


def _normalise(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def parse_dma_csv(data: bytes | str, *, bbox: tuple | None = None,
                  ) -> pd.DataFrame:
    """One DMA CSV into the position frame `insert_positions` expects.

    `bbox` is `(lat_min, lat_max, lon_min, lon_max)`. Filtering here rather
    than after storage is not premature optimisation: a single national daily
    archive is millions of rows, and the Dutch EEZ is a small corner of it.
    """
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) \
        else data
    frame = pd.read_csv(io.StringIO(text), low_memory=False)

    mapping = {}
    for column in frame.columns:
        target = _COLUMN_MAP.get(_normalise(column))
        if target and target not in mapping.values():
            mapping[column] = target
    frame = frame.rename(columns=mapping)

    missing = [c for c in _REQUIRED if c not in frame.columns]
    if missing:
        raise SchemaError(
            f"DMA archive is missing {', '.join(missing)}. Columns found: "
            f"{', '.join(map(str, frame.columns[:20]))}. The mapping in "
            f"sentinel/ingest/dma_ais.py was written from the published "
            f"format description and has not been checked against a live "
            f"file; if the format has moved, that table is what needs "
            f"updating.")

    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], format=_TIMESTAMP_FORMAT, errors="coerce")
    for column in ("lat", "lon", "sog", "cog", "heading"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame[frame["timestamp"].notna()
                  & frame["lat"].notna() & frame["lon"].notna()]

    # AIS carries 91/181 as "position unavailable" rather than as null, and
    # a vessel at latitude 91 would otherwise sail straight into the store.
    frame = frame[frame["lat"].between(-90, 90)
                  & frame["lon"].between(-180, 180)]

    if bbox is not None:
        lat_min, lat_max, lon_min, lon_max = bbox
        frame = frame[frame["lat"].between(lat_min, lat_max)
                      & frame["lon"].between(lon_min, lon_max)]

    if frame.empty:
        return frame

    frame = frame.copy()
    frame["entity_key"] = ("mmsi:"
                           + frame["entity_key"].astype(str).str.strip())
    frame["entity_kind"] = "vessel"
    frame["source_key"] = DMA_SOURCE.key
    if "vessel_class" in frame.columns:
        frame["vessel_class"] = (frame["vessel_class"].astype(str)
                                 .str.strip().str.lower()
                                 .replace({"nan": None, "": None,
                                           "undefined": None}))

    keep = ["entity_key", "entity_kind", "timestamp", "lat", "lon",
            "sog", "cog", "heading", "vessel_class", "source_key"]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


@dataclass
class DmaAisConnector:
    """Daily DMA archives for a date range, filtered to a region's box.

    One archive per day, fetched independently. A day that fails does not stop
    the others: an AIS history with a hole in it is still worth having, and the
    hole is reported rather than papered over.
    """

    fetcher: Fetcher = field(default_factory=UrllibFetcher)
    source: Source = DMA_SOURCE
    base_url: str = "https://web.ais.dk/aisdata"
    bbox: tuple | None = None
    #: Days between a day's traffic and its archive appearing. The archive is
    #: what we could actually have downloaded, so this is the arrival time.
    archive_lag_days: int = 1
    timeout: float = 300.0

    def url_for(self, day: datetime) -> str:
        return f"{self.base_url}/aisdk-{day.strftime('%Y-%m-%d')}.zip"

    def fetch_day(self, day: datetime) -> pd.DataFrame:
        """One day's archive, parsed and filtered. Raises on failure."""
        payload = self.fetcher.get(self.url_for(day), timeout=self.timeout)
        if payload[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [n for n in archive.namelist()
                         if n.lower().endswith(".csv")]
                if not names:
                    raise SchemaError(
                        f"archive for {day.date()} contains no CSV: "
                        f"{archive.namelist()}")
                payload = archive.read(names[0])

        frame = parse_dma_csv(payload, bbox=self.bbox)
        if not frame.empty:
            # Arrival is when the archive existed, not when vessels
            # transmitted. Without this every replay would credit the tool
            # with same-day access to a file published the next morning.
            frame = frame.copy()
            frame["ingested_at"] = pd.Timestamp(
                day + timedelta(days=self.archive_lag_days))
        return frame

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> pd.DataFrame:
        """Every archive in the range, concatenated. Failures are collected.

        Days that could not be fetched are recorded in `frame.attrs` rather
        than raised, so a month with two bad days still yields twenty-eight
        good ones and says which two are missing.
        """
        if since is None:
            raise ValueError(
                "DMA archives are per-day; a start date is required. Fetching "
                "'everything' would be several terabytes.")
        until = until or since
        if until < since:
            raise ValueError("until is before since")

        frames, failures = [], []
        day = since
        while day <= until:
            try:
                frames.append(self.fetch_day(day))
            except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                failures.append(f"{day.date()}: {type(exc).__name__}: {exc}")
            day += timedelta(days=1)

        usable = [f for f in frames if not f.empty]
        result = (pd.concat(usable, ignore_index=True) if usable
                  else pd.DataFrame())
        result.attrs["failed_days"] = tuple(failures)
        return result
