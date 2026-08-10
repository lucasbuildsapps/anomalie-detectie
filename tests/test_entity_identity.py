"""Identity conflicts: one identifier, more vessels than there can be.

The last of the four NLD EEZ indicators to get a primitive. Before this, the
`identity_swap` scenario produced no events and the indicator could only ever
report insufficient data.

What is under test is mostly restraint. The detector says *one identifier was
used by what must be more than one vessel* — not who the impostor is, not that
anyone intended anything, and specifically not that a renamed vessel is
suspicious.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.entity import BehaviourConfig, detect_identity_conflicts

CONFIG = BehaviourConfig()


def _track(points, key="mmsi:1", vessel_class="cargo") -> pd.DataFrame:
    return pd.DataFrame({
        "entity_key": key,
        "timestamp": pd.to_datetime([p[0] for p in points]),
        "lat": [p[1] for p in points],
        "lon": [p[2] for p in points],
        "vessel_class": vessel_class,
    })


# =========================================================================
# what it catches
# =========================================================================
def test_an_impossible_jump_is_a_conflict():
    """400 km in ten minutes is not a fast ship."""
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 57.6, 4.0),
    ]), CONFIG)
    assert len(events) == 1
    assert events[0].event_type == "identity_conflict"
    assert events[0].magnitude > 100
    assert events[0].unit == "knots"


def test_an_ordinary_passage_is_not_a_conflict():
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 54.02, 4.0),
        ("2024-06-01 00:20", 54.04, 4.0),
    ]), CONFIG)
    assert events == []


def test_even_a_fast_craft_is_not_a_conflict():
    """The threshold is a physics check, not a speed limit. A 40-knot ferry
    must not be reported as two vessels sharing an identifier."""
    # ~40 knots: 0.12 degrees of latitude in 10 minutes is ~13 km ~ 43 kn.
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 54.12, 4.0),
    ]), CONFIG)
    assert events == []


def test_the_event_is_dated_to_the_report_that_revealed_it():
    """Dating it to the earlier position would claim we knew before the
    second message arrived."""
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 57.6, 4.0),
    ]), CONFIG)
    assert events[0].event_time == pd.Timestamp("2024-06-01 00:10")
    assert events[0].ingested_at == events[0].event_time


def test_the_event_carries_what_makes_it_checkable():
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 57.6, 4.0),
    ]), CONFIG)
    attrs = events[0].attrs
    assert attrs["distance_km"] > 300
    assert attrs["gap_seconds"] == 600
    assert attrs["from_lat"] == 54.0


# =========================================================================
# what it refuses to do
# =========================================================================
def test_duplicate_reports_at_one_instant_are_not_a_conflict():
    """The same message heard by two receivers is routine in AIS and says
    nothing about identity. Treating it as infinite speed would bury the
    real conflicts under reception noise."""
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:00", 54.5, 4.5),
    ]), CONFIG)
    assert events == []


def test_a_single_position_cannot_conflict_with_anything():
    assert detect_identity_conflicts(
        _track([("2024-06-01 00:00", 54.0, 4.0)]), CONFIG) == []


def test_out_of_order_reports_are_sorted_before_judging():
    """AIS arrives out of order routinely; judging on arrival order would
    manufacture conflicts out of ordinary late messages."""
    events = detect_identity_conflicts(_track([
        ("2024-06-01 00:20", 54.04, 4.0),
        ("2024-06-01 00:00", 54.0, 4.0),
        ("2024-06-01 00:10", 54.02, 4.0),
    ]), CONFIG)
    assert events == []


def test_a_threshold_near_real_vessel_speeds_is_refused():
    """It would turn a hard impossibility into a soft judgement about how
    fast ships go, which is a different and much weaker claim."""
    with pytest.raises(ValueError, match="physics check"):
        BehaviourConfig(implausible_speed_knots=40)


# =========================================================================
# through the fleet, and into a floor
# =========================================================================
def test_spoofed_identifiers_are_found_and_others_are_not():
    from sentinel.eval.entity_power import _identity_recall

    recall, false_alarms = _identity_recall(80)
    assert recall == 1.0
    assert false_alarms == 0


def test_a_small_displacement_is_not_detectable():
    """Below the physics boundary there is nothing to find, and the floor
    has to say so rather than implying total coverage."""
    from sentinel.eval.entity_power import _identity_recall

    recall, _ = _identity_recall(10)
    assert recall == 0.0


def test_the_floor_is_a_distance_and_names_its_cadence():
    """A displacement only becomes an implied speed once divided by the
    reporting interval, so the number describes the feed as much as the
    detector. Quoting it bare would misattribute that."""
    from sentinel.eval.entity_power import identity_detection_power

    power = identity_detection_power(n_repeats=16)
    assert power.unit == "km"
    assert np.isfinite(power.floor_magnitude)
    assert "cadence" in power.describe()
    assert "sparser feed" in power.describe()


def test_the_floor_refuses_attribution():
    from sentinel.eval.entity_power import identity_detection_power

    text = identity_detection_power(n_repeats=16).describe().lower()
    assert "never which one" in text
    assert "decoding error" in text
    for word in ("spoofing", "deliberate", "hostile"):
        assert word not in text


def test_the_indicator_is_wired_to_the_primitive_it_now_has():
    """The declared event type must match what the primitive emits, or the
    indicator keeps reporting insufficient data with a floor sitting unused."""
    from sentinel.regions import get_region

    indicator = next(i for i in get_region("nld_eez").indicators
                     if i.key == "identity_inconsistency")
    assert "identity_conflict" in indicator.test_config["event_types"]
