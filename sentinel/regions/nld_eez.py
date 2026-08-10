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
All four now have one, each measured against its own rule:

| indicator | floor | judged on |
|---|---|---|
| loitering | 1 hour | rarity *and* magnitude |
| dark period | 720 min | duration only — a dropout happens *to* a vessel, so asking whether its class does this would report receiver coverage as conduct |
| identity conflict | 40 km | physical impossibility at a ten-minute cadence |
| unidentified density | 3x | level deviation |

The identity floor is a distance, and a distance only becomes an implied
speed once divided by the reporting interval — so it describes the feed as
much as the detector. What it does *not* cover is a changed name, IMO or
callsign: reflagging is legitimate and constant, and a detector built on it
would spend its life reporting paperwork. Those fields are not stored, and
that is a deliberate order of work rather than an oversight.

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
from sentinel.ingest.dma_ais import DMA_SOURCE
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
    # Imported rather than restated: the connector is where this source's
    # grading is maintained, and two copies would drift the moment one is
    # revised. A region declares *which* sources it uses, not what they are.
    DMA_SOURCE,
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
            # Judged on duration against peers, never on rarity. A dropout
            # happens *to* a vessel, so "does this class do this" measures
            # receiver coverage rather than conduct — and the measured floor
            # below was taken under exactly this setting, so changing it here
            # would invalidate the number.
            "use_rarity": False,
        },
    ),
    Indicator(
        key="identity_inconsistency",
        region_key=KEY,
        name="Conflicting vessel identity",
        question=("Is one identifier being used by what must be more than "
                  "one vessel?"),
        meaning=("Two reports no single hull could have travelled between. "
                 "Reflagging and renaming are legitimate and common, so the "
                 "test is deliberately not about changed paperwork — it is "
                 "about physical impossibility. A decoding error produces the "
                 "same signature, and the record says which vessel broadcast "
                 "what, never who intended anything."),
        test_type=IndicatorTest.ENTITY_BEHAVIOUR,
        entity_kind="vessel",
        status=IndicatorStatus.DRAFT,
        test_config={
            "event_types": ["identity_conflict"],
            "peer_baseline": ["vessel_class"],
        },
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
        "static-identity fields (name, IMO, callsign) in the position store, "
        "if conflicting paperwork is ever to be tested alongside the "
        "physical-impossibility check that exists now",
    ),
    summary=("Maritime behaviour in the Dutch EEZ. Positions, derived events, "
             "peer baselines and a measured floor for every indicator are in "
             "place and run end to end; there is no AIS feed yet, so nothing "
             "is being tested."),
)
