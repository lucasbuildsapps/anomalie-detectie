"""Position storage, and the denominator it exists to supply.

The obvious job is somewhere for a feed to land. The load-bearing job is the
*observed population*: every entity that emitted anything, including the silent
majority that did nothing interesting. Without it, participation is 1.0 by
construction and rarity — the primary entity signal — cannot fire at all.

The mismatch test near the bottom is the one worth reading. Scoping the
numerator and the denominator to different windows is a one-line mistake that
produces false positives in exactly the direction the peer baseline is supposed
to protect against, and `entity_providers` exists to make it unavailable.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
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
from sentinel.regions.providers import (
    entity_providers,
    events_from_view,
    population_from_view,
)

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


def _region() -> RegionModule:
    return RegionModule(
        key=REGION_KEY, name="Netherlands EEZ",
        status=MonitoringStatus.MONITORED,
        geography=GeoScope(lat_min=51.0, lat_max=56.0, lon_min=2.0,
                           lon_max=7.5),
        indicators=(_indicator(),), summary="test region")


@pytest.fixture()
def fleet_db(tmp_path, monkeypatch):
    """Positions stored the way a feed would store them, then events derived."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'pos.db'}")
    storage.init_db()
    ds = storage.create_dataset("fleet", "", {})

    fleet = build_fleet(seed=42)
    n = storage.insert_positions(ds, fleet.positions, arrival="event_time")
    assert n == len(fleet.positions)

    events = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group, region_key=REGION_KEY))
    storage.insert_entity_events(ds, events)
    return ds, fleet


# =========================================================================
# storage
# =========================================================================
def test_positions_round_trip(fleet_db):
    ds, fleet = fleet_db
    stored = storage.load_positions_as_of(ds, AS_OF)
    assert len(stored) == len(fleet.positions)
    assert stored["entity_key"].nunique() == fleet.positions["entity_key"].nunique()
    assert set(stored["vessel_class"]) == set(fleet.positions["vessel_class"])


def test_reimporting_the_same_positions_adds_nothing(fleet_db):
    """AIS duplicates via multiple receivers are the rule, not the exception."""
    ds, fleet = fleet_db
    assert storage.insert_positions(ds, fleet.positions,
                                    arrival="event_time") == 0


def test_a_position_without_identity_time_or_place_is_refused(fleet_db):
    ds, _fleet = fleet_db
    with pytest.raises(ValueError, match="entity_key"):
        storage.insert_positions(ds, pd.DataFrame([{"timestamp": "2024-06-01",
                                                    "lat": 54.0, "lon": 3.0}]))


def test_rows_missing_a_fix_are_skipped_not_stored(fleet_db):
    ds, _fleet = fleet_db
    frame = pd.DataFrame([
        {"entity_key": "x", "timestamp": "2024-06-01", "lat": None, "lon": 3.0},
        {"entity_key": "x", "timestamp": None, "lat": 54.0, "lon": 3.0},
    ])
    assert storage.insert_positions(ds, frame, arrival="event_time") == 0


def test_positions_respect_the_point_in_time_boundary(fleet_db):
    ds, _fleet = fleet_db
    early = storage.load_positions_as_of(ds, dt.datetime(2024, 6, 1, 12))
    late = storage.load_positions_as_of(ds, AS_OF)
    assert 0 < len(early) < len(late)
    assert early["timestamp"].max() <= pd.Timestamp("2024-06-01 12:00")


def test_a_bounding_box_excludes_what_falls_outside_it(fleet_db):
    ds, _fleet = fleet_db
    inside = storage.load_positions_as_of(
        ds, AS_OF, bbox=(51.0, 56.0, 2.0, 7.5))
    elsewhere = storage.load_positions_as_of(
        ds, AS_OF, bbox=(10.0, 20.0, 100.0, 110.0))
    assert len(inside) > 0
    assert len(elsewhere) == 0


def test_the_view_reads_positions_causally(fleet_db):
    ds, _fleet = fleet_db
    view = PointInTimeStore().view(dt.datetime(2024, 6, 1, 6))
    frame = view.positions(ds)
    assert not frame.empty
    assert frame["timestamp"].max() <= pd.Timestamp("2024-06-01 06:00")


# =========================================================================
# the population
# =========================================================================
def test_the_population_includes_vessels_that_did_nothing(fleet_db):
    """The whole point. Events only record vessels that acted; the denominator
    has to include the ones that were merely present."""
    ds, fleet = fleet_db
    view = PointInTimeStore().view(AS_OF)
    population = population_from_view(view, ds, group_by=("vessel_class",))

    event_entities = {e.entity.key
                      for e in events_from_view(view, ds, REGION_KEY)
                      if e.entity}

    assert len(population) == fleet.positions["entity_key"].nunique()
    assert len(population) > len(event_entities), (
        "if every observed vessel produced an event the fixture is not "
        "exercising the silent majority this denominator exists for")


def test_an_empty_position_store_yields_an_empty_population(tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    storage.init_db()
    ds = storage.create_dataset("empty", "", {})
    population = population_from_view(PointInTimeStore().view(AS_OF), ds)
    assert population.empty
    assert list(population.columns) == ["entity_key", "vessel_class"]


# =========================================================================
# the payoff: rarity becomes testable
# =========================================================================
def test_rarity_is_testable_once_positions_exist(fleet_db):
    ds, _fleet = fleet_db
    region = _region()
    events, population = entity_providers(
        ds, region, PointInTimeStore().view)

    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=events, population_provider=population)

    signal = status.signals[0]
    assert signal.verdict is Verdict.ACTIVE, signal.insufficient_reason
    assert "could not be tested" not in (signal.insufficient_reason or "")


def test_the_planted_vessel_is_the_one_flagged(fleet_db):
    ds, fleet = fleet_db
    region = _region()
    events, population = entity_providers(ds, region,
                                          PointInTimeStore().view)
    status = evaluate_region(
        region, lambda i, a: None, AS_OF, DetectionPowerCatalog(),
        event_provider=events, population_provider=population)

    evidence = " ".join(e.summary for e in status.signals[0].evidence)
    assert any(key in evidence for key in fleet.target_keys)
    assert "fish" not in evidence


# =========================================================================
# the mismatch this pairing prevents
# =========================================================================
def test_the_two_providers_share_one_window(fleet_db):
    """Numerator and denominator must be scoped alike.

    A 30-day numerator against an all-time denominator makes every behaviour
    look rare — a vessel seen three years ago and never since still counts as
    observed. That drags participation under the rarity threshold and produces
    false positives in the exact direction peer baselines exist to prevent.
    `entity_providers` takes one `window_days` and applies it to both.
    """
    ds, _fleet = fleet_db
    region = _region()
    view = PointInTimeStore().view
    indicator = _indicator()

    wide_events, wide_population = entity_providers(ds, region, view)
    narrow_events, narrow_population = entity_providers(
        ds, region, view, window_days=1)

    assert len(narrow_events(indicator, AS_OF)) < \
        len(wide_events(indicator, AS_OF)), "the window must bite on events"
    assert len(narrow_population(indicator, AS_OF)) < \
        len(wide_population(indicator, AS_OF)), (
            "and on the population too — a window that narrows only the "
            "numerator is the bug this pairing exists to prevent")


def test_the_population_is_grouped_as_the_indicator_declares(fleet_db):
    ds, _fleet = fleet_db
    region = _region()
    _events, population = entity_providers(ds, region,
                                           PointInTimeStore().view)
    frame = population(_indicator(test_config={
        "event_types": ["loiter"], "peer_baseline": ["vessel_class"]}), AS_OF)
    assert list(frame.columns) == ["entity_key", "vessel_class"]


def test_the_region_bounding_box_is_applied_to_the_population(fleet_db):
    """A denominator drawn from outside the region would understate
    participation for everything inside it."""
    ds, _fleet = fleet_db
    elsewhere = RegionModule(
        key=REGION_KEY, name="Elsewhere", status=MonitoringStatus.MONITORED,
        geography=GeoScope(lat_min=10.0, lat_max=20.0, lon_min=100.0,
                           lon_max=110.0),
        indicators=(_indicator(),), summary="far away")
    _events, population = entity_providers(ds, elsewhere,
                                           PointInTimeStore().view)
    assert population(_indicator(), AS_OF).empty


def test_a_worldwide_scope_declares_no_bounding_box():
    """None rather than the whole planet: filtering by it would be pure cost,
    and it distinguishes 'declared its extent' from 'nobody narrowed it'."""
    assert GeoScope().bbox is None
    assert GeoScope(lat_min=51.0, lat_max=56.0).bbox is not None
