"""Entity layer: geodesy, behaviour primitives, and what they refuse to decide.

The load-bearing test here is
`test_loiter_events_alone_cannot_separate_fishing_from_suspicion`. It encodes
the reason the peer baseline lives at the indicator layer rather than in this
module — if that test ever starts passing trivially, someone has fused
observation with judgement and the trawler false-positive problem is back.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.core.contracts import Producer
from sentinel.entity import (
    BehaviourConfig,
    bearing,
    cross_track_distance,
    detect_ais_gaps,
    detect_loiter,
    detect_route_deviation,
    elapsed_seconds,
    extract_events,
    haversine,
    speed_knots,
)
from sentinel.eval.synthetic.vessels import (
    CABLE_CORRIDOR,
    VESSEL_SCENARIOS,
    VesselTrackGenerator,
)

ROUTE = ((51.80, 2.60), (54.60, 5.20))


def _positions(lat, lon, minutes_apart=10, key="v1",
               vessel_class="cargo") -> pd.DataFrame:
    n = len(lat)
    return pd.DataFrame({
        "entity_key": key,
        "timestamp": pd.date_range("2024-06-01", periods=n,
                                   freq=f"{minutes_apart}min"),
        "lat": np.asarray(lat, dtype=float),
        "lon": np.asarray(lon, dtype=float),
        "sog": np.zeros(n),
        "vessel_class": vessel_class,
    })


# =========================================================================
# geodesy
# =========================================================================
def test_haversine_matches_an_independently_computed_distance():
    """Rotterdam to Hamburg, ~413 km great-circle.

    Cross-checked against an equirectangular approximation rather than a
    remembered figure: dlat 1.63 deg is ~181 km, dlon 5.51 deg at 52.7 deg
    latitude is ~371 km, and sqrt(181^2 + 371^2) is ~413 km. Two independent
    methods agreeing is worth more here than one number from memory.
    """
    metres = float(haversine(51.92, 4.48, 53.55, 9.99))
    assert 405_000 < metres < 420_000

    # The approximation the figure above rests on, computed rather than assumed.
    dlat_km = (53.55 - 51.92) * 111.0
    dlon_km = (9.99 - 4.48) * 111.0 * np.cos(np.radians(52.735))
    assert metres / 1000.0 == pytest.approx(
        float(np.hypot(dlat_km, dlon_km)), rel=0.02)


def test_haversine_is_zero_for_the_same_point():
    assert float(haversine(52.0, 4.0, 52.0, 4.0)) == pytest.approx(0.0)


def test_bearing_cardinal_directions():
    assert float(bearing(52.0, 4.0, 53.0, 4.0)) == pytest.approx(0.0, abs=0.5)
    assert float(bearing(52.0, 4.0, 52.0, 5.0)) == pytest.approx(90.0, abs=0.5)


def test_elapsed_seconds_is_unit_safe():
    """pandas 3 stores datetime64[us], pandas 2 used [ns].

    The familiar `.astype('int64') / 1e9` is silently a thousand times wrong
    on one of them, and it fails in the worst way: speeds that are merely
    implausible rather than obviously broken. This is why callers pass
    timestamps rather than seconds.
    """
    stamps = pd.date_range("2024-06-01", periods=4, freq="10min")
    seconds = elapsed_seconds(stamps)
    assert np.isnan(seconds[0])
    assert np.allclose(seconds[1:], 600.0)


def test_speed_of_a_known_transit():
    """One degree of latitude in one hour is about 60 knots."""
    frame = _positions([52.0, 53.0], [4.0, 4.0], minutes_apart=60)
    speeds = speed_knots(frame["lat"], frame["lon"], frame["timestamp"])
    assert np.isnan(speeds[0])
    assert speeds[1] == pytest.approx(60.0, rel=0.02)


def test_first_speed_is_nan_not_zero():
    """Zero would invent a stationary period a loiter test would then flag."""
    frame = _positions([52.0, 52.0, 52.0], [4.0, 4.0, 4.0])
    assert np.isnan(speed_knots(frame["lat"], frame["lon"],
                                frame["timestamp"])[0])


def test_cross_track_distance_is_zero_on_the_line_and_signed_off_it():
    a, b = (52.0, 3.0), (54.0, 3.0)
    on_line = float(cross_track_distance([53.0], [3.0], *a, *b)[0])
    assert abs(on_line) < 1_000
    east = float(cross_track_distance([53.0], [3.5], *a, *b)[0])
    west = float(cross_track_distance([53.0], [2.5], *a, *b)[0])
    assert np.sign(east) != np.sign(west), "sides must be distinguishable"


# =========================================================================
# loiter
# =========================================================================
def test_loiter_detected_when_a_vessel_sits_still():
    frame = _positions([52.0] * 12, [4.0] * 12)  # two hours stationary
    events = detect_loiter(frame, BehaviourConfig())
    assert len(events) == 1
    assert events[0].event_type == "loiter"
    assert events[0].magnitude >= 60.0


def test_short_pause_is_not_a_loiter():
    frame = _positions([52.0] * 3, [4.0] * 3)  # 20 minutes
    assert detect_loiter(frame, BehaviourConfig()) == []


def test_moving_vessel_produces_no_loiter():
    lat = np.linspace(52.0, 53.0, 12)
    frame = _positions(lat, [4.0] * 12)
    assert detect_loiter(frame, BehaviourConfig()) == []


def test_loiter_uses_measured_speed_not_the_broadcast_field():
    """A stationary vessel reporting a stale speed-over-ground is exactly
    the case worth catching, so `sog` must not be taken on trust."""
    frame = _positions([52.0] * 12, [4.0] * 12)
    frame["sog"] = 12.0  # claims to be under way
    assert len(detect_loiter(frame, BehaviourConfig())) == 1


# =========================================================================
# gaps
# =========================================================================
def test_gap_detected_and_anchored_at_the_last_known_position():
    stamps = list(pd.date_range("2024-06-01", periods=3, freq="10min"))
    stamps.append(stamps[-1] + pd.Timedelta(hours=4))
    frame = pd.DataFrame({
        "entity_key": "v1", "timestamp": stamps,
        "lat": [52.0, 52.1, 52.2, 53.0], "lon": [4.0, 4.1, 4.2, 4.9],
        "sog": 12.0, "vessel_class": "cargo",
    })
    events = detect_ais_gaps(frame, BehaviourConfig())
    assert len(events) == 1
    gap = events[0]
    assert gap.magnitude == pytest.approx(240.0, rel=0.01)
    # Anchored where we last knew the vessel was, not where it reappeared.
    assert gap.geo.lat == pytest.approx(52.2)


def test_regular_reporting_produces_no_gaps():
    frame = _positions(np.linspace(52, 53, 12), np.linspace(4, 5, 12))
    assert detect_ais_gaps(frame, BehaviourConfig()) == []


# =========================================================================
# route deviation
# =========================================================================
def test_sustained_departure_from_the_lane_is_detected():
    lat = np.linspace(52.0, 53.0, 12)
    lon = np.full(12, 3.0)
    lon[4:9] = 3.6  # about 40 km off, for 50 minutes
    events = detect_route_deviation(_positions(lat, lon), BehaviourConfig(),
                                    route=((52.0, 3.0), (53.0, 3.0)))
    assert len(events) == 1
    assert events[0].magnitude > 10.0


def test_single_position_wobble_is_not_a_deviation():
    """GPS jitter must not bury the analyst in noise."""
    lat = np.linspace(52.0, 53.0, 12)
    lon = np.full(12, 3.0)
    lon[5] = 3.6  # one point off
    assert detect_route_deviation(_positions(lat, lon), BehaviourConfig(),
                                  route=((52.0, 3.0), (53.0, 3.0))) == []


# =========================================================================
# the events are observations, not verdicts
# =========================================================================
def test_loiter_events_alone_cannot_separate_fishing_from_suspicion():
    """Why the peer baseline must live at the indicator layer.

    A trawler working a fishing ground and a cargo vessel sitting on a cable
    corridor both produce a `loiter` event of the same type. Nothing in the
    event stream distinguishes them — the difference is vessel class and
    where they are, which is a baseline question.

    If this test ever fails because the primitives started filtering, someone
    has fused observation with judgement, and every trawler in the North Sea
    is about to be flagged daily.
    """
    generator = VesselTrackGenerator()
    fishing = extract_events(generator.fishing().positions)
    cable = extract_events(generator.loiter_near_cable().positions)

    fishing_loiters = [e for e in fishing if e.event_type == "loiter"]
    cable_loiters = [e for e in cable if e.event_type == "loiter"]
    assert fishing_loiters, "a trawler does loiter, and the record says so"
    assert cable_loiters
    assert (fishing_loiters[0].event_type == cable_loiters[0].event_type)


def test_events_carry_what_a_peer_baseline_will_need():
    """Vessel class and position must survive into the event, or the
    indicator layer cannot make the distinction the primitives declined to."""
    events = extract_events(VesselTrackGenerator().fishing().positions)
    loiter = next(e for e in events if e.event_type == "loiter")
    assert loiter.attrs.get("vessel_class") == "fishing"
    assert loiter.geo is not None


def test_ordinary_transit_produces_nothing_at_all():
    """The one control where silence really is the right answer."""
    assert extract_events(VesselTrackGenerator().transit().positions) == []


# =========================================================================
# contract conformance and causality
# =========================================================================
def test_events_are_marked_as_derived_and_name_their_method():
    events = extract_events(VesselTrackGenerator().fishing().positions)
    assert events
    for event in events:
        assert event.is_derived
        assert event.lineage.producer is Producer.ENTITY_ENGINE
        assert event.lineage.method
        assert event.entity is not None


def test_events_are_knowable_only_when_the_behaviour_completes():
    """A four-hour loiter is not knowable when it begins.

    Backdating detectability would make replay optimistic in exactly the way
    v1's missing arrival times did.
    """
    events = extract_events(VesselTrackGenerator().loiter_near_cable().positions)
    loiter = next(e for e in events if e.event_type == "loiter")
    assert loiter.ingested_at > loiter.event_time
    assert not loiter.known_at(loiter.event_time)
    assert loiter.known_at(loiter.ingested_at)


def test_every_scenario_can_be_processed_without_error():
    generator = VesselTrackGenerator()
    for kind in VESSEL_SCENARIOS:
        scenario = generator.build(kind)
        extract_events(scenario.positions, route=ROUTE)


def test_config_rejects_nonsense():
    with pytest.raises(ValueError, match="loiter speed"):
        BehaviourConfig(loiter_max_speed_knots=0)
    with pytest.raises(ValueError, match="gap threshold"):
        BehaviourConfig(gap_min_minutes=0)


# =========================================================================
# generator
# =========================================================================
def test_controls_are_labelled_as_such():
    generator = VesselTrackGenerator()
    for kind in ("transit", "fishing", "anchorage_wait"):
        assert generator.build(kind).is_control


def test_targets_declare_when_the_behaviour_happened():
    generator = VesselTrackGenerator()
    for kind in ("loiter_near_cable", "ais_gap", "route_deviation",
                 "identity_swap"):
        scenario = generator.build(kind)
        assert not scenario.is_control
        assert scenario.truth_start is not None


def test_generation_is_deterministic():
    a = VesselTrackGenerator(seed=5).loiter_near_cable().positions
    b = VesselTrackGenerator(seed=5).loiter_near_cable().positions
    pd.testing.assert_frame_equal(a, b)


def test_ais_gap_scenario_omits_positions_rather_than_zeroing_them():
    """Silence must be absence, or the detector is being asked to read a
    value instead of noticing nothing arrived."""
    scenario = VesselTrackGenerator().ais_gap()
    gaps = elapsed_seconds(scenario.positions["timestamp"]) / 60.0
    assert np.nanmax(gaps) > 60.0
    assert scenario.positions["lat"].notna().all()


def test_fishing_ground_is_genuinely_away_from_the_cable_corridor():
    """Otherwise the control and the target are the same place with
    different labels, and the suite proves nothing."""
    (lat_a, lon_a), (lat_b, lon_b) = CABLE_CORRIDOR
    fishing = VesselTrackGenerator().fishing().positions
    distance_km = np.abs(cross_track_distance(
        fishing["lat"], fishing["lon"], lat_a, lon_a, lat_b, lon_b)) / 1000.0
    assert distance_km.min() > 50.0
