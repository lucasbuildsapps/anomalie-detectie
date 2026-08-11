"""Proximity to critical infrastructure: the last unemitted event type.

`loiter_near_infrastructure` has declared `proximity_critical_infra` in its
watched event types since the region was written, and nothing produced it. The
indicator therefore judged loitering anywhere in the EEZ rather than loitering
*near a cable*, which is a much weaker question — most of the North Sea is not
interesting, and the whole point of the indicator is where the vessel stopped.

Corridors are declared, not derived
-----------------------------------
A cable is a line with a buffer, not a bounding box, and where it runs is
published information that belongs in version control next to the reference
period — an analytical declaration someone signs off, reviewable in a diff.
`data/infrastructure/nld_eez.json` ships **empty**, for the same reason the
chronology does: a file pre-filled with plausible-looking cable routes would
be indistinguishable from a curated one, and nobody should be quietly relying
on coordinates this tool invented.

Why the geometry is done here rather than in PostGIS
----------------------------------------------------
`distance_to_segment` is thirty lines and needs no spatial extension. That
keeps the SQLite test path intact, which the whole suite runs on. PostGIS
earns its place when corridors have to be *indexed* — when there are hundreds
of them and every position has to be tested against all of them. With a
handful of declared corridors, a linear scan is not the bottleneck and the
dependency would buy nothing.

What the event means
--------------------
That a vessel was within the declared buffer of a declared corridor. Nothing
more. It is not a finding on its own — passing over a cable is what the North
Sea is *for* — which is why the indicator pairs it with loitering rather than
alerting on proximity alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.core.contracts import (
    EntityRef,
    Event,
    GeoPoint,
    Lineage,
    Producer,
)
from sentinel.entity.geo import distance_to_segment

__all__ = ["Corridor", "detect_proximity", "load_corridors"]

_METHOD_VERSION = "v1"


@dataclass(frozen=True)
class Corridor:
    """A declared piece of infrastructure, as a polyline with a buffer."""

    key: str
    kind: str
    #: `(lat, lon)` vertices in order. Two points is a straight segment.
    vertices: tuple[tuple[float, float], ...]
    #: How close counts as near, in metres. Carried per corridor because a
    #: wind-farm boundary and a deep-water cable do not warrant the same
    #: margin, and a single global buffer would be wrong for both.
    buffer_m: float = 2000.0
    source_url: str | None = None
    attrs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("corridor key cannot be empty")
        if len(self.vertices) < 2:
            raise ValueError(
                f"corridor {self.key!r} needs at least two vertices; a single "
                f"point is a location, not a corridor")
        if self.buffer_m <= 0:
            raise ValueError(f"corridor {self.key!r} needs a positive buffer")

    def distance_m(self, lat, lon) -> np.ndarray:
        """Distance from each point to the nearest segment of this corridor."""
        distances = [
            distance_to_segment(lat, lon, a[0], a[1], b[0], b[1])
            for a, b in zip(self.vertices[:-1], self.vertices[1:], strict=True)
        ]
        return np.min(np.vstack([np.atleast_1d(d) for d in distances]), axis=0)


def load_corridors(path: Path | str) -> tuple[Corridor, ...]:
    """Read declared corridors from JSON. An absent file means none declared.

    Absent is not an error: a region with no declared infrastructure simply
    cannot test proximity, and the indicator reports that rather than the
    loader raising. Inventing a default corridor would be far worse.
    """
    path = Path(path)
    if not path.exists():
        return ()
    raw = json.loads(path.read_text() or "[]")
    return tuple(
        Corridor(
            key=str(item["key"]),
            kind=str(item.get("kind", "unknown")),
            vertices=tuple((float(v[0]), float(v[1]))
                           for v in item["vertices"]),
            buffer_m=float(item.get("buffer_m", 2000.0)),
            source_url=item.get("source_url"),
            attrs=dict(item.get("attrs", {})),
        )
        for item in raw
    )


def detect_proximity(positions: pd.DataFrame, corridors, config=None,
                     region_key: str = "nld_eez") -> list[Event]:
    """One event per unbroken approach to a corridor.

    Per *approach*, not per position report. A vessel crossing a cable at ten
    knots emits a position every few seconds; one event each would bury the
    indicator in a single legitimate transit. The run is collapsed and its
    magnitude is the closest approach, because that is the number an analyst
    would ask for.
    """
    if not corridors or positions.empty:
        return []

    frame = positions.sort_values("timestamp").reset_index(drop=True)
    lat = frame["lat"].to_numpy(dtype=float)
    lon = frame["lon"].to_numpy(dtype=float)

    events: list[Event] = []
    for corridor in corridors:
        distance = np.atleast_1d(corridor.distance_m(lat, lon))
        near = distance <= corridor.buffer_m
        if not near.any():
            continue

        # Contiguous runs of "near": one approach, one event. Padding with
        # False at both ends turns the run boundaries into a plain diff, which
        # avoids the special cases for a track that starts or ends inside the
        # buffer — and those are exactly the cases a vessel that stopped on a
        # cable produces.
        padded = np.concatenate([[False], near, [False]])
        changes = np.flatnonzero(np.diff(padded.astype(np.int8)))
        starts = changes[0::2].astype(int)
        ends = (changes[1::2] - 1).astype(int)

        for start, end in zip(starts, ends, strict=True):
            window = distance[start:end + 1]
            closest = int(np.argmin(window)) + start
            events.append(Event(
                event_type="proximity_critical_infra",
                event_time=pd.Timestamp(
                    frame["timestamp"].iloc[start]).to_pydatetime(),
                ingested_at=pd.Timestamp(
                    frame["timestamp"].iloc[end]).to_pydatetime(),
                region_key=region_key,
                entity=EntityRef("vessel",
                                 str(frame["entity_key"].iloc[start])),
                geo=GeoPoint(lat=float(lat[closest]), lon=float(lon[closest]),
                             precision_m=100.0),
                area_key=corridor.key,
                magnitude=float(distance[closest]),
                unit="metres",
                lineage=Lineage(producer=Producer.ENTITY_ENGINE,
                                method=f"proximity.{_METHOD_VERSION}"),
                attrs={
                    "corridor_key": corridor.key,
                    "corridor_kind": corridor.kind,
                    "closest_approach_m": float(distance[closest]),
                    "buffer_m": float(corridor.buffer_m),
                    "vessel_class": frame.get(
                        "vessel_class", pd.Series(["unknown"])).iloc[start],
                },
            ))
    return sorted(events, key=lambda e: e.event_time)
