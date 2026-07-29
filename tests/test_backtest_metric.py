"""Tests voor de backtest-metriek (MASE) en het tijdschaal-advies.

Achtergrond: de oude metriek was |fout| / max(|werkelijk|, 1). Op een reeks
met veel nullen deelt dat door ~1, waardoor één lege dag met een
voorspelling van 75 een fout van 7500% opleverde. Gevolg: dagdata leek
20x slechter dan weekdata, en de *leegste* reeksen scoorden het best.
MASE deelt door één vaste schaal per fold en heeft dat probleem niet.
"""
import numpy as np
import pandas as pd
import pytest

from core.normbeeld import (
    _naive_scale,
    backtest_all_methods,
    recommend_timescale,
)


def _series(values, freq="D"):
    return pd.Series(
        np.asarray(values, dtype=float),
        index=pd.date_range("2024-01-01", periods=len(values), freq=freq),
    )


def _frame(values, freq="D", location="A"):
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=len(values), freq=freq),
        "value": np.asarray(values, dtype=float),
        "location_name": location,
    })


class TestNaiveScale:
    def test_is_mean_absolute_first_difference(self):
        assert _naive_scale(np.array([1.0, 3.0, 6.0])) == pytest.approx(2.5)

    def test_constant_series_falls_back_not_to_zero(self):
        # Zonder terugval zou MASE delen door 0 worden
        assert _naive_scale(np.array([5.0] * 10)) > 0

    def test_single_point_is_safe(self):
        assert _naive_scale(np.array([5.0])) == 1.0

    def test_ignores_seasonal_period_for_comparability(self):
        """m blijft 1, ook als een periode wordt meegegeven: anders
        vergelijkt MASE tussen tijdschalen appels met peren."""
        train = np.array([1.0, 5.0] * 20)
        assert _naive_scale(train, period=7) == _naive_scale(train, period=1)


class TestMaseVsPercentage:
    def test_zeros_do_not_explode_the_score(self):
        """Kern van de bug: veel nullen mogen de fout niet opblazen."""
        rng = np.random.default_rng(1)
        vals = rng.poisson(3, 200).astype(float)
        vals[rng.random(200) < 0.4] = 0.0   # 40% lege perioden
        scores = backtest_all_methods(_series(vals), period=7, horizon=14)
        assert scores
        for s in scores.values():
            # De oude metriek gaf hier honderden procenten; MASE blijft
            # in een interpreteerbaar bereik rond 1.
            assert 0 < s.mase < 10, f"MASE onrealistisch: {s.mase}"

    def test_score_carries_both_metrics(self):
        rng = np.random.default_rng(2)
        scores = backtest_all_methods(
            _series(20 + rng.normal(0, 3, 150)), period=7, horizon=14)
        s = next(iter(scores.values()))
        assert s.mase > 0
        assert np.isfinite(s.wmape)
        assert s.n_obs > 0

    def test_random_walk_scores_near_one(self):
        """Op een random walk is niets beter dan 'volgende = vorige';
        MASE hoort daar rond 1 uit te komen."""
        rng = np.random.default_rng(5)
        walk = 100 + np.cumsum(rng.normal(0, 1, 300))
        scores = backtest_all_methods(_series(walk), period=7, horizon=7)
        best = min(s.mase for s in scores.values())
        assert 0.3 < best < 3.0

    def test_predictable_series_beats_naive(self):
        """Een strak seizoenspatroon hoort de naïeve benchmark te verslaan."""
        t = np.arange(400)
        seasonal = 50 + 20 * np.sin(2 * np.pi * t / 7)
        scores = backtest_all_methods(_series(seasonal), period=7, horizon=14)
        best = min(s.mase for s in scores.values())
        assert best < 1.0


class TestTimescaleAdvice:
    def test_sparse_daily_series_is_advised_upward(self):
        """92% lege dagen: dagbasis is formeel 'voorspelbaar' (altijd nul)
        maar nutteloos. Het advies hoort naar een grovere schaal te gaan."""
        rng = np.random.default_rng(7)
        vals = np.zeros(730)
        hits = rng.choice(730, size=60, replace=False)
        vals[hits] = rng.integers(1, 5, 60)
        advice = recommend_timescale(_frame(vals))
        assert advice is not None
        assert advice.recommended in ("weekly", "monthly")

    def test_dense_daily_series_stays_daily(self):
        rng = np.random.default_rng(8)
        t = np.arange(500)
        vals = np.clip(60 + 15 * np.sin(2 * np.pi * t / 7)
                       + rng.normal(0, 4, 500), 0, None)
        advice = recommend_timescale(_frame(vals))
        assert advice is not None
        assert advice.recommended == "daily"

    def test_advice_explains_itself(self):
        rng = np.random.default_rng(9)
        advice = recommend_timescale(_frame(rng.poisson(5, 400)))
        assert advice is not None
        assert "MASE" in advice.reason
        assert len(advice.reason) > 60          # echte uitleg, geen label
        assert advice.heuristic in ("daily", "weekly", "monthly")

    def test_scores_include_evidence_per_timescale(self):
        rng = np.random.default_rng(10)
        advice = recommend_timescale(_frame(rng.poisson(8, 400)))
        for sc in advice.scores.values():
            for key in ("mase", "wmape", "n_periods", "zero_share",
                        "method", "rank_score"):
                assert key in sc

    def test_short_series_gives_no_advice(self):
        assert recommend_timescale(_frame([1.0, 2.0, 3.0])) is None

    def test_empty_input_is_safe(self):
        assert recommend_timescale(pd.DataFrame()) is None

    def test_location_filter_is_applied(self):
        rng = np.random.default_rng(11)
        a = _frame(rng.poisson(9, 400), location="A")
        b = _frame(np.zeros(400), location="B")
        advice = recommend_timescale(pd.concat([a, b], ignore_index=True),
                                     location="A")
        assert advice is not None
        assert advice.scores
