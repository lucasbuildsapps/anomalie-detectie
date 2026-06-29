"""Tests voor vergelijkings-analyse: reeks-opbouw, lag-detectie, change-points."""
import numpy as np
import pandas as pd

from core.comparison import (
    build_series, cross_correlation_lag, detect_change_points,
    seasonality_profile,
)


def _df(location, values, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({
        "timestamp": idx, "value": values,
        "location_name": location,
    })


def test_build_series_aggregates():
    df = _df("A", [1.0] * 30)
    s = build_series(df, "A", [], "daily")
    assert len(s) == 30
    assert s.sum() == 30


def test_build_series_category_filter():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=4, freq="D"),
        "value": [1.0, 2.0, 3.0, 4.0],
        "location_name": ["A"] * 4,
        "category": ["x", "y", "x", "y"],
    })
    s = build_series(df, "A", ["x"], "daily")
    assert s.sum() == 4.0  # alleen de 'x'-rijen (1 + 3)


def test_cross_correlation_detects_known_lag():
    """B is een met 5 dagen vertraagde kopie van A → lag moet ~5 zijn."""
    rng = np.random.default_rng(0)
    base = rng.normal(10, 3, 120).cumsum() % 50
    a_vals = base
    b_vals = np.concatenate([np.zeros(5), base[:-5]])  # 5 dagen vertraagd
    idx = pd.date_range("2025-01-01", periods=120, freq="D")
    sa = pd.Series(a_vals, index=idx)
    sb = pd.Series(b_vals, index=idx)
    lag = cross_correlation_lag(sa, sb, "daily")
    assert lag is not None
    assert abs(lag.best_lag - 5) <= 1
    assert lag.best_corr > 0.7


def test_cross_correlation_too_short_returns_none():
    idx = pd.date_range("2025-01-01", periods=5, freq="D")
    s = pd.Series([1.0, 2, 3, 4, 5], index=idx)
    assert cross_correlation_lag(s, s, "daily") is None


def test_detect_change_points_finds_level_shift():
    """Reeks die halverwege van niveau 2 naar niveau 20 springt."""
    vals = [2.0] * 30 + [20.0] * 30
    s = pd.Series(vals, index=pd.date_range("2025-01-01", periods=60, freq="D"))
    cps = detect_change_points(s)
    assert len(cps) >= 1
    assert cps[0]["direction"] == "stijging"
    # rond de overgang (dag 30)
    assert abs((cps[0]["date"] - pd.Timestamp("2025-01-31")).days) <= 6


def test_detect_change_points_flat_series_none():
    s = pd.Series([5.0] * 40,
                  index=pd.date_range("2025-01-01", periods=40, freq="D"))
    assert detect_change_points(s) == []


def test_seasonality_weekly_pattern():
    """Sterk weekend-effect → seizoensprofiel moet dat oppikken."""
    idx = pd.date_range("2025-01-01", periods=84, freq="D")
    vals = [10.0 if d.dayofweek >= 5 else 2.0 for d in idx]
    s = pd.Series(vals, index=idx)
    prof = seasonality_profile(s, "daily")
    assert prof is not None
    assert prof["peak"] in ("za", "zo")
