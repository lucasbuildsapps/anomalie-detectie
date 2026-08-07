"""Scenario generation and the detection-power harness.

These tests check the *measuring instrument*. If the harness itself is wrong,
every number it produces about detectors is wrong too — and unlike a detector
bug, a harness bug is invisible, because it makes the results look fine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval import (
    SCENARIO_KINDS,
    ScenarioGenerator,
    false_alarm_rate,
    power_curve,
    run_scenario,
    run_suite,
)

# --- detectors used purely to exercise the harness ------------------------
def never(series: pd.Series) -> pd.Series:
    return pd.Series(False, index=series.index)


def always(series: pd.Series) -> pd.Series:
    return pd.Series(True, index=series.index)


def oracle(scenario) -> object:
    """A detector that flags exactly the injected truth. Upper bound."""
    def run(series: pd.Series) -> pd.Series:
        flags = pd.Series(False, index=series.index)
        for episode in scenario.truth:
            flags.loc[episode.start:episode.end] = True
        return flags
    return run


# --- generator ------------------------------------------------------------
def test_every_declared_scenario_can_be_built():
    gen = ScenarioGenerator()
    for kind in SCENARIO_KINDS:
        scenario = gen.build(kind)
        assert scenario.kind == kind
        assert len(scenario.series) > 0


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError, match="unknown scenario"):
        ScenarioGenerator().build("apocalypse")


def test_generation_is_deterministic():
    """A score change must mean a code change, never a different draw."""
    a = ScenarioGenerator(seed=7).sustained_increase()
    b = ScenarioGenerator(seed=7).sustained_increase()
    pd.testing.assert_series_equal(a.series, b.series)


def test_different_seeds_give_different_series():
    a = ScenarioGenerator(seed=1).baseline()
    b = ScenarioGenerator(seed=2).baseline()
    assert not a.equals(b)


def test_baseline_is_non_negative_integer_counts():
    """The band models assume counts; a Gaussian baseline would flatter them."""
    series = ScenarioGenerator().baseline()
    values = series.to_numpy()
    assert (values >= 0).all()
    assert np.allclose(values, np.round(values))


def test_negative_controls_inject_nothing():
    gen = ScenarioGenerator()
    for kind in ("noise", "missing_data", "source_change"):
        scenario = gen.build(kind)
        assert scenario.truth == []
        assert scenario.is_negative_control


def test_positive_scenarios_all_carry_truth():
    gen = ScenarioGenerator()
    for kind in SCENARIO_KINDS:
        scenario = gen.build(kind)
        if not scenario.is_negative_control:
            assert scenario.truth, f"{kind} must declare what was injected"


def test_spike_actually_raises_the_value():
    scenario = ScenarioGenerator().spike(magnitude=5.0)
    at = scenario.params["at"]
    baseline = ScenarioGenerator().baseline()
    assert scenario.series.iloc[at] > baseline.iloc[at] * 3


def test_gradual_escalation_ends_higher_than_it_starts():
    scenario = ScenarioGenerator().gradual_escalation(magnitude=4.0,
                                                      duration=60)
    onset = scenario.params["onset"]
    early = scenario.series.iloc[onset:onset + 10].mean()
    late = scenario.series.iloc[-10:].mean()
    assert late > early * 2


def test_gradual_escalation_has_no_single_anomalous_step():
    """The property that makes this scenario hard, and worth having.

    Gradual means no period stands out against its own neighbourhood — the
    trajectory carries the signal, not any single point. Compared against a
    local median rather than a global level, because the level is rising by
    design and a global comparison would flag the ramp's own success.
    """
    scenario = ScenarioGenerator().gradual_escalation(magnitude=3.0,
                                                      duration=60)
    onset = scenario.params["onset"]
    ramp = scenario.series.iloc[onset:]
    local = ramp.rolling(7, center=True, min_periods=3).median()
    ratio = (ramp / local.clip(lower=1.0)).to_numpy()

    spike = ScenarioGenerator().spike(magnitude=5.0)
    at = spike.params["at"]
    spike_local = spike.series.iloc[at - 3:at + 4].median()
    spike_ratio = spike.series.iloc[at] / max(spike_local, 1.0)

    assert ratio.max() < spike_ratio, (
        f"no period in the ramp (max ratio {ratio.max():.1f}) should stand "
        f"out as much as an injected spike ({spike_ratio:.1f})"
    )


def test_silence_is_real_zeros_and_missing_data_is_nan():
    """The distinction the whole gap policy rests on."""
    silence = ScenarioGenerator().silence()
    onset, duration = silence.params["onset"], silence.params["duration"]
    assert (silence.series.iloc[onset:onset + duration] == 0).all()
    assert silence.series.notna().all()

    gap = ScenarioGenerator().missing_data()
    onset, duration = gap.params["onset"], gap.params["duration"]
    assert gap.series.iloc[onset:onset + duration].isna().all()
    assert gap.unobserved is not None and gap.unobserved.sum() == duration


# --- harness --------------------------------------------------------------
def test_silent_detector_passes_controls_and_fails_events():
    scores = {s.scenario_kind: s for s in run_suite(never)}
    assert scores["noise"].passed
    assert scores["missing_data"].passed
    assert not scores["spike"].passed


def test_always_firing_detector_fails_every_control():
    """Perfect recall must not be mistaken for a working detector."""
    scores = {s.scenario_kind: s for s in run_suite(always)}
    assert scores["spike"].passed          # trivially
    assert not scores["noise"].passed      # but useless
    assert not scores["source_change"].passed


def test_oracle_detector_scores_perfectly_on_its_own_scenario():
    """Sanity bound: if the oracle cannot pass, the harness is broken."""
    for kind in SCENARIO_KINDS:
        scenario = ScenarioGenerator().build(kind)
        score = run_scenario(oracle(scenario), scenario)
        assert score.passed, f"oracle failed on {kind}"


def test_flags_on_unobserved_periods_are_ignored():
    """A detector cannot be right or wrong about what was never observed."""
    gap = ScenarioGenerator().missing_data()

    def only_in_the_gap(series: pd.Series) -> pd.Series:
        return gap.unobserved.copy()

    assert run_scenario(only_in_the_gap, gap).match.false_alarms == 0


def test_detector_returning_short_series_is_reindexed_not_crashed():
    def partial(series: pd.Series) -> pd.Series:
        return pd.Series(True, index=series.index[:5])

    score = run_scenario(partial, ScenarioGenerator().noise())
    assert score.match.false_alarms == 1


# --- power curve ----------------------------------------------------------
def test_power_curve_floor_is_nan_when_nothing_is_detected():
    curve = power_curve(never, n_repeats=2)
    assert all(r == 0.0 for r in curve.recalls)
    assert np.isnan(curve.floor)
    assert "not reliably detected" in curve.describe()


def test_always_firing_detector_is_reported_as_confounded_not_perfect():
    """The flaw this calibration exists to prevent.

    A detector that always fires has recall 1.0 at every magnitude. Without
    a chance baseline that reads as a perfect detection floor, which is the
    exact opposite of the truth.
    """
    def detect_everything(series: pd.Series) -> pd.Series:
        return pd.Series(True, index=series.index)

    curve = power_curve(detect_everything, magnitudes=(1.5, 2.0, 3.0),
                        n_repeats=2)
    assert all(r == 1.0 for r in curve.recalls)
    assert all(c == 1.0 for c in curve.chance_rates)
    assert all(n == 0.0 for n in curve.net_recalls)
    assert curve.is_confounded
    assert np.isnan(curve.floor)
    assert "too noisy" in curve.describe()


def test_selective_detector_gets_a_real_floor():
    """A detector needing a *sustained* excursion has low chance overlap.

    Note a single-period threshold does not qualify: at 1.6x the mean it
    trips on Poisson noise somewhere in a 30-period window most of the time,
    and the calibration correctly refuses it a floor. Requiring a 7-period
    rolling mean to clear the bar is what makes the detection attributable.
    """
    def sustained_detector(series: pd.Series) -> pd.Series:
        reference = series.iloc[:200].mean()
        smoothed = series.rolling(7, min_periods=7).mean()
        return (smoothed > reference * 1.4).fillna(False)

    curve = power_curve(sustained_detector, kind="sustained_increase",
                        magnitudes=(1.5, 2.0, 3.0), n_repeats=5)
    assert not curve.is_confounded, curve.chance_rates
    assert np.isfinite(curve.floor), (curve.recalls, curve.chance_rates)
    assert "reliably detected from" in curve.describe()


def test_power_curve_is_monotone_for_a_threshold_detector():
    """Bigger effects must not be harder to find — a basic sanity property."""
    def threshold_detector(series: pd.Series) -> pd.Series:
        cut = series.iloc[:200].mean() * 1.8
        return series > cut

    curve = power_curve(threshold_detector, kind="sustained_increase",
                        magnitudes=(1.1, 1.5, 2.0, 3.0, 5.0), n_repeats=5)
    assert curve.recalls == sorted(curve.recalls), curve.recalls
    assert curve.net_recalls == sorted(curve.net_recalls), curve.net_recalls


def test_power_curve_frame_round_trips():
    curve = power_curve(never, magnitudes=(2.0, 3.0), n_repeats=1)
    frame = curve.to_frame()
    assert list(frame.columns) == ["magnitude", "recall", "chance",
                                   "net_recall"]
    assert len(frame) == 2


# --- false alarm rate -----------------------------------------------------
def test_false_alarm_rate_is_zero_for_a_silent_detector():
    assert false_alarm_rate(never, n_repeats=3) == 0.0


def test_false_alarm_rate_is_positive_for_a_noisy_detector():
    assert false_alarm_rate(always, n_repeats=3) > 0
