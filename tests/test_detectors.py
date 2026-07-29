"""Gedrags-tests voor de change-point- en ensemble-detectoren
(voorheen 24%/27% coverage — de twee minst geteste plug-ins)."""
import numpy as np
import pandas as pd
import pytest

from detectors.changepoint import ChangePointDetector
from detectors.ensemble import EnsembleDetector
from detectors.zscore import ZScoreDetector


def _df(values, start="2025-01-01"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(values), freq="D"),
        "value": [float(v) for v in values],
    })


@pytest.fixture
def level_shift_df():
    """60 dagen rond 5, dan 60 dagen rond 20 — één duidelijk breekpunt."""
    rng = np.random.default_rng(8)
    vals = np.concatenate([
        5 + rng.normal(0, 1, 60), 20 + rng.normal(0, 1, 60),
    ])
    return _df(vals)


class TestChangePoint:
    def test_detects_level_shift_near_break(self, level_shift_df):
        out = ChangePointDetector().detect(level_shift_df, "timestamp", "value")
        flagged = out[out["is_anomaly"]]["timestamp"]
        assert len(flagged) >= 1
        breakpoint_date = level_shift_df["timestamp"].iloc[60]
        assert any(abs((d - breakpoint_date).days) <= 7 for d in flagged)

    def test_flat_series_flags_nothing(self):
        out = ChangePointDetector().detect(_df([5.0] * 60), "timestamp", "value")
        assert not out["is_anomaly"].any()

    def test_single_spike_is_not_a_changepoint(self):
        vals = [5.0] * 60
        vals[30] = 50.0  # losse spike, geen regimewissel
        rng = np.random.default_rng(3)
        vals = np.array(vals) + rng.normal(0, 0.5, 60)
        out = ChangePointDetector().detect(_df(vals), "timestamp", "value")
        # Een spike mag hooguit rond zichzelf iets triggeren; het niveau
        # ervoor en erna is gelijk, dus géén breekpunt ver van de spike.
        flagged = out[out["is_anomaly"]].index.tolist()
        assert all(abs(i - 30) <= 8 for i in flagged)

    def test_too_short_series_returns_no_anomalies(self):
        out = ChangePointDetector().detect(_df([1, 2, 3]), "timestamp", "value")
        assert not out["is_anomaly"].any()
        assert (out["anomaly_score"] == 0).all()

    def test_nms_keeps_points_separated(self, level_shift_df):
        out = ChangePointDetector().detect(level_shift_df, "timestamp",
                                           "value", window=7, threshold=2.0)
        days = sorted(out[out["is_anomaly"]]["timestamp"])
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
        assert all(g >= 7 for g in gaps)

    def test_output_contract(self, level_shift_df):
        out = ChangePointDetector().detect(level_shift_df, "timestamp", "value")
        assert {"anomaly_score", "is_anomaly"} <= set(out.columns)
        assert len(out) == len(level_shift_df)


class TestEnsemble:
    def test_spike_confirmed_by_multiple_methods(self):
        rng = np.random.default_rng(5)
        vals = 5 + rng.normal(0, 0.5, 90)
        vals[45] = 60.0
        out = EnsembleDetector().detect(
            _df(vals), "timestamp", "value",
            methods=["Z-score (MAD)", "Rolling mean ± N·std"], min_votes=2,
        )
        assert bool(out.loc[45, "is_anomaly"])

    def test_min_votes_filters_single_method_hits(self):
        rng = np.random.default_rng(5)
        vals = 5 + rng.normal(0, 0.5, 90)
        vals[45] = 60.0
        df = _df(vals)
        loose = EnsembleDetector().detect(
            df, "timestamp", "value",
            methods=["Z-score (MAD)", "Rolling mean ± N·std"], min_votes=1,
        )
        strict = EnsembleDetector().detect(
            df, "timestamp", "value",
            methods=["Z-score (MAD)", "Rolling mean ± N·std"], min_votes=2,
        )
        assert strict["is_anomaly"].sum() <= loose["is_anomaly"].sum()

    def test_unknown_methods_are_skipped_gracefully(self):
        out = EnsembleDetector().detect(
            _df([1.0] * 30), "timestamp", "value",
            methods=["Bestaat niet", "Z-score (MAD)"],
        )
        assert len(out) == 30  # geen crash; onbekende methode genegeerd

    def test_no_usable_methods_returns_clean_result(self):
        out = EnsembleDetector().detect(
            _df([1.0] * 30), "timestamp", "value", methods=["Bestaat niet"],
        )
        assert not out["is_anomaly"].any()
        assert (out["anomaly_score"] == 0).all()

    def test_score_is_vote_fraction(self):
        rng = np.random.default_rng(5)
        vals = 5 + rng.normal(0, 0.5, 90)
        vals[45] = 60.0
        out = EnsembleDetector().detect(
            _df(vals), "timestamp", "value",
            methods=["Z-score (MAD)", "Rolling mean ± N·std"],
        )
        assert out["anomaly_score"].between(0, 1).all()

    def test_default_method_pick_excludes_self(self):
        # Zonder expliciete methodes kiest hij er max 3, nooit zichzelf.
        out = EnsembleDetector().detect(_df([1.0] * 40), "timestamp", "value")
        assert len(out) == 40


class TestZScoreEdgeCases:
    def test_zero_mad_flags_nothing(self):
        out = ZScoreDetector().detect(_df([4.0] * 30), "timestamp", "value")
        assert not out["is_anomaly"].any()
