"""Regions that are declared but not yet watched.

These exist so the interface tells the truth. A tab that is simply absent
suggests the region is out of scope; a tab that is present but empty suggests
it is quiet. Neither is accurate, and the second is dangerous — it is the
null-result problem at region scale.

So each of these carries NOT_MONITORED plus the specific conditions that
would move it. That turns the shell into a visible roadmap: an analyst opening
the MENA tab learns that nothing is being watched and exactly what is missing,
rather than inferring calm from a blank panel.

Each module is deliberately thin. When one activates, its indicators are
declared the same way Euro-Atlantic's are, and the same engine evaluates
them. If activating a region ever requires new core code, the abstraction has
failed and that is worth knowing early.
"""
from __future__ import annotations

from sentinel.core.contracts import MonitoringStatus
from sentinel.regions.base import GeoScope, RegionModule

MENA = RegionModule(
    key="mena",
    name="Middle East and North Africa",
    status=MonitoringStatus.NOT_MONITORED,
    geography=GeoScope(lat_min=12.0, lat_max=40.0, lon_min=25.0, lon_max=63.0,
                       areas=("levant", "gulf", "red_sea")),
    activation_requirements=(
        "a licensed conflict-event feed with usable geographic precision",
        "an independently compiled incident chronology for validation",
        "a declared reference period, which is contested here in a way it is "
        "not elsewhere",
    ),
    summary=("Strike activity and escalation between Iran, Israel and the US. "
             "The hardest data-quality problem of the five: sparse, bursty "
             "and heavily contested reporting."),
)

INDO_PACIFIC = RegionModule(
    key="indo_pacific",
    name="Indo-Pacific",
    status=MonitoringStatus.NOT_MONITORED,
    geography=GeoScope(lat_min=18.0, lat_max=30.0, lon_min=115.0, lon_max=128.0,
                       areas=("taiwan_adiz", "taiwan_strait", "bashi_channel")),
    activation_requirements=(
        "a parser for the Taiwan MND daily activity releases",
        "an agreed reference period for pre-escalation ADIZ activity",
    ),
    summary=("Air and naval activity around Taiwan. The cleanest structured "
             "count data available in open sources, and a well-documented "
             "escalation ladder — the best test case for sustained-escalation "
             "detection after Euro-Atlantic."),
)

CARIBBEAN = RegionModule(
    key="caribbean",
    name="Caribbean",
    status=MonitoringStatus.NOT_MONITORED,
    geography=GeoScope(lat_min=8.0, lat_max=24.0, lon_min=-88.0, lon_max=-58.0,
                       areas=("western_caribbean", "eastern_caribbean")),
    activation_requirements=(
        "AIS coverage for the area, which is far thinner than the North Sea",
        "the entity engine, for route and behaviour analysis",
        "an interdiction record to validate against",
    ),
    summary=("Maritime trafficking behaviour. Note that network analysis is "
             "deliberately out of scope: the relationship data it needs does "
             "not exist in open sources, and a network diagram built without "
             "it would be invention."),
)

MODULES = (MENA, INDO_PACIFIC, CARIBBEAN)
