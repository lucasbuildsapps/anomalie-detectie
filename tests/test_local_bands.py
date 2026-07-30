"""Tests voor niveau- en regime-afhankelijke banden.

Aanleiding: op de demo-dataset (Oekraïne, dagbasis) liep het gemiddelde
van ~9 per dag in 2022 naar ~200 in 2026. De band was echter één vaste
breedte over de hele historie, gekalibreerd op het drukke regime. In 2022
stond er 0–469 bij een gemiddelde van 9, en in drie volle jaren (826
dagen) werd geen enkele afwijking gevonden. De tool was daarmee blind
voor alles vóór het huidige regime.

Twee dingen moesten lokaal worden: het niveau (dat was het al) én de
spreiding.
"""
import numpy as np
import pandas as pd
import pytest

from core.normbeeld import (
    _is_count_series,
    _local_dispersion,
    _local_residual_scale,
    compute_normbeeld,
)


def _frame(values, start="2022-01-01"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(values), freq="D"),
        "value": np.asarray(values, dtype=float),
        "location_name": "A",
    })


@pytest.fixture
def regime_shift():
    """Rustig jaar (~8/dag) gevolgd door een druk jaar (~200/dag) —
    dezelfde vorm als de echte data."""
    rng = np.random.default_rng(17)
    quiet = rng.poisson(8, 400)
    busy = rng.poisson(200, 400)
    return _frame(np.concatenate([quiet, busy]))


class TestCountGate:
    def test_high_count_integers_are_count_data(self):
        rng = np.random.default_rng(1)
        assert _is_count_series(pd.Series(rng.poisson(200, 300).astype(float)))

    def test_sparse_integers_are_count_data(self):
        assert _is_count_series(pd.Series([0.0, 1.0, 0.0, 3.0]))

    def test_continuous_data_is_not(self):
        assert not _is_count_series(pd.Series([1.5, 2.25, 3.75] * 10))

    def test_negative_data_is_not(self):
        assert not _is_count_series(pd.Series([-2.0, 1.0, 3.0]))


class TestLocalSpread:
    def test_dispersion_tracks_the_regime(self):
        """Rustige periode hoort een lagere dispersie te krijgen dan een
        onstuimige — één getal voor de hele reeks kan dat niet."""
        rng = np.random.default_rng(3)
        quiet = rng.poisson(10, 300).astype(float)
        wild = rng.poisson(10, 300).astype(float) * rng.choice(
            [0.2, 4.0], 300)
        y = np.concatenate([quiet, wild])
        mu = pd.Series(y).rolling(30, min_periods=1).mean().shift(1).bfill().values
        phi = _local_dispersion(y, mu)
        assert phi[:300].mean() < phi[300:].mean()

    def test_dispersion_never_below_one(self):
        rng = np.random.default_rng(4)
        y = rng.poisson(20, 200).astype(float)
        mu = np.full(200, 20.0)
        assert (_local_dispersion(y, mu) >= 1.0).all()

    def test_spread_does_not_include_its_own_point(self):
        """Leakage-bewaking: zonder shift verbreedt een uitschieter zijn
        eigen band en verdwijnt hij erin — dezelfde fout die eerder in de
        rolling-detector zat."""
        y = np.full(200, 10.0)
        y[150] = 500.0
        mu = np.full(200, 10.0)
        phi = _local_dispersion(y, mu)
        # Op het punt zelf mag de piek nog niet meegewogen zijn.
        assert phi[150] < phi[151]

    def test_residual_scale_is_never_zero(self):
        assert (_local_residual_scale(np.zeros(100)) > 0).all()


class TestBandFollowsRegime:
    def test_quiet_period_gets_a_narrower_band(self, regime_shift):
        nb = compute_normbeeld(regime_shift, location="A", horizon_days=14,
                               aggregation="daily")
        h = nb.historical
        width = (h["upper"] - h["lower"]).values
        quiet_w, busy_w = width[:400].mean(), width[400:].mean()
        assert quiet_w < busy_w / 3, (
            f"rustige band {quiet_w:.0f} hoort veel smaller dan drukke "
            f"band {busy_w:.0f}")

    def test_quiet_band_is_proportional_to_its_level(self, regime_shift):
        """Concreet: bij ~8 per dag hoort geen bovengrens van honderden."""
        nb = compute_normbeeld(regime_shift, location="A", horizon_days=14,
                               aggregation="daily")
        early = nb.historical.iloc[50:400]
        assert early["upper"].mean() < 60, (
            "bovengrens in de rustige periode is losgezongen van het niveau")

    def test_deviations_are_found_in_every_regime(self, regime_shift):
        """De oude band vond in de rustige jaren letterlijk niets."""
        vals = regime_shift["value"].to_numpy().copy()
        vals[120] = 90.0      # forse uitschieter in het rustige regime
        vals[600] = 900.0     # idem in het drukke regime
        nb = compute_normbeeld(_frame(vals), location="A", horizon_days=14,
                               aggregation="daily")
        h = nb.historical
        assert h.iloc[120]["status"] == "boven", "piek in rustig regime gemist"
        assert h.iloc[600]["status"] == "boven", "piek in druk regime gemist"

    def test_coverage_stays_calibrated(self, regime_shift):
        nb = compute_normbeeld(regime_shift, location="A", horizon_days=14,
                               aggregation="daily")
        target = 1.0 - 2.0 * nb.band_alpha
        assert abs(nb.band_coverage - target) < 0.06, (
            f"dekking {nb.band_coverage:.3f} wijkt te ver van doel {target:.3f}")

    def test_band_invariants_hold(self, regime_shift):
        nb = compute_normbeeld(regime_shift, location="A", horizon_days=14,
                               aggregation="daily")
        for df in (nb.historical, nb.forecast):
            assert np.all(df["upper"].values >= df["lower"].values - 1e-9)
            assert np.all(df["lower"].values >= -1e-9)


def test_real_demo_data_finds_deviations_in_early_years():
    """Regressie op de echte dataset: de vraag die dit alles startte was
    waarom de band in 2022 al zo hoog stond."""
    from pathlib import Path

    from core.import_data import apply_mapping

    csv = Path(__file__).resolve().parent.parent / "data" / "missile_attacks_demo.csv"
    if not csv.exists():
        pytest.skip("demo-CSV niet aanwezig")
    raw = pd.read_csv(csv)
    df, _ = apply_mapping(raw, {
        "time": "time_start", "value": "launched", "location_name": "target",
        "category": "model", "lat": None, "lon": None, "extras": [],
    })
    nb = compute_normbeeld(df, location="Ukraine", horizon_days=14,
                           aggregation="daily")
    h = nb.historical.copy()
    h["jaar"] = pd.to_datetime(h["date"]).dt.year

    early = h[h["jaar"] <= 2023]
    assert early["upper"].mean() < 200, (
        "band in 2022-2023 is nog steeds gekalibreerd op het drukke regime")
    assert (early["status"] != "normaal").sum() > 0, (
        "geen enkele afwijking in 2022-2023 — de tool is blind voor het "
        "vroege regime")
