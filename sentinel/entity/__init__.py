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

__all__ = [
    "BehaviourConfig",
    "bearing",
    "cross_track_distance",
    "detect_ais_gaps",
    "detect_loiter",
    "detect_route_deviation",
    "elapsed_seconds",
    "extract_events",
    "haversine",
    "speed_knots",
]
