"""Confidence scoring: what it must and must not be built from.

The tests that matter most here are negative. v1's confidence was effectively
a length check, and the failure mode was silent — "high" with the single
reason "no particular issues", during a fivefold escalation. So these check
what cannot influence the score as hard as what can.
"""
from __future__ import annotations

import pytest

from sentinel.core.confidence import assess_confidence, score_pillars
from sentinel.core.contracts import (
    ConfidenceInputs,
    ConfidenceLevel,
    Reliability,
)


def _good(**overrides) -> ConfidenceInputs:
    kwargs = dict(
        data_coverage=0.98,
        staleness_days=1,
        calibration_gap=0.01,
        historical_precision=0.9,
        historical_recall=0.85,
        effective_corroboration=3.5,
        source_reliability=Reliability.B,
        reconstruction_faithful=True,
        regime_stable=True,
    )
    kwargs.update(overrides)
    return ConfidenceInputs(**kwargs)


# =========================================================================
# what it must NOT be built from
# =========================================================================
def test_series_length_is_not_an_input():
    """The v1 failure, blocked at the type level."""
    fields = set(vars(ConfidenceInputs()))
    for banned in ("n_periods", "series_length", "n_rows", "n_observations"):
        assert banned not in fields


def test_no_inputs_gives_low_confidence_not_a_default():
    """Absence of evidence must not read as evidence of quality."""
    confidence = assess_confidence(ConfidenceInputs())
    assert confidence.level is ConfidenceLevel.LOW
    assert "nothing measurable" in confidence.reasons[0]


def test_a_single_excellent_input_cannot_reach_high():
    """Two pillars is not a basis for a confident judgement, however good."""
    confidence = assess_confidence(ConfidenceInputs(calibration_gap=0.0))
    assert confidence.level is not ConfidenceLevel.HIGH
    assert any("criteria could be assessed" in r for r in confidence.reasons)


# =========================================================================
# the four pillars
# =========================================================================
def test_all_pillars_good_gives_high():
    confidence = assess_confidence(_good())
    assert confidence.level is ConfidenceLevel.HIGH


def test_pillars_abstain_rather_than_scoring_neutral():
    pillars = {p.name: p for p in score_pillars(ConfidenceInputs())}
    assert all(not p.spoke for p in pillars.values())
    partial = score_pillars(ConfidenceInputs(calibration_gap=0.01))
    assert sum(p.spoke for p in partial) == 1


def test_stale_data_lowers_confidence_and_says_so():
    confidence = assess_confidence(_good(staleness_days=90))
    assert confidence.level is not ConfidenceLevel.HIGH
    assert any("90 days old" in r for r in confidence.reasons)


def test_poor_coverage_lowers_confidence():
    confidence = assess_confidence(_good(data_coverage=0.4))
    assert any("40% of periods" in r for r in confidence.reasons)


def test_unreliable_source_lowers_confidence():
    confidence = assess_confidence(_good(source_reliability=Reliability.E))
    assert any("graded E" in r for r in confidence.reasons)


def test_ungradable_source_counts_against_not_neutral():
    """'Cannot be judged' cannot support a confident judgement either."""
    confidence = assess_confidence(_good(source_reliability=Reliability.F))
    assert any("graded F" in r for r in confidence.reasons)


def test_bad_measured_performance_lowers_confidence():
    confidence = assess_confidence(_good(historical_precision=0.2,
                                         historical_recall=0.3))
    assert confidence.level is not ConfidenceLevel.HIGH
    assert any("precision 20%" in r for r in confidence.reasons)


def test_weak_corroboration_lowers_confidence():
    confidence = assess_confidence(_good(effective_corroboration=1.0))
    assert any("independent sources" in r for r in confidence.reasons)


def test_poor_calibration_lowers_confidence():
    confidence = assess_confidence(_good(calibration_gap=0.25))
    assert any("away from its target" in r for r in confidence.reasons)


# =========================================================================
# caps
# =========================================================================
def test_assumed_arrival_times_cap_confidence():
    """A replay on assumed arrivals is optimistic by an unknown margin."""
    confidence = assess_confidence(_good(reconstruction_faithful=False))
    assert confidence.level is not ConfidenceLevel.HIGH
    assert any("assumed rather than observed" in r for r in confidence.reasons)


def test_regime_change_caps_confidence():
    """Every other quality signal describes the regime that just ended."""
    confidence = assess_confidence(_good(regime_stable=False))
    assert confidence.level is not ConfidenceLevel.HIGH
    assert any("regime change" in r for r in confidence.reasons)


def test_caps_apply_even_when_every_pillar_is_perfect():
    confidence = assess_confidence(_good(reconstruction_faithful=False,
                                         regime_stable=False))
    assert confidence.level is ConfidenceLevel.MODERATE


# =========================================================================
# contract conformance
# =========================================================================
def test_confidence_always_carries_reasons():
    """The contract requires it; check every branch actually supplies them."""
    for inputs in (ConfidenceInputs(), _good(),
                   _good(regime_stable=False),
                   _good(data_coverage=0.1, staleness_days=400)):
        assert assess_confidence(inputs).reasons


def test_inputs_are_carried_through_for_inspection():
    inputs = _good()
    assert assess_confidence(inputs).inputs is inputs


@pytest.mark.parametrize("coverage,expected_worse", [(0.95, False), (0.4, True)])
def test_confidence_is_monotone_in_data_quality(coverage, expected_worse):
    baseline = assess_confidence(_good())
    candidate = assess_confidence(_good(data_coverage=coverage))
    order = [ConfidenceLevel.LOW, ConfidenceLevel.MODERATE, ConfidenceLevel.HIGH]
    if expected_worse:
        assert order.index(candidate.level) <= order.index(baseline.level)
    else:
        assert candidate.level is baseline.level
