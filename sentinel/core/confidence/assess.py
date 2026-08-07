"""Confidence from measurable inputs, not from self-assessment.

v1's confidence was, in effect, a series-length check wearing ICD 203
clothing: 60 or more periods plus a coverage figure the model had tuned
itself against produced "high" almost automatically. Measured directly, a
365-period series with on-target coverage scored high with the single reason
"no particular issues" — during a fivefold escalation.

Two rules follow, and they shape everything here:

1. **Series length is not an input.** It is not a field on `ConfidenceInputs`
   and a test asserts it never becomes one. Length tells you how much data
   there is, not whether the judgement is sound.
2. **Nothing self-calibrated counts.** Calibration must be measured on a
   holdout the model did not tune against, which is why `calibration_gap`
   comes from the evaluation harness rather than from the fit.

Scoring
-------
Four pillars, each scoring in [-1, +1] or abstaining when its inputs are
missing. The level comes from the mean of the pillars that actually spoke,
with caps applied afterwards.

Abstention is the important part. v1 treated a missing input as neutral,
which let a judgement resting on almost nothing drift upward. Here, few
pillars means a cap: you cannot reach high confidence on two inputs, however
good those two look.
"""
from __future__ import annotations

from dataclasses import dataclass

from sentinel.core.contracts import (
    Confidence,
    ConfidenceInputs,
    ConfidenceLevel,
    Reliability,
)

__all__ = ["PillarScore", "assess_confidence", "score_pillars"]

#: Below this many contributing pillars, high confidence is not available.
_MIN_PILLARS_FOR_HIGH = 3


@dataclass(frozen=True)
class PillarScore:
    """One pillar's verdict, or its abstention."""

    name: str
    score: float | None          # None = no inputs available
    reason: str | None = None    # stated only when it moved the outcome

    @property
    def spoke(self) -> bool:
        return self.score is not None


def _data_quality(inputs: ConfidenceInputs) -> PillarScore:
    parts: list[float] = []
    problems: list[str] = []

    if inputs.data_coverage is not None:
        coverage = float(inputs.data_coverage)
        if coverage >= 0.9:
            parts.append(1.0)
        elif coverage >= 0.7:
            parts.append(0.0)
        else:
            parts.append(-1.0)
            problems.append(f"only {coverage:.0%} of periods carry data")

    if inputs.staleness_days is not None:
        days = int(inputs.staleness_days)
        if days <= 7:
            parts.append(1.0)
        elif days <= 30:
            parts.append(0.0)
        else:
            parts.append(-1.0)
            problems.append(f"most recent data is {days} days old")

    if inputs.source_reliability is not None:
        grade = inputs.source_reliability
        if grade in (Reliability.A, Reliability.B):
            parts.append(1.0)
        elif grade is Reliability.C:
            parts.append(0.0)
        else:
            parts.append(-1.0)
            # F is "cannot be judged", which is not the same as unreliable,
            # but it is equally unable to support a confident judgement.
            problems.append(f"source graded {grade.value}")

    if not parts:
        return PillarScore("data quality", None)
    return PillarScore("data quality", sum(parts) / len(parts),
                       "; ".join(problems) or None)


def _calibration(inputs: ConfidenceInputs) -> PillarScore:
    if inputs.calibration_gap is None:
        return PillarScore("calibration", None)
    gap = float(inputs.calibration_gap)
    if gap <= 0.03:
        return PillarScore("calibration", 1.0)
    if gap <= 0.10:
        return PillarScore("calibration", 0.0)
    return PillarScore(
        "calibration", -1.0,
        f"band coverage is {gap:.0%} away from its target out of sample")


def _performance(inputs: ConfidenceInputs) -> PillarScore:
    precision = inputs.historical_precision
    recall = inputs.historical_recall
    if precision is None and recall is None:
        return PillarScore("historical performance", None)

    parts: list[float] = []
    problems: list[str] = []
    for name, value in (("precision", precision), ("recall", recall)):
        if value is None:
            continue
        value = float(value)
        if value >= 0.8:
            parts.append(1.0)
        elif value >= 0.5:
            parts.append(0.0)
        else:
            parts.append(-1.0)
            problems.append(f"measured {name} {value:.0%} on this indicator")
    return PillarScore("historical performance", sum(parts) / len(parts),
                       "; ".join(problems) or None)


def _corroboration(inputs: ConfidenceInputs) -> PillarScore:
    if inputs.effective_corroboration is None:
        return PillarScore("corroboration", None)
    effective = float(inputs.effective_corroboration)
    if effective >= 3.0:
        return PillarScore("corroboration", 1.0)
    if effective >= 2.0:
        return PillarScore("corroboration", 0.0)
    return PillarScore(
        "corroboration", -1.0,
        f"only {effective:.1f} effectively independent sources")


def score_pillars(inputs: ConfidenceInputs) -> list[PillarScore]:
    """All four pillars, including the ones that abstained."""
    return [
        _data_quality(inputs),
        _calibration(inputs),
        _performance(inputs),
        _corroboration(inputs),
    ]


def assess_confidence(inputs: ConfidenceInputs) -> Confidence:
    """Build an ICD 203 confidence level with its grounds.

    Caps are applied after scoring, and each one has the same shape of
    argument behind it: some fact makes the *basis* of the judgement weak
    regardless of how the measurable pillars came out.
    """
    pillars = score_pillars(inputs)
    spoke = [p for p in pillars if p.spoke]
    reasons: list[str] = [p.reason for p in pillars if p.reason]

    if not spoke:
        return Confidence(
            level=ConfidenceLevel.LOW,
            reasons=("nothing measurable was available to assess this "
                     "judgement against",),
            inputs=inputs,
        )

    mean = sum(float(p.score) for p in spoke) / len(spoke)
    if mean >= 0.5:
        level = ConfidenceLevel.HIGH
    elif mean >= -0.25:
        level = ConfidenceLevel.MODERATE
    else:
        level = ConfidenceLevel.LOW

    # --- caps ----------------------------------------------------------
    # High confidence is conjunctive, not an average. ICD 203 reserves it for
    # good quality *and* corroboration *and* unambiguity; a mean lets one
    # strong criterion launder a weak one, so three perfect pillars could
    # carry data that is three months stale. Any adverse finding — which is
    # exactly when a pillar states a reason — caps the level.
    if reasons and level is ConfidenceLevel.HIGH:
        level = ConfidenceLevel.MODERATE

    if len(spoke) < _MIN_PILLARS_FOR_HIGH and level is ConfidenceLevel.HIGH:
        level = ConfidenceLevel.MODERATE
        reasons.append(
            f"only {len(spoke)} of {len(pillars)} quality criteria could be "
            f"assessed")

    if inputs.reconstruction_faithful is False:
        # The point-in-time reconstruction rests on assumed arrival times, so
        # any historical performance figure behind this is optimistic by an
        # unknown margin.
        if level is ConfidenceLevel.HIGH:
            level = ConfidenceLevel.MODERATE
        reasons.append("arrival times were assumed rather than observed, so "
                       "historical performance here is optimistic")

    if inputs.regime_stable is False:
        # Every other quality signal describes the *old* regime. However long
        # and clean the series, the baseline is not describing what is
        # happening now.
        if level is ConfidenceLevel.HIGH:
            level = ConfidenceLevel.MODERATE
        reasons.append("recent regime change; the baseline has not settled")

    if not reasons:
        reasons.append(
            f"{len(spoke)} of {len(pillars)} quality criteria assessed, none "
            f"of them adverse")

    return Confidence(level=level, reasons=tuple(reasons), inputs=inputs)
