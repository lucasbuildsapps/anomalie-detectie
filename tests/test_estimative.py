"""Tests voor gestandaardiseerde onzekerheidstaal (ICD 203 / NATO).

De kern: waarschijnlijkheid en zekerheid zijn twee verschillende dingen
en mogen niet in één zin. Zonder die scheiding weet de lezer niet wát er
onzeker is — de gebeurtenis of het oordeel erover.
"""
import pytest

from core.estimative import (
    LCA_DEFINITIONS,
    WEP_RANGES,
    Assessment,
    assess_confidence,
    exceedance_probability,
    format_judgment,
    violates_separation,
    wep_phrase,
    wep_term,
)


class TestEstimativeProbability:
    @pytest.mark.parametrize("p,expected", [
        (0.00, "zeer onwaarschijnlijk"),
        (0.05, "zeer onwaarschijnlijk"),
        (0.25, "onwaarschijnlijk"),
        (0.50, "ongeveer even waarschijnlijk"),
        (0.75, "waarschijnlijk"),
        (0.95, "zeer waarschijnlijk"),
        (1.00, "zeer waarschijnlijk"),
    ])
    def test_scale_boundaries(self, p, expected):
        assert wep_term(p) == expected

    def test_out_of_range_is_clamped(self):
        assert wep_term(-5) == "zeer onwaarschijnlijk"
        assert wep_term(42) == "zeer waarschijnlijk"

    def test_phrase_includes_the_range(self):
        """Een term zonder bereik laat te veel ruimte voor eigen invulling."""
        phrase = wep_phrase(0.03)
        assert "zeer onwaarschijnlijk" in phrase
        assert "< 10%" in phrase

    def test_every_term_has_a_documented_range(self):
        for _, term in [(0, t) for t in WEP_RANGES]:
            assert WEP_RANGES[term]


class TestExceedance:
    def test_high_percentile_above_band_is_rare(self):
        assert exceedance_probability(0.99, "boven") == pytest.approx(0.01)

    def test_low_percentile_below_band_is_rare(self):
        assert exceedance_probability(0.01, "onder") == pytest.approx(0.01)

    def test_middle_is_unremarkable(self):
        assert exceedance_probability(0.5, "boven") == pytest.approx(0.5)


class TestConfidenceAssessment:
    def test_good_conditions_give_high(self):
        level, _ = assess_confidence(
            coverage=0.97, target_coverage=0.98, n_periods=400,
            data_coverage=0.95, staleness_days=2, source_reliability="B",
            effective_methods=4.5, regime_stable=True,
        )
        assert level == "hoog"

    def test_stale_and_sparse_gives_low(self):
        level, reasons = assess_confidence(
            n_periods=15, data_coverage=0.4, staleness_days=120,
            source_reliability="E", effective_methods=1.2,
        )
        assert level == "laag"
        assert any("oud" in r for r in reasons)
        assert any("korte reeks" in r for r in reasons)

    def test_regime_change_dominates(self):
        """Een verse regimewissel maakt zelfs een verder perfecte reeks
        onzeker: het normbeeld kent het nieuwe niveau nog niet."""
        level, reasons = assess_confidence(
            coverage=0.98, target_coverage=0.98, n_periods=500,
            data_coverage=0.99, staleness_days=1, source_reliability="A",
            effective_methods=5.0, regime_stable=False,
        )
        assert level in ("gemiddeld", "laag")
        assert any("regimewissel" in r for r in reasons)

    def test_miscalibrated_band_is_penalised(self):
        level, reasons = assess_confidence(
            coverage=0.60, target_coverage=0.98, n_periods=200,
        )
        assert any("dekt" in r for r in reasons)
        assert level in ("gemiddeld", "laag")

    def test_reasons_are_never_empty(self):
        _, reasons = assess_confidence(n_periods=100)
        assert reasons and all(isinstance(r, str) for r in reasons)

    def test_level_is_always_valid(self):
        for kwargs in ({}, {"n_periods": 5}, {"effective_methods": 5.0}):
            level, _ = assess_confidence(**kwargs)
            assert level in LCA_DEFINITIONS


class TestSeparationRule:
    def test_mixed_sentence_is_flagged(self):
        assert violates_separation(
            "Dit is waarschijnlijk een aanval, met hoge zekerheid.")

    def test_probability_alone_is_fine(self):
        assert not violates_separation(
            "Een waarde als deze is zeer onwaarschijnlijk onder het normbeeld.")

    def test_confidence_alone_is_fine(self):
        assert not violates_separation(
            "Het vertrouwen in deze beoordeling is hoog vertrouwen.")

    def test_formatted_judgment_never_mixes(self):
        """De formatter moet de scheiding afdwingen, niet alleen adviseren."""
        assessment = Assessment(
            statement="Een waarde als deze in Kharkiv",
            probability=0.02,
            confidence="hoog",
            confidence_reasons=["lange reeks", "goed gekalibreerde band"],
        )
        text = format_judgment(assessment)
        for sentence in text.split("\n"):
            assert not violates_separation(sentence), sentence

    def test_judgment_states_both_scales_separately(self):
        assessment = Assessment(
            statement="Deze waarneming",
            probability=0.02,
            confidence="gemiddeld",
            confidence_reasons=["data 45 dagen oud"],
        )
        text = format_judgment(assessment)
        assert "zeer onwaarschijnlijk" in text
        assert "gemiddeld" in text
        assert "data 45 dagen oud" in text
        assert len(text.split("\n")) == 2

    def test_judgment_without_probability_still_reports_confidence(self):
        text = format_judgment(Assessment(
            statement="Onvoldoende data voor een uitspraak",
            probability=None, confidence="laag",
            confidence_reasons=["korte reeks"],
        ))
        assert "laag" in text
        assert "korte reeks" in text
