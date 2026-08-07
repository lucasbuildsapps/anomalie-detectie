"""Controlled vocabularies.

Every value here is a decision that must not drift between modules. Regions
add *values* to open string fields (event types, entity kinds); they never add
members to these enums. If a region needs a fifth test type, that is a core
design change with a case to argue, not a regional convenience — see
ARCHITECTURE_V2.md §4.2.

Labels are English. The contracts layer stays language-neutral in structure
and English in wording; translation happens at the UI edge, never in the
middle of a judgement.
"""
from __future__ import annotations

from enum import StrEnum

__all__ = [
    "BaselineKind",
    "ConfidenceLevel",
    "Credibility",
    "Direction",
    "EvidenceKind",
    "IndicatorStatus",
    "LikelihoodBand",
    "MonitoringStatus",
    "Producer",
    "Reliability",
    "IndicatorTest",
    "Verdict",
]


class Verdict(StrEnum):
    """The outcome of testing one indicator at one instant.

    Three-valued on purpose. v1 could not distinguish "we looked and found
    nothing" from "we could not look", and that is exactly where warning
    failures hide.
    """

    ACTIVE = "active"
    NOT_ACTIVE = "not_active"
    INSUFFICIENT_DATA = "insufficient_data"

    @property
    def is_null_result(self) -> bool:
        return self is Verdict.NOT_ACTIVE


class MonitoringStatus(StrEnum):
    """Whether a region is actually being watched.

    Carried separately from any verdict because an unmonitored region must
    never render as a quiet one. A blank panel reads as "nothing is
    happening"; the truth may be "nobody is looking".
    """

    NOT_MONITORED = "not_monitored"
    DATA_ONLY = "data_only"        # data flows in, no indicators defined yet
    MONITORED = "monitored"

    @property
    def can_produce_verdicts(self) -> bool:
        return self is MonitoringStatus.MONITORED


class IndicatorTest(StrEnum):
    """The four permitted tests. A fifth needs an argument, not a commit."""

    LEVEL_DEVIATION = "level_deviation"
    SUSTAINED_DIVERGENCE = "sustained_divergence"
    CONDITION = "condition"
    ENTITY_BEHAVIOUR = "entity_behaviour"


class BaselineKind(StrEnum):
    """Adaptive follows the recent regime; fixed is declared by an analyst.

    Divergence between the two is itself the sustained-escalation signal, so
    both must be first-class rather than one being a variant of the other.
    """

    ADAPTIVE = "adaptive"
    FIXED_REFERENCE = "fixed_reference"


class Direction(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class EvidenceKind(StrEnum):
    """What a piece of evidence does to a judgement.

    None of these can change a verdict — see `Signal`. Corroboration and
    context move confidence; an alternative offers a competing explanation
    the analyst has to rule out.
    """

    CORROBORATION = "corroboration"
    ALTERNATIVE = "alternative"
    CONTEXT = "context"


class IndicatorStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class Producer(StrEnum):
    """Which layer created an event — ingestion, or the entity engine."""

    INGEST = "ingest"
    ENTITY_ENGINE = "entity_engine"


class Reliability(StrEnum):
    """Admiralty source reliability. F means it cannot be judged, which is
    different from being unreliable."""

    A = "A"  # completely reliable
    B = "B"  # usually reliable
    C = "C"  # fairly reliable
    D = "D"  # not usually reliable
    E = "E"  # unreliable
    F = "F"  # cannot be judged

    @property
    def is_gradable(self) -> bool:
        return self is not Reliability.F


class Credibility(StrEnum):
    """Admiralty information credibility, 1 (confirmed) to 6 (cannot judge)."""

    C1 = "1"
    C2 = "2"
    C3 = "3"
    C4 = "4"
    C5 = "5"
    C6 = "6"

    @property
    def is_gradable(self) -> bool:
        return self is not Credibility.C6


class LikelihoodBand(StrEnum):
    """ICD 203 / NATO words of estimative probability.

    Kept apart from confidence deliberately: one describes how likely the
    event is, the other how solid the judgement is. Mixing them in a sentence
    leaves the reader unable to tell which is uncertain, which is why
    `Assessment` renders them separately.
    """

    VERY_UNLIKELY = "very_unlikely"
    UNLIKELY = "unlikely"
    ROUGHLY_EVEN = "roughly_even"
    LIKELY = "likely"
    VERY_LIKELY = "very_likely"

    @property
    def range_text(self) -> str:
        return {
            LikelihoodBand.VERY_UNLIKELY: "< 10%",
            LikelihoodBand.UNLIKELY: "10-40%",
            LikelihoodBand.ROUGHLY_EVEN: "40-60%",
            LikelihoodBand.LIKELY: "60-90%",
            LikelihoodBand.VERY_LIKELY: "> 90%",
        }[self]

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")

    @classmethod
    def from_probability(cls, probability: float) -> LikelihoodBand:
        p = min(max(float(probability), 0.0), 1.0)
        if p < 0.10:
            return cls.VERY_UNLIKELY
        if p < 0.40:
            return cls.UNLIKELY
        if p < 0.60:
            return cls.ROUGHLY_EVEN
        if p < 0.90:
            return cls.LIKELY
        return cls.VERY_LIKELY


class ConfidenceLevel(StrEnum):
    """ICD 203 levels of confidence in the assessment (LCA).

    About the quality of the basis, not the likelihood of the event.
    """

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"

    @property
    def definition(self) -> str:
        return {
            ConfidenceLevel.HIGH: (
                "good information quality, corroborated by independent means, "
                "unambiguous to assess"),
            ConfidenceLevel.MODERATE: (
                "credible information that lacks corroboration or admits "
                "several interpretations"),
            ConfidenceLevel.LOW: (
                "fragmentary information, or a source of doubtful "
                "reliability"),
        }[self]
