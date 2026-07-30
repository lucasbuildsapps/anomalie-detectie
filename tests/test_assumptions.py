"""Tests voor het aannameregister (ICD 203: gegevens vs. aannames).

De tool maakt keuzes die de uitkomst sturen — gap-beleid, tijdschaal,
methode, bandmodel. Die waren onzichtbaar: de analist erfde ze zonder ze
te zien. Deze tests bewaken dat ze expliciet worden gemaakt, mét wat er
gebeurt als ze niet kloppen.
"""
import numpy as np
import pandas as pd
import pytest

from core.assumptions import (
    BASIS_DEFAULT,
    BASIS_MEASURED,
    BASIS_USER,
    collect,
    critical_only,
    summarise,
)
from core.normbeeld import compute_normbeeld


@pytest.fixture
def normbeeld():
    rng = np.random.default_rng(31)
    vals = np.clip(20 + rng.normal(0, 4, 200), 0, None).round()
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=200, freq="D"),
        "value": vals, "location_name": "A",
    })
    return compute_normbeeld(df, location="A", horizon_days=14,
                             aggregation="daily")


class TestGapPolicy:
    def test_default_zero_is_flagged_critical(self, normbeeld):
        """De gevaarlijkste aanname: collectie-uitval leest als stilte."""
        gap = next(a for a in collect(normbeeld, gap_policy="zero")
                   if a.topic == "Gap-beleid")
        assert gap.critical
        assert gap.basis == BASIS_DEFAULT
        assert "uitval" in gap.if_wrong

    def test_interpolate_has_the_opposite_risk(self, normbeeld):
        gap = next(a for a in collect(normbeeld, gap_policy="interpolate")
                   if a.topic == "Gap-beleid")
        assert gap.critical
        assert "daling onzichtbaar" in gap.if_wrong

    def test_mask_is_the_honest_choice(self, normbeeld):
        """Bij 'mask' wordt bewust géén aanname gedaan."""
        gap = next(a for a in collect(normbeeld, gap_policy="mask")
                   if a.topic == "Gap-beleid")
        assert not gap.critical
        assert "bewust geen aanname" in gap.if_wrong


class TestBasisDistinction:
    def test_user_choice_is_marked_as_such(self, normbeeld):
        """Kern van de standaard: wat is gemeten, wat is aangenomen, en
        wat heeft de gebruiker zelf gekozen."""
        items = collect(normbeeld, aggregation_choice="weekly")
        tijd = next(a for a in items if a.topic == "Tijdschaal")
        assert tijd.basis == BASIS_USER

    def test_auto_timescale_is_a_default(self, normbeeld):
        tijd = next(a for a in collect(normbeeld, aggregation_choice="auto")
                    if a.topic == "Tijdschaal")
        assert tijd.basis == BASIS_DEFAULT

    def test_backtest_selection_counts_as_measured(self, normbeeld):
        methode = next(a for a in collect(normbeeld)
                       if a.topic == "Voorspelmethode")
        assert methode.basis in (BASIS_MEASURED, BASIS_DEFAULT)

    def test_manual_methods_are_a_user_choice(self, normbeeld):
        methode = next(a for a in collect(normbeeld, methods_override=["ets"])
                       if a.topic == "Voorspelmethode")
        assert methode.basis == BASIS_USER

    def test_band_model_is_measured(self, normbeeld):
        band = next(a for a in collect(normbeeld) if a.topic == "Bandmodel")
        assert band.basis == BASIS_MEASURED


class TestCalibrationAndSource:
    def test_calibration_is_reported_with_numbers(self, normbeeld):
        cal = next(a for a in collect(normbeeld) if a.topic == "Kalibratie")
        assert "%" in cal.statement
        assert cal.basis == BASIS_MEASURED

    def test_missing_source_reliability_is_itself_an_assumption(self,
                                                                normbeeld):
        bron = next(a for a in collect(normbeeld) if a.topic == "Bron")
        assert "geen betrouwbaarheid" in bron.statement

    def test_poor_source_is_critical(self, normbeeld):
        bron = next(a for a in collect(normbeeld, source_reliability="E")
                    if a.topic == "Bron")
        assert bron.critical
        assert "rapportage" in bron.if_wrong

    def test_good_source_is_not_critical(self, normbeeld):
        bron = next(a for a in collect(normbeeld, source_reliability="A")
                    if a.topic == "Bron")
        assert not bron.critical


class TestSensitivity:
    def test_quota_driven_tuning_is_disclosed(self, normbeeld):
        """De lijst is quota-gestuurd: er verschijnen altijd bevindingen.
        Dat hoort een lezer te weten."""
        sens = next(a for a in collect(normbeeld, sensitivity="soepel")
                    if a.topic == "Gevoeligheid")
        assert sens.critical
        assert "quota" in sens.if_wrong

    def test_absent_when_not_applicable(self, normbeeld):
        topics = [a.topic for a in collect(normbeeld)]
        assert "Gevoeligheid" not in topics


class TestPresentation:
    def test_every_assumption_states_its_consequence(self, normbeeld):
        for a in collect(normbeeld, sensitivity="normaal",
                         source_reliability="C"):
            assert a.if_wrong, f"{a.topic} mist een gevolg"
            assert a.basis
            assert a.statement

    def test_line_marks_critical_items(self, normbeeld):
        crit = critical_only(collect(normbeeld, gap_policy="zero"))
        assert crit
        assert crit[0].as_line().startswith("⚠")

    def test_summary_names_the_risky_ones(self, normbeeld):
        text = summarise(collect(normbeeld, gap_policy="zero"))
        assert "Gap-beleid" in text

    def test_summary_without_normbeeld_still_works(self):
        items = collect(None, gap_policy="mask")
        assert items
        assert summarise(items)

    def test_summary_of_nothing(self):
        assert "Geen aannames" in summarise([])
