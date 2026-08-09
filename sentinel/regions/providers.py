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

from collections.abc import Callable
from datetime import datetime

import pandas as pd

from sentinel.core.contracts import Indicator
from sentinel.core.time import AsOfView
from sentinel.regions.base import RegionModule

__all__ = ["AGGREGATION_FREQ", "series_from_view", "storage_provider"]

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
