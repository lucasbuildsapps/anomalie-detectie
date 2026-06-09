"""Tests voor het normbeeld: banden, forecast, backtest, methode-selectie."""
import numpy as np
import pandas as pd
import pytest

from core.normbeeld import (
    PREDICTION_METHODS, backtest_all_methods, compute_normbeeld,
    _forecast_with, _weighted_quantile,
)


def test_normbeeld_basics(synthetic_daily):
    nb = compute_normbeeld(synthetic_daily, location="BASIS-A",
                           horizon_days=14, aggregation="daily")
    assert nb is not None
    assert nb.upper_band > nb.lower_band
    assert nb.lower_band >= 0
    assert len(nb.forecast) == 14
    assert {"date", "actual", "expected", "lower", "upper", "status"} <= set(
        nb.historical.columns
    )


def test_band_lower_bound_not_degenerate(synthetic_daily):
    """De oude ±2σ-band hing de ondergrens op 0 voor elke drukke reeks.
    De quantile-band moet een zinvolle ondergrens geven (> 20% van het
    verwachte niveau bij een stabiele reeks rond 3-4/dag)."""
    nb = compute_normbeeld(synthetic_daily, location="BASIS-A",
                           horizon_days=7, aggregation="daily")
    assert nb.lower_band > 0.2 * nb.expected_value


def test_spikes_detected_as_boven(synthetic_daily):
    nb = compute_normbeeld(synthetic_daily, location="BASIS-A",
                           horizon_days=7, aggregation="daily")
    boven = nb.historical[nb.historical["status"] == "boven"]
    flagged_dates = {pd.Timestamp(d).date() for d in boven["date"]}
    assert pd.Timestamp("2025-03-02").date() in flagged_dates  # dag 60 spike
    assert pd.Timestamp("2025-04-01").date() in flagged_dates  # dag 90 spike


def test_flagged_fraction_reasonable(synthetic_daily):
    """Niet meer dan ~25% van de historie mag geflagd zijn — anders is de
    band te smal en is alles 'afwijkend' (alert-moeheid)."""
    nb = compute_normbeeld(synthetic_daily, location="BASIS-A",
                           horizon_days=7, aggregation="daily")
    frac = (nb.historical["status"] != "normaal").mean()
    assert frac < 0.25


@pytest.mark.parametrize("method", list(PREDICTION_METHODS))
def test_each_method_produces_forecast(synthetic_daily, method):
    series = (
        synthetic_daily.set_index("timestamp")["value"].resample("D").sum()
    )
    pred, reason = _forecast_with(method, series, period=7, horizon=14)
    assert pred is not None, f"{method} geskipt: {reason}"
    expected_hist, future, _ = pred
    assert len(expected_hist) == len(series)
    assert len(future) == 14
    assert np.all(np.isfinite(future))


def test_forecast_with_unknown_method():
    series = pd.Series(
        np.ones(30),
        index=pd.date_range("2025-01-01", periods=30, freq="D"),
    )
    pred, reason = _forecast_with("bestaat-niet", series, 7, 7)
    assert pred is None
    assert "onbekende methode" in reason


def test_backtest_returns_scores(synthetic_daily):
    series = (
        synthetic_daily.set_index("timestamp")["value"].resample("D").sum()
    )
    scores = backtest_all_methods(series, period=7, horizon=14)
    assert len(scores) >= 3
    for v in scores.values():
        assert np.isfinite(v) and v >= 0


def test_backtest_selection_picks_best_two(synthetic_daily):
    nb = compute_normbeeld(synthetic_daily, location="BASIS-A",
                           horizon_days=14, aggregation="daily",
                           select="backtest")
    assert nb.backtest_scores is not None
    assert len(nb.methods_used) <= 2
    best = min(nb.backtest_scores, key=nb.backtest_scores.get)
    assert best in nb.methods_used


def test_weighted_quantile_unweighted_matches_numpy():
    vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    w = np.ones(5)
    assert abs(_weighted_quantile(vals, 0.5, w) - 3.0) < 0.6


def test_weighted_quantile_recency():
    """Met al het gewicht op de laatste waarden moet de quantile daarheen."""
    vals = np.array([0.0] * 50 + [10.0] * 5)
    w = np.array([0.001] * 50 + [1.0] * 5)
    assert _weighted_quantile(vals, 0.5, w) > 5.0


def test_too_little_data_returns_none():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=2, freq="D"),
        "value": [1.0, 2.0],
    })
    assert compute_normbeeld(df, horizon_days=7) is None


def test_incomplete_trailing_month_dropped():
    """Maand-aggregatie: data die halverwege de maand stopt mag geen
    kunstmatig lage laatste bucket opleveren."""
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", "2025-06-10", freq="D"),
        "value": 10.0,
    })
    nb = compute_normbeeld(df, horizon_days=3, aggregation="monthly")
    assert nb is not None
    last_hist_date = pd.Timestamp(nb.historical["date"].max())
    # Juni (incompleet, stopt op de 10e) moet weggelaten zijn
    assert last_hist_date == pd.Timestamp("2025-05-01")
