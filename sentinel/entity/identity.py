"""Identity conflicts: one identifier, more vessels than there can be.

The last of the four NLD EEZ indicators without a primitive behind it. The
`identity_swap` scenario produced no events, so the indicator could only ever
report insufficient data no matter what arrived.

Which conflict this detects, and why that one
----------------------------------------------
An MMSI is a claim, not a fact. Two things can go wrong with it, and they are
not equally worth building:

**Static-field conflict** — the same identifier broadcasting a different name,
IMO or callsign than before. Easy to detect, and mostly *legitimate*:
reflagging and renaming happen constantly, and a detector built on this would
spend its life reporting paperwork. It also needs those fields stored, which
they are not. Not built; see the region README.

**Kinematic impossibility** — the same identifier reported at two places no
single hull could have travelled between in the elapsed time. This one cannot
be explained away by paperwork. Either the position is wrong or the identity
is, and both are worth an analyst's attention. It also needs nothing but
`lat`, `lon` and `timestamp`, which is exactly what the position store holds.

So this module detects the second. The event says *one identifier was used by
what must be more than one vessel*; it does not say which one is the impostor,
or that anyone intended anything — an AIS decoding error produces the same
signature, and that alternative travels with the assessment.

The threshold is not a speed limit
----------------------------------
`implausible_speed_knots` is deliberately far above any real vessel. The
question is not "was this ship speeding" but "is this physically one ship at
all", and setting it near a plausible maximum would turn a hard impossibility
into a soft judgement about vessel performance. A fast ferry does 40 knots; a
GPS glitch does 400.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.core.contracts import (
    EntityRef,
    Event,
    GeoPoint,
    Lineage,
    Producer,
)
from sentinel.entity.geo import elapsed_seconds, haversine

__all__ = ["detect_identity_conflicts"]

_METHOD_VERSION = "v1"


def detect_identity_conflicts(positions: pd.DataFrame, config,
                              region_key: str = "nld_eez") -> list[Event]:
    """Jumps that no single vessel could have made.

    Anchored at the *later* of the two positions: that is the report which
    made the conflict visible, and dating the event to the earlier one would
    claim we knew before the second message arrived.

    Consecutive duplicate timestamps are skipped rather than treated as
    infinite speed. Two reports at the same instant are usually the same
    message received twice, which is routine in AIS and says nothing about
    identity.
    """
    if len(positions) < 2:
        return []

    frame = positions.sort_values("timestamp").reset_index(drop=True)
    seconds = elapsed_seconds(frame["timestamp"])
    distance_m = np.full(len(frame), np.nan)
    distance_m[1:] = haversine(
        frame["lat"].to_numpy()[:-1], frame["lon"].to_numpy()[:-1],
        frame["lat"].to_numpy()[1:], frame["lon"].to_numpy()[1:])

    with np.errstate(divide="ignore", invalid="ignore"):
        knots = (distance_m / np.maximum(seconds, 1e-9)) * 1.943844

    threshold = float(getattr(config, "implausible_speed_knots", 100.0))
    events: list[Event] = []
    for i in np.flatnonzero(np.nan_to_num(knots, nan=0.0) > threshold):
        if not np.isfinite(seconds[i]) or seconds[i] <= 0:
            # Same-instant duplicates: one message heard twice, not two
            # vessels. Reporting these would bury the real conflicts.
            continue
        before, after = frame.iloc[i - 1], frame.iloc[i]
        events.append(Event(
            event_type="identity_conflict",
            event_time=pd.Timestamp(after["timestamp"]).to_pydatetime(),
            ingested_at=pd.Timestamp(after["timestamp"]).to_pydatetime(),
            region_key=region_key,
            entity=EntityRef("vessel", str(after["entity_key"])),
            geo=GeoPoint(lat=float(after["lat"]), lon=float(after["lon"]),
                         precision_m=100.0),
            magnitude=float(knots[i]),
            unit="knots",
            lineage=Lineage(producer=Producer.ENTITY_ENGINE,
                            method=f"identity_conflict.{_METHOD_VERSION}"),
            attrs={
                "implied_speed_knots": float(knots[i]),
                "gap_seconds": float(seconds[i]),
                "distance_km": float(distance_m[i] / 1000.0),
                "from_lat": float(before["lat"]),
                "from_lon": float(before["lon"]),
                "vessel_class": after.get("vessel_class", "unknown"),
            },
        ))
    return events
