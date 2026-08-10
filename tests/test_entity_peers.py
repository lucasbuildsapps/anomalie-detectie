"""Peer baselines: unusual *for this class, here* — and the traps in that.

Three failures are pinned here, each of which was real during development:
including a vessel in its own baseline, requiring peer *events* before rarity
can be judged, and mishandling ties in the magnitude percentile. All three
produce plausible-looking output, which is why they need tests rather than
care.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.core.contracts import (
    EntityRef,
    Event,
    Lineage,
    Producer,
)
from sentinel.entity import extract_events
from sentinel.entity.peers import (
    PeerBaseline,
    PeerConfig,
    events_to_frame,
)
from sentinel.eval.synthetic.vessels import build_fleet

T0 = datetime(2024, 6, 1)


def _event(entity: str, vessel_class: str = "cargo", magnitude: float = 100.0,
           event_type: str = "loiter", hours: int = 0) -> Event:
    moment = T0 + pd.Timedelta(hours=hours).to_pytimedelta()
    return Event(
        event_type=event_type,
        event_time=moment,
        ingested_at=moment,
        region_key="nld_eez",
        entity=EntityRef("vessel", entity),
        magnitude=magnitude,
        lineage=Lineage(producer=Producer.ENTITY_ENGINE, method="loiter.v1"),
        attrs={"vessel_class": vessel_class},
    )


def _population(n_cargo: int = 0, n_fishing: int = 0) -> pd.DataFrame:
    rows = [{"entity_key": f"cargo-{i}", "vessel_class": "cargo"}
            for i in range(n_cargo)]
    rows += [{"entity_key": f"fish-{i}", "vessel_class": "fishing"}
             for i in range(n_fishing)]
    return pd.DataFrame(rows)


# =========================================================================
# leave-one-out
# =========================================================================
def test_a_vessel_never_informs_its_own_baseline():
    """Otherwise a ship that loiters constantly teaches the baseline that
    constant loitering is normal, and then passes unremarked."""
    events = [_event("cargo-0", magnitude=500.0, hours=h) for h in range(20)]
    baseline = PeerBaseline.fit(events, population=_population(n_cargo=30))
    assessment = baseline.assess(events[0])
    assert assessment is not None
    assert assessment.n_peer_events == 0, "its own events must be excluded"
    assert assessment.n_peers_with_behaviour == 0


def test_leave_one_out_keeps_other_vessels_of_the_class():
    events = [_event(f"cargo-{i}") for i in range(12)]
    baseline = PeerBaseline.fit(events, population=_population(n_cargo=30))
    assessment = baseline.assess(events[0])
    assert assessment.n_peer_events == 11


# =========================================================================
# the two denominators
# =========================================================================
def test_rare_behaviour_is_assessable_despite_having_few_peer_events():
    """The flagship case, and the one an events-only threshold silences.

    One cargo vessel stopping on a cable route has almost no peer events
    precisely because the behaviour is rare. Requiring many peer events
    before judging would decline exactly when the signal is strongest.
    """
    events = [_event("cargo-0"), _event("cargo-1")]
    baseline = PeerBaseline.fit(events, population=_population(n_cargo=60))
    assessment = baseline.assess(events[0])
    assert assessment is not None
    assert assessment.rarity_assessable
    assert not assessment.magnitude_assessable, "one peer event is not a scale"
    assert assessment.is_rare_for_class
    assert assessment.is_unusual


def test_common_behaviour_is_not_rare_however_many_events():
    events = [_event(f"fish-{i}", vessel_class="fishing")
              for i in range(38)]
    baseline = PeerBaseline.fit(events, population=_population(n_fishing=40))
    assessment = baseline.assess(events[0])
    assert assessment.participation > 0.9
    assert not assessment.is_rare_for_class


def test_population_is_the_denominator_not_the_event_set():
    """Counting only vessels that did the thing makes every behaviour look
    universal within its class, inverting the rarity signal."""
    events = [_event(f"cargo-{i}") for i in range(3)]
    with_population = PeerBaseline.fit(events,
                                       population=_population(n_cargo=100))
    assert with_population.assess(events[0]).participation < 0.05

    without = PeerBaseline.fit(events)  # falls back to the event set
    assert without.assess(events[0]) is None or \
        without.assess(events[0]).participation == 1.0


def test_thin_population_declines_rather_than_guessing():
    events = [_event("cargo-0"), _event("cargo-1")]
    baseline = PeerBaseline.fit(events, population=_population(n_cargo=3))
    assert baseline.assess(events[0]) is None


# =========================================================================
# magnitude percentile and ties
# =========================================================================
def test_percentile_uses_the_midpoint_of_a_tie_range():
    """Coarsely quantised durations make ties the rule, not the exception.

    With a plain `<=`, everything tied at the maximum scores 1.0 and an
    entire tie group crosses the extremity threshold together — measured on
    a synthetic fleet, that flagged 31 of 40 trawlers.
    """
    events = [_event(f"fish-{i}", vessel_class="fishing", magnitude=100.0)
              for i in range(20)]
    baseline = PeerBaseline.fit(events, population=_population(n_fishing=40))
    assessment = baseline.assess(events[0])
    # Tied with every peer: the midpoint is 0.5, not 1.0.
    assert assessment.magnitude_percentile == pytest.approx(0.5)
    assert not assessment.is_extreme_magnitude


def test_a_genuine_outlier_still_scores_high():
    events = [_event(f"fish-{i}", vessel_class="fishing",
                     magnitude=float(60 + i * 5)) for i in range(30)]
    outlier = _event("fish-99", vessel_class="fishing", magnitude=10_000.0)
    baseline = PeerBaseline.fit(events, population=_population(n_fishing=40))
    assessment = baseline.assess(outlier)
    assert assessment.magnitude_percentile == pytest.approx(1.0)
    assert assessment.is_extreme_magnitude


# =========================================================================
# causality
# =========================================================================
def test_baseline_can_be_restricted_to_what_was_known():
    """Uses ingested_at: a behaviour not yet detected cannot have informed
    a baseline."""
    events = [_event(f"cargo-{i}", hours=i) for i in range(20)]
    early = PeerBaseline.fit(events, population=_population(n_cargo=60),
                             as_of=T0 + pd.Timedelta(hours=5).to_pytimedelta())
    late = PeerBaseline.fit(events, population=_population(n_cargo=60))
    assert len(early.events) < len(late.events)


# =========================================================================
# the whole point, on a fleet
# =========================================================================
def test_the_fleet_case_finds_targets_without_flagging_trawlers():
    """102 vessels: 40 trawlers that loiter by trade, 60 cargo that do not,
    and 2 cargo vessels that stopped where they should not.

    This is the test the entire entity layer exists to pass. Failing it in
    either direction — missing the targets, or flagging the fishing fleet —
    makes the capability unusable at North Sea scale.
    """
    fleet = build_fleet()
    events: list[Event] = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group))

    baseline = PeerBaseline.from_positions(fleet.positions, events)
    flagged = {
        event.entity.key for event in events
        if (assessment := baseline.assess(event)) is not None
        and assessment.is_unusual
    }

    assert set(fleet.target_keys) <= flagged, "both targets must be found"
    assert not [k for k in flagged if k.startswith("fish")], (
        "no trawler may be flagged for doing its job")
    assert len(flagged) == len(fleet.target_keys)


def test_the_fleet_explanation_names_the_comparison():
    fleet = build_fleet()
    events: list[Event] = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group))
    baseline = PeerBaseline.from_positions(fleet.positions, events)

    target = next(e for e in events if e.entity.key in fleet.target_keys)
    text = baseline.assess(target).describe()
    assert "cargo" in text
    assert "loiter" in text
    assert "observed" in text


# =========================================================================
# plumbing
# =========================================================================
def test_events_to_frame_lifts_grouping_attributes():
    frame = events_to_frame([_event("v1", vessel_class="tanker")])
    assert frame["vessel_class"].iloc[0] == "tanker"
    assert frame["entity_key"].iloc[0] == "v1"


def test_empty_inputs_are_safe():
    baseline = PeerBaseline.fit([], population=pd.DataFrame())
    assert baseline.assess(_event("v1")) is None


def test_config_rejects_nonsense():
    with pytest.raises(ValueError, match="grouped by something"):
        PeerConfig(group_by=())
    with pytest.raises(ValueError, match="not a peer group"):
        PeerConfig(min_peer_entities=1)
    with pytest.raises(ValueError, match="fraction"):
        PeerConfig(extreme_percentile=1.5)


def test_score_is_comparable_across_the_two_routes_to_unusual():
    """Rarity and magnitude must be orderable against each other, or one
    silently dominates any ranking built on the score."""
    rare_events = [_event("cargo-0"), _event("cargo-1")]
    rare = PeerBaseline.fit(rare_events, population=_population(n_cargo=100))
    rare_score = rare.assess(rare_events[0]).score

    common = [_event(f"fish-{i}", vessel_class="fishing",
                     magnitude=float(60 + i)) for i in range(30)]
    outlier = _event("fish-99", vessel_class="fishing", magnitude=9_999.0)
    extreme = PeerBaseline.fit(common, population=_population(n_fishing=40))
    extreme_score = extreme.assess(outlier).score

    assert 0.0 <= rare_score <= 1.0
    assert 0.0 <= extreme_score <= 1.0
    assert rare_score > 0.5 and extreme_score > 0.5


def test_normal_behaviour_scores_low():
    events = [_event(f"fish-{i}", vessel_class="fishing",
                     magnitude=float(60 + i)) for i in range(30)]
    baseline = PeerBaseline.fit(events, population=_population(n_fishing=40))
    middle = _event("fish-99", vessel_class="fishing", magnitude=75.0)
    assessment = baseline.assess(middle)
    assert not assessment.is_unusual
    assert assessment.score < 0.9
    assert np.isfinite(assessment.score)
