"""Tests voor de vier verfijningen van de baseline-kwaliteit:

1. spreiding per regime (breuken vervuilen de schatting niet meer),
2. spreiding per seizoensfase (rustige weekdagen krijgen een smallere band),
3. venstergrootte gekozen op gemeten kalibratie i.p.v. een vast getal,
4. vertrouwen dat regime-stabiliteit meeweegt, niet alleen reekslengte.
"""
import numpy as np
import pandas as pd
import pytest

from core.normbeeld import (
    _confidence,
    _pick_spread_window,
    _seasonal_spread_factors,
    _segment_ids,
    compute_normbeeld,
)


def _frame(values, start="2023-01-01"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(values), freq="D"),
        "value": np.asarray(values, dtype=float),
        "location_name": "A",
    })


def _series(values, start="2023-01-01"):
    return pd.Series(np.asarray(values, dtype=float),
                     index=pd.date_range(start, periods=len(values), freq="D"))


class TestSegments:
    def test_sharp_break_is_detected(self):
        rng = np.random.default_rng(5)
        s = _series(np.concatenate([rng.poisson(10, 200),
                                    rng.poisson(120, 200)]))
        segs = _segment_ids(s)
        assert len(set(segs)) >= 2
        # De breuk hoort ergens rond het midden te liggen.
        first_change = int(np.argmax(segs != segs[0]))
        assert 150 < first_change < 250

    def test_stable_series_stays_one_segment(self):
        rng = np.random.default_rng(6)
        assert len(set(_segment_ids(_series(rng.poisson(20, 400))))) == 1

    def test_short_series_is_one_segment(self):
        assert len(set(_segment_ids(_series([1.0] * 20)))) == 1

    def test_tiny_segments_are_not_created(self):
        """Van een handvol punten valt geen spreiding te schatten."""
        rng = np.random.default_rng(7)
        s = _series(np.concatenate([rng.poisson(10, 300), rng.poisson(90, 5)]))
        segs = _segment_ids(s, min_segment=30)
        assert len(set(segs)) == 1


class TestSeasonalSpread:
    def test_calm_phase_gets_a_narrower_band(self):
        """Weekdagen rustig én regelmatig, weekend onstuimig: de factoren
        horen dat te weerspiegelen."""
        rng = np.random.default_rng(8)
        n = 350
        resid = np.where(np.arange(n) % 7 < 5,
                         rng.normal(0, 1, n), rng.normal(0, 6, n))
        f = _seasonal_spread_factors(resid, period=7)
        weekday = f[np.arange(n) % 7 < 5].mean()
        weekend = f[np.arange(n) % 7 >= 5].mean()
        assert weekday < weekend

    def test_factors_are_normalised(self):
        """Gemiddeld 1: de fase-correctie mag de totale kalibratie niet
        verschuiven, alleen herverdelen."""
        rng = np.random.default_rng(9)
        n = 280
        resid = np.where(np.arange(n) % 7 == 0,
                         rng.normal(0, 5, n), rng.normal(0, 1, n))
        assert _seasonal_spread_factors(resid, 7).mean() == pytest.approx(1.0,
                                                                         abs=0.02)

    def test_no_period_means_no_correction(self):
        rng = np.random.default_rng(10)
        assert (_seasonal_spread_factors(rng.normal(0, 1, 200), None) == 1).all()

    def test_too_little_data_per_phase_is_left_alone(self):
        rng = np.random.default_rng(11)
        assert (_seasonal_spread_factors(rng.normal(0, 1, 20), 7) == 1).all()

    def test_factors_are_bounded(self):
        """Eén toevallig rustige fase mag het beeld niet overnemen."""
        rng = np.random.default_rng(12)
        n = 350
        resid = np.where(np.arange(n) % 7 == 0, 0.001, rng.normal(0, 3, n))
        f = _seasonal_spread_factors(resid, 7)
        assert f.min() > 0.2 and f.max() < 3.0


class TestWindowSelection:
    def test_returns_a_candidate(self):
        rng = np.random.default_rng(13)
        s = _series(rng.poisson(30, 400))
        mu = s.rolling(14, min_periods=1).mean().shift(1).bfill().values
        w = _pick_spread_window(s, mu, 0.02, np.zeros(len(s), dtype=int))
        assert w in (30, 60, 90, 180)

    def test_short_series_falls_back(self):
        s = _series([5.0] * 25)
        mu = np.full(25, 5.0)
        assert _pick_spread_window(s, mu, 0.02, np.zeros(25, dtype=int)) > 0

    def test_selection_improves_calibration(self):
        """Het gekozen venster hoort minstens zo goed te kalibreren als
        het oude vaste getal van 90."""
        from core.normbeeld import _count_band, _seasonal_spread_factors
        rng = np.random.default_rng(14)
        s = _series(np.concatenate([rng.poisson(8, 200), rng.poisson(150, 250)]))
        mu = s.rolling(14, min_periods=1).mean().shift(1).bfill().values
        segs = np.zeros(len(s), dtype=int)
        alpha, target = 0.02, 0.96
        season = _seasonal_spread_factors(s.values - mu, 7)

        def coverage(w):
            lo, hi, _, _ = _count_band(s, mu, alpha, window=w, segments=segs,
                                       season_factor=season)
            return float(np.mean((s.values >= lo) & (s.values <= hi)))

        chosen = _pick_spread_window(s, mu, alpha, segs, season_period=7)
        assert abs(coverage(chosen) - target) <= abs(coverage(90) - target) + 1e-9


class TestConfidence:
    def test_long_stable_series_is_high(self):
        assert _confidence(400, True) == "hoog"

    def test_fresh_regime_downgrades_a_long_series(self):
        """Kern van de verbetering: drie jaar historie met een breuk van
        vorige maand is minder betrouwbaar dan de lengte suggereert."""
        assert _confidence(400, True, periods_since_break=10) == "laag"
        assert _confidence(400, True, periods_since_break=25) == "midden"
        assert _confidence(400, True, periods_since_break=200) == "hoog"

    def test_miscalibrated_band_downgrades(self):
        assert _confidence(400, True, coverage=0.60,
                           target_coverage=0.96) == "midden"

    def test_well_calibrated_band_keeps_level(self):
        assert _confidence(400, True, coverage=0.95,
                           target_coverage=0.96) == "hoog"

    def test_short_series_stays_low(self):
        assert _confidence(10, False) == "laag"


class TestEndToEnd:
    def test_regime_break_does_not_contaminate_the_new_band(self):
        """Na een scherpe daling mag de band niet maandenlang de breedte
        van het oude, drukke regime houden."""
        rng = np.random.default_rng(15)
        vals = np.concatenate([rng.poisson(200, 300), rng.poisson(10, 120)])
        nb = compute_normbeeld(_frame(vals), location="A", horizon_days=14,
                               aggregation="daily")
        tail = nb.historical.tail(60)
        assert tail["upper"].mean() < 80, (
            "band na de breuk hangt nog aan het oude regime")

    def test_confidence_drops_right_after_a_break(self):
        rng = np.random.default_rng(16)
        vals = np.concatenate([rng.poisson(20, 300), rng.poisson(200, 12)])
        nb = compute_normbeeld(_frame(vals), location="A", horizon_days=14,
                               aggregation="daily")
        assert nb.confidence in ("laag", "midden")

    def test_calibration_holds_on_real_data(self):
        from pathlib import Path

        from core.import_data import apply_mapping
        csv = Path(__file__).resolve().parent.parent / "data" / "missile_attacks_demo.csv"
        if not csv.exists():
            pytest.skip("demo-CSV niet aanwezig")
        raw = pd.read_csv(csv)
        df, _ = apply_mapping(raw, {
            "time": "time_start", "value": "launched",
            "location_name": "target", "category": "model",
            "lat": None, "lon": None, "extras": [],
        })
        for loc in ("Ukraine", "Mykolaiv oblast", "Kyiv oblast"):
            nb = compute_normbeeld(df, location=loc, horizon_days=14,
                                   aggregation="daily")
            target = 1.0 - 2.0 * nb.band_alpha
            assert abs(nb.band_coverage - target) < 0.05, (
                f"{loc}: dekking {nb.band_coverage:.3f} vs doel {target:.2f}")
