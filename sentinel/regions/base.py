"""A region is configuration, not an application.

Five regional tabs must not become five codebases. So a region declares what
it watches, where, against which reference, and from which sources — and the
same engine evaluates all of them. If a region cannot be expressed without
changing core, that is a design conversation, not a patch (ARCHITECTURE_V2.md
§7.1).

The honest empty tab
--------------------
`MonitoringStatus` is carried on the module itself and is not optional. A
region panel with no alerts reads as "quiet"; for an unmonitored region the
truth is "nobody is looking". That is the null-result problem promoted from
the alert level to the region level, and it is the single most misleading
thing this product could do. So an unmonitored region must declare what
activating it would require, and `RegionStatus.headline()` refuses to conflate
the two states.

Done that way the shell is both honest and a visible roadmap: the tab tells
you what is missing rather than implying there is nothing to see.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sentinel.core.contracts import (
    DateRange,
    Indicator,
    IndicatorStatus,
    MonitoringStatus,
    Source,
)

__all__ = ["GeoScope", "RegionModule"]


@dataclass(frozen=True)
class GeoScope:
    """Where a region is, coarsely.

    A bounding box and named areas, deliberately without geometry libraries.
    Precise polygons belong with the entity engine and PostGIS; carrying them
    here would make every region module depend on a spatial stack it does not
    need to describe itself.
    """

    lat_min: float = -90.0
    lat_max: float = 90.0
    lon_min: float = -180.0
    lon_max: float = 180.0
    areas: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.lat_min > self.lat_max:
            raise ValueError("lat_min above lat_max")
        if self.lon_min > self.lon_max:
            raise ValueError("lon_min above lon_max")

    def contains(self, lat: float, lon: float) -> bool:
        return (self.lat_min <= lat <= self.lat_max
                and self.lon_min <= lon <= self.lon_max)

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        """`(lat_min, lat_max, lon_min, lon_max)`, or None if worldwide.

        None for the default scope on purpose: filtering a query by a box that
        covers the planet is pure cost, and it also lets a caller distinguish
        "this region declared its extent" from "nobody narrowed it".
        """
        if (self.lat_min, self.lat_max, self.lon_min, self.lon_max) == \
                (-90.0, 90.0, -180.0, 180.0):
            return None
        return (self.lat_min, self.lat_max, self.lon_min, self.lon_max)


@dataclass(frozen=True)
class RegionModule:
    """One monitored area: its sources, indicators and declared normal."""

    key: str
    name: str
    status: MonitoringStatus
    geography: GeoScope = field(default_factory=GeoScope)
    sources: tuple[Source, ...] = ()
    indicators: tuple[Indicator, ...] = ()
    default_aggregation: str = "daily"
    reference_period: DateRange | None = None
    #: What would have to be true for this region to move to MONITORED.
    #: Required when it is not — an empty tab must explain itself.
    activation_requirements: tuple[str, ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("region key cannot be empty")
        if not str(self.name).strip():
            raise ValueError(f"region {self.key!r} needs a display name")

        if self.status is MonitoringStatus.NOT_MONITORED:
            if not self.activation_requirements:
                raise ValueError(
                    f"region {self.key!r} is not monitored but does not say "
                    f"what activating it would require; an empty panel then "
                    f"reads as 'quiet' when it means 'unwatched'"
                )
            if self.indicators:
                raise ValueError(
                    f"region {self.key!r} is marked not monitored but carries "
                    f"indicators; pick one"
                )

        if self.status is MonitoringStatus.MONITORED and not self.indicators:
            raise ValueError(
                f"region {self.key!r} claims to be monitored but defines no "
                f"indicators; nothing would be tested"
            )

        for indicator in self.indicators:
            if indicator.region_key != self.key:
                raise ValueError(
                    f"indicator {indicator.key!r} belongs to region "
                    f"{indicator.region_key!r}, not {self.key!r}"
                )

    @property
    def active_indicators(self) -> tuple[Indicator, ...]:
        """Indicators watching *now*. For a replay, use `indicators_at`."""
        return tuple(i for i in self.indicators
                     if i.status is IndicatorStatus.ACTIVE)

    def indicators_at(self, as_of) -> tuple[Indicator, ...]:
        """Indicators that were watching at `as_of`.

        The configuration counterpart of `AsOfView`. Evaluating today's
        indicator set against 2022 credits the system with an indicator
        written last month, which inflates every retrospective warning time
        that indicator contributes to.
        """
        return tuple(i for i in self.indicators if i.was_active_at(as_of))

    @property
    def undated_indicators(self) -> tuple[Indicator, ...]:
        """Active indicators that cannot say when they started watching.

        Their coverage in a replay is assumed rather than known — the same
        distinction `ingest_estimated` draws for observations.
        """
        return tuple(i for i in self.active_indicators
                     if not i.activation_dated)

    @property
    def is_watched(self) -> bool:
        return self.status.can_produce_verdicts

    def describe(self) -> str:
        if self.status is MonitoringStatus.NOT_MONITORED:
            return (f"{self.name}: not monitored. Requires "
                    f"{'; '.join(self.activation_requirements)}.")
        if self.status is MonitoringStatus.DATA_ONLY:
            return (f"{self.name}: data collected, no indicators defined. "
                    f"Nothing is being tested.")
        return (f"{self.name}: {len(self.active_indicators)} active "
                f"indicator(s) across {len(self.sources)} source(s).")
