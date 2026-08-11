"""Corridor proximity: the last declared-but-unemitted event type.

`loiter_near_infrastructure` has watched `proximity_critical_infra` since the
region was written and nothing produced it, so the indicator was judging
loitering anywhere in the EEZ rather than loitering *near a cable*.

The geometry tests matter more than they look. A cable is a finite line, and
the difference between "near this cable" and "near the great circle it lies
on" is the difference between a corridor and a hemisphere.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from sentinel.entity import (
    Corridor,
    detect_proximity,
    distance_to_segment,
    load_corridors,
)

CABLE = Corridor(key="cable_a", kind="cable",
                 vertices=((54.0, 4.0), (54.0, 5.0)), buffer_m=2000.0)


def _track(lats, lons=None, key="mmsi:1") -> pd.DataFrame:
    lons = lons or [4.5] * len(lats)
    return pd.DataFrame({
        "entity_key": key,
        "timestamp": pd.date_range("2024-06-01", periods=len(lats),
                                   freq="10min"),
        "lat": lats, "lon": lons, "vessel_class": "cargo",
    })


# =========================================================================
# geometry
# =========================================================================
def test_a_point_beyond_the_end_is_not_on_the_cable():
    """The failure this exists to prevent. Cross-track alone measures the
    infinite great circle, so a vessel far past the end reads as sitting on
    top of a cable it is nowhere near."""
    beyond = float(distance_to_segment(54.0, 7.0, 54.0, 4.0, 54.0, 5.0))
    assert beyond > 100_000


def test_perpendicular_distance_is_measured_correctly():
    """0.1 degree of latitude is about 11.1 km anywhere."""
    d = float(distance_to_segment(54.1, 4.5, 54.0, 4.0, 54.0, 5.0))
    assert 10_500 < d < 11_500


def test_a_point_on_the_segment_is_close_to_zero():
    d = float(distance_to_segment(54.0, 4.5, 54.0, 4.0, 54.0, 5.0))
    assert d < 200


def test_a_multi_segment_corridor_takes_the_nearest_leg():
    bent = Corridor(key="bent", kind="cable",
                    vertices=((54.0, 4.0), (54.0, 5.0), (53.0, 5.0)))
    near_second_leg = float(bent.distance_m(53.5, 5.02)[0])
    assert near_second_leg < 2_000


# =========================================================================
# events
# =========================================================================
def test_one_approach_produces_one_event():
    """A vessel crossing at ten knots emits a position every few seconds. One
    event each would bury the indicator in a single legitimate transit."""
    events = detect_proximity(
        _track([54.5, 54.1, 54.005, 54.001, 54.1, 54.6]), [CABLE])
    assert len(events) == 1
    assert events[0].event_type == "proximity_critical_infra"


def test_separate_approaches_are_separate_events():
    events = detect_proximity(
        _track([54.001, 54.5, 54.5, 54.001, 54.5, 54.001]), [CABLE])
    assert len(events) == 3


def test_a_track_that_starts_inside_the_buffer_is_not_lost():
    """The padding case, and the one a vessel that stopped on a cable
    produces: the run has no rising edge to find."""
    events = detect_proximity(_track([54.001, 54.001, 54.5]), [CABLE])
    assert len(events) == 1


def test_a_track_that_never_approaches_produces_nothing():
    assert detect_proximity(_track([56.0, 56.1, 56.2]), [CABLE]) == []


def test_the_magnitude_is_the_closest_approach():
    events = detect_proximity(_track([54.015, 54.001, 54.015]), [CABLE])
    assert events[0].magnitude < 500
    assert events[0].unit == "metres"


def test_the_event_names_the_corridor_it_is_about():
    """An analyst asked to check a proximity alert needs to know which cable."""
    events = detect_proximity(_track([54.001]), [CABLE])
    assert events[0].area_key == "cable_a"
    assert events[0].attrs["corridor_kind"] == "cable"
    assert events[0].attrs["buffer_m"] == 2000.0


def test_no_declared_corridors_means_no_events_rather_than_an_error():
    """A region with nothing declared cannot test proximity, and that is a
    null result rather than a crash."""
    assert detect_proximity(_track([54.001]), []) == []


def test_each_corridor_carries_its_own_buffer():
    """A wind-farm boundary and a deep-water cable do not warrant the same
    margin, and one global buffer would be wrong for both."""
    tight = Corridor(key="t", kind="cable", vertices=CABLE.vertices,
                     buffer_m=100.0)
    wide = Corridor(key="w", kind="wind_farm", vertices=CABLE.vertices,
                    buffer_m=20_000.0)
    track = _track([54.05])  # ~5.5 km off
    assert detect_proximity(track, [tight]) == []
    assert len(detect_proximity(track, [wide])) == 1


# =========================================================================
# declaration
# =========================================================================
def test_a_corridor_needs_at_least_two_vertices():
    with pytest.raises(ValueError, match="not a corridor"):
        Corridor(key="p", kind="cable", vertices=((54.0, 4.0),))


def test_a_corridor_needs_a_positive_buffer():
    with pytest.raises(ValueError, match="positive buffer"):
        Corridor(key="p", kind="cable", vertices=CABLE.vertices, buffer_m=0)


def test_corridors_load_from_a_declaration_file(tmp_path):
    path = tmp_path / "corridors.json"
    path.write_text(json.dumps([{
        "key": "cable_x", "kind": "cable",
        "vertices": [[54.1, 3.2], [54.05, 4.4]],
        "buffer_m": 1500, "source_url": "https://example.org/x",
    }]))
    corridors = load_corridors(path)
    assert len(corridors) == 1
    assert corridors[0].buffer_m == 1500
    assert corridors[0].source_url == "https://example.org/x"


def test_an_absent_declaration_is_not_an_error(tmp_path):
    """A region with no declared infrastructure simply cannot test
    proximity. Inventing a default corridor would be far worse."""
    assert load_corridors(tmp_path / "nope.json") == ()


def test_the_shipped_declaration_is_empty():
    """It ships empty on purpose: invented coordinates would be
    indistinguishable from verified ones."""
    assert load_corridors("data/infrastructure/nld_eez.json") == ()


def test_extract_events_emits_proximity_when_corridors_are_declared():
    from sentinel.entity import extract_events

    track = _track([54.001] * 4)
    without = [e.event_type for e in extract_events(track)]
    with_corridors = [e.event_type for e in
                      extract_events(track, corridors=[CABLE])]
    assert "proximity_critical_infra" not in without
    assert "proximity_critical_infra" in with_corridors
