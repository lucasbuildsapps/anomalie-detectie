"""NLD EEZ: maritime behaviour in the Dutch exclusive economic zone.

Status is DATA_ONLY, and that is the honest answer today rather than a
placeholder. What is missing is no longer the machinery — the entity engine
exists, emits typed events, judges them against peer baselines, and has a
measured detection floor for loitering. What is missing is the data: there is
no live AIS feed and no infrastructure geometry to measure proximity against.
Marking the region MONITORED without those would let a tab that cannot see
anything present itself as watching.

The two-layer design is why these can be declared now at all. The entity
engine emits typed events (`ais_gap`, `loiter`, `proximity_critical_infra`)
into the same event store as any count source, and the indicators below are
evaluated by exactly the machinery Euro-Atlantic already uses. No second
engine, no parallel truth model.

What the measured floor does and does not cover
-----------------------------------------------
Only `loiter_near_infrastructure` has one. The fleet harness injects
loitering, so that is the only behaviour whose floor has been measured; the
catalogue declines to quote a number for the AIS-gap and identity indicators
rather than lending them a floor from a different behaviour. Those two will
report insufficient data even once AIS lands — correctly, and visibly.

The loiter floor also carries a condition worth reading before trusting a
quiet answer: detection depends on the behaviour staying *rare* within its
class, and collapses to nothing once roughly a tenth of the class does it.
See `sentinel/eval/entity_power.py`.

A note on baselines here
------------------------
Maritime behaviour needs a *peer* baseline, not only a historical one:
fishing vessels loiter as a matter of course, bulk carriers do not. "Unusual"
means unusual for that vessel class, in that area, at that time of year. The
indicators record that in `test_config` so the requirement is visible before
the engine is built, rather than being discovered afterwards.
"""
from __future__ import annotations

from sentinel.core.contracts import (
    Credibility,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    MonitoringStatus,
    Reliability,
    Source,
)
from sentinel.regions.base import GeoScope, RegionModule

KEY = "nld_eez"

SOURCES = (
    Source(
        key="aisstream",
        name="aisstream.io live AIS",
        kind="ais",
        reliability=Reliability.C,
        credibility=Credibility.C3,
        url="https://aisstream.io",
        licence="free tier; no historical archive",
        redistribution_allowed=False,
    ),
    Source(
        key="dma_ais",
        name="Danish Maritime Authority historical AIS",
        kind="ais",
        reliability=Reliability.B,
        credibility=Credibility.C2,
        licence="open data",
        redistribution_allowed=True,
    ),
)

INDICATORS = (
    Indicator(
        key="loiter_near_infrastructure",
        region_key=KEY,
        name="Loitering near critical infrastructure",
        question=("Is a vessel behaving near cables, pipelines or wind farms "
                  "in a way that is unusual for its class in that area?"),
        meaning=("Loitering by a vessel class that does not normally loiter, "
                 "close to infrastructure, is a recognised precursor "
                 "behaviour. It is not attribution and must not be reported "
                 "as intent."),
        test_type=IndicatorTest.ENTITY_BEHAVIOUR,
        entity_kind="vessel",
        status=IndicatorStatus.DRAFT,
        test_config={
            "event_types": ["loiter", "proximity_critical_infra"],
            "peer_baseline": ["vessel_class", "area", "month"],
            "min_duration_minutes": 60,
        },
    ),
    Indicator(
        key="ais_gap_near_infrastructure",
        region_key=KEY,
        name="AIS gap near critical infrastructure",
        question=("Did a vessel stop transmitting while close to "
                  "infrastructure, for longer than is normal for its class?"),
        meaning=("A gap may be equipment failure, poor reception, or "
                 "deliberate. The tool reports the gap and its context; which "
                 "of the three it is belongs to the analyst."),
        test_type=IndicatorTest.ENTITY_BEHAVIOUR,
        entity_kind="vessel",
        status=IndicatorStatus.DRAFT,
        test_config={
            "event_types": ["ais_gap"],
            "peer_baseline": ["vessel_class", "area"],
            "min_gap_minutes": 30,
        },
    ),
    Indicator(
        key="identity_inconsistency",
        region_key=KEY,
        name="Conflicting vessel identity",
        question=("Is a vessel broadcasting identifiers that conflict with "
                  "what it broadcast before?"),
        meaning=("Reflagging is legitimate and common; spoofing is not. The "
                 "record shows the conflict, and the distinction needs "
                 "context the numbers do not carry."),
        test_type=IndicatorTest.ENTITY_BEHAVIOUR,
        entity_kind="vessel",
        status=IndicatorStatus.DRAFT,
        test_config={"event_types": ["identity_inconsistency"]},
    ),
    Indicator(
        key="unknown_vessel_density",
        region_key=KEY,
        name="Density of unidentified contacts",
        question=("Is the count of contacts without a resolvable identity "
                  "unusual for this area?"),
        meaning=("A rise may reflect a collection problem or a genuine "
                 "increase in vessels avoiding identification. Counted rather "
                 "than tracked, so this one works without the entity engine."),
        test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.DRAFT,
        test_config={"threshold": 3.5, "aggregation": "daily"},
    ),
)

MODULE = RegionModule(
    key=KEY,
    name="Netherlands EEZ",
    status=MonitoringStatus.DATA_ONLY,
    geography=GeoScope(lat_min=51.0, lat_max=56.0, lon_min=2.0, lon_max=7.5,
                       areas=("north_sea_south", "north_sea_central",
                              "wind_farm_zones", "cable_corridors")),
    sources=SOURCES,
    indicators=INDICATORS,
    activation_requirements=(
        "an AIS feed with retained history for the Dutch EEZ — the storage, "
        "derivation and evaluation path all exist; the data does not",
        "infrastructure geometry (cables, pipelines, wind farms), which needs "
        "corridor shapes rather than the bounding boxes stored today",
        "identity resolution, for the identity-conflict indicator",
        "a measured floor for AIS gaps, which the fleet harness does not "
        "yet inject",
    ),
    summary=("Maritime behaviour in the Dutch EEZ. Positions, derived events, "
             "peer baselines and a measured loiter floor are all in place and "
             "run end to end; there is no AIS feed yet, so nothing is being "
             "tested."),
)
