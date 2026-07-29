"""Tests voor de discrete band op schaarse telling-data (METHODS.md §6b).

Achtergrond: bij ~0.6 gebeurtenissen/dag is een residual-quantile-band
gedomineerd door een paar spikes. Een Poisson/negatief-binomiaal-interval
rond de verwachting is daar het statistisch juiste mechanisme.
"""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from core.normbeeld import _is_low_count_series, compute_normbeeld


def _frame(values):
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=len(values), freq="D"),
        "value": [float(v) for v in values],
        "location_name": "A",
    })


@pytest.fixture
def sparse_poisson():
    rng = np.random.default_rng(21)
    return _frame(rng.poisson(0.8, 150))


@pytest.fixture
def clustered_counts():
    """Overdispersed: meestal stil, af en toe een golf — zoals aanvalsdata."""
    rng = np.random.default_rng(22)
    vals = rng.poisson(0.4, 150)
    burst_days = rng.choice(150, size=10, replace=False)
    vals[burst_days] += rng.poisson(12, 10)
    return _frame(vals)


class TestGate:
    def test_sparse_integers_are_low_count(self):
        assert _is_low_count_series(pd.Series([0, 1, 0, 2, 0, 1, 3, 0]))

    def test_high_level_counts_are_not(self):
        rng = np.random.default_rng(1)
        assert not _is_low_count_series(pd.Series(rng.poisson(50, 100).astype(float)))

    def test_continuous_data_is_not(self):
        assert not _is_low_count_series(pd.Series([0.5, 1.2, 0.8, 2.3] * 10))

    def test_negative_data_is_not(self):
        assert not _is_low_count_series(pd.Series([-1.0, 0.0, 2.0, 1.0]))


class TestCountBandInNormbeeld:
    def test_sparse_series_gets_count_band(self, sparse_poisson):
        nb = compute_normbeeld(sparse_poisson, location="A", horizon_days=10)
        assert nb.band_model in ("poisson", "negbin")
        assert nb.dispersion is not None and nb.dispersion >= 1.0

    def test_band_is_sharp_for_pure_poisson(self, sparse_poisson):
        # Bij mu ~0.8 hoort de bovengrens dicht bij het Poisson-quantiel te
        # liggen, niet op spike-gedreven quantile-hoogte.
        nb = compute_normbeeld(sparse_poisson, location="A", horizon_days=10)
        mu = nb.historical["expected"].tail(30).mean()
        theoretical = stats.poisson.ppf(1 - nb.band_alpha, max(mu, 0.1))
        # Ruime marge voor negbin-verbreding: binnen 3x het Poisson-quantiel.
        assert nb.upper_band <= 3 * theoretical + 1

    def test_overdispersed_series_uses_negbin_and_is_wider(self, clustered_counts):
        nb = compute_normbeeld(clustered_counts, location="A", horizon_days=10)
        assert nb.band_model == "negbin"
        assert nb.dispersion > 1.3
        mu = max(nb.historical["expected"].tail(30).mean(), 0.1)
        poisson_upper = stats.poisson.ppf(1 - nb.band_alpha, mu)
        assert nb.upper_band >= poisson_upper  # clustering -> ruimere band

    def test_invariants_hold(self, clustered_counts):
        nb = compute_normbeeld(clustered_counts, location="A", horizon_days=14)
        for df in (nb.historical, nb.forecast):
            assert np.all(df["upper"].values >= df["lower"].values - 1e-9)
            assert np.all(df["lower"].values >= -1e-9)

    def test_forecast_band_widens_with_horizon(self, sparse_poisson):
        nb = compute_normbeeld(sparse_poisson, location="A", horizon_days=14)
        width = (nb.forecast["upper"] - nb.forecast["lower"]).values
        assert width[-1] >= width[0] - 1e-9

    def test_continuous_series_keeps_quantile_band(self):
        rng = np.random.default_rng(9)
        nb = compute_normbeeld(_frame(50 + rng.normal(0, 5, 120)),
                               location="A", horizon_days=10)
        assert nb.band_model == "quantile"
        assert nb.dispersion is None

    def test_coverage_still_reported(self, sparse_poisson):
        nb = compute_normbeeld(sparse_poisson, location="A", horizon_days=10)
        assert nb.band_coverage is not None
        assert 0.6 <= nb.band_coverage <= 1.0
