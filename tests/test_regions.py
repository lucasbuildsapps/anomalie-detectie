"""Region modules: configuration, not applications — and honest empty tabs.

Two claims are under test. First, that a region is expressible as declaration
alone, so five tabs do not become five codebases. Second, and more important
as a product matter, that an unmonitored region can never be mistaken for a
quiet one.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.core.contracts import (
    DateRange,
    DetectionPower,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    MonitoringStatus,
    Verdict,
)
from sentinel.core.detect.power import (
    DetectionPowerCatalog,
    production_detector,
    scenario_for,
)
from sentinel.regions import (
    REGIONS,
    GeoScope,
    RegionModule,
    evaluate_region,
    get_region,
    watched_regions,
)

AS_OF = datetime(2023, 12, 5)
REFERENCE = DateRange(datetime(2019, 1, 1), datetime(2021, 12, 31))


def _series(escalate_from: str | None = None, n: int = 1800) -> pd.Series:
    rng = np.random.default_rng(7)
    index = pd.date_range("2019-01-01", periods=n, freq="D")
    level = np.full(n, 20.0)
    if escalate_from:
        level = np.where(index < pd.Timestamp(escalate_from), 20.0, 60.0)
    seasonal = 1.0 + 0.25 * np.sin(2 * np.pi * np.arange(n) / 7)
    return pd.Series(rng.poisson(level * seasonal), index=index, dtype=float)


def _catalog() -> DetectionPowerCatalog:
    return DetectionPowerCatalog(entries={
        '{"config": {"aggregation": "daily"}, "test": "sustained_divergence"}':
            DetectionPower("adaptation_failure", 1.5),
        '{"config": {"aggregation": "daily", "threshold": 3.5}, '
        '"test": "level_deviation"}': DetectionPower("spike", 2.0),
    })


def _provider(series: pd.Series):
    return lambda indicator, as_of: series


# =========================================================================
# the registry
# =========================================================================
def test_all_five_regions_are_registered():
    assert {r.key for r in REGIONS} == {
        "euro_atlantic", "nld_eez", "mena", "indo_pacific", "caribbean"}


def test_watched_regions_come_first_in_display_order():
    keys = [r.key for r in REGIONS]
    watched = [r.key for r in REGIONS if r.is_watched]
    assert keys[:len(watched)] == watched


def test_unknown_region_fails_loudly():
    """A silent default would attach one region's verdicts to another."""
    with pytest.raises(KeyError, match="unknown region"):
        get_region("atlantis")


def test_only_euro_atlantic_is_currently_watched():
    assert [r.key for r in watched_regions()] == ["euro_atlantic"]


# =========================================================================
# the honest empty tab
# =========================================================================
def test_unmonitored_region_must_declare_activation_requirements():
    with pytest.raises(ValueError, match="reads as 'quiet'"):
        RegionModule(key="x", name="X",
                     status=MonitoringStatus.NOT_MONITORED)


def test_unmonitored_region_cannot_also_carry_indicators():
    indicator = Indicator(
        key="i", region_key="x", name="I", question="q?", meaning="m",
        test_type=IndicatorTest.LEVEL_DEVIATION)
    with pytest.raises(ValueError, match="not monitored but carries"):
        RegionModule(key="x", name="X",
                     status=MonitoringStatus.NOT_MONITORED,
                     activation_requirements=("something",),
                     indicators=(indicator,))


def test_monitored_region_must_actually_define_indicators():
    with pytest.raises(ValueError, match="defines no indicators"):
        RegionModule(key="x", name="X", status=MonitoringStatus.MONITORED)


def test_indicators_must_belong_to_their_region():
    stray = Indicator(
        key="i", region_key="elsewhere", name="I", question="q?", meaning="m",
        test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.ACTIVE)
    with pytest.raises(ValueError, match="belongs to region"):
        RegionModule(key="x", name="X", status=MonitoringStatus.MONITORED,
                     indicators=(stray,))


@pytest.mark.parametrize("key", ["mena", "indo_pacific", "caribbean"])
def test_unmonitored_regions_never_read_as_quiet(key):
    """The product risk this whole design guards against."""
    region = get_region(key)
    status = evaluate_region(region, _provider(_series()), AS_OF)
    headline = status.headline()
    assert "not monitored" in headline
    assert not status.is_quiet
    assert not status.signals, "an unwatched region must not produce verdicts"
    assert any(req in headline for req in region.activation_requirements)


def test_data_only_region_says_nothing_is_being_tested():
    status = evaluate_region(get_region("nld_eez"), _provider(_series()),
                             AS_OF)
    assert "no indicators defined" in status.headline()
    assert not status.is_quiet


def test_nld_eez_is_data_only_because_the_entity_engine_is_missing():
    """Marking it monitored would let a tab that cannot see the interesting
    cases present itself as watching for them."""
    region = get_region("nld_eez")
    assert region.status is MonitoringStatus.DATA_ONLY
    entity_indicators = [i for i in region.indicators
                         if i.test_type is IndicatorTest.ENTITY_BEHAVIOUR]
    assert entity_indicators
    assert all(i.status is IndicatorStatus.DRAFT for i in entity_indicators)


# =========================================================================
# evaluation
# =========================================================================
def test_quiet_region_produces_null_results_with_floors():
    status = evaluate_region(get_region("euro_atlantic"),
                             _provider(_series()), AS_OF, _catalog())
    assert status.is_quiet
    assert "tested and quiet" in status.headline()
    for signal in status.signals:
        assert signal.verdict is Verdict.NOT_ACTIVE
        assert signal.detection_power is not None


def test_escalation_after_the_reference_window_is_reported():
    status = evaluate_region(get_region("euro_atlantic"),
                             _provider(_series(escalate_from="2022-03-01")),
                             AS_OF, _catalog())
    assert not status.is_quiet
    active = {s.indicator_key for s in status.active}
    assert "strike_tempo_sustained" in active


def test_data_entirely_inside_the_reference_window_is_not_judged():
    """Scoring the data that defined normal against itself is circular.

    A short series wholly inside the declared reference has nothing to be
    compared against, and the correct answer is silence rather than a
    confident verdict drawn from the reference's own inputs.
    """
    inside = _series(escalate_from="2019-06-01", n=300)  # all within 2019-2021
    status = evaluate_region(get_region("euro_atlantic"), _provider(inside),
                             AS_OF, _catalog())
    sustained = next(s for s in status.signals
                     if s.indicator_key == "strike_tempo_sustained")
    assert sustained.verdict is not Verdict.ACTIVE


def test_missing_data_gives_insufficient_not_quiet():
    status = evaluate_region(get_region("euro_atlantic"),
                             lambda i, a: None, AS_OF, _catalog())
    assert not status.is_quiet
    assert len(status.unavailable) == len(status.signals)
    assert all("no data available" in s.insufficient_reason
               for s in status.signals)


def test_without_a_power_catalogue_nothing_is_reported_as_quiet():
    """A null result the system cannot back up must not be issued."""
    status = evaluate_region(get_region("euro_atlantic"),
                             _provider(_series()), AS_OF)
    statistical = [s for s in status.signals
                   if s.indicator_key != "reporting_silence"]
    assert all(s.verdict is Verdict.INSUFFICIENT_DATA for s in statistical)
    assert all("detection power has not been measured" in s.insufficient_reason
               for s in statistical)


def test_confidence_inputs_are_derived_from_the_series():
    status = evaluate_region(get_region("euro_atlantic"),
                             _provider(_series()), AS_OF, _catalog())
    inputs = status.signals[0].confidence.inputs
    assert inputs.data_coverage == pytest.approx(1.0)
    assert inputs.staleness_days is not None


# =========================================================================
# geography
# =========================================================================
def test_geoscope_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="lat_min above"):
        GeoScope(lat_min=10.0, lat_max=5.0)


def test_geoscope_containment():
    scope = get_region("nld_eez").geography
    assert scope.contains(53.0, 4.0)      # North Sea
    assert not scope.contains(53.0, 30.0)  # far east of the EEZ


# =========================================================================
# detection power catalogue
# =========================================================================
def test_scenario_mapping_measures_each_test_on_its_real_job():
    """Sustained divergence is measured on the case it exists for."""
    assert scenario_for(IndicatorTest.SUSTAINED_DIVERGENCE) == "adaptation_failure"
    assert scenario_for(IndicatorTest.LEVEL_DEVIATION) == "spike"
    assert scenario_for(IndicatorTest.CONDITION) is None


def test_conditions_get_deterministic_power_without_measurement():
    indicator = Indicator(
        key="s", region_key="euro_atlantic", name="S", question="q?",
        meaning="m", test_type=IndicatorTest.CONDITION,
        test_config={"rule": "silence"}, status=IndicatorStatus.ACTIVE)
    power = DetectionPowerCatalog().power_for(indicator)
    assert power.is_deterministic
    assert "fixed rule" in power.describe()
    assert "0x or larger" not in power.describe()


def test_unmeasured_configuration_returns_none():
    indicator = Indicator(
        key="t", region_key="euro_atlantic", name="T", question="q?",
        meaning="m", test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.ACTIVE)
    assert DetectionPowerCatalog().power_for(indicator) is None


def test_catalogue_keys_on_configuration_not_identity():
    """Two indicators configured alike detect alike; one measurement serves
    both, and separate entries would multiply cost for no information."""
    common = dict(region_key="euro_atlantic", question="q?", meaning="m",
                  test_type=IndicatorTest.LEVEL_DEVIATION,
                  test_config={"threshold": 3.5},
                  status=IndicatorStatus.ACTIVE)
    a = Indicator(key="a", name="A", **common)
    b = Indicator(key="b", name="B", **common)
    catalog = DetectionPowerCatalog(entries={})
    catalog.entries['{"config": {"threshold": 3.5}, "test": "level_deviation"}'] = \
        DetectionPower("spike", 2.0)
    assert catalog.power_for(a) is catalog.power_for(b)


def test_catalogue_round_trips_through_json(tmp_path):
    catalog = _catalog()
    path = tmp_path / "power.json"
    catalog.save(path)
    restored = DetectionPowerCatalog.load(path)
    assert restored.entries.keys() == catalog.entries.keys()
    assert all(restored.entries[k].floor_magnitude
               == catalog.entries[k].floor_magnitude
               for k in catalog.entries)


def test_loading_a_missing_catalogue_is_empty_not_an_error(tmp_path):
    assert DetectionPowerCatalog.load(tmp_path / "absent.json").entries == {}


def test_round_trip_preserves_the_unit_and_the_caveat(tmp_path):
    """The caveat is the part that must survive persistence.

    A floor whose qualifying condition was dropped in serialisation is worse
    than no floor: it reads as unconditional coverage.
    """
    catalog = DetectionPowerCatalog(entries={
        "k": DetectionPower("Loitering", 1.0, unit="hours",
                            caveat="Only while rarer than 9% of the class."),
    })
    path = tmp_path / "power.json"
    catalog.save(path)
    restored = DetectionPowerCatalog.load(path).entries["k"]
    assert restored.unit == "hours"
    assert restored.caveat == "Only while rarer than 9% of the class."
    assert "1 hours" in restored.describe()
    assert "9%" in restored.describe()


# =========================================================================
# entity behaviour: measured for what the fleet harness injects, and only
# for that
# =========================================================================
def _entity_indicator(event_types, **overrides) -> Indicator:
    return Indicator(
        key="e", region_key="nld_eez", name="E", question="q?", meaning="m",
        test_type=IndicatorTest.ENTITY_BEHAVIOUR, entity_kind="vessel",
        status=IndicatorStatus.ACTIVE,
        test_config={"event_types": list(event_types), **overrides})


def test_entity_power_comes_from_the_committed_catalogue():
    """The loiter indicator can support a null result without re-measuring."""
    catalog = DetectionPowerCatalog.load("config/detection_power.json")
    power = catalog.power_for(_entity_indicator(
        ["loiter", "proximity_critical_infra"],
        peer_baseline=["vessel_class", "area", "month"],
        min_duration_minutes=60))
    assert power is not None
    assert power.unit == "hours"
    assert power.caveat and "rarer than" in power.caveat


def test_a_gap_indicator_gets_the_gap_floor_not_the_loiter_one():
    """Each behaviour is measured against its own rule. Lending one floor to
    another behaviour would let a null result claim coverage nobody
    measured — the failure this dispatch exists to prevent."""
    catalog = DetectionPowerCatalog(measure_missing=True, n_repeats=3)
    gap = catalog.measure(_entity_indicator(["ais_gap"]))
    assert gap is not None
    assert gap.unit == "minutes"
    assert "Loitering" not in gap.scenario_kind


def test_a_behaviour_the_harness_does_not_inject_gets_no_floor():
    """The harness injects loitering, dark periods and spoofed identifiers.
    Anything else is unmeasured, and declining is the only honest answer —
    lending it a floor from another behaviour would let a null result claim
    coverage nobody measured."""
    catalog = DetectionPowerCatalog(measure_missing=True, n_repeats=3)
    assert catalog.measure(_entity_indicator(["route_deviation"])) is None
    assert catalog.measure(
        _entity_indicator(["proximity_critical_infra"])) is None
    assert catalog.entries == {}


def test_catalogue_does_not_measure_on_demand_by_default():
    """A live evaluation must not block for minutes on a measurement."""
    assert DetectionPowerCatalog().measure_missing is False


# =========================================================================
# the measured detector must be the shipped detector
# =========================================================================
def test_production_detector_exists_for_measurable_tests():
    """v1 scored five raw detectors while shipping a tuned ensemble over a
    different aggregation, so its numbers described a system nobody used."""
    for test_type in (IndicatorTest.LEVEL_DEVIATION,
                      IndicatorTest.SUSTAINED_DIVERGENCE):
        indicator = Indicator(
            key="a", region_key="euro_atlantic", name="A", question="q?",
            meaning="m", test_type=test_type,
            reference_period=REFERENCE if
            test_type is IndicatorTest.SUSTAINED_DIVERGENCE else None,
            status=IndicatorStatus.ACTIVE)
        detector = production_detector(indicator)
        flags = detector(_series())
        assert flags.dtype == bool
        assert len(flags) == len(_series())


def test_production_detector_honours_the_indicator_threshold():
    """The measurement must move when the shipped configuration moves."""
    common = dict(region_key="euro_atlantic", question="q?", meaning="m",
                  test_type=IndicatorTest.LEVEL_DEVIATION,
                  status=IndicatorStatus.ACTIVE)
    series = _series()
    series.iloc[-1] = series.iloc[-1] * 2.0
    strict = production_detector(Indicator(key="s", name="S",
                                           test_config={"threshold": 20.0},
                                           **common))
    loose = production_detector(Indicator(key="l", name="L",
                                          test_config={"threshold": 0.5},
                                          **common))
    assert strict(series).sum() < loose(series).sum()


def test_production_detector_refuses_unmeasurable_tests():
    indicator = Indicator(
        key="e", region_key="nld_eez", name="E", question="q?", meaning="m",
        test_type=IndicatorTest.ENTITY_BEHAVIOUR, entity_kind="vessel",
        status=IndicatorStatus.ACTIVE)
    with pytest.raises(ValueError, match="cannot be measured"):
        production_detector(indicator)


def test_production_detector_excludes_warmup_periods():
    """Warm-up is untested, not quiet; counting it would skew the floor."""
    indicator = Indicator(
        key="a", region_key="euro_atlantic", name="A", question="q?",
        meaning="m", test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.ACTIVE)
    flags = production_detector(indicator)(_series())
    assert not flags.iloc[:21].any()


def test_committed_catalogue_covers_every_active_measurable_indicator():
    """A shipped indicator without measured power cannot report a null result.

    This is the check that keeps the committed catalogue in step with the
    region definitions: change a threshold and this fails until
    scripts/precompute_power.py is re-run.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "config" / \
        "detection_power.json"
    catalog = DetectionPowerCatalog.load(path)
    missing = [
        indicator.key
        for region in REGIONS
        for indicator in region.active_indicators
        if indicator.test_type in (IndicatorTest.LEVEL_DEVIATION,
                                   IndicatorTest.SUSTAINED_DIVERGENCE)
        and catalog.power_for(indicator) is None
    ]
    assert not missing, (
        f"no measured detection power for {missing}; "
        f"run scripts/precompute_power.py"
    )


def test_committed_catalogue_produces_real_null_results():
    """End to end: the committed numbers reach the analyst-facing sentence."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "config" / \
        "detection_power.json"
    status = evaluate_region(get_region("euro_atlantic"),
                             _provider(_series()), AS_OF,
                             DetectionPowerCatalog.load(path))
    assert status.is_quiet
    sustained = next(s for s in status.signals
                     if s.indicator_key == "strike_tempo_sustained")
    assert "1.5x or larger" in sustained.null_statement()
