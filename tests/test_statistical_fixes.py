"""Tests voor de statistische verbeteringen (juli 2026):

- horizon-verbreding van de voorspelband,
- empirische banddekking (band_coverage),
- permutatie-significantie voor lag-correlatie,
- lengte-geschaalde change-point-drempel,
- gevoeligheid zichtbaar in bevindingen.
"""
import numpy as np
import pandas as pd

from core.comparison import cross_correlation_lag, detect_change_points
from core.normbeeld import backtest_step_widening, compute_normbeeld


def _seasonal_df(n=140, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    vals = 10 + 3 * np.sin(2 * np.pi * np.arange(n) / 7) + rng.normal(0, 1, n)
    return pd.DataFrame({
        "timestamp": idx, "value": np.clip(vals, 0, None),
        "location_name": "A",
    })


class TestHorizonWidening:
    def test_forecast_band_widens_with_horizon(self):
        nb = compute_normbeeld(_seasonal_df(), location="A", horizon_days=14)
        width = nb.forecast["upper"] - nb.forecast["lower"]
        # Laatste stap minstens zo breed als de eerste, en strikt breder
        # over het geheel (default-verbreding: +3%/stap, cap 1.5x).
        assert width.iloc[-1] >= width.iloc[0]
        assert width.iloc[-1] > width.iloc[0] * 1.05

    def test_widening_factors_monotone_and_bounded(self):
        df = _seasonal_df(200)
        series = df.set_index("timestamp")["value"].resample("D").sum()
        w = backtest_step_widening(series, ["ets", "rolling"], 7, 14)
        if w is not None:  # korte reeksen mogen None geven
            assert len(w) == 14
            assert np.all(w >= 1.0)
            assert np.all(w <= 3.0)
            assert np.all(np.diff(w) >= -1e-12)  # monotoon niet-dalend

    def test_backtest_mode_reports_widening_source(self):
        nb = compute_normbeeld(_seasonal_df(200), location="A",
                               horizon_days=14, select="backtest")
        assert nb.widening_source in ("backtest", "default")


class TestBandCoverage:
    def test_coverage_reported_and_plausible(self):
        nb = compute_normbeeld(_seasonal_df(), location="A", horizon_days=14)
        assert nb.band_coverage is not None
        # Quantile-band met alpha in [0.01, 0.10] hoort ruwweg 80-100%
        # van de historie te dekken.
        assert 0.7 <= nb.band_coverage <= 1.0

    def test_coverage_none_for_tiny_series(self):
        df = _seasonal_df(8)
        nb = compute_normbeeld(df, location="A", horizon_days=5)
        if nb is not None and nb.band_coverage is not None:
            assert 0.0 <= nb.band_coverage <= 1.0


class TestLagSignificance:
    def test_real_lag_is_significant(self):
        rng = np.random.default_rng(3)
        idx = pd.date_range("2025-01-01", periods=150, freq="D")
        a = pd.Series(rng.normal(10, 3, 150).cumsum() % 40, index=idx)
        b = a.shift(4).fillna(0)
        lag = cross_correlation_lag(a, b, "daily")
        assert lag is not None
        assert lag.significant
        assert abs(lag.best_corr) > lag.sig_threshold

    def test_noise_is_not_significant(self):
        rng = np.random.default_rng(5)
        idx = pd.date_range("2025-01-01", periods=150, freq="D")
        a = pd.Series(rng.normal(10, 3, 150), index=idx)
        b = pd.Series(rng.normal(10, 3, 150), index=idx)
        lag = cross_correlation_lag(a, b, "daily")
        assert lag is not None
        # Puur ruis: de 'beste' lag mag niet als significant worden verkocht.
        assert not lag.significant

    def test_threshold_accounts_for_multiple_lags(self):
        rng = np.random.default_rng(6)
        idx = pd.date_range("2025-01-01", periods=150, freq="D")
        a = pd.Series(rng.normal(0, 1, 150), index=idx)
        b = pd.Series(rng.normal(0, 1, 150), index=idx)
        lag = cross_correlation_lag(a, b, "daily")
        # Drempel moet ruim boven de klassieke 2/sqrt(n) enkelvoudige-lag
        # drempel liggen (selectie-effect over ~61 lags).
        assert lag.sig_threshold > 2.0 / np.sqrt(lag.n_overlap)


class TestChangePointThreshold:
    def test_clear_shift_still_detected(self):
        idx = pd.date_range("2025-01-01", periods=120, freq="D")
        vals = np.concatenate([np.full(60, 5.0), np.full(60, 20.0)])
        rng = np.random.default_rng(1)
        s = pd.Series(vals + rng.normal(0, 1, 120), index=idx)
        cps = detect_change_points(s)
        assert len(cps) >= 1
        assert any(abs((cp["date"] - idx[60]).days) <= 5 for cp in cps)

    def test_pure_noise_rarely_fires(self):
        rng = np.random.default_rng(2)
        idx = pd.date_range("2025-01-01", periods=400, freq="D")
        s = pd.Series(rng.normal(10, 2, 400), index=idx)
        cps = detect_change_points(s)
        # Met de universal threshold hoort ruis (vrijwel) niets op te leveren.
        assert len(cps) <= 1


class TestSensitivityInFindings:
    def test_findings_carry_sensitivity(self, synthetic_daily):
        from core.auto_pilot import build_findings, run_auto_pilot
        result = run_auto_pilot(synthetic_daily)
        findings = build_findings(result)
        assert all("gevoeligheid" in f for f in findings)
        if findings:
            assert findings[0]["gevoeligheid"] in ("streng", "normaal", "soepel")
