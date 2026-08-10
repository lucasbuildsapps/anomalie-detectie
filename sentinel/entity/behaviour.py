"""Behaviour primitives: turn positions into typed events.

The two-layer bridge from ARCHITECTURE_V2.md §2.2 in working form. This
module emits `Event` objects into the same representation an ACLED strike
lands in, and they are then judged by exactly the machinery Euro-Atlantic
uses. Two paradigms, one anomaly truth model.

What this module does *not* do
------------------------------
It does not decide that anything is anomalous. A `loiter` event asserts that
a vessel moved slowly for a while — nothing more. Whether that matters is an
indicator's business, against a baseline of how much loitering is normal for
that vessel class in that area.

The distinction is not pedantry. Fishing vessels loiter for a living, and a
detector that equates "stopped" with "suspicious" flags every trawler in the
North Sea, every day. Fusing observation with judgement here is precisely how
a detector quietly becomes a quota — and the synthetic controls
(`fishing`, `anchorage_wait`) exist to keep that honest.

Causality
---------
A four-hour loiter is not knowable when it begins. So each event carries
`event_time` at the start of the behaviour and `ingested_at` at the moment it
became detectable — the end of the window. Backdating detectability would
make any replay optimistic in the same way v1's missing arrival times did.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sentinel.core.contracts import (
    EntityRef,
    Event,
    GeoPoint,
    Lineage,
    Producer,
)
from sentinel.entity.geo import cross_track_distance, elapsed_seconds, speed_knots

__all__ = [
    "BehaviourConfig",
    "detect_ais_gaps",
    "detect_loiter",
    "detect_route_deviation",
    "extract_events",
]

_METHOD_VERSION = "v1"


@dataclass(frozen=True)
class BehaviourConfig:
    """Thresholds for the primitives.

    These are observation thresholds, not alert thresholds — they decide what
    counts as "slow" or "a gap", not what counts as worrying. Set them
    loosely: a missed observation cannot be recovered downstream, whereas an
    uninteresting one is cheap for an indicator to ignore.
    """

    loiter_max_speed_knots: float = 1.5
    loiter_min_minutes: float = 60.0
    gap_min_minutes: float = 30.0
    #: Implied speed above which one identifier cannot be one vessel.
    #: Far above any real hull on purpose: the question is whether this is
    #: physically one ship, not whether it was speeding. See
    #: `sentinel/entity/identity.py`.
    implausible_speed_knots: float = 100.0
    deviation_min_km: float = 10.0
    deviation_min_minutes: float = 30.0

    def __post_init__(self) -> None:
        if self.loiter_max_speed_knots <= 0:
            raise ValueError("loiter speed threshold must be positive")
        if self.gap_min_minutes <= 0:
            raise ValueError("gap threshold must be positive")
        if self.implausible_speed_knots <= 50:
            raise ValueError(
                "an implausibility threshold at or below 50 knots would flag "
                "fast craft as identity conflicts; this is a physics check, "
                "not a speed limit")


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous (start, end_inclusive) index runs where mask is True."""
    positions = np.flatnonzero(mask)
    if positions.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(positions) > 1)
    starts = np.concatenate([[positions[0]], positions[breaks + 1]])
    ends = np.concatenate([positions[breaks], [positions[-1]]])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _event(entity_key: str, event_type: str, method: str,
           start: pd.Timestamp, detectable_at: pd.Timestamp,
           region_key: str, lat: float, lon: float,
           magnitude: float, unit: str, **attrs) -> Event:
    return Event(
        event_type=event_type,
        event_time=pd.Timestamp(start).to_pydatetime(),
        # When it became knowable, not when it began.
        ingested_at=pd.Timestamp(detectable_at).to_pydatetime(),
        region_key=region_key,
        entity=EntityRef("vessel", entity_key),
        geo=GeoPoint(lat=float(lat), lon=float(lon), precision_m=100.0),
        magnitude=float(magnitude),
        unit=unit,
        lineage=Lineage(producer=Producer.ENTITY_ENGINE,
                        method=f"{method}.{_METHOD_VERSION}"),
        attrs=dict(attrs),
    )


def detect_loiter(positions: pd.DataFrame, config: BehaviourConfig,
                  region_key: str = "nld_eez") -> list[Event]:
    """Stretches where a vessel moved slowly for longer than the threshold.

    Speed is derived from consecutive positions rather than trusting the
    broadcast `sog` field: a vessel that is stationary but reporting a stale
    speed-over-ground is exactly the case worth catching, and taking the
    transmitted value on trust would miss it.
    """
    if len(positions) < 3:
        return []
    frame = positions.sort_values("timestamp").reset_index(drop=True)
    speeds = speed_knots(frame["lat"], frame["lon"], frame["timestamp"])
    slow = np.nan_to_num(speeds, nan=np.inf) <= config.loiter_max_speed_knots

    events: list[Event] = []
    for start, end in _runs(slow):
        begin = pd.Timestamp(frame["timestamp"].iloc[start])
        finish = pd.Timestamp(frame["timestamp"].iloc[end])
        minutes = (finish - begin).total_seconds() / 60.0
        if minutes < config.loiter_min_minutes:
            continue
        segment = frame.iloc[start:end + 1]
        events.append(_event(
            entity_key=str(frame["entity_key"].iloc[start]),
            event_type="loiter", method="loiter",
            start=begin, detectable_at=finish, region_key=region_key,
            lat=float(segment["lat"].mean()), lon=float(segment["lon"].mean()),
            magnitude=minutes, unit="minutes",
            median_speed_knots=float(np.nanmedian(speeds[start:end + 1])),
            vessel_class=str(frame.get("vessel_class",
                                       pd.Series(["unknown"])).iloc[start]),
        ))
    return events


def detect_ais_gaps(positions: pd.DataFrame, config: BehaviourConfig,
                    region_key: str = "nld_eez") -> list[Event]:
    """Intervals where a vessel stopped reporting.

    The event is anchored at the *last* position before the silence, which is
    the only place we know the vessel was. Where it went during the gap is
    the open question, and the event should not pretend otherwise.
    """
    if len(positions) < 2:
        return []
    frame = positions.sort_values("timestamp").reset_index(drop=True)
    minutes = elapsed_seconds(frame["timestamp"]) / 60.0

    events: list[Event] = []
    for i in np.flatnonzero(np.nan_to_num(minutes, nan=0.0)
                            > config.gap_min_minutes):
        before = frame.iloc[i - 1]
        events.append(_event(
            entity_key=str(before["entity_key"]),
            event_type="ais_gap", method="ais_gap",
            start=pd.Timestamp(before["timestamp"]),
            detectable_at=pd.Timestamp(frame["timestamp"].iloc[i]),
            region_key=region_key,
            lat=float(before["lat"]), lon=float(before["lon"]),
            magnitude=float(minutes[i]), unit="minutes",
            resumed_at=str(frame["timestamp"].iloc[i]),
            vessel_class=str(before.get("vessel_class", "unknown")),
        ))
    return events


def detect_route_deviation(positions: pd.DataFrame, config: BehaviourConfig,
                           route: tuple[tuple[float, float],
                                        tuple[float, float]],
                           region_key: str = "nld_eez") -> list[Event]:
    """Sustained departures from an expected lane.

    Requires the departure to persist: a single position off the line is a
    GPS wobble, and flagging it would bury the analyst in noise from ordinary
    positional jitter.
    """
    if len(positions) < 3:
        return []
    frame = positions.sort_values("timestamp").reset_index(drop=True)
    (lat_a, lon_a), (lat_b, lon_b) = route
    offset_km = np.abs(cross_track_distance(
        frame["lat"], frame["lon"], lat_a, lon_a, lat_b, lon_b)) / 1000.0
    away = offset_km > config.deviation_min_km

    events: list[Event] = []
    for start, end in _runs(away):
        begin = pd.Timestamp(frame["timestamp"].iloc[start])
        finish = pd.Timestamp(frame["timestamp"].iloc[end])
        minutes = (finish - begin).total_seconds() / 60.0
        if minutes < config.deviation_min_minutes:
            continue
        peak = int(start + np.argmax(offset_km[start:end + 1]))
        events.append(_event(
            entity_key=str(frame["entity_key"].iloc[start]),
            event_type="route_deviation", method="route_deviation",
            start=begin, detectable_at=finish, region_key=region_key,
            lat=float(frame["lat"].iloc[peak]),
            lon=float(frame["lon"].iloc[peak]),
            magnitude=float(offset_km[peak]), unit="km",
            duration_minutes=minutes,
            vessel_class=str(frame.get("vessel_class",
                                       pd.Series(["unknown"])).iloc[start]),
        ))
    return events


def extract_events(positions: pd.DataFrame,
                   config: BehaviourConfig | None = None,
                   route: tuple[tuple[float, float],
                                tuple[float, float]] | None = None,
                   region_key: str = "nld_eez") -> list[Event]:
    """Run every primitive over one vessel's positions.

    Returns events sorted by time. Emitting for *all* vessels, including
    those behaving entirely normally, is the intended behaviour: the events
    are observations, and the peer baseline that decides which of them are
    unusual lives at the indicator layer.
    """
    from sentinel.entity.identity import detect_identity_conflicts

    config = config or BehaviourConfig()
    events = detect_loiter(positions, config, region_key)
    events += detect_ais_gaps(positions, config, region_key)
    events += detect_identity_conflicts(positions, config, region_key)
    if route is not None:
        events += detect_route_deviation(positions, config, route, region_key)
    return sorted(events, key=lambda e: e.event_time)
