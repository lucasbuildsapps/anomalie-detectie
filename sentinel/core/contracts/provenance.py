"""Where information came from, and how far it can be traced back.

Provenance is not metadata here — it is an input to the judgement. Source
grading feeds confidence, and lineage is what lets an analyst answer "on what
does this rest?" without reading code. An assessment whose basis cannot be
walked back to raw observations is not defensible, and v2 treats that as a
structural property rather than a documentation habit.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from sentinel.core.contracts.enums import Credibility, Producer, Reliability

__all__ = ["Lineage", "Source"]

_EMPTY: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class Source:
    """A feed, database or human channel, with its Admiralty grading.

    `kind` is an open string, not an enum: a region may bring a feed nobody
    anticipated, and forcing that through a core enum change is exactly the
    coupling this layer exists to prevent.

    Grading is optional but not free — an ungraded source contributes nothing
    to confidence rather than silently counting as good. Someone has to make
    the judgement for it to mean anything.
    """

    key: str
    name: str
    kind: str = "unknown"
    reliability: Reliability | None = None
    credibility: Credibility | None = None
    url: str | None = None
    licence: str | None = None
    redistribution_allowed: bool = False
    retention_days: int | None = None

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("source key cannot be empty")
        if not str(self.name).strip():
            raise ValueError(f"source {self.key!r} needs a human-readable name")
        if self.retention_days is not None and int(self.retention_days) < 0:
            raise ValueError("retention_days cannot be negative")

    @property
    def is_graded(self) -> bool:
        """True only when both scales are set and both are judgeable."""
        return (self.reliability is not None
                and self.credibility is not None
                and self.reliability.is_gradable
                and self.credibility.is_gradable)

    @property
    def grading(self) -> str:
        """Admiralty pair, e.g. 'B2'. 'ungraded' when not assessed."""
        if self.reliability is None or self.credibility is None:
            return "ungraded"
        return f"{self.reliability.value}{self.credibility.value}"

    def describe(self) -> str:
        if not self.is_graded:
            return (f"{self.name} ({self.grading}) — contributes no confidence "
                    f"until graded")
        return f"{self.name} ({self.grading})"


@dataclass(frozen=True)
class Lineage:
    """How a derived item traces back to what was actually observed.

    Kept deliberately thin: source keys, upstream identifiers, and the layer
    that produced the item. Enough to reconstruct a chain, not so much that
    every event carries a copy of its inputs.
    """

    source_keys: tuple[str, ...] = ()
    upstream_ids: tuple[str, ...] = ()
    producer: Producer = Producer.INGEST
    method: str | None = None          # e.g. 'loiter_detector.v1'
    attrs: Mapping = field(default_factory=lambda: _EMPTY)

    def __post_init__(self) -> None:
        if self.producer is Producer.ENTITY_ENGINE and not self.method:
            # A derived event without a named method cannot be reproduced or
            # argued with. Raw ingestion is exempt: the source is the method.
            raise ValueError(
                "entity-engine output must name the method that produced it"
            )

    @property
    def is_derived(self) -> bool:
        return self.producer is Producer.ENTITY_ENGINE

    def with_source(self, key: str) -> Lineage:
        if key in self.source_keys:
            return self
        return Lineage(
            source_keys=self.source_keys + (key,),
            upstream_ids=self.upstream_ids,
            producer=self.producer,
            method=self.method,
            attrs=self.attrs,
        )

    def describe(self) -> str:
        origin = ", ".join(self.source_keys) or "unknown source"
        if self.is_derived:
            return f"derived by {self.method} from {origin}"
        return f"ingested from {origin}"
