"""Property-based tests (hypothesis): invarianten van de kern-wiskunde.

Waar de unit-tests specifieke scenario's dekken, zoekt hypothesis actief
naar pathologische reeksen (nullen, constanten, extreme spikes, korte
reeksen) die de invarianten breken — precies de klasse defecten die
eerder handmatig gevonden werd ('Voorspellingsaudit: 8 defecten').
"""
import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st_h

from core.auto_pilot import classify_severity
from core.normbeeld import compute_normbeeld

_SETTINGS = settings(
    max_examples=25, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _frame(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=len(values), freq="D"),
        "value": values,
        "location_name": "A",
    })


# Niet-negatieve reeksen (count-achtig), inclusief nullen en spikes.
nonneg_series = st_h.lists(
    st_h.floats(min_value=0, max_value=1e4, allow_nan=False,
                allow_infinity=False),
    min_size=8, max_size=200,
)

# Reeksen die ook negatief mogen zijn (delta's, temperaturen).
signed_series = st_h.lists(
    st_h.floats(min_value=-1e4, max_value=1e4, allow_nan=False,
                allow_infinity=False),
    min_size=8, max_size=120,
)


class TestNormbeeldInvariants:
    @_SETTINGS
    @given(nonneg_series)
    def test_bands_ordered_and_finite(self, values):
        nb = compute_normbeeld(_frame(values), location="A", horizon_days=10)
        if nb is None:  # te weinig data mag; crashen niet
            return
        for df in (nb.historical, nb.forecast):
            assert np.all(np.isfinite(df["lower"]))
            assert np.all(np.isfinite(df["upper"]))
            assert np.all(df["upper"].values >= df["lower"].values - 1e-9)
        assert np.all(np.isfinite(nb.forecast["expected"]))

    @_SETTINGS
    @given(nonneg_series)
    def test_nonneg_data_gives_nonneg_bands(self, values):
        nb = compute_normbeeld(_frame(values), location="A", horizon_days=10)
        if nb is None:
            return
        assert np.all(nb.forecast["lower"].values >= -1e-9)
        assert np.all(nb.historical["lower"].values >= -1e-9)

    @_SETTINGS
    @given(signed_series)
    def test_signed_data_never_crashes(self, values):
        nb = compute_normbeeld(_frame(values), location="A", horizon_days=10)
        if nb is None:
            return
        assert len(nb.forecast) == 10
        assert np.all(np.isfinite(nb.forecast["expected"]))

    @_SETTINGS
    @given(nonneg_series)
    def test_forecast_horizon_respected(self, values):
        nb = compute_normbeeld(_frame(values), location="A", horizon_days=7)
        if nb is None:
            return
        assert len(nb.forecast) == 7
        # Forecast begint ná het laatste historiepunt
        assert nb.forecast["date"].min() > nb.historical["date"].max()


class TestSeverityInvariants:
    @given(
        st_h.integers(min_value=1, max_value=8),
        st_h.lists(st_h.integers(min_value=0, max_value=8),
                   min_size=1, max_size=50),
    )
    def test_severity_needs_two_votes_when_possible(self, n_methods, votes):
        votes = np.array([min(v, n_methods) for v in votes])
        sev = classify_severity(votes, n_methods)
        for v, s in zip(votes, sev, strict=True):
            if n_methods >= 2 and v < 2:
                assert s is None, "minder dan 2 stemmen mag nooit severity geven"
            if s == "hoog":
                assert v >= max(3, n_methods - 1)
