"""Peer baselines: is this behaviour unusual *for this kind of vessel, here*?

The missing half of the entity layer. The primitives emit `loiter` events for
trawlers and cargo vessels alike, deliberately; this module supplies the
comparison that separates them.

Two questions, and both are needed
----------------------------------
1. **Rarity** — do vessels of this class, in this area, do this at all? A
   cargo vessel stopping mid-sea is notable because cargo vessels do not
   normally stop mid-sea. A trawler stopping is not.
2. **Magnitude** — given that they do, is *this* instance extreme? A trawler
   loitering twelve hours where its peers manage three is worth a look even
   though loitering itself is routine for the class.

Judging on rarity alone flags every fishing vessel. Judging on magnitude
alone misses the cargo vessel that stopped once, briefly, on a cable route —
which is the case that matters most.

Leave-one-out, always
---------------------
A vessel is never part of its own peer baseline. Include it and a ship that
loiters constantly teaches the baseline that constant loitering is normal,
then passes unremarked — the behaviour most worth catching becoming the
thing that hides it. This is the same failure as the adaptive baseline
absorbing an escalation, one level down, and it is corrected the same way:
by comparing against something the subject cannot move.

Two denominators, not one
-------------------------
Rarity and magnitude need different evidence, and conflating them breaks the
flagship case. Rarity asks "how many comparable vessels did this at all",
whose denominator is every vessel of the class we *observed* — including the
overwhelming majority that did nothing. Magnitude asks "how extreme is this
instance", whose denominator is the peer events themselves.

The first version of this module required a minimum number of peer *events*
before assessing anything. That made rare behaviour unassessable by
construction: one cargo vessel stopping on a cable route has almost no peer
events precisely because the behaviour is rare, so the check that was meant
to guard against thin evidence silenced the strongest signal. Sufficiency is
now tested separately for each question, and an assessment reports which of
the two it could actually answer.

Cold start
----------
A peer group of one is not a peer group. Below the configured minimum of
observed entities, `assess` returns None, which the indicator layer turns
into INSUFFICIENT_DATA rather than a verdict. Guessing from three
observations would be worse than declining.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from sentinel.core.contracts import Event

__all__ = ["PeerAssessment", "PeerBaseline", "PeerConfig", "events_to_frame"]


@dataclass(frozen=True)
class PeerConfig:
    """How peers are grouped, and how much evidence is needed to judge.

    The minimums are deliberately not small. With four peers, one unusual
    vessel is a quarter of the reference — the baseline would describe the
    outlier as much as the norm.
    """

    group_by: tuple[str, ...] = ("vessel_class",)
    #: Observed vessels of the class needed before rarity means anything.
    min_peer_entities: int = 5
    #: Peer *events* needed before a magnitude percentile means anything.
    #: Deliberately not a precondition for rarity — see the module docstring.
    min_peer_events: int = 10
    #: Magnitude at or above this percentile of peers counts as extreme.
    extreme_percentile: float = 0.95
    #: A behaviour seen in at most this fraction of peer entities is rare
    #: for the class, and therefore notable in itself.
    rare_participation: float = 0.10

    def __post_init__(self) -> None:
        if not self.group_by:
            raise ValueError("peers must be grouped by something")
        if not 0.0 < self.extreme_percentile < 1.0:
            raise ValueError("extreme_percentile must be a fraction")
        if self.min_peer_entities < 2:
            raise ValueError("a peer group of one is not a peer group")


def events_to_frame(events: Iterable[Event]) -> pd.DataFrame:
    """Flatten events into a frame, lifting the attrs a baseline groups on."""
    rows = []
    for event in events:
        row = {
            "event_type": event.event_type,
            "event_time": pd.Timestamp(event.event_time),
            "ingested_at": pd.Timestamp(event.ingested_at),
            "entity_key": event.entity.key if event.entity else None,
            "magnitude": event.magnitude,
            "area_key": event.area_key,
            "region_key": event.region_key,
        }
        row.update({k: v for k, v in event.attrs.items()})
        rows.append(row)
    frame = pd.DataFrame(rows)
    if not frame.empty and "vessel_class" not in frame.columns:
        frame["vessel_class"] = "unknown"
    return frame


def _percentile_rank(peers: np.ndarray, value: float) -> float:
    """Percentile rank of `value` among `peers`, handling ties correctly.

    Uses the midpoint of the tie range — the standard definition — rather
    than the fraction at or below the value. The difference is not cosmetic
    here. Behaviour durations are coarsely quantised (a loiter is a whole
    number of reporting intervals), so ties are the rule, not the exception.
    With a plain `<=`, every event tied at the maximum scores 1.0, and an
    entire tie group crosses an extremity threshold together: measured on a
    synthetic fleet that flagged 31 of 40 trawlers as extreme, which is the
    exact false-positive failure the peer baseline exists to prevent.
    """
    if peers.size == 0:
        return 0.0
    below = float(np.count_nonzero(peers < value))
    equal = float(np.count_nonzero(peers == value))
    return (below + 0.5 * equal) / peers.size


@dataclass(frozen=True)
class PeerAssessment:
    """How one event compares with its peers."""

    group: tuple
    event_type: str
    #: Vessels of this class we observed at all, excluding the subject.
    n_peers_observed: int
    #: Of those, how many showed this behaviour.
    n_peers_with_behaviour: int
    n_peer_events: int
    #: Fraction of observed peers that exhibit this behaviour at all.
    participation: float
    #: Where this event's magnitude sits in the peer distribution, 0-1.
    #: None when there were too few peer events to say.
    magnitude_percentile: float | None
    config: PeerConfig

    @property
    def rarity_assessable(self) -> bool:
        return self.n_peers_observed >= self.config.min_peer_entities

    @property
    def magnitude_assessable(self) -> bool:
        return (self.magnitude_percentile is not None
                and self.n_peer_events >= self.config.min_peer_events)

    @property
    def is_rare_for_class(self) -> bool:
        """Few peers do this at all, so doing it is itself notable."""
        return (self.rarity_assessable
                and self.participation <= self.config.rare_participation)

    @property
    def is_extreme_magnitude(self) -> bool:
        """Peers do this, but not to this degree."""
        return (self.magnitude_assessable
                and self.magnitude_percentile >= self.config.extreme_percentile)

    @property
    def is_unusual(self) -> bool:
        return self.is_rare_for_class or self.is_extreme_magnitude

    @property
    def score(self) -> float:
        """A single comparable number, for ranking within a region.

        Rarity is scaled to be commensurate with a percentile so the two
        routes to "unusual" can be ordered against one another rather than
        one silently dominating.
        """
        rarity = (1.0 - min(self.participation
                            / max(self.config.rare_participation, 1e-9), 1.0)
                  if self.rarity_assessable else 0.0)
        magnitude = (self.magnitude_percentile
                     if self.magnitude_assessable else 0.0)
        return float(max(rarity, magnitude))

    def describe(self) -> str:
        where = ", ".join(str(g) for g in self.group)
        if self.is_rare_for_class:
            return (f"only {self.participation:.0%} of {where} vessels show "
                    f"{self.event_type} at all "
                    f"({self.n_peers_observed} observed)")
        if self.is_extreme_magnitude:
            return (f"longer than {self.magnitude_percentile:.0%} of "
                    f"{self.event_type} events by {where} vessels "
                    f"({self.n_peer_events} compared)")
        if not self.magnitude_assessable:
            return (f"{self.participation:.0%} of {where} vessels show "
                    f"{self.event_type}; too few peer events "
                    f"({self.n_peer_events}) to judge how extreme this one is")
        return (f"within the normal range for {where} vessels "
                f"({self.n_peer_events} events compared)")


@dataclass(frozen=True)
class PeerBaseline:
    """Behaviour of comparable entities, fitted from past events.

    Holds two things, because rarity and magnitude need different evidence:
    the events themselves, and the *population* — every entity observed,
    including the silent majority that produced no events at all. Without the
    population, participation is computed only over vessels that did the
    thing, which makes every behaviour look universal within its class and
    inverts the rarity signal entirely.
    """

    events: pd.DataFrame
    population: pd.DataFrame
    config: PeerConfig
    #: False when the population was inferred from the events themselves
    #: rather than supplied. Participation is then 1.0 by construction, so
    #: rarity — the primary signal — was never actually tested. Carried so a
    #: caller can refuse to call that outcome "quiet".
    rarity_testable: bool = True

    @property
    def is_degenerate(self) -> bool:
        return not self.rarity_testable

    @classmethod
    def fit(cls, events: Iterable[Event] | pd.DataFrame,
            population: pd.DataFrame | None = None,
            config: PeerConfig | None = None,
            as_of: datetime | None = None) -> PeerBaseline:
        """Build from events plus the population they were drawn from.

        The `as_of` filter uses `ingested_at`, not `event_time`, for the same
        reason `AsOfView` does: a behaviour that had not yet been detected
        cannot have informed a baseline.
        """
        config = config or PeerConfig()
        frame = (events if isinstance(events, pd.DataFrame)
                 else events_to_frame(events))
        if as_of is not None and not frame.empty:
            frame = frame[frame["ingested_at"] <= pd.Timestamp(as_of)]

        rarity_testable = population is not None
        if population is None:
            # Fall back to the entities seen in the events. Honest but weak:
            # participation can then only ever be 1.0, so rarity says nothing.
            # Callers with access to the full traffic picture should pass it.
            population = (frame[["entity_key", *[c for c in config.group_by
                                                 if c in frame.columns]]]
                          .drop_duplicates()
                          if not frame.empty else pd.DataFrame())

        return cls(events=frame.reset_index(drop=True),
                   population=population.reset_index(drop=True),
                   config=config, rarity_testable=rarity_testable)

    @classmethod
    def from_positions(cls, positions: pd.DataFrame,
                       events: Iterable[Event] | pd.DataFrame,
                       config: PeerConfig | None = None,
                       as_of: datetime | None = None) -> PeerBaseline:
        """Convenience: derive the population from the position stream.

        Every vessel that reported a position was observed, whether or not it
        did anything — which is exactly the denominator rarity needs.
        """
        config = config or PeerConfig()
        columns = ["entity_key", *[c for c in config.group_by
                                   if c in positions.columns]]
        population = positions[columns].drop_duplicates()
        return cls.fit(events, population=population, config=config,
                       as_of=as_of)

    def assess(self, event: Event) -> PeerAssessment | None:
        """Compare one event with its peers, or decline.

        Returns None only when the *population* is too thin to say anything.
        A group with plenty of observed vessels but few events is still
        assessable — that is the rare-behaviour case, and it is the one worth
        catching.
        """
        subject = event.entity.key if event.entity else None
        attrs = dict(event.attrs)
        wanted = tuple(attrs.get(key) if key in attrs
                       else getattr(event, key, None)
                       for key in self.config.group_by)

        def _matching(frame: pd.DataFrame) -> pd.DataFrame:
            out = frame
            for key, value in zip(self.config.group_by, wanted, strict=True):
                if key in out.columns:
                    out = out[out[key] == value]
            return out

        if self.population.empty:
            return None
        peers_observed = _matching(self.population)
        peers_observed = peers_observed[
            peers_observed["entity_key"] != subject]
        n_observed = int(peers_observed["entity_key"].nunique())
        if n_observed < self.config.min_peer_entities:
            return None

        peer_events = self.events
        if not peer_events.empty:
            peer_events = _matching(
                peer_events[peer_events["event_type"] == event.event_type])
            # Leave-one-out: the subject never informs its own baseline.
            peer_events = peer_events[peer_events["entity_key"] != subject]

        n_events = len(peer_events)
        n_with = int(peer_events["entity_key"].nunique()) if n_events else 0

        percentile: float | None = None
        if n_events:
            magnitudes = peer_events["magnitude"].dropna().to_numpy(dtype=float)
            if magnitudes.size and event.magnitude is not None:
                percentile = _percentile_rank(magnitudes, float(event.magnitude))

        return PeerAssessment(
            group=wanted,
            event_type=event.event_type,
            n_peers_observed=n_observed,
            n_peers_with_behaviour=n_with,
            n_peer_events=n_events,
            participation=float(n_with / n_observed) if n_observed else 0.0,
            magnitude_percentile=percentile,
            config=self.config,
        )
