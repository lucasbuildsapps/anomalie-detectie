"""Indicators: one question, one test, one verdict.

The unit of judgement in v2. Everything the system asserts comes from testing
an indicator; there is no second path and no voting. See ARCHITECTURE_V2.md
§4.1.

Two fields are required that a purely technical design would treat as
optional: `question` and `meaning`. An indicator without a stated question is
not testable, and one without a stated meaning cannot be acted on — it
produces alerts nobody can interpret. Requiring both is how pre-registration
stops being a convention and starts being a constraint: you must write down
what you are watching for, and why it would matter, *before* it fires.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from sentinel.core.contracts.enums import (
    BaselineKind,
    IndicatorStatus,
    IndicatorTest,
)
from sentinel.core.contracts.primitives import DateRange

__all__ = ["Baseline", "Indicator"]

_EMPTY: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class Baseline:
    """A fitted notion of normal, of one kind, as of one moment.

    Both kinds are first-class. The adaptive baseline follows the recent
    regime; the fixed reference is declared by an analyst and does not move.
    Divergence between them is the sustained-escalation signal — the failure
    v1 could not see, because it had only the adaptive one and a developing
    threat simply became the new normal.
    """

    kind: BaselineKind
    as_of: object                       # datetime; kept loose for storage round-trips
    params: Mapping = field(default_factory=lambda: _EMPTY)
    n_periods: int = 0
    #: Out-of-sample coverage, measured on data not used to fit or tune.
    coverage_oos: float | None = None
    reference_period: DateRange | None = None

    def __post_init__(self) -> None:
        if self.kind is BaselineKind.FIXED_REFERENCE \
                and self.reference_period is None:
            raise ValueError(
                "a fixed reference baseline must declare the period it "
                "refers to — an undeclared reference cannot be reviewed"
            )
        if self.coverage_oos is not None and not 0.0 <= self.coverage_oos <= 1.0:
            raise ValueError(
                f"coverage must be a fraction, got {self.coverage_oos}")

    @property
    def is_declared(self) -> bool:
        return self.kind is BaselineKind.FIXED_REFERENCE


@dataclass(frozen=True)
class Indicator:
    """A pre-registered thing worth watching, and how to test for it."""

    key: str
    region_key: str
    name: str
    question: str
    meaning: str
    test_type: IndicatorTest
    test_config: Mapping = field(default_factory=lambda: _EMPTY)
    status: IndicatorStatus = IndicatorStatus.DRAFT
    area_key: str | None = None
    entity_kind: str | None = None
    reference_period: DateRange | None = None
    #: When this indicator started watching, and when it stopped.
    #:
    #: Configuration is subject to the same causal rule as data. Selecting
    #: indicators by *today's* status while replaying 2022 evaluates an
    #: indicator written last month as though it had been watching then, and
    #: that inflates every retrospective warning time it contributes to. The
    #: `AsOfView` prevents that for observations; these two fields are what
    #: prevent it for configuration.
    #:
    #: Both optional. An undated indicator is treated as always active — the
    #: only workable default for a system whose indicators predate this
    #: field — and `activation_dated` reports that so a replay can say its
    #: coverage is assumed rather than known.
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    owner: str | None = None
    attrs: Mapping = field(default_factory=lambda: _EMPTY)

    def __post_init__(self) -> None:
        for name_, value in (("key", self.key), ("region_key", self.region_key),
                             ("name", self.name)):
            if not str(value).strip():
                raise ValueError(f"indicator {name_} cannot be empty")
        if not str(self.question).strip():
            raise ValueError(
                f"indicator {self.key!r} must state the question it answers; "
                f"an indicator without a question is not testable"
            )
        if not str(self.meaning).strip():
            raise ValueError(
                f"indicator {self.key!r} must state what it would mean if it "
                f"fired; an alert nobody can interpret is not worth raising"
            )
        if self.test_type is IndicatorTest.SUSTAINED_DIVERGENCE \
                and self.reference_period is None:
            raise ValueError(
                f"indicator {self.key!r} tests sustained divergence, which "
                f"compares against a declared reference period — none given"
            )
        if self.test_type is IndicatorTest.ENTITY_BEHAVIOUR and not self.entity_kind:
            raise ValueError(
                f"indicator {self.key!r} tests entity behaviour but does not "
                f"say which kind of entity"
            )

    @property
    def is_active(self) -> bool:
        return self.status is IndicatorStatus.ACTIVE

    @property
    def activation_dated(self) -> bool:
        """Whether this indicator can say when it started watching."""
        return self.activated_at is not None

    def was_active_at(self, as_of: datetime) -> bool:
        """Whether this indicator was watching at `as_of`.

        A retired indicator is *not* excluded from the past it covered: it
        was watching then, and a replay that drops it would understate what
        the system saw. Retirement ends coverage from `retired_at` onwards,
        it does not erase it.
        """
        if self.status is IndicatorStatus.DRAFT:
            return False
        if self.activated_at is not None and as_of < self.activated_at:
            return False
        return not (self.retired_at is not None
                    and as_of >= self.retired_at)

    @property
    def scope(self) -> str:
        parts = [self.region_key]
        if self.area_key:
            parts.append(self.area_key)
        if self.entity_kind:
            parts.append(f"{self.entity_kind}s")
        return " / ".join(parts)

    def describe(self) -> str:
        return f"{self.name} ({self.scope}): {self.question}"
