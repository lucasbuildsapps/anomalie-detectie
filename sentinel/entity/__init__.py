"""Entity layer: vessel behaviour as typed events.

Emits observations, never verdicts. Whether a behaviour is unusual is decided
by an indicator against a peer baseline — see `behaviour` for why that
separation is load-bearing rather than tidy.
"""
from sentinel.entity.behaviour import (
    BehaviourConfig,
    detect_ais_gaps,
    detect_loiter,
    detect_route_deviation,
    extract_events,
)
from sentinel.entity.geo import (
    bearing,
    cross_track_distance,
    elapsed_seconds,
    haversine,
    speed_knots,
)
from sentinel.entity.identity import detect_identity_conflicts
from sentinel.entity.peers import (
    PeerAssessment,
    PeerBaseline,
    PeerConfig,
    events_to_frame,
)

__all__ = [
    "BehaviourConfig",
    "PeerAssessment",
    "PeerBaseline",
    "PeerConfig",
    "bearing",
    "cross_track_distance",
    "detect_ais_gaps",
    "detect_identity_conflicts",
    "detect_loiter",
    "detect_route_deviation",
    "elapsed_seconds",
    "events_to_frame",
    "extract_events",
    "haversine",
    "speed_knots",
]
