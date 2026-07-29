"""Tests Fase 3: variantie-, persistentie-, change- en analogie-signalen."""
import numpy as np
import pandas as pd

from core.signals import (
    change_signal,
    collect_signals,
    persistence_signal,
    similar_period,
    variability_signal,
)


def _hist(actual, expected=None):
    n = len(actual)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "actual": [float(v) for v in actual],
        "expected": ([float(v) for v in expected] if expected is not None
                     else [float(np.mean(actual))] * n),
    })


def test_variability_detects_recent_chaos():
    rng = np.random.default_rng(1)
    calm = 10 + rng.normal(0, 0.5, 80)
    wild = 10 + rng.normal(0, 6.0, 15)
    sig = variability_signal(_hist(np.concatenate([calm, wild])))
    assert sig is not None and sig["richting"] == "grilliger"


def test_variability_none_on_stable():
    rng = np.random.default_rng(2)
    sig = variability_signal(_hist(10 + rng.normal(0, 1, 90)))
    assert sig is None


def test_persistence_detects_run_above():
    rng = np.random.default_rng(3)
    actual = list(10 + rng.normal(0, 1, 50)) + [14, 15, 13, 14, 16, 15, 14]
    sig = persistence_signal(_hist(actual, expected=[10.0] * 57))
    assert sig is not None
    assert sig["richting"] == "boven"
    assert sig["run"] >= 5
    assert sig["p"] < 0.05


def test_persistence_none_on_alternating():
    actual = [9, 11] * 30  # wisselt steeds van kant
    sig = persistence_signal(_hist(actual, expected=[10.0] * 60))
    assert sig is None


def test_change_signal_recent_shift():
    vals = [5.0] * 50 + [20.0] * 10  # verschuiving 10 periodes geleden
    sig = change_signal(_hist(vals), recent_periods=14)
    assert sig is not None
    assert sig["direction"] == "stijging"


def test_change_signal_old_shift_ignored():
    vals = [5.0] * 20 + [20.0] * 60  # verschuiving lang geleden
    sig = change_signal(_hist(vals), recent_periods=14)
    assert sig is None


def test_similar_period_finds_planted_match():
    """Plant hetzelfde patroon op twee plekken; het recente venster moet de
    eerdere kopie terugvinden."""
    rng = np.random.default_rng(4)
    pattern = 10 + np.array([0, 2, 5, 9, 12, 9, 5, 2, 0, -2] * 2, dtype=float)
    def noise(n):
        return rng.normal(0, 0.3, n)
    vals = np.concatenate([
        5 + noise(30), pattern + noise(20), 5 + noise(40),
        pattern + noise(20),
    ])
    sig = similar_period(_hist(vals), window=20)
    assert sig is not None
    assert sig["corr"] > 0.7
    # de match moet rond de eerste kopie liggen (start ~ dag 30)
    assert 20 <= (sig["start"] - pd.Timestamp("2025-01-01")).days <= 40


def test_collect_signals_never_crashes_on_short():
    out = collect_signals(_hist([1.0, 2.0, 3.0]))
    assert isinstance(out, list)
