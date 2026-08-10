"""Entities and their identifiers over time.

`kind` is an open string ('vessel', 'aircraft', 'actor', 'site'). Core has no
opinion about what kinds exist; the maritime module brings vessels, and that
must not require a core change.

Identity is time-varying on purpose. A vessel's MMSI is not a primary key —
it is a claim the vessel broadcasts, and it changes, legitimately (reflagging)
and otherwise (spoofing). Modelling identity as a fixed attribute makes both
invisible: the reflagged ship looks like a new one, and the spoofed one looks
like the ship it is pretending to be. So identifiers carry validity intervals
and a confidence, and resolution always happens as-of a moment.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

__all__ = ["Entity", "EntityIdentifier", "EntityRef"]

_EMPTY: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class EntityRef:
    """A stable, internal handle for an entity.

    Separate from any broadcast identifier precisely because those are not
    stable. Events point at this, so a later identity correction does not
    orphan history.
    """

    kind: str
    key: str

    def __post_init__(self) -> None:
        if not str(self.kind).strip():
            raise ValueError("entity kind cannot be empty")
        if not str(self.key).strip():
            raise ValueError("entity key cannot be empty")

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True)
class EntityIdentifier:
    """A claimed identifier, valid over an interval.

    `confidence` records how much the claim is trusted: an MMSI read straight
    off an AIS message differs from one inferred after a gap, and treating
    them alike is how a spoof gets laundered into the record.
    """

    scheme: str                      # 'mmsi' | 'imo' | 'callsign' | 'name'
    value: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: float = 1.0
    source_key: str | None = None

    def __post_init__(self) -> None:
        if not str(self.scheme).strip():
            raise ValueError("identifier scheme cannot be empty")
        if not str(self.value).strip():
            raise ValueError("identifier value cannot be empty")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(
                f"identifier confidence must be in [0, 1], got {self.confidence}"
            )
        if (self.valid_from is not None and self.valid_to is not None
                and self.valid_to < self.valid_from):
            raise ValueError(
                f"identifier {self.scheme}={self.value} expires before it "
                f"becomes valid"
            )

    def valid_at(self, moment: datetime) -> bool:
        if self.valid_from is not None and moment < self.valid_from:
            return False
        return not (self.valid_to is not None and moment > self.valid_to)


@dataclass(frozen=True)
class Entity:
    """Something the world contains that we track across observations."""

    ref: EntityRef
    display_name: str | None = None
    identifiers: tuple[EntityIdentifier, ...] = ()
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    attrs: Mapping = field(default_factory=lambda: _EMPTY)

    def __post_init__(self) -> None:
        if (self.first_seen is not None and self.last_seen is not None
                and self.last_seen < self.first_seen):
            raise ValueError(
                f"entity {self.ref} was last seen before it was first seen"
            )

    @property
    def kind(self) -> str:
        return self.ref.kind

    def identifiers_at(self, moment: datetime,
                       scheme: str | None = None,
                       ) -> tuple[EntityIdentifier, ...]:
        """Identifiers valid at a given moment, optionally by scheme.

        As-of by construction: asking "what was this called then" is the only
        question that has a defensible answer once identity can change.
        """
        return tuple(
            i for i in self.identifiers
            if i.valid_at(moment) and (scheme is None or i.scheme == scheme)
        )

    def identifier_at(self, moment: datetime, scheme: str,
                      ) -> EntityIdentifier | None:
        """The most trusted identifier of one scheme at a moment."""
        candidates = self.identifiers_at(moment, scheme)
        if not candidates:
            return None
        return max(candidates, key=lambda i: i.confidence)

    def has_conflicting_identity(self, moment: datetime, scheme: str) -> bool:
        """True when several distinct values of one scheme are valid at once.

        Not an anomaly verdict — a fact about the record. It may mean a
        spoof, or merely a messy merge. Which of those it is belongs to an
        indicator with a baseline, not to this class.
        """
        values = {i.value for i in self.identifiers_at(moment, scheme)}
        return len(values) > 1
