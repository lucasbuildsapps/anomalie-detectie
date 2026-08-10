"""Wiring between stored observations and region evaluation.

`evaluate_region` takes a provider callable rather than reaching for data
itself, which keeps regions free of storage and testable without a database.
This module supplies the real one.

Everything here reads through an `AsOfView`, so the point-in-time guarantee
reaches the screen rather than stopping at the analytics. A page rendered for
a past date shows what was knowable then — including the late-arriving
reports that were not yet in, which is the whole reason the view exists.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import pandas as pd

from sentinel.core.contracts import (
    EntityRef,
    Event,
    GeoPoint,
    Indicator,
    Lineage,
    Producer,
)
from sentinel.core.time import AsOfView
from sentinel.regions.base import RegionModule

__all__ = [
    "AGGREGATION_FREQ",
    "events_from_view",
    "series_from_view",
    "storage_event_provider",
    "storage_provider",
]

#: Indicator aggregation -> pandas resample rule.
AGGREGATION_FREQ = {
    "hourly": "h",
    "daily": "D",
    "weekly": "W",
    "monthly": "MS",
}


def series_from_view(view: AsOfView, dataset_id: int,
                     aggregation: str = "daily",
                     areas: tuple[str, ...] = (),
                     category: str | None = None) -> pd.Series:
    """Aggregate the observations knowable at `view.as_of` into one series.

    Empty buckets become zero, matching v1's default gap policy: for event
    data, no report usually means no activity. That is an assumption rather
    than a fact, and it is the one worth revisiting first if a region's
    reporting is patchy — `mask` would be the honest alternative where
    absence means "not collected" rather than "nothing happened".
    """
    frame = view.observations(dataset_id)
    if frame.empty:
        return pd.Series(dtype=float)

    work = frame
    if areas and "location_name" in work.columns:
        matching = work[work["location_name"].isin(areas)]
        # Fall back to everything rather than returning nothing: a region
        # whose area names do not match the dataset's should look
        # unconfigured, not silently empty.
        if not matching.empty:
            work = matching
    if category and "category" in work.columns:
        work = work[work["category"] == category]
    if work.empty:
        return pd.Series(dtype=float)

    freq = AGGREGATION_FREQ.get(aggregation, "D")
    work = work.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"])
    series = work.set_index("timestamp")["value"].resample(freq).sum()
    return series.fillna(0.0).astype(float)


def storage_provider(dataset_id: int, region: RegionModule,
                     view_factory: Callable[[datetime], AsOfView],
                     ) -> Callable[[Indicator, datetime], pd.Series | None]:
    """A provider bound to one dataset and region.

    `view_factory` builds the point-in-time view for a given instant, so the
    caller decides what the region may see. Passing a factory rather than a
    view keeps replay possible: the same provider works across a sweep of
    dates without being rebuilt.
    """

    def provide(indicator: Indicator, as_of: datetime) -> pd.Series | None:
        aggregation = str(indicator.test_config.get(
            "aggregation", region.default_aggregation))
        areas = ((indicator.area_key,) if indicator.area_key
                 else region.geography.areas)
        series = series_from_view(
            view_factory(as_of), dataset_id,
            aggregation=aggregation, areas=areas,
            category=indicator.test_config.get("category"),
        )
        # None rather than an empty series: the detect layer turns it into an
        # explicit "no data available" verdict instead of a quiet one.
        return series if len(series) else None

    return provide


# ---------------------------------------------------------------------------
# Entity events
# ---------------------------------------------------------------------------
def _row_to_event(row: dict) -> Event | None:
    """One stored row back into an `Event`, or None if it cannot be trusted.

    Returning None rather than raising: a single malformed row should cost the
    system that row, not the whole region's evaluation. The count of dropped
    rows is what the caller surfaces.
    """
    entity = None
    if row.get("entity_key"):
        entity = EntityRef(kind=str(row.get("entity_kind") or "unknown"),
                           key=str(row["entity_key"]))

    geo = None
    lat, lon = row.get("lat"), row.get("lon")
    if lat is not None and lon is not None and pd.notna(lat) and pd.notna(lon):
        geo = GeoPoint(lat=float(lat), lon=float(lon))

    try:
        source_keys = tuple(json.loads(row.get("source_keys") or "[]"))
        attrs = json.loads(row.get("attrs") or "{}")
    except (TypeError, ValueError):
        source_keys, attrs = (), {}

    try:
        producer = Producer(str(row.get("producer") or Producer.INGEST.value))
    except ValueError:
        producer = Producer.INGEST

    try:
        return Event(
            event_type=str(row["event_type"]),
            event_time=pd.Timestamp(row["event_time"]).to_pydatetime(),
            ingested_at=pd.Timestamp(row["ingested_at"]).to_pydatetime(),
            region_key=str(row["region_key"]),
            lineage=Lineage(source_keys=source_keys, producer=producer,
                            method=row.get("method") or None),
            entity=entity,
            geo=geo,
            area_key=row.get("area_key") or None,
            magnitude=(None if row.get("magnitude") is None
                       or pd.isna(row.get("magnitude"))
                       else float(row["magnitude"])),
            unit=row.get("unit") or None,
            ingest_estimated=bool(row.get("ingest_estimated") or False),
            attrs=attrs,
        )
    except (ValueError, KeyError, TypeError):
        # The contract refused it — a derived event without an entity, a
        # missing region. Dropping one row beats fabricating a valid-looking
        # one to satisfy the constructor.
        return None


def events_from_view(view: AsOfView, dataset_id: int, region_key: str,
                     event_types: tuple[str, ...] = (),
                     areas: tuple[str, ...] = ()) -> tuple[Event, ...]:
    """Entity events knowable at `view.as_of`, as contract objects.

    Reads through the view rather than storage directly, so entity indicators
    inherit exactly the point-in-time guarantee the count indicators have.
    """
    frame = view.events(dataset_id, region_key)
    if frame.empty:
        return ()

    work = frame
    if event_types:
        work = work[work["event_type"].isin(event_types)]
    if areas and "area_key" in work.columns:
        matching = work[work["area_key"].isin(areas)]
        # Same fallback as the series provider: a region whose area names do
        # not match the data should look unconfigured, not silently empty.
        if not matching.empty:
            work = matching
    if work.empty:
        return ()

    events = [_row_to_event(row) for row in work.to_dict("records")]
    return tuple(event for event in events if event is not None)


def storage_event_provider(dataset_id: int, region: RegionModule,
                           view_factory: Callable[[datetime], AsOfView],
                           ) -> Callable[[Indicator, datetime],
                                         tuple[Event, ...]]:
    """An event provider bound to one dataset and region.

    Mirrors `storage_provider` deliberately. Two providers with the same shape
    is what lets `evaluate_region` treat a count indicator and an entity
    indicator as the same kind of question with different inputs.
    """

    def provide(indicator: Indicator, as_of: datetime) -> tuple[Event, ...]:
        event_types = tuple(indicator.test_config.get("event_types", ()))
        areas = ((indicator.area_key,) if indicator.area_key
                 else region.geography.areas)
        return events_from_view(view_factory(as_of), dataset_id, region.key,
                                event_types=event_types, areas=areas)

    return provide
