"""Signal -> written assessment. Non-negotiable #8, made mechanical.

Every alert has to say what happened, why it is notable, what it was compared
against, how confident the judgement is, what the supporting evidence is, what
else could explain it, and what to do next. Leaving that to whoever writes the
UI guarantees it is done inconsistently and, under time pressure, not at all.
So it is composed here, from the signal, and the contract refuses to build an
active assessment that is missing an alternative or a follow-up.

Three rules this module holds to
--------------------------------
**It never changes a verdict.** The composer reads a `Signal` and writes prose.
If it could soften or sharpen a finding it would be a second truth model, and
the point of retiring v1's voting scheme was to have exactly one.

**It never asserts intent.** A vessel that stopped over a cable did that; why
it did is not in the data. Every statement here describes behaviour or level,
and the follow-ups are collection tasks rather than conclusions.

**It declines to quote a probability it cannot compute.** `effect_size` does
not mean the same thing across tests — it is a standardised deviation for the
two series tests, a raw distance from a threshold for `condition:above`, a
count of periods for `condition:silence`, and a peer ranking score for entity
behaviour. Running a normal tail over all four would produce confident-looking
numbers for three of them that mean nothing. Only the two standardised tests
get a probability; the rest render without one, which `Assessment.format()`
already handles by stating the baseline plainly instead.

That last rule is worth being stubborn about. The estimative-language band is
the most quotable thing in the output, and a number attached to the wrong
scale would be quoted anyway.
"""
from __future__ import annotations

import math
from datetime import datetime

from sentinel.core.contracts import (
    Assessment,
    Direction,
    EvidenceKind,
    Indicator,
    IndicatorTest,
    MonitoringStatus,
    RegionStatus,
    Signal,
    Verdict,
)

__all__ = ["compose", "compose_region"]

#: Tests whose `effect_size` is a standardised deviation, and so can be turned
#: into "how unusual is a value like this under the baseline".
_STANDARDISED = frozenset({
    IndicatorTest.LEVEL_DEVIATION,
    IndicatorTest.SUSTAINED_DIVERGENCE,
})


def _tail_probability(z: float) -> float:
    """Two-sided normal tail. The chance of a value at least this extreme.

    Note what this is *not*: it is not the probability that something is
    happening. It is the rarity of the observation under the fitted baseline,
    which is why `Assessment.format()` renders it as "a value like this is
    ...". Reading it as the likelihood of a hypothesis would be a category
    error, and the wording exists to make that harder.
    """
    return math.erfc(abs(float(z)) / math.sqrt(2.0))


# =========================================================================
# per-test language
# =========================================================================
def _statement(signal: Signal, indicator: Indicator) -> str:
    """What happened, in one sentence, without saying what it means."""
    when = signal.as_of.date()
    where = indicator.scope
    effect = signal.effect_size
    towards = "above" if signal.direction is Direction.ABOVE else "below"

    if indicator.test_type is IndicatorTest.LEVEL_DEVIATION:
        return (f"{indicator.name} in {where} ran {effect:.1f} scale units "
                f"{towards} the recent baseline in the period to {when}")

    if indicator.test_type is IndicatorTest.SUSTAINED_DIVERGENCE:
        return (f"{indicator.name} in {where} has held {effect:.1f} scale "
                f"units {towards} its declared reference level, as of {when}")

    if indicator.test_type is IndicatorTest.CONDITION:
        rule = str(indicator.test_config.get("rule", "")).strip()
        periods = max(1, int(indicator.test_config.get("periods", 1)))
        if rule == "silence":
            return (f"{indicator.name} in {where} recorded no activity for "
                    f"{periods} consecutive period(s) to {when}, where the "
                    f"expected level is not zero")
        return (f"{indicator.name} in {where} met its pre-registered "
                f"'{rule}' condition for {periods} consecutive period(s) "
                f"to {when}")

    if indicator.test_type is IndicatorTest.ENTITY_BEHAVIOUR:
        return (f"A {indicator.entity_kind} in {where} behaved unusually for "
                f"its class in the period to {when}")

    return f"{indicator.name} in {where} is active as of {when}"


def _baseline_description(signal: Signal, indicator: Indicator) -> str:
    """What the observation was compared against.

    Required by the contract, and the reason is practical: a deviation whose
    comparison is unstated cannot be argued with. "Up sharply" invites the
    question "against what?", and an assessment that cannot answer it is not
    reviewable.
    """
    if indicator.test_type is IndicatorTest.SUSTAINED_DIVERGENCE:
        period = indicator.reference_period
        if period is not None:
            return (f"declared reference period "
                    f"{period.start.date()} to {period.end.date()}")
        return "a declared reference period"

    if indicator.test_type is IndicatorTest.CONDITION:
        rule = str(indicator.test_config.get("rule", "")).strip()
        threshold = indicator.test_config.get("threshold")
        if rule == "silence":
            return "a pre-registered rule: activity is normally non-zero here"
        if threshold is not None:
            return f"a pre-registered threshold of {threshold}"
        return f"a pre-registered '{rule}' rule"

    if indicator.test_type is IndicatorTest.ENTITY_BEHAVIOUR:
        grouping = indicator.test_config.get(
            "peer_baseline", ("vessel_class", "area"))
        return (f"peers matched on {', '.join(grouping)} — unusual for that "
                f"class here, not unusual in general")

    return "an adaptive baseline fitted on the preceding periods"


#: Competing explanations that always apply to a test type, used only when the
#: signal did not carry its own. These are deliberately specific: a generic
#: "could be something else" would satisfy the contract while informing nobody,
#: which would turn the requirement into a rubber stamp.
_DEFAULT_ALTERNATIVES = {
    IndicatorTest.LEVEL_DEVIATION: (
        "a change in reporting volume or source coverage produces the same "
        "excursion as a change in the world; check source continuity first",
    ),
    IndicatorTest.SUSTAINED_DIVERGENCE: (
        "the declared reference may simply be stale — verify it still "
        "describes a period a reader would accept as normal",
    ),
    IndicatorTest.CONDITION: (
        "a silent source and a silent world are identical in this data; "
        "confirm the feed delivered before reading absence as a finding",
    ),
    IndicatorTest.ENTITY_BEHAVIOUR: (
        "equipment failure, weather, or a legitimate operational reason "
        "produce the same track; behaviour is not intent",
    ),
}

_FOLLOW_UP = {
    IndicatorTest.LEVEL_DEVIATION: (
        "check the underlying records for the flagged period against a "
        "second source",
        "compare collection volume for the period with the preceding weeks",
    ),
    IndicatorTest.SUSTAINED_DIVERGENCE: (
        "review the run of periods since the level shifted for a step change "
        "in reporting practice",
        "re-examine whether the declared reference period is still the right "
        "comparison",
    ),
    IndicatorTest.CONDITION: (
        "confirm the source delivered data for the affected periods",
        "check whether the condition's threshold still matches current "
        "operating levels",
    ),
    IndicatorTest.ENTITY_BEHAVIOUR: (
        "retrieve the full track for the entity over the surrounding days",
        "check receiver coverage in the area for the period, so a reception "
        "gap is not read as behaviour",
    ),
}


def _alternatives(signal: Signal, indicator: Indicator) -> tuple[str, ...]:
    """The signal's own competing explanations, or the type's defaults."""
    carried = tuple(e.summary for e in signal.evidence
                    if e.kind is EvidenceKind.ALTERNATIVE)
    if carried:
        return carried
    return _DEFAULT_ALTERNATIVES.get(
        indicator.test_type,
        ("a collection or processing change could produce the same result",))


# =========================================================================
# composition
# =========================================================================
def compose(signal: Signal, indicator: Indicator, *,
            analyst: str | None = None,
            created_at: datetime | None = None) -> Assessment:
    """Write the assessment for one signal, whatever its verdict.

    All three verdicts get written product. A null result and an untestable
    indicator are findings an analyst has to be able to read and cite; leaving
    them as bare enum values would mean only alarming outcomes ever reach the
    page, which is how a warning system trains its readers to expect alarm.
    """
    if signal.indicator_key != indicator.key:
        raise ValueError(
            f"signal is for {signal.indicator_key!r} but indicator is "
            f"{indicator.key!r}; composing across them would attach one "
            f"finding's reasoning to another's verdict")

    common = dict(signal=signal, analyst=analyst, created_at=created_at)

    if signal.verdict is Verdict.INSUFFICIENT_DATA:
        return Assessment(
            statement=(f"{indicator.name} in {indicator.scope} could not be "
                       f"tested as of {signal.as_of.date()}: "
                       f"{signal.insufficient_reason}"),
            baseline_description=(
                "no comparison was possible, so this is not a statement "
                "about whether anything is happening"),
            **common,
        )

    if signal.verdict is Verdict.NOT_ACTIVE:
        return Assessment(
            statement=signal.null_statement().rstrip(),
            baseline_description=_baseline_description(signal, indicator),
            **common,
        )

    probability = (_tail_probability(signal.effect_size)
                   if indicator.test_type in _STANDARDISED
                   and signal.effect_size is not None else None)

    return Assessment(
        statement=_statement(signal, indicator),
        baseline_description=_baseline_description(signal, indicator),
        probability=probability,
        alternatives=_alternatives(signal, indicator),
        follow_up=_FOLLOW_UP.get(
            indicator.test_type,
            ("confirm the finding against a second source before acting",)),
        **common,
    )


def compose_region(status: RegionStatus,
                   indicators: dict[str, Indicator] | None = None,
                   ) -> str:
    """The periodic product for one region: headline, then each finding.

    Ordered active first, then quiet, then untestable. That ordering is a
    judgement about attention, not about importance — the untestable ones are
    often the most consequential thing on the page, so they are listed rather
    than dropped, under a heading that does not let them read as calm.
    """
    lookup = indicators or {}
    lines = [status.headline()]

    if status.monitoring is not MonitoringStatus.MONITORED:
        return lines[0]

    for heading, group in (("Active", status.active),
                           ("Tested and quiet", status.tested),
                           ("Could not be tested", status.unavailable)):
        if not group:
            continue
        lines.append("")
        lines.append(f"## {heading}")
        for signal in group:
            indicator = lookup.get(signal.indicator_key)
            lines.append("")
            if indicator is None:
                # Without the indicator we have no question, scope or
                # reference to write against. Saying so beats inventing them.
                lines.append(f"{signal.indicator_key}: {signal.verdict.value} "
                             f"(indicator definition unavailable, so this "
                             f"finding cannot be written up)")
                continue
            lines.append(f"### {indicator.name}")
            lines.append(compose(signal, indicator).format())

    return "\n".join(lines)
