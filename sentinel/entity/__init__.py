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
    along_track_distance,
    bearing,
    cross_track_distance,
    distance_to_segment,
    elapsed_seconds,
    haversine,
    speed_knots,
)
from sentinel.entity.identity import detect_identity_conflicts
from sentinel.entity.infrastructure import (
    Corridor,
    detect_proximity,
    load_corridors,
)
from sentinel.entity.peers import (
    PeerAssessment,
    PeerBaseline,
    PeerConfig,
    events_to_frame,
)

__all__ = [
    "BehaviourConfig",
    "Corridor",
    "PeerAssessment",
    "PeerBaseline",
    "PeerConfig",
    "bearing",
    "along_track_distance",
    "cross_track_distance",
    "distance_to_segment",
    "detect_ais_gaps",
    "detect_identity_conflicts",
    "detect_loiter",
    "detect_proximity",
    "detect_route_deviation",
    "elapsed_seconds",
    "events_to_frame",
    "extract_events",
    "haversine",
    "load_corridors",
    "speed_knots",
]
