"""Causal baselines: the recursion, and the property that makes it causal.

The single most important test in this file is
`test_future_observations_cannot_change_past_predictions`. Everything else
about v2's evaluation rests on it: if a baseline peeks forward, every
detection-power number is inflated by an unknown amount and no null result
means anything.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.core.baseline import (
    BaselineConfig,
    fit_adaptive,
    fit_reference,
    phases_for,
)
from sentinel.core.contracts import BaselineKind, DateRange

START = "2024-01-01"


def _series(values) -> pd.Series:
    values = np.asarray(values, dtype=float)
    return pd.Series(values,
                     index=pd.date_range(START, periods=len(values), freq="D"))


def _quiet(n: int = 200, level: float = 20.0, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    phase = np.arange(n)
    seasonal = 1.0 + 0.25 * np.sin(2 * np.pi * phase / 7)
    return _series(rng.poisson(level * seasonal))


# =========================================================================
# causality — the load-bearing property
# =========================================================================
def test_future_observations_cannot_change_past_predictions():
    """Rewrite the future; the past must not move.

    This is what "causal" means operationally, and it is checkable from
    outside the implementation. v1 failed exactly here — centred smoothing
    and hindsight segmentation both let later data reshape earlier
    expectations.
    """
    original = _quiet(200, seed=3)
    cut = 120

    tampered = original.copy()
    tampered.iloc[cut:] = 9999.0  # an absurd future

    a = fit_adaptive(original)
    b = fit_adaptive(tampered)

    pd.testing.assert_series_equal(a.expected.iloc[:cut], b.expected.iloc[:cut])
    pd.testing.assert_series_equal(a.scale.iloc[:cut], b.scale.iloc[:cut])


def test_prediction_at_t_ignores_the_observation_at_t():
    """Not merely 'no future' — the current value must not inform its own
    expectation either, or a spike dampens its own detection."""
    base = _quiet(120, seed=5)
    spiked = base.copy()
    spiked.iloc[100] = base.iloc[100] * 50

    a = fit_adaptive(base)
    b = fit_adaptive(spiked)

    assert a.expected.iloc[100] == pytest.approx(b.expected.iloc[100])


def test_a_spike_barely_moves_the_level_afterwards():
    """Robust clipping: one outlier must not drag the baseline after it."""
    base = _quiet(200, seed=7)
    spiked = base.copy()
    spiked.iloc[100] = base.iloc[100] * 50

    a = fit_adaptive(base)
    b = fit_adaptive(spiked)
    drift = abs(b.expected.iloc[110] - a.expected.iloc[110])
    assert drift < 0.3 * float(base.median()), (
        f"a single spike shifted the baseline by {drift:.1f}"
    )


# =========================================================================
# adaptive behaviour
# =========================================================================
def test_adaptive_tracks_a_genuine_regime_change_eventually():
    """Damping slows adaptation; it must not prevent it.

    A baseline that never adapts is a different kind of wrong — it would
    report a permanent alert on a world that genuinely changed.
    """
    values = np.concatenate([
        np.full(150, 20.0), np.full(200, 60.0),
    ])
    rng = np.random.default_rng(11)
    series = _series(rng.poisson(values))

    fit = fit_adaptive(series)
    assert fit.expected.iloc[160] < 45, "should not jump instantly"
    assert fit.expected.iloc[-1] > 45, "should have learned the new level"


def test_damping_slows_absorption_of_a_sustained_rise():
    """The mechanism that keeps a developing threat from becoming normal."""
    rng = np.random.default_rng(13)
    values = np.concatenate([np.full(150, 20.0), np.full(120, 60.0)])
    series = _series(rng.poisson(values))

    damped = fit_adaptive(series, BaselineConfig(adaptation_damping=True))
    undamped = fit_adaptive(series, BaselineConfig(adaptation_damping=False))

    # Midway through the rise the damped baseline must still be lower, i.e.
    # still treating the elevated level as a departure rather than the norm.
    assert damped.expected.iloc[200] < undamped.expected.iloc[200]
    assert (damped.damping.iloc[150:250] < 1.0).any()


def test_missing_values_do_not_update_state():
    """A collection gap is not evidence that the level changed."""
    series = _quiet(150, seed=17)
    with_gap = series.copy()
    with_gap.iloc[100:120] = np.nan

    a = fit_adaptive(series)
    b = fit_adaptive(with_gap)

    # The level and seasonal offsets are frozen across the gap, so the
    # prediction repeats exactly one seasonal cycle later. (It is not flat:
    # the weekly shape still applies — freezing the state is not the same as
    # forgetting the day of the week.)
    across = b.expected.iloc[100:120].to_numpy()
    assert np.allclose(across[:13], across[7:20], atol=1e-9)

    # And entering the gap, nothing has changed relative to the ungapped run.
    assert abs(b.expected.iloc[100] - a.expected.iloc[100]) < 1e-9


def test_short_series_is_marked_unusable_rather_than_guessed_at():
    fit = fit_adaptive(_quiet(10, seed=19))
    assert not fit.is_usable.any()
    assert fit.params.get("reason") == "insufficient history"


def test_warmup_periods_are_not_usable():
    config = BaselineConfig(min_history=21)
    fit = fit_adaptive(_quiet(120, seed=23), config)
    assert not fit.is_usable.iloc[:21].any()
    assert fit.is_usable.iloc[21:].all()


# =========================================================================
# count-aware deviation
# =========================================================================
def test_drops_are_scored_on_a_count_scale_not_a_gaussian_one():
    """The asymmetry a plain z-score gets wrong.

    Counts are bounded below by zero, so the left tail compresses and a
    near-total collapse in activity reads as a mild z. Under Poisson it is
    extreme, and the deviance residual says so.
    """
    series = _quiet(200, seed=29)
    series.iloc[150] = 1.0

    fit = fit_adaptive(series)
    deviation = fit.deviation(series)
    gaussian_z = (series - fit.expected) / fit.scale

    assert deviation.iloc[150] < gaussian_z.iloc[150], (
        "count-aware residual should judge the drop more extreme"
    )
    assert deviation.iloc[150] < -3.0


def test_continuous_data_keeps_the_ordinary_z_score():
    rng = np.random.default_rng(31)
    series = _series(rng.normal(0.0, 1.0, 200))  # negative values -> not counts
    fit = fit_adaptive(series)
    expected = (series - fit.expected) / fit.scale.replace(0, np.nan)
    pd.testing.assert_series_equal(fit.deviation(series), expected)


def test_quiet_series_deviations_are_roughly_calibrated():
    """Most periods must sit well inside the band, or thresholds mean nothing."""
    series = _quiet(400, seed=37)
    fit = fit_adaptive(series)
    deviation = fit.deviation(series)[fit.is_usable].dropna()
    assert (deviation.abs() > 3.5).mean() < 0.02


# =========================================================================
# reference baseline
# =========================================================================
def test_reference_baseline_does_not_move():
    rng = np.random.default_rng(41)
    values = np.concatenate([np.full(150, 20.0), np.full(150, 80.0)])
    series = _series(rng.poisson(values))
    window = DateRange(datetime(2024, 1, 1), datetime(2024, 4, 1))

    fit = fit_reference(series, window)
    expected = fit.expected.to_numpy()
    # Only the weekly shape varies; the level is frozen.
    assert expected[-7:].mean() == pytest.approx(expected[100:107].mean(),
                                                 rel=0.02)


def test_reference_baseline_keeps_flagging_a_sustained_rise():
    """What the adaptive baseline cannot do, by construction."""
    rng = np.random.default_rng(43)
    values = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    series = _series(rng.poisson(values))
    window = DateRange(datetime(2024, 1, 1), datetime(2024, 4, 1))

    reference = fit_reference(series, window)
    adaptive = fit_adaptive(series)

    assert reference.deviation(series).iloc[-30:].mean() > 3.0
    assert adaptive.deviation(series).iloc[-30:].abs().mean() < 2.0, (
        "the adaptive baseline is expected to have normalised the rise"
    )


def test_reference_excludes_its_own_fitting_window():
    """Scoring the data that defined normal against itself is circular."""
    series = _quiet(200, seed=47)
    window = DateRange(datetime(2024, 1, 1), datetime(2024, 3, 1))
    fit = fit_reference(series, window)
    assert not fit.is_usable.iloc[:55].any()
    assert fit.is_usable.iloc[100:].all()


def test_reference_refuses_a_window_with_too_little_data():
    series = _quiet(200, seed=53)
    tiny = DateRange(datetime(2024, 1, 1), datetime(2024, 1, 5))
    with pytest.raises(ValueError, match="at least"):
        fit_reference(series, tiny)


def test_phase_alignment_is_timestamp_based_not_positional():
    """Two windows starting on different weekdays must agree on phase.

    Positional phase silently rotates the weekly pattern between windows,
    producing a baseline that looks plausible and is simply shifted.
    """
    full = pd.date_range("2024-01-01", periods=60, freq="D")
    epoch = full[0]
    later = full[10:]
    assert list(phases_for(later, 7, epoch)) == list(
        np.arange(10, 60) % 7)


# =========================================================================
# contract conformance
# =========================================================================
def test_fit_converts_to_a_contract_baseline():
    series = _quiet(200, seed=59)
    fit = fit_adaptive(series)
    contract = fit.to_contract(as_of=datetime(2024, 7, 1), coverage_oos=0.97)
    assert contract.kind is BaselineKind.ADAPTIVE
    assert contract.n_periods == int(fit.is_usable.sum())
    assert contract.coverage_oos == 0.97
    assert not contract.is_declared


def test_reference_fit_converts_to_a_declared_contract_baseline():
    series = _quiet(200, seed=61)
    window = DateRange(datetime(2024, 1, 1), datetime(2024, 3, 1))
    contract = fit_reference(series, window).to_contract(
        as_of=datetime(2024, 7, 1))
    assert contract.kind is BaselineKind.FIXED_REFERENCE
    assert contract.is_declared
    assert contract.reference_period == window


# =========================================================================
# config validation
# =========================================================================
def test_config_rejects_nonsense():
    with pytest.raises(ValueError, match="half_life"):
        BaselineConfig(half_life=0)
    with pytest.raises(ValueError, match="min_history"):
        BaselineConfig(min_history=2)
    with pytest.raises(ValueError, match="seasonal_period"):
        BaselineConfig(seasonal_period=1)
