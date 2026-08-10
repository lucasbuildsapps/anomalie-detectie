"""Divergence between the two baselines, and the state v1 could not express.

The centrepiece is `test_normalised_escalation_is_detected_and_named`: a
sustained rise that the adaptive baseline has absorbed must still be
reportable, because "we stopped noticing" is not the same as "it stopped".
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.core.baseline import (
    DivergenceConfig,
    DivergenceState,
    assess_divergence,
    cusum,
    divergence_detector,
    fit_adaptive,
    fit_reference,
)
from sentinel.core.contracts import DateRange

START = "2024-01-01"
REFERENCE = DateRange(datetime(2024, 1, 1), datetime(2024, 5, 1))


def _series(values) -> pd.Series:
    values = np.asarray(values, dtype=float)
    return pd.Series(values,
                     index=pd.date_range(START, periods=len(values), freq="D"))


def _build(values, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    values = np.asarray(values, dtype=float)
    phase = np.arange(len(values))
    seasonal = 1.0 + 0.25 * np.sin(2 * np.pi * phase / 7)
    return _series(rng.poisson(values * seasonal))


def _assess(series: pd.Series, config: DivergenceConfig | None = None):
    adaptive = fit_adaptive(series)
    reference = fit_reference(series, REFERENCE)
    return assess_divergence(series, adaptive, reference, config)


# =========================================================================
# CUSUM
# =========================================================================
def test_cusum_accumulates_persistent_one_sided_deviation():
    result, _ = cusum(pd.Series([1.0] * 20), slack=0.5)
    assert result.iloc[-1] == pytest.approx(10.0)
    assert result.is_monotonic_increasing


def test_cusum_ignores_deviation_within_slack():
    upper, lower = cusum(pd.Series([0.4] * 30), slack=0.5)
    assert upper.iloc[-1] == 0.0
    assert lower.iloc[-1] == 0.0


def test_cusum_resets_when_evidence_turns():
    values = pd.Series([2.0] * 10 + [-2.0] * 10)
    upper, _ = cusum(values, slack=0.5)
    assert upper.iloc[9] > 10.0
    assert upper.iloc[-1] == 0.0


def test_cusum_is_causal():
    base = pd.Series([1.0] * 30)
    tampered = base.copy()
    tampered.iloc[15:] = 99.0
    a, _ = cusum(base)
    b, _ = cusum(tampered)
    pd.testing.assert_series_equal(a.iloc[:15], b.iloc[:15])


def test_cusum_treats_gaps_as_no_evidence():
    """A missing period must not dilute an accumulation, nor add to it."""
    values = pd.Series([1.0] * 10 + [np.nan] * 5 + [1.0] * 10)
    upper, _ = cusum(values, slack=0.5)
    assert upper.iloc[14] == pytest.approx(upper.iloc[9])
    assert upper.iloc[-1] > upper.iloc[9]


def test_lower_arm_catches_a_sustained_fall():
    _, lower = cusum(pd.Series([-1.0] * 20), slack=0.5)
    assert lower.iloc[-1] == pytest.approx(10.0)


# =========================================================================
# the four states
# =========================================================================
def test_quiet_series_stays_quiet():
    series = _build(np.full(320, 20.0), seed=3)
    result = _assess(series)
    tested = result.state[result.state != DivergenceState.UNTESTED]
    quiet_fraction = (tested == DivergenceState.QUIET).mean()
    assert quiet_fraction > 0.98
    assert result.latest_state() is DivergenceState.QUIET


def test_isolated_spike_is_an_incident_not_an_escalation():
    values = np.full(320, 20.0)
    series = _build(values, seed=5)
    series.iloc[250] = 300.0
    result = _assess(series)
    assert result.state.iloc[250] == DivergenceState.INCIDENT
    assert not result.escalation_mask.any()


def test_normalised_escalation_is_detected_and_named():
    """The failure v1 could not express, now a first-class state.

    A long sustained rise: the adaptive baseline has adapted to it, so no
    per-period alert fires — and that silence is precisely what must be
    reported, not treated as calm.
    """
    values = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    series = _build(values, seed=7)
    result = _assess(series)

    tail = result.state.iloc[-30:]
    assert (tail == DivergenceState.NORMALISED_ESCALATION).any()
    assert result.latest_state() is DivergenceState.NORMALISED_ESCALATION

    # The adaptive baseline really has gone quiet — that is the premise.
    adaptive = fit_adaptive(series)
    assert adaptive.deviation(series).iloc[-30:].abs().mean() < 2.0

    summary = result.summary()
    assert "habituation" in summary
    assert "not a return to normal" in summary


def test_acute_state_when_a_spike_lands_on_a_raised_level():
    values = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    series = _build(values, seed=11)
    series.iloc[330] = 600.0
    result = _assess(series)
    assert result.state.iloc[330] == DivergenceState.ACUTE


def test_warmup_periods_report_untested_not_quiet():
    series = _build(np.full(320, 20.0), seed=13)
    result = _assess(series)
    assert (result.state.iloc[:10] == DivergenceState.UNTESTED).all()


# =========================================================================
# guard rails
# =========================================================================
def test_a_brief_excursion_is_not_called_sustained():
    """`min_sustained_periods` exists so a short bump is not an escalation."""
    values = np.full(320, 20.0)
    values[250:255] = 60.0
    series = _build(values, seed=17)
    result = _assess(series)
    assert not result.escalation_mask.any()


def test_without_a_reference_escalation_cannot_be_reported():
    """Honest degradation: no declared normal, no normalised-escalation state.

    This is why `Indicator` refuses to build a SUSTAINED_DIVERGENCE test
    without a reference period.
    """
    values = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    series = _build(values, seed=19)
    result = assess_divergence(series, fit_adaptive(series), reference=None)
    assert not result.escalation_mask.any()
    assert result.latest_state() in (DivergenceState.QUIET,
                                     DivergenceState.INCIDENT)


def test_state_labels_are_written_for_an_analyst():
    for state in DivergenceState:
        assert state.label
        assert state.label[0].islower()
    assert DivergenceState.QUIET.is_notable is False
    assert DivergenceState.UNTESTED.is_notable is False
    assert DivergenceState.NORMALISED_ESCALATION.is_notable is True


def test_config_rejects_nonsense():
    with pytest.raises(ValueError, match="cusum_slack"):
        DivergenceConfig(cusum_slack=-1)
    with pytest.raises(ValueError, match="cusum_threshold"):
        DivergenceConfig(cusum_threshold=0)


# =========================================================================
# harness adapter
# =========================================================================
def test_detector_returns_a_boolean_series_aligned_to_input():
    series = _build(np.full(320, 20.0), seed=23)
    flags = divergence_detector(reference_periods=180)(series)
    assert flags.dtype == bool
    assert flags.index.equals(series.index)


def test_detector_is_silent_on_a_series_too_short_to_judge():
    series = _build(np.full(40, 20.0), seed=29)
    assert not divergence_detector(reference_periods=180)(series).any()


def test_detector_declares_the_reference_from_the_start_of_the_series():
    """Taking a recent window would fold the current situation into the
    definition of normal — the exact failure the reference exists to avoid."""
    values = np.concatenate([np.full(180, 20.0), np.full(170, 60.0)])
    series = _build(values, seed=31)
    flags = divergence_detector(reference_periods=180)(series)
    assert flags.iloc[-30:].any(), (
        "a rise after the reference window must remain visible"
    )
