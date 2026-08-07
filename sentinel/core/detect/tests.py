"""The four indicator tests. One indicator, one test, one verdict.

This is where everything built so far becomes a product output. The baselines
say what was expected, the harness says what could have been caught, the
contracts say what a defensible judgement looks like — and this module
assembles them into a `Signal`.

The consequence worth noticing
------------------------------
`Signal` refuses to be constructed as NOT_ACTIVE without `DetectionPower`.
That is not a nuisance to route around: it means the system **cannot claim
nothing is happening unless it has measured what it would have caught**.
When power has not been measured for a configuration, the honest verdict is
INSUFFICIENT_DATA — "I cannot tell you it is quiet, because I do not know
how loud something would have to be for me to hear it."

v1 could not express that at all. It always had something to say, and the
quota guaranteed it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from sentinel.core.baseline import (
    BaselineFit,
    DivergenceConfig,
    DivergenceState,
    assess_divergence,
)
from sentinel.core.confidence import assess_confidence
from sentinel.core.contracts import (
    Confidence,
    ConfidenceInputs,
    DetectionPower,
    Direction,
    Evidence,
    EvidenceKind,
    Indicator,
    IndicatorTest,
    Signal,
    Verdict,
)

__all__ = ["IndicatorContext", "evaluate_indicator"]


@dataclass(frozen=True)
class IndicatorContext:
    """Everything a test needs, assembled by the caller.

    The tests take a context rather than fetching anything themselves. That
    keeps them free of storage, free of `as_of` bookkeeping, and testable
    without a database — and it is what the causal-boundary test enforces.
    """

    indicator: Indicator
    series: pd.Series
    as_of: datetime
    adaptive: BaselineFit
    reference: BaselineFit | None = None
    power: DetectionPower | None = None
    inputs: ConfidenceInputs = field(default_factory=ConfidenceInputs)
    divergence_config: DivergenceConfig | None = None

    @property
    def confidence(self) -> Confidence:
        return assess_confidence(self.inputs)


def _insufficient(context: IndicatorContext, reason: str) -> Signal:
    return Signal(
        indicator_key=context.indicator.key,
        as_of=context.as_of,
        verdict=Verdict.INSUFFICIENT_DATA,
        confidence=context.confidence,
        insufficient_reason=reason,
    )


def _quiet(context: IndicatorContext,
           evidence: tuple[Evidence, ...] = ()) -> Signal:
    """A null result — only constructible with measured detection power.

    Without power we do not downgrade the claim quietly; we say plainly that
    we cannot support it. A "nothing found" that has never been calibrated is
    the most expensive kind of reassurance.
    """
    if context.power is None:
        return _insufficient(
            context,
            "detection power has not been measured for this configuration, "
            "so a null result cannot be supported",
        )
    return Signal(
        indicator_key=context.indicator.key,
        as_of=context.as_of,
        verdict=Verdict.NOT_ACTIVE,
        confidence=context.confidence,
        detection_power=context.power,
        evidence=evidence,
    )


def _active(context: IndicatorContext, effect_size: float, direction: Direction,
            evidence: tuple[Evidence, ...] = ()) -> Signal:
    return Signal(
        indicator_key=context.indicator.key,
        as_of=context.as_of,
        verdict=Verdict.ACTIVE,
        confidence=context.confidence,
        effect_size=float(effect_size),
        direction=direction,
        detection_power=context.power,
        evidence=evidence,
    )


def _latest_usable(context: IndicatorContext) -> int | None:
    """Index of the most recent period the baseline can actually judge."""
    usable = context.adaptive.is_usable
    positions = np.flatnonzero(usable.to_numpy())
    return int(positions[-1]) if positions.size else None


# =========================================================================
# level_deviation
# =========================================================================
def _test_level_deviation(context: IndicatorContext) -> Signal:
    """Is the latest period unusual against the recent baseline?"""
    threshold = float(context.indicator.test_config.get("threshold", 3.5))
    position = _latest_usable(context)
    if position is None:
        return _insufficient(
            context, "no period has enough history behind it to be judged")

    deviation = context.adaptive.deviation(context.series)
    value = deviation.iloc[position]
    if not np.isfinite(value):
        return _insufficient(
            context,
            f"the most recent judgeable period "
            f"({deviation.index[position].date()}) has no observation")

    if abs(value) <= threshold:
        return _quiet(context)

    return _active(
        context,
        effect_size=abs(float(value)),
        direction=Direction.ABOVE if value > 0 else Direction.BELOW,
        evidence=(
            Evidence(
                kind=EvidenceKind.CONTEXT,
                summary=(
                    f"observed {context.series.iloc[position]:.0f} against an "
                    f"expected {context.adaptive.expected.iloc[position]:.1f} "
                    f"({abs(value):.1f} scale units away)"),
                weight=0.0,
            ),
        ),
    )


# =========================================================================
# sustained_divergence
# =========================================================================
def _test_sustained_divergence(context: IndicatorContext) -> Signal:
    """Has activity run above the declared reference long enough to matter?

    The test that answers the question a purely adaptive system cannot: not
    "is today unusual" but "has the definition of normal quietly moved".
    """
    if context.reference is None:
        return _insufficient(
            context,
            "no reference baseline is available, so a sustained shift cannot "
            "be distinguished from a new normal",
        )

    result = assess_divergence(context.series, context.adaptive,
                               context.reference, context.divergence_config)
    state = result.latest_state()

    if state is DivergenceState.UNTESTED:
        return _insufficient(
            context, "no period has enough history behind it to be judged")

    if state in (DivergenceState.NORMALISED_ESCALATION, DivergenceState.ACUTE):
        run = int(result.sustained_run.iloc[-1])
        reference_dev = float(result.reference_deviation.iloc[-1])
        evidence = [
            Evidence(
                kind=EvidenceKind.CONTEXT,
                summary=(f"{run} consecutive periods away from the declared "
                         f"reference; currently {abs(reference_dev):.1f} "
                         f"scale units from it"),
                weight=0.0,
            ),
        ]
        if state is DivergenceState.NORMALISED_ESCALATION:
            evidence.append(Evidence(
                kind=EvidenceKind.CONTEXT,
                summary=("the recent baseline has adapted to this level, so "
                         "no per-period alert is firing; the quiet is "
                         "habituation, not a return to normal"),
                weight=0.0,
            ))
        evidence.append(Evidence(
            kind=EvidenceKind.ALTERNATIVE,
            summary=("a change in reporting or collection would produce the "
                     "same level shift; check source continuity before "
                     "treating this as a change in the world"),
            weight=-0.3,
        ))
        return _active(
            context,
            effect_size=abs(reference_dev),
            direction=Direction.ABOVE if reference_dev > 0 else Direction.BELOW,
            evidence=tuple(evidence),
        )

    if state is DivergenceState.INCIDENT:
        # A per-period excursion is a level_deviation finding, not a sustained
        # one. Reporting it here would let one indicator answer two questions.
        return _quiet(context, evidence=(
            Evidence(
                kind=EvidenceKind.CONTEXT,
                summary=("an isolated excursion was seen but the level has "
                         "not shifted against the reference"),
                weight=0.0,
            ),
        ))

    return _quiet(context)


# =========================================================================
# condition
# =========================================================================
def _test_condition(context: IndicatorContext) -> Signal:
    """A deterministic, pre-registered rule.

    No baseline, no statistics — an analyst wrote down a condition and we
    check it. Absence counts: `silence` fires when activity stops where it
    normally is not zero, which is a classic warning signal and a systematic
    blind spot of anything built around peak-finding.
    """
    config = context.indicator.test_config
    rule = str(config.get("rule", "")).strip()
    periods = max(1, int(config.get("periods", 1)))
    threshold = config.get("threshold")

    series = context.series.dropna()
    if len(series) < periods:
        return _insufficient(
            context,
            f"only {len(series)} observed periods, {periods} needed for this "
            f"condition")

    recent = series.iloc[-periods:]
    if rule == "above":
        if threshold is None:
            return _insufficient(context, "rule 'above' needs a threshold")
        met = bool((recent > float(threshold)).all())
        effect = float(recent.mean() - float(threshold))
        direction = Direction.ABOVE
    elif rule == "below":
        if threshold is None:
            return _insufficient(context, "rule 'below' needs a threshold")
        met = bool((recent < float(threshold)).all())
        effect = float(float(threshold) - recent.mean())
        direction = Direction.BELOW
    elif rule == "silence":
        met = bool((recent == 0).all())
        effect = float(periods)
        direction = Direction.BELOW
    else:
        return _insufficient(context, f"unknown condition rule {rule!r}")

    if not met:
        # A deterministic rule has perfect detection power by construction:
        # it either holds or it does not, and there is no effect size below
        # which it would be missed. Saying so explicitly keeps the null
        # result honest without pretending a measurement happened.
        return Signal(
            indicator_key=context.indicator.key,
            as_of=context.as_of,
            verdict=Verdict.NOT_ACTIVE,
            confidence=context.confidence,
            detection_power=DetectionPower(
                scenario_kind=f"condition:{rule}",
                floor_magnitude=0.0, threshold=1.0, n_repeats=0,
            ),
        )

    return _active(
        context, effect_size=abs(effect), direction=direction,
        evidence=(
            Evidence(
                kind=EvidenceKind.CONTEXT,
                summary=(f"condition '{rule}' held for {periods} consecutive "
                         f"period(s) to {recent.index[-1].date()}"),
                weight=0.0,
            ),
        ),
    )


# =========================================================================
# dispatch
# =========================================================================
_TESTS = {
    IndicatorTest.LEVEL_DEVIATION: _test_level_deviation,
    IndicatorTest.SUSTAINED_DIVERGENCE: _test_sustained_divergence,
    IndicatorTest.CONDITION: _test_condition,
}


def evaluate_indicator(context: IndicatorContext) -> Signal:
    """Run the indicator's declared test and return its verdict.

    One indicator, one test, one verdict. There is no voting and no second
    opinion: an indicator that needs a different question asked needs a
    different indicator.
    """
    indicator: Indicator = context.indicator
    if not indicator.is_active:
        return _insufficient(
            context, f"indicator is {indicator.status.value}, not active")

    test = _TESTS.get(indicator.test_type)
    if test is None:
        # ENTITY_BEHAVIOUR lands here until the entity engine exists. Saying
        # so is better than silently treating an unbuilt capability as quiet.
        return _insufficient(
            context,
            f"the {indicator.test_type.value} test is not implemented yet, "
            f"so this indicator cannot be evaluated")
    return test(context)
