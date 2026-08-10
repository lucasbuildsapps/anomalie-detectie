"""The entity engine reaching the production evaluation path.

Until this existed, `sentinel/entity/` produced `Event` objects that lived in
memory and were never written anywhere, and `evaluate_region` never passed
events or peers into a context — so every entity indicator returned
INSUFFICIENT_DATA no matter what the data said. The machinery was built,
measured, and unreachable.

These tests are the end-to-end claim: positions -> typed events -> storage ->
`AsOfView` -> `evaluate_region` -> a verdict, through the same
`evaluate_indicator` call every count indicator uses.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core import storage
from sentinel.core.contracts import (
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    MonitoringStatus,
    Verdict,
)
from sentinel.core.detect.power import DetectionPowerCatalog
from sentinel.core.time import PointInTimeStore
from sentinel.entity import extract_events
from sentinel.eval.synthetic.vessels import build_fleet
from sentinel.regions import evaluate_region
from sentinel.regions.base import GeoScope, RegionModule
from sentinel.regions.providers import events_from_view, storage_event_provider

#: After the synthetic fleet's last position, so everything it produced is
#: knowable. The fleet spans 2024-06-01 to 2024-06-02.
AS_OF = dt.datetime(2024, 6, 5)
REGION_KEY = "nld_eez"


def _indicator(**overrides) -> Indicator:
    kwargs = dict(
        key="loiter_near_infrastructure", region_key=REGION_KEY,
        name="Loitering", question="q?", meaning="m",
        test_type=IndicatorTest.ENTITY_BEHAVIOUR, entity_kind="vessel",
        status=IndicatorStatus.ACTIVE,
        test_config={"event_types": ["loiter"],
                     "peer_baseline": ["vessel_class"]})
    kwargs.update(overrides)
    return Indicator(**kwargs)


def _region(indicators) -> RegionModule:
    return RegionModule(
        key=REGION_KEY, name="Netherlands EEZ",
        status=MonitoringStatus.MONITORED,
        geography=GeoScope(lat_min=51.0, lat_max=56.0, lon_min=2.0,
                           lon_max=7.5),
        indicators=tuple(indicators),
        summary="test region")


@pytest.fixture()
def fleet_dataset(tmp_path, monkeypatch):
    """A synthetic fleet, its events derived and stored like production."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'entity.db'}")
    storage.init_db()
    ds = storage.create_dataset("fleet", "", {})

    fleet = build_fleet(seed=42)
    events = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group, region_key=REGION_KEY))

    stored = storage.insert_entity_events(ds, events)
    assert stored > 0, "the fixture must actually persist something"
    return ds, fleet, events


# =========================================================================
# persistence
# =========================================================================
def test_events_survive_a_round_trip(fleet_dataset):
    ds, _fleet, events = fleet_dataset
    view = PointInTimeStore().view(AS_OF)
    restored = events_from_view(view, ds, REGION_KEY)

    assert len(restored) == len(events)
    assert {e.event_type for e in restored} == {e.event_type for e in events}
    assert all(e.entity is not None for e in restored)
    assert all(e.lineage.method for e in restored), (
        "a derived event without its method cannot be reproduced or argued "
        "with, and the contract requires one")


def test_re_running_the_detector_adds_nothing(fleet_dataset):
    """Idempotence. The first time we saw the behaviour is when we knew it."""
    ds, _fleet, events = fleet_dataset
    assert storage.insert_entity_events(ds, events) == 0


# =========================================================================
# point-in-time
# =========================================================================
def test_events_are_invisible_before_they_were_derived(fleet_dataset):
    ds, _fleet, events = fleet_dataset
    earliest = min(e.ingested_at for e in events)
    before = PointInTimeStore().view(earliest - dt.timedelta(days=1))
    assert events_from_view(before, ds, REGION_KEY) == ()


def test_a_region_filter_does_not_leak_other_regions(fleet_dataset):
    ds, _fleet, _events = fleet_dataset
    view = PointInTimeStore().view(AS_OF)
    assert events_from_view(view, ds, "euro_atlantic") == ()


# =========================================================================
# the wiring itself
# =========================================================================
def test_an_entity_indicator_now_produces_a_verdict(fleet_dataset):
    """The gap this work closes. Before, this was INSUFFICIENT_DATA always."""
    ds, fleet, _events = fleet_dataset
    store = PointInTimeStore()
    region = _region([_indicator()])

    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=storage_event_provider(ds, region, store.view),
        population_provider=lambda i, a: fleet.positions[
            ["entity_key", "vessel_class"]].drop_duplicates(),
    )

    signal = status.signals[0]
    assert signal.verdict is Verdict.ACTIVE, signal.insufficient_reason
    assert signal.effect_size is not None


def test_the_flagged_vessel_is_the_planted_one(fleet_dataset):
    """A verdict is only worth having if it points at the right vessel."""
    ds, fleet, _events = fleet_dataset
    store = PointInTimeStore()
    region = _region([_indicator()])

    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=storage_event_provider(ds, region, store.view),
        population_provider=lambda i, a: fleet.positions[
            ["entity_key", "vessel_class"]].drop_duplicates(),
    )

    evidence = " ".join(e.summary for e in status.signals[0].evidence)
    assert any(key in evidence for key in fleet.target_keys)
    assert "fish" not in evidence, "the trawler fleet must not be flagged"


def test_without_an_event_provider_the_indicator_says_why(fleet_dataset):
    """Not a silent skip: an unconfigured region has to be visible."""
    _ds, _fleet, _events = fleet_dataset
    status = evaluate_region(_region([_indicator()]), lambda i, a: None,
                             AS_OF, DetectionPowerCatalog())
    signal = status.signals[0]
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no entity-event source configured" in signal.insufficient_reason


def test_an_empty_event_store_is_untested_not_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    storage.init_db()
    ds = storage.create_dataset("empty", "", {})
    region = _region([_indicator()])
    store = PointInTimeStore()

    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=storage_event_provider(ds, region, store.view))
    signal = status.signals[0]
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no entity events" in signal.insufficient_reason


# =========================================================================
# the degenerate denominator
# =========================================================================
def test_without_a_population_a_null_result_is_refused(fleet_dataset):
    """Rarity needs every vessel observed, including the silent majority.

    Without it participation is 1.0 by construction, only magnitude can flag,
    and "nothing unusual" would mean "the question we mainly rely on was
    never asked". That must not read as quiet.
    """
    ds, _fleet, _events = fleet_dataset
    store = PointInTimeStore()
    # Trawlers only: they loiter constantly, so magnitude alone finds nothing.
    region = _region([_indicator(
        test_config={"event_types": ["loiter"],
                     "peer_baseline": ["vessel_class"]})])

    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=storage_event_provider(ds, region, store.view),
        population_provider=None)

    signal = status.signals[0]
    assert signal.verdict is Verdict.INSUFFICIENT_DATA, (
        "a magnitude-only assessment must not be reported as quiet")
    assert "rare for its class could not be tested" in \
        signal.insufficient_reason


def test_a_supplied_population_makes_rarity_testable(fleet_dataset):
    from sentinel.entity import PeerBaseline

    _ds, fleet, events = fleet_dataset
    population = fleet.positions[["entity_key", "vessel_class"]].drop_duplicates()

    assert PeerBaseline.fit(events, population=population).rarity_testable
    assert PeerBaseline.fit(events).is_degenerate
