"""Composition of written assessments.

The tests here are mostly about restraint: what the composer must *not* say.
Prose is the layer where a careful system most easily becomes an overconfident
one, because fluent wording reads as authority regardless of what is behind it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from sentinel.core.contracts import (
    Confidence,
    ConfidenceLevel,
    DateRange,
    DetectionPower,
    Direction,
    Evidence,
    EvidenceKind,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    MonitoringStatus,
    RegionStatus,
    Signal,
    Verdict,
)
from sentinel.report import compose, compose_region

AS_OF = datetime(2026, 8, 10)
CONF = Confidence(level=ConfidenceLevel.MODERATE, reasons=("one source only",))
REFERENCE = DateRange(datetime(2015, 1, 1), datetime(2021, 12, 31))


def _indicator(test_type=IndicatorTest.LEVEL_DEVIATION, **overrides) -> Indicator:
    kwargs = dict(
        key="k", region_key="euro_atlantic", name="Strike tempo",
        question="q?", meaning="m", test_type=test_type,
        status=IndicatorStatus.ACTIVE)
    if test_type is IndicatorTest.SUSTAINED_DIVERGENCE:
        kwargs["reference_period"] = REFERENCE
    if test_type is IndicatorTest.ENTITY_BEHAVIOUR:
        kwargs["entity_kind"] = "vessel"
    kwargs.update(overrides)
    return Indicator(**kwargs)


def _active(indicator, effect_size=2.4, evidence=()) -> Signal:
    return Signal(indicator_key=indicator.key, as_of=AS_OF,
                  verdict=Verdict.ACTIVE, confidence=CONF,
                  effect_size=effect_size, direction=Direction.ABOVE,
                  evidence=evidence)


def _quiet(indicator, power=None) -> Signal:
    return Signal(indicator_key=indicator.key, as_of=AS_OF,
                  verdict=Verdict.NOT_ACTIVE, confidence=CONF,
                  detection_power=power or DetectionPower("spike", 5.0))


def _untested(indicator, reason="only 2 observed periods, 5 needed") -> Signal:
    return Signal(indicator_key=indicator.key, as_of=AS_OF,
                  verdict=Verdict.INSUFFICIENT_DATA, confidence=CONF,
                  insufficient_reason=reason)


# =========================================================================
# the composer is not a second truth model
# =========================================================================
def test_composition_never_changes_the_verdict():
    for signal in (_active(_indicator()), _quiet(_indicator()),
                   _untested(_indicator())):
        assert compose(signal, _indicator()).signal.verdict is signal.verdict


def test_composing_across_two_indicators_is_refused():
    """Attaching one finding's reasoning to another's verdict."""
    signal = _active(_indicator())
    with pytest.raises(ValueError, match="composing across"):
        compose(signal, _indicator(key="other"))


# =========================================================================
# every verdict gets written product
# =========================================================================
@pytest.mark.parametrize("test_type", list(IndicatorTest))
def test_every_test_type_produces_an_active_assessment(test_type):
    """The contract requires an alternative and a follow-up for an active
    signal, so a test type the composer forgot would raise here."""
    indicator = _indicator(test_type)
    assessment = compose(_active(indicator), indicator)
    assert assessment.alternatives
    assert assessment.follow_up
    assert assessment.format()


def test_a_null_result_states_what_would_have_been_caught():
    indicator = _indicator()
    text = compose(_quiet(indicator), indicator).format()
    assert "No significant deviation" in text
    assert "5x or larger" in text


def test_an_untestable_indicator_is_still_written_up():
    """Otherwise only alarming outcomes ever reach the page."""
    indicator = _indicator()
    text = compose(_untested(indicator), indicator).format()
    assert "could not be tested" in text
    assert "only 2 observed periods" in text


def test_untestable_does_not_render_as_a_confident_judgement():
    """"Confidence is high" under "could not be tested" reads as "we are
    confident nothing happened", which is the opposite of the truth."""
    indicator = _indicator()
    signal = Signal(indicator_key=indicator.key, as_of=AS_OF,
                    verdict=Verdict.INSUFFICIENT_DATA,
                    confidence=Confidence(level=ConfidenceLevel.HIGH,
                                          reasons=("sources agree",)),
                    insufficient_reason="the feed delivered nothing")
    text = compose(signal, indicator).format()
    assert "Confidence is high" not in text
    assert "No judgement was reached" in text


# =========================================================================
# the probability rule — the thing most likely to be quoted
# =========================================================================
def test_standardised_tests_get_a_probability():
    indicator = _indicator(IndicatorTest.LEVEL_DEVIATION)
    assessment = compose(_active(indicator, effect_size=3.5), indicator)
    assert assessment.probability is not None
    assert assessment.probability < 0.01
    assert "a value like this is" in assessment.format()


def test_a_period_count_is_never_rendered_as_a_probability():
    """`condition:silence` reports its effect size in *periods*. A normal
    tail over that would produce a confident-looking number about nothing."""
    indicator = _indicator(IndicatorTest.CONDITION,
                           test_config={"rule": "silence", "periods": 5})
    assessment = compose(_active(indicator, effect_size=5.0), indicator)
    assert assessment.probability is None
    assert "a value like this is" not in assessment.format()


def test_a_peer_ranking_score_is_never_rendered_as_a_probability():
    indicator = _indicator(IndicatorTest.ENTITY_BEHAVIOUR)
    assessment = compose(_active(indicator, effect_size=0.97), indicator)
    assert assessment.probability is None


def test_larger_deviations_are_rarer():
    indicator = _indicator()
    small = compose(_active(indicator, effect_size=2.0), indicator).probability
    large = compose(_active(indicator, effect_size=4.0), indicator).probability
    assert large < small


# =========================================================================
# what the prose owes the reader
# =========================================================================
def test_the_signals_own_alternatives_win_over_the_defaults():
    """A test that worked out why *this* signal might be wrong knows more
    than a table keyed on the test type."""
    indicator = _indicator()
    signal = _active(indicator, evidence=(
        Evidence(kind=EvidenceKind.ALTERNATIVE,
                 summary="the reporting agency changed methodology in June",
                 weight=-0.3),))
    assert compose(signal, indicator).alternatives == (
        "the reporting agency changed methodology in June",)


def test_an_alternative_is_offered_even_when_the_signal_carries_none():
    indicator = _indicator()
    alternatives = compose(_active(indicator), indicator).alternatives
    assert alternatives
    assert all(len(a) > 30 for a in alternatives), (
        "a generic alternative satisfies the contract while informing "
        "nobody, which turns the requirement into a rubber stamp")


def test_supporting_evidence_is_rendered_not_just_stored():
    indicator = _indicator()
    signal = _active(indicator, evidence=(
        Evidence(kind=EvidenceKind.CONTEXT,
                 summary="observed 41 against an expected 12.0", weight=0.0),))
    assert "observed 41 against an expected 12.0" in compose(
        signal, indicator).format()


def test_alternatives_are_not_repeated_as_supporting_evidence():
    indicator = _indicator()
    signal = _active(indicator, evidence=(
        Evidence(kind=EvidenceKind.ALTERNATIVE, summary="a source change",
                 weight=-0.3),))
    text = compose(signal, indicator).format()
    assert text.count("a source change") == 1


def test_the_baseline_is_always_named():
    """A deviation whose comparison is unstated cannot be argued with."""
    for test_type in IndicatorTest:
        indicator = _indicator(test_type)
        assert compose(_active(indicator), indicator).baseline_description


def test_the_declared_reference_period_appears_verbatim():
    indicator = _indicator(IndicatorTest.SUSTAINED_DIVERGENCE)
    assert "2015-01-01 to 2021-12-31" in compose(
        _active(indicator), indicator).format()


def test_entity_findings_describe_behaviour_not_intent():
    indicator = _indicator(IndicatorTest.ENTITY_BEHAVIOUR)
    text = compose(_active(indicator), indicator).format().lower()
    for word in ("sabotage", "deliberate", "hostile", "intent to", "attack"):
        assert word not in text
    assert "behaviour is not intent" in text


def test_scenario_slugs_do_not_reach_the_page():
    """'adaptation_failure' names a failure mode being probed; on an
    analyst's page it reads as a fault in the tool."""
    indicator = _indicator()
    text = compose(_quiet(indicator, DetectionPower("adaptation_failure", 1.5)),
                   indicator).format()
    assert "adaptation_failure" not in text
    assert "a sustained increase of 1.5x" in text.lower()


# =========================================================================
# the region product
# =========================================================================
def _region(signals, monitoring=MonitoringStatus.MONITORED,
            requirements=()) -> RegionStatus:
    return RegionStatus(region_key="euro_atlantic", name="Euro-Atlantic",
                        monitoring=monitoring, signals=signals,
                        activation_requirements=requirements)


def test_region_report_leads_with_the_headline():
    indicator = _indicator()
    report = compose_region(_region((_active(indicator),)),
                            {indicator.key: indicator})
    assert report.splitlines()[0].startswith("Euro-Atlantic: 1 of 1")


def test_untestable_indicators_are_listed_not_dropped():
    """They are often the most consequential thing on the page."""
    indicator = _indicator()
    report = compose_region(_region((_untested(indicator),)),
                            {indicator.key: indicator})
    assert "Could not be tested" in report
    assert "no significant activity" not in report.lower()


def test_an_unmonitored_region_produces_a_headline_and_nothing_else():
    """Section headings under an unwatched region would imply someone looked."""
    report = compose_region(_region((), MonitoringStatus.NOT_MONITORED,
                                    ("a parser for the daily releases",)))
    assert "not monitored" in report
    assert "##" not in report


def test_a_missing_indicator_definition_is_admitted_not_invented():
    indicator = _indicator()
    report = compose_region(_region((_active(indicator),)), {})
    assert "cannot be written up" in report


def test_active_findings_come_before_quiet_ones():
    active, quiet = _indicator(key="a"), _indicator(key="b")
    report = compose_region(
        _region((_quiet(quiet), _active(active))),
        {"a": active, "b": quiet})
    assert report.index("## Active") < report.index("## Tested and quiet")
