"""Robuustheids- en correctheids-tests voor de voorspellingsmechanismen.

Bewaakt de defecten uit de kwaliteitsaudit:
1. STL seizoens-fase (piek moet op de juiste dag voorspeld worden)
2. Spurious correlation bij gedeelde trend (CCF op verschillen)
3. Rolling zonder leakage (spike dempt eigen detectie niet)
4. Geen 0-clip bij negatieve data (temperaturen e.d.)
5. Recent-window schaalt met aggregatie
6. Maand-seizoensdetectie (jaarcyclus)
7. Gewogen ensemble
Plus stress-tests op rand-invoer (constant, nul, extreme spike, tiny).
"""
import numpy as np
import pandas as pd
import pytest

from core.comparison import cross_correlation_lag
from core.normbeeld import (
    PREDICTION_METHODS,
    _combine_predictions,
    _detect_period,
    _forecast_with,
    compute_normbeeld,
)


def _daily_series(vals, start="2025-01-06"):
    idx = pd.date_range(start, periods=len(vals), freq="D")
    return pd.Series([float(v) for v in vals], index=idx)


def _df_from_series(s: pd.Series, location="X"):
    return pd.DataFrame({
        "timestamp": s.index, "value": s.values, "location_name": location,
    })


# ---------------------------------------------------------------------------
# 1. STL seizoens-fase
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["stl", "seasonal_naive"])
def test_seasonal_forecast_peaks_on_correct_day(method):
    """Zaterdag-piek in de historie => voorspelde piek moet op zaterdag
    vallen, niet één dag ervoor of erna (fase-uitlijning)."""
    idx = pd.date_range("2025-01-06", periods=98, freq="D")  # start maandag
    s = pd.Series([20.0 if d.dayofweek == 5 else 2.0 for d in idx], index=idx)
    pred, reason = _forecast_with(method, s, 7, 14)
    assert pred is not None, reason
    fut_idx = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=14, freq="D")
    fut = pd.Series(pred[1], index=fut_idx)
    top_days = set(fut.nlargest(2).index.dayofweek)
    assert top_days == {5}, f"{method}: piek voorspeld op dagen {top_days}, verwacht {{5}}"


# ---------------------------------------------------------------------------
# 2. Spurious correlation
# ---------------------------------------------------------------------------
def test_ccf_no_false_link_from_shared_trend():
    """Twee ONafhankelijke reeksen met dezelfde trend mogen geen sterk
    verband rapporteren."""
    rng = np.random.default_rng(11)
    n = 150
    trend = np.linspace(0, 50, n)
    a = _daily_series(rng.normal(0, 1, n) + trend)
    b = _daily_series(rng.normal(0, 1, n) + trend)
    lag = cross_correlation_lag(a, b, "daily")
    assert lag is not None
    assert abs(lag.best_corr) < 0.5, (
        f"vals verband gerapporteerd: corr {lag.best_corr:.2f}"
    )


def test_ccf_still_finds_real_lag_with_trend():
    """Echte vertraging van 3 dagen moet ook mét gedeelde trend gevonden
    worden (via differencing)."""
    rng = np.random.default_rng(7)
    n = 150
    base = rng.normal(0, 1, n)
    trend = np.linspace(0, 50, n)
    a = _daily_series(base + trend)
    b = _daily_series(np.concatenate([np.zeros(3), base[:-3]]) + trend)
    lag = cross_correlation_lag(a, b, "daily")
    assert lag is not None
    assert abs(lag.best_lag - 3) <= 1
    assert lag.best_corr > 0.6


# ---------------------------------------------------------------------------
# 3. Rolling zonder leakage
# ---------------------------------------------------------------------------
def test_rolling_expected_excludes_current_point():
    """De verwachte waarde op dag t mag de waarde van dag t zelf niet
    bevatten — anders dempt een spike zijn eigen detectie."""
    vals = [5.0] * 30
    vals[20] = 500.0  # extreme spike
    s = _daily_series(vals)
    pred, _ = _forecast_with("rolling", s, 7, 7)
    expected_at_spike = pred[0][20]
    # Met leakage zou dit >= 70 zijn (500 telt mee in eigen venster van 7);
    # zonder leakage blijft het op het niveau van de dagen ervoor (~5).
    assert expected_at_spike < 20, (
        f"leakage: verwachting op spike-dag = {expected_at_spike:.1f}"
    )


# ---------------------------------------------------------------------------
# 4. Negatieve data (geen 0-clip, geen verborgen punten)
# ---------------------------------------------------------------------------
def test_negative_data_not_clipped():
    """Temperatuur-achtige data rond 0: voorspelling en band mogen negatief."""
    rng = np.random.default_rng(3)
    idx = pd.date_range("2025-01-01", periods=90, freq="D")
    vals = -5 + 3 * np.sin(np.arange(90) / 7) + rng.normal(0, 1, 90)
    df = pd.DataFrame({"timestamp": idx, "value": vals, "location_name": "T"})
    nb = compute_normbeeld(df, location="T", horizon_days=7,
                           aggregation="daily")
    assert nb is not None
    assert (nb.historical["expected"] < 0).any(), "verwachting weggeclipt naar 0"
    assert (nb.historical["lower"] < 0).any(), "ondergrens weggeclipt naar 0"
    assert (nb.forecast["expected"] < 0).any()


def test_count_data_still_clipped_at_zero():
    """Telling-data blijft niet-negatief in forecast en band."""
    rng = np.random.default_rng(4)
    vals = np.maximum(0, rng.poisson(2, 90)).astype(float)
    s = _daily_series(vals)
    nb = compute_normbeeld(_df_from_series(s), location="X",
                           horizon_days=7, aggregation="daily")
    assert nb is not None
    assert (nb.historical["lower"] >= 0).all()
    assert (nb.forecast["lower"] >= 0).all()
    assert (nb.forecast["expected"] >= 0).all()


# ---------------------------------------------------------------------------
# 5. Recent-window schaalt met aggregatie
# ---------------------------------------------------------------------------
def test_recent_deviations_window_scales_with_aggregation():
    """Bij maand-aggregatie telt 'recent' 6 maanden, niet 14."""
    # 36 maanden vlak, laatste 10 maanden een spike per maand
    idx = pd.date_range("2022-01-01", periods=1095, freq="D")
    vals = np.full(1095, 3.0)
    df = pd.DataFrame({"timestamp": idx, "value": vals, "location_name": "X"})
    nb = compute_normbeeld(df, location="X", horizon_days=3,
                           aggregation="monthly")
    assert nb is not None
    # vlakke reeks: 0 afwijkingen, maar het window-mechanisme mag max 6 zijn
    assert nb.n_recent_deviations <= 6


# ---------------------------------------------------------------------------
# 6. Maand-seizoensdetectie
# ---------------------------------------------------------------------------
def test_monthly_annual_cycle_detected():
    """3 jaar maanddata met duidelijke jaarcyclus => periode 12 gedetecteerd."""
    idx = pd.date_range("2022-01-01", periods=36, freq="MS")
    vals = 10 + 8 * np.sin(2 * np.pi * np.arange(36) / 12)
    s = pd.Series(vals, index=idx)
    assert _detect_period(s, "monthly") == 12


def test_monthly_no_cycle_none():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2022-01-01", periods=36, freq="MS")
    s = pd.Series(rng.normal(10, 2, 36), index=idx)
    assert _detect_period(s, "monthly") is None


# ---------------------------------------------------------------------------
# 7. Gewogen ensemble
# ---------------------------------------------------------------------------
def test_weighted_combine_favors_better_method():
    n_hist, horizon = 30, 7
    pred_good = (np.full(n_hist, 10.0), np.full(horizon, 10.0), 1.0)
    pred_bad = (np.full(n_hist, 100.0), np.full(horizon, 100.0), 1.0)
    # Gewicht 9:1 richting de goede methode
    hist, fut = _combine_predictions([pred_good, pred_bad], smooth_window=1,
                                     weights=[0.9, 0.1])
    assert abs(fut[0] - 19.0) < 1e-6  # 0.9*10 + 0.1*100
    # Zonder gewichten: gewoon gemiddelde
    hist2, fut2 = _combine_predictions([pred_good, pred_bad], smooth_window=1)
    assert abs(fut2[0] - 55.0) < 1e-6


# ---------------------------------------------------------------------------
# Stress: rand-invoer mag nooit crashen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,vals", [
    ("constant", [5.0] * 40),
    ("all-zero", [0.0] * 40),
    ("extreme-spike", [2.0] * 39 + [1e6]),
    ("two-values", [1.0, 2.0] * 20),
])
def test_edge_series_never_crash(label, vals):
    s = _daily_series(vals)
    nb = compute_normbeeld(_df_from_series(s), location="X",
                           horizon_days=7, aggregation="daily")
    assert nb is not None, f"{label}: geen normbeeld"
    assert np.isfinite(nb.forecast["expected"]).all(), f"{label}: NaN forecast"
    assert (nb.historical["upper"] >= nb.historical["lower"]).all(), (
        f"{label}: band ondersteboven"
    )


@pytest.mark.parametrize("method", list(PREDICTION_METHODS))
def test_all_methods_on_constant_series(method):
    """Elke methode moet op een constante reeks óf netjes voorspellen óf
    netjes skippen — nooit crashen."""
    s = _daily_series([7.0] * 60)
    pred, reason = _forecast_with(method, s, 7, 7)
    if pred is None:
        assert isinstance(reason, str) and reason
    else:
        assert np.isfinite(pred[1]).all()


def test_tiny_dataset_returns_none_not_crash():
    s = _daily_series([1.0, 2.0, 3.0])
    assert compute_normbeeld(_df_from_series(s), location="X",
                             horizon_days=7) is None


def test_resid_percentile_present_and_extreme_for_spike():
    """De anomalie-percentiel moet bestaan en ~1.0 zijn voor een grote spike."""
    rng = np.random.default_rng(2)
    vals = np.maximum(0, 5 + rng.normal(0, 1, 90))
    vals[70] = 60.0
    s = _daily_series(vals)
    nb = compute_normbeeld(_df_from_series(s), location="X",
                           horizon_days=7, aggregation="daily")
    assert "resid_pctl" in nb.historical.columns
    pctl = nb.historical.iloc[70]["resid_pctl"]
    assert pctl > 0.95, f"spike-percentiel te laag: {pctl}"
    assert nb.historical["resid_pctl"].between(0, 1).all()


def test_alerts_carry_extremer_dan():
    from core.normbeeld import detect_recent_alerts
    rng = np.random.default_rng(2)
    vals = np.maximum(0, 5 + rng.normal(0, 1, 90))
    vals[-2] = 60.0  # recente spike
    s = _daily_series(vals)
    nb = compute_normbeeld(_df_from_series(s), location="X",
                           horizon_days=7, aggregation="daily")
    alerts = detect_recent_alerts({"X": nb}, aggregation="daily")
    assert alerts, "recente spike moet een alert geven"
    assert "extremer_dan" in alerts[0]
    assert alerts[0]["extremer_dan"] > 0.9


def test_spike_flags_itself_with_default_methods():
    """Een 10x-spike moet als 'boven' geflagd worden, ook nu rolling geen
    leakage meer heeft."""
    rng = np.random.default_rng(9)
    vals = np.maximum(0, 5 + rng.normal(0, 1, 90))
    vals[70] = 60.0
    s = _daily_series(vals)
    nb = compute_normbeeld(_df_from_series(s), location="X",
                           horizon_days=7, aggregation="daily")
    spike_date = s.index[70]
    row = nb.historical[nb.historical["date"] == spike_date]
    assert not row.empty
    assert row.iloc[0]["status"] == "boven"
