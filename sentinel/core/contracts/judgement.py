"""Signals, evidence, confidence and assessments — what the system asserts.

This module is where the architecture's promises become type errors rather
than review comments:

- A `NOT_ACTIVE` signal **cannot be constructed without detection power**. A
  bare "nothing found" is less trustworthy than a quota, because the reader
  cannot tell whether anyone looked properly. Requiring the floor makes the
  null result a claim with a number behind it (§5.1).
- An `ACTIVE` signal **cannot be constructed without an effect size and
  direction**. "Something is off" is not actionable.
- An `INSUFFICIENT_DATA` signal **must say why** — otherwise it is
  indistinguishable from a quiet one, which is the confusion the three-valued
  verdict exists to end.
- `Confidence` **cannot be constructed without reasons**. A level with no
  stated grounds is not reviewable.
- An `Assessment` of an active signal **must carry an alternative explanation
  and a follow-up**. Non-negotiable #8, enforced rather than encouraged.

Evidence never changes a verdict. It moves confidence and raises competing
explanations; the verdict comes from the indicator's single test. That is the
whole point of retiring the voting model (§4.4).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sentinel.core.contracts.enums import (
    ConfidenceLevel,
    Direction,
    EvidenceKind,
    LikelihoodBand,
    MonitoringStatus,
    Reliability,
    Verdict,
)

__all__ = [
    "Assessment",
    "Confidence",
    "ConfidenceInputs",
    "DetectionPower",
    "Evidence",
    "RegionStatus",
    "Signal",
]


#: Harness scenario names in analyst language. The keys are deliberately
#: internal — they name what the evaluation probes — and several of them read
#: badly on a page written for someone deciding whether to act.
_SCENARIO_LABELS = {
    "adaptation_failure": "a sustained increase",
    "sustained_increase": "a sustained increase",
    "gradual_escalation": "a gradual escalation",
    "spike": "an isolated spike",
    "drop": "a sudden fall",
    "regime_change": "a shift to a new level",
    "silence": "a stop in activity",
    "source_change": "a change in source behaviour",
}


@dataclass(frozen=True)
class DetectionPower:
    """What this configuration can actually find.

    Produced by the evaluation harness (`sentinel.eval.harness.PowerCurve`).
    `floor_magnitude` is the smallest effect detected at `threshold` rate
    *after* subtracting chance overlap; NaN when no magnitude qualified.

    `confounded` marks a detector that flags the same window so often on
    quiet data that no floor can be quoted. Measured on v1's STL detector
    this was not hypothetical, so it is a first-class state rather than a
    footnote.
    """

    scenario_kind: str
    floor_magnitude: float
    threshold: float = 0.8
    n_repeats: int = 5
    confounded: bool = False
    #: Unit of `floor_magnitude`. "x" is a multiple of the baseline; entity
    #: measurements are in real units such as hours, and rendering those as
    #: "1x" would be quietly wrong.
    unit: str = "x"
    #: False when the measurement did not resolve the floor: the deciding
    #: magnitude sat within sampling noise of the recall threshold, so more
    #: repeats could move it. Quoting such a floor as exact overstates what
    #: was measured — the spike configuration read 5x at 8 repeats and 3x at
    #: 24 before this was surfaced.
    resolved: bool = True
    #: A condition the floor depends on. Some capabilities fail along a
    #: dimension the floor does not express — entity behaviour stops being
    #: detectable once it stops being rare — and an unqualified floor would
    #: overstate what the null result covers.
    caveat: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 < float(self.threshold) <= 1.0:
            raise ValueError(
                f"threshold must be in (0, 1], got {self.threshold}")
        if self.confounded and not math.isnan(self.floor_magnitude):
            raise ValueError(
                "a confounded measurement cannot also report a floor; "
                "that is the error this flag exists to prevent"
            )

    @property
    def is_deterministic(self) -> bool:
        """A rule that either holds or does not, with nothing to measure.

        Marked by `n_repeats == 0`: no trials were run because none were
        needed. Distinct from an unmeasured configuration, which reports no
        power at all and blocks a null result entirely.
        """
        return self.n_repeats == 0

    @property
    def is_quotable(self) -> bool:
        """Whether this number may be shown to an analyst."""
        return not self.confounded and math.isfinite(self.floor_magnitude)

    @property
    def scenario_label(self) -> str:
        """The scenario in analyst language rather than harness slugs.

        `adaptation_failure` names the *failure mode being probed*, which is
        the right name inside the evaluation code and the wrong one on an
        analyst's page — it reads as a fault in the tool rather than as the
        thing that was looked for.
        """
        return _SCENARIO_LABELS.get(
            self.scenario_kind, self.scenario_kind.replace("_", " "))

    def describe(self) -> str:
        if self.is_deterministic:
            # An effect size is meaningless here, and phrasing one would
            # imply a measurement that never happened.
            return (f"This is a fixed rule ({self.scenario_kind}): it either "
                    f"holds or it does not, so there is no effect size below "
                    f"which it would be missed.")
        if self.confounded:
            return (f"Detection power for {self.scenario_label} is not "
                    f"measurable: this configuration flags comparable windows "
                    f"on quiet data too often for a floor to mean anything.")
        if not math.isfinite(self.floor_magnitude):
            return (f"No effect size of {self.scenario_label} was reliably "
                    f"detected at any tested magnitude. A null result here "
                    f"carries little weight.")
        size = (f"{self.floor_magnitude:g}{self.unit}" if self.unit == "x"
                else f"{self.floor_magnitude:g} {self.unit}")
        text = (f"{self.scenario_label} of {size} or larger would have "
                f"been detected {self.threshold:.0%} of the time.")
        text = text[0].upper() + text[1:]
        if not self.resolved:
            text += (f" This floor is not resolved at {self.n_repeats} runs; "
                     f"treat it as approximate.")
        if self.caveat:
            text += f" {self.caveat}"
        return text


@dataclass(frozen=True)
class ConfidenceInputs:
    """The measurable things confidence is built from.

    Explicitly *not* series length, and explicitly not any quantity the model
    calibrated against itself. v1's confidence was effectively a length check
    wearing ICD 203 clothing: 60+ periods plus self-tuned coverage produced
    "high" almost automatically. Each field here is either measured
    out-of-sample or supplied by a human.
    """

    #: Fraction of periods with data, 0-1.
    data_coverage: float | None = None
    #: Days between the latest observation and the assessment.
    staleness_days: int | None = None
    #: |out-of-sample coverage - target|; small is good.
    calibration_gap: float | None = None
    #: Historical performance of *this* indicator, from the eval harness.
    historical_precision: float | None = None
    historical_recall: float | None = None
    #: Corroborating sources, corrected for how independent they really are.
    effective_corroboration: float | None = None
    source_reliability: Reliability | None = None
    #: False when the point-in-time reconstruction rests on assumed arrivals.
    reconstruction_faithful: bool | None = None
    regime_stable: bool | None = None

    def __post_init__(self) -> None:
        for name in ("data_coverage", "calibration_gap",
                     "historical_precision", "historical_recall"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be a fraction, got {value}")

    @property
    def n_supplied(self) -> int:
        """How many inputs were actually available.

        Surfaced because a confidence level resting on two of nine inputs
        deserves to be read differently from one resting on eight.
        """
        return sum(1 for v in vars(self).values() if v is not None)


@dataclass(frozen=True)
class Confidence:
    """An ICD 203 confidence level, with the grounds that produced it."""

    level: ConfidenceLevel
    reasons: tuple[str, ...]
    inputs: ConfidenceInputs = field(default_factory=ConfidenceInputs)

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError(
                "confidence must state its grounds; a level without reasons "
                "cannot be reviewed or challenged"
            )

    def describe(self) -> str:
        return (f"Confidence is {self.level.value} "
                f"({self.level.definition}). Grounds: "
                f"{'; '.join(self.reasons)}.")


@dataclass(frozen=True)
class Evidence:
    """Something that bears on a judgement without deciding it.

    `weight` runs -1 to 1. Negative undermines. An ALTERNATIVE always carries
    weight <= 0 by definition: a competing explanation cannot strengthen the
    reading it competes with.
    """

    kind: EvidenceKind
    summary: str
    weight: float = 0.0
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.summary).strip():
            raise ValueError("evidence must say something")
        if not -1.0 <= float(self.weight) <= 1.0:
            raise ValueError(f"weight must be in [-1, 1], got {self.weight}")
        if self.kind is EvidenceKind.ALTERNATIVE and float(self.weight) > 0:
            raise ValueError(
                "an alternative explanation cannot have positive weight; it "
                "competes with the reading, it does not support it"
            )


@dataclass(frozen=True)
class Signal:
    """The verdict on one indicator at one instant. The single truth."""

    indicator_key: str
    as_of: datetime
    verdict: Verdict
    confidence: Confidence
    effect_size: float | None = None
    direction: Direction | None = None
    detection_power: DetectionPower | None = None
    evidence: tuple[Evidence, ...] = ()
    insufficient_reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict is Verdict.ACTIVE:
            if self.effect_size is None or self.direction is None:
                raise ValueError(
                    f"active signal for {self.indicator_key!r} must state how "
                    f"large the deviation is and in which direction; "
                    f"'something is off' is not actionable"
                )
        elif self.verdict is Verdict.NOT_ACTIVE:
            if self.detection_power is None:
                raise ValueError(
                    f"null result for {self.indicator_key!r} must carry "
                    f"detection power. 'Nothing found' without stating what "
                    f"would have been found is not a finding — see "
                    f"ARCHITECTURE_V2.md §5.1"
                )
        elif (self.verdict is Verdict.INSUFFICIENT_DATA
                and not (self.insufficient_reason or "").strip()):
            raise ValueError(
                f"signal for {self.indicator_key!r} reports insufficient "
                f"data but does not say why; that is indistinguishable "
                f"from a quiet result"
            )

    @property
    def is_null_result(self) -> bool:
        return self.verdict.is_null_result

    def evidence_of(self, kind: EvidenceKind) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.kind is kind)

    @property
    def alternatives(self) -> tuple[Evidence, ...]:
        return self.evidence_of(EvidenceKind.ALTERNATIVE)

    @property
    def net_evidence_weight(self) -> float:
        """Sum of evidence weights. Informational — it moves confidence,
        never the verdict."""
        return sum(float(e.weight) for e in self.evidence)

    def null_statement(self) -> str:
        """The analyst-facing sentence for a quiet result."""
        if not self.is_null_result:
            raise ValueError("not a null result")
        power = self.detection_power
        return (f"No significant deviation detected for "
                f"{self.indicator_key}. {power.describe()}")


@dataclass(frozen=True)
class Assessment:
    """A written judgement, in ICD 203 form.

    `format()` keeps likelihood and confidence in separate sentences. That is
    not styling: combined, the reader cannot tell whether the event or the
    judgement is the uncertain part.

    It also renders the signal's own evidence rather than only the composer's
    prose. An assessment whose supporting observations are not shown asks to
    be trusted on the strength of its wording, which is the opposite of the
    intent — the reader has to be able to disagree with the reasoning while
    looking at the same facts.
    """

    signal: Signal
    statement: str
    baseline_description: str
    probability: float | None = None
    alternatives: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    created_at: datetime | None = None
    analyst: str | None = None

    def __post_init__(self) -> None:
        if not str(self.statement).strip():
            raise ValueError("assessment must state what happened")
        if not str(self.baseline_description).strip():
            raise ValueError(
                "assessment must say what the observation was compared "
                "against; a deviation without a stated baseline cannot be "
                "checked"
            )
        if self.probability is not None and not 0.0 <= self.probability <= 1.0:
            raise ValueError(
                f"probability must be in [0, 1], got {self.probability}")
        if self.signal.verdict is Verdict.ACTIVE:
            if not self.alternatives:
                raise ValueError(
                    "an active assessment must offer at least one alternative "
                    "explanation; a reading with no competitor has not been "
                    "tested"
                )
            if not self.follow_up:
                raise ValueError(
                    "an active assessment must recommend a follow-up; an "
                    "alert with no next step is noise the analyst has to "
                    "dispose of"
                )

    @property
    def likelihood(self) -> LikelihoodBand | None:
        if self.probability is None:
            return None
        return LikelihoodBand.from_probability(self.probability)

    def format(self) -> str:
        lines = [self.statement.rstrip(".") + "."]
        band = self.likelihood
        if band is not None:
            lines.append(
                f"Under the current baseline ({self.baseline_description}), "
                f"a value like this is {band.label} ({band.range_text})."
            )
        else:
            lines.append(f"Baseline: {self.baseline_description}.")
        if self.signal.verdict is Verdict.INSUFFICIENT_DATA:
            # Rendering a confidence level under "could not be tested" invites
            # the reading "we are confident nothing happened". Confidence
            # qualifies a judgement, and no judgement was reached — so the
            # line states what the level actually refers to.
            lines.append(
                f"No judgement was reached, so this carries no confidence in "
                f"either direction. (Input quality was rated "
                f"{self.signal.confidence.level.value}.)")
        else:
            lines.append(self.signal.confidence.describe())
        support = [e for e in self.signal.evidence
                   if e.kind is not EvidenceKind.ALTERNATIVE]
        if support:
            lines.append("Evidence: "
                         + "; ".join(e.summary for e in support) + ".")
        if self.alternatives:
            lines.append("Alternative explanations: "
                         + "; ".join(self.alternatives) + ".")
        if self.follow_up:
            lines.append("Recommended follow-up: "
                         + "; ".join(self.follow_up) + ".")
        return "\n".join(lines)


@dataclass(frozen=True)
class RegionStatus:
    """A region's state, including whether anyone is watching it.

    Exists because of a product risk, not a technical one. A region panel
    with no alerts reads as "quiet". For an unmonitored region the truth is
    "nobody is looking", and presenting the second as the first is the null
    result problem promoted from the alert level to the region level.
    `headline()` refuses to conflate them.
    """

    region_key: str
    name: str
    monitoring: MonitoringStatus
    signals: tuple[Signal, ...] = ()
    activation_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.monitoring is MonitoringStatus.NOT_MONITORED:
            if not self.activation_requirements:
                raise ValueError(
                    f"region {self.region_key!r} is not monitored but does "
                    f"not say what activating it would require; an empty "
                    f"panel then reads as 'quiet' when it means 'unwatched'"
                )
            if self.signals:
                raise ValueError(
                    f"region {self.region_key!r} is marked not monitored but "
                    f"carries signals"
                )

    @property
    def active(self) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.verdict is Verdict.ACTIVE)

    @property
    def unavailable(self) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals
                     if s.verdict is Verdict.INSUFFICIENT_DATA)

    @property
    def tested(self) -> tuple[Signal, ...]:
        """Indicators that actually produced a verdict."""
        return tuple(s for s in self.signals if s.verdict is Verdict.NOT_ACTIVE)

    @property
    def is_quiet(self) -> bool:
        """Genuinely quiet: monitored, actually tested, and nothing found.

        Requires at least one indicator to have been *tested*, not merely the
        absence of active ones. A region whose every indicator returned
        insufficient data has produced no evidence of calm — reading it as
        quiet is the "we could not look" / "nothing is happening" confusion
        that the three-valued verdict exists to prevent, resurfacing one
        level up.
        """
        return (self.monitoring.can_produce_verdicts
                and bool(self.tested)
                and not self.active)

    def headline(self) -> str:
        if self.monitoring is MonitoringStatus.NOT_MONITORED:
            return (f"{self.name}: not monitored. Requires "
                    f"{'; '.join(self.activation_requirements)}.")
        if self.monitoring is MonitoringStatus.DATA_ONLY:
            return (f"{self.name}: data collected, no indicators defined. "
                    f"Nothing is being tested, so nothing can be reported.")
        if not self.signals:
            return (f"{self.name}: monitored, but no indicator produced a "
                    f"verdict this run.")
        if self.active:
            names = ", ".join(s.indicator_key for s in self.active[:3])
            more = f" (+{len(self.active) - 3})" if len(self.active) > 3 else ""
            return (f"{self.name}: {len(self.active)} of {len(self.signals)} "
                    f"indicators active — {names}{more}.")
        unavailable = len(self.unavailable)
        if not self.tested:
            # Nothing was testable. Leading with "no significant activity"
            # here would report a blind spot as a clean result.
            return (f"{self.name}: nothing could be tested — "
                    f"{unavailable} of {len(self.signals)} indicators "
                    f"returned insufficient data. This is not a quiet result.")
        tail = (f" {unavailable} could not be tested."
                if unavailable else "")
        return (f"{self.name}: no significant activity. "
                f"{len(self.tested)} indicators tested and quiet.{tail}")
