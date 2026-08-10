"""The five regions, and the one engine that evaluates all of them.

Registration order is display order. Watched regions come first because that
is what an analyst opens the tool for; the unmonitored ones follow, each
stating what would activate it. Hiding them would be worse — a missing tab
suggests the region is out of scope, when the truth is that it is in scope
and not yet built.
"""
from sentinel.regions import euro_atlantic, nld_eez, pending
from sentinel.regions.base import GeoScope, RegionModule
from sentinel.regions.evaluate import (
    EventProvider,
    PopulationProvider,
    SeriesProvider,
    evaluate_region,
)

#: Display order: watched first, then declared-but-not-yet-watched.
REGIONS: tuple[RegionModule, ...] = (
    euro_atlantic.MODULE,
    nld_eez.MODULE,
    *pending.MODULES,
)

_BY_KEY = {region.key: region for region in REGIONS}

if len(_BY_KEY) != len(REGIONS):  # pragma: no cover - guards a typo at import
    raise RuntimeError("duplicate region keys in the registry")


def get_region(key: str) -> RegionModule:
    """Look up a region, or fail loudly.

    No silent default: returning some other region because a key was
    mistyped would attach one region's verdicts to another's name.
    """
    try:
        return _BY_KEY[key]
    except KeyError:
        known = ", ".join(sorted(_BY_KEY))
        raise KeyError(f"unknown region {key!r}; known regions: {known}") from None


def watched_regions() -> tuple[RegionModule, ...]:
    """Regions that can actually produce verdicts."""
    return tuple(r for r in REGIONS if r.is_watched)


__all__ = [
    "REGIONS",
    "EventProvider",
    "GeoScope",
    "PopulationProvider",
    "RegionModule",
    "SeriesProvider",
    "evaluate_region",
    "get_region",
    "watched_regions",
]
