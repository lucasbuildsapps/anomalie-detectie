"""Observations and events — the shared representation everything reads.

`Event` is the bridge described in ARCHITECTURE_V2.md §2.2. The entity engine
does not emit judgements; it emits typed events that land in the same place as
an ACLED strike or a Taiwan MND release, and are then assessed by identical
machinery. That is what makes one anomaly truth model possible across two
detection paradigms instead of two systems sharing a logo.

`event_type` and `region_key` are open strings. A region introducing
`dark_rendezvous` must not require an edit to core, or the modularity is
decorative.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from sentinel.core.contracts.entity import EntityRef
from sentinel.core.contracts.primitives import GeoPoint
from sentinel.core.contracts.provenance import Lineage

__all__ = ["Event", "Observation"]

_EMPTY: Mapping = MappingProxyType({})


def _check_times(event_time: datetime, ingested_at: datetime,
                 what: str) -> None:
    """Arrival cannot precede occurrence.

    Learning about something before it happens means a clock is wrong, a
    timezone was mishandled, or a synthetic fixture is malformed. Any of
    those silently corrupts every point-in-time reconstruction downstream,
    so it is rejected at construction rather than discovered in a metric.
    """
    if ingested_at < event_time:
        raise ValueError(
            f"{what}: ingested_at ({ingested_at.isoformat()}) precedes "
            f"event_time ({event_time.isoformat()}) — we cannot know "
            f"something before it happens"
        )


@dataclass(frozen=True)
class Observation:
    """One measurement as reported, before any interpretation.

    Carries both clocks. `event_time` is when it happened; `ingested_at` is
    when we learned it. Only the pair supports honest replay — see
    `sentinel.core.time`.
    """

    event_time: datetime
    ingested_at: datetime
    value: float
    source_key: str
    region_key: str | None = None
    area_key: str | None = None
    category: str | None = None
    entity: EntityRef | None = None
    geo: GeoPoint | None = None
    #: True when `ingested_at` was assumed rather than observed (bulk import).
    ingest_estimated: bool = False
    attrs: Mapping = field(default_factory=lambda: _EMPTY)

    def __post_init__(self) -> None:
        _check_times(self.event_time, self.ingested_at, "observation")
        if not str(self.source_key).strip():
            raise ValueError("observation must name its source")

    def known_at(self, as_of: datetime) -> bool:
        """Whether this observation was available at `as_of`.

        The same two-filter rule `AsOfView` applies, expressed on the object
        so callers holding loose observations cannot get it subtly wrong.
        """
        return self.ingested_at <= as_of and self.event_time <= as_of

    @property
    def reporting_lag(self) -> float:
        """Hours between occurrence and arrival. NaN when only assumed."""
        if self.ingest_estimated:
            return float("nan")
        return (self.ingested_at - self.event_time).total_seconds() / 3600.0


@dataclass(frozen=True)
class Event:
    """A typed occurrence, from ingestion or from the entity engine.

    The unit indicators are evaluated over. An `Event` asserts that something
    of a named type happened at a time and place — never that it was unusual.
    Whether it matters is the indicator's business, against a baseline. Fusing
    those two questions is how a detector quietly becomes a quota.
    """

    event_type: str
    event_time: datetime
    ingested_at: datetime
    region_key: str
    lineage: Lineage
    entity: EntityRef | None = None
    geo: GeoPoint | None = None
    area_key: str | None = None
    magnitude: float | None = None
    unit: str | None = None
    ingest_estimated: bool = False
    attrs: Mapping = field(default_factory=lambda: _EMPTY)

    def __post_init__(self) -> None:
        _check_times(self.event_time, self.ingested_at, "event")
        if not str(self.event_type).strip():
            raise ValueError("event must have a type")
        if not str(self.region_key).strip():
            raise ValueError(
                f"event {self.event_type!r} must belong to a region"
            )
        if self.lineage.is_derived and self.entity is None:
            # Entity-engine output describes a specific actor's behaviour.
            # Without the reference the analyst cannot follow it back to a
            # track, and the event is unreviewable.
            raise ValueError(
                f"derived event {self.event_type!r} must reference the entity "
                f"whose behaviour produced it"
            )

    def known_at(self, as_of: datetime) -> bool:
        return self.ingested_at <= as_of and self.event_time <= as_of

    @property
    def is_derived(self) -> bool:
        return self.lineage.is_derived

    def describe(self) -> str:
        where = self.area_key or self.region_key
        who = f" [{self.entity.key}]" if self.entity else ""
        size = f" magnitude {self.magnitude:g}" if self.magnitude is not None else ""
        return (f"{self.event_time.date().isoformat()} {self.event_type} "
                f"at {where}{who}{size}")
