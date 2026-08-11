"""Geodesy, in numpy, with no geometry stack.

Everything the behaviour primitives need — distance, bearing, speed,
distance-to-a-route — is a handful of formulas. Pulling in shapely, GEOS and
proj for that would add a build dependency, a wheel that breaks on some
platforms, and an install step, for arithmetic that fits on a page. A solo
maintainer pays that cost every time the environment is rebuilt.

PostGIS will still be needed for *storage* and spatial indexing when the
position table lands. That is a different job from computing whether a vessel
sat still for two hours, which is all this module does.

Distances are metres, bearings degrees clockwise from north, speeds knots.
The spherical-earth approximation is good to about 0.5%, which is far below
the positional uncertainty of the AIS reports being judged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "EARTH_RADIUS_M",
    "bearing",
    "cross_track_distance",
    "elapsed_seconds",
    "haversine",
    "speed_knots",
]

EARTH_RADIUS_M = 6_371_008.8
_M_PER_NM = 1852.0


def haversine(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in metres. Broadcasts over arrays."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float))
                              for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (np.sin(dlat / 2.0) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Initial bearing in degrees clockwise from north, in [0, 360)."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float))
                              for v in (lat1, lon1, lat2, lon2))
    dlon = lon2 - lon1
    y = np.sin(dlon) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    return np.degrees(np.arctan2(y, x)) % 360.0


def elapsed_seconds(timestamps) -> np.ndarray:
    """Seconds between consecutive timestamps, unit-safe.

    Takes timestamps rather than a caller-computed number of seconds, and
    that is deliberate. pandas 3 stores datetimes as `datetime64[us]` where
    pandas 2 used `[ns]`, so the familiar `.astype("int64") / 1e9` is silently
    a thousand times wrong on one of them — and it fails in the worst way,
    producing speeds that are merely implausible rather than obviously
    broken. Converting through `np.timedelta64` removes the trap from every
    caller at once.

    The first element is NaN: there is no preceding position.
    """
    values = pd.to_datetime(pd.Series(timestamps)).to_numpy()
    out = np.full(len(values), np.nan)
    if len(values) < 2:
        return out
    deltas = np.diff(values) / np.timedelta64(1, "s")
    out[1:] = deltas.astype(float)
    return out


def speed_knots(lat, lon, timestamps) -> np.ndarray:
    """Speed between consecutive positions, in knots.

    The first element is NaN — there is no previous position to measure
    against, and returning zero there would invent a stationary period that
    a loitering test would then happily flag.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)

    out = np.full(len(lat), np.nan)
    if len(lat) < 2:
        return out
    metres = haversine(lat[:-1], lon[:-1], lat[1:], lon[1:])
    elapsed = elapsed_seconds(timestamps)[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.where(elapsed > 0,
                           (metres / elapsed) * 3600.0 / _M_PER_NM,
                           np.nan)
    return out


def cross_track_distance(lat, lon, lat_a: float, lon_a: float,
                         lat_b: float, lon_b: float) -> np.ndarray:
    """Signed perpendicular distance in metres from the great circle A→B.

    Used for route deviation: how far off the expected lane is this vessel.
    Signed so that a consistent drift to one side is distinguishable from
    zig-zagging around the line, which are different behaviours.

    Note this measures distance to the *infinite* great circle, not to the
    segment. A vessel far beyond B reads as close to the line, so callers
    testing a bounded corridor should also check along-track position.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    d13 = haversine(lat_a, lon_a, lat, lon) / EARTH_RADIUS_M
    theta13 = np.radians(bearing(lat_a, lon_a, lat, lon))
    theta12 = np.radians(bearing(lat_a, lon_a, lat_b, lon_b))
    return np.arcsin(
        np.clip(np.sin(d13) * np.sin(theta13 - theta12), -1.0, 1.0)
    ) * EARTH_RADIUS_M


def along_track_distance(lat, lon, lat_a: float, lon_a: float,
                         lat_b: float, lon_b: float) -> np.ndarray:
    """How far along A→B the projection of a point falls, in metres.

    The companion `cross_track_distance` needs: negative means the point lies
    behind A, and greater than the A→B length means beyond B. Without this a
    bounded corridor cannot be distinguished from the infinite great circle
    through it, and a vessel a hundred miles past the end of a cable reads as
    sitting on top of it.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    d13 = haversine(lat_a, lon_a, lat, lon) / EARTH_RADIUS_M
    cross = cross_track_distance(lat, lon, lat_a, lon_a,
                                 lat_b, lon_b) / EARTH_RADIUS_M
    ratio = np.cos(d13) / np.maximum(np.cos(cross), 1e-12)
    return np.arccos(np.clip(ratio, -1.0, 1.0)) * EARTH_RADIUS_M * np.sign(
        np.cos(np.radians(bearing(lat_a, lon_a, lat, lon))
               - np.radians(bearing(lat_a, lon_a, lat_b, lon_b))))


def distance_to_segment(lat, lon, lat_a: float, lon_a: float,
                        lat_b: float, lon_b: float) -> np.ndarray:
    """Distance in metres to the *segment* A-B, not the line through it.

    Clamped at both ends: past either endpoint the answer is the distance to
    that endpoint. This is the difference between "near this cable" and "near
    the great circle this cable happens to lie on", and cables are finite.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    length = float(haversine(lat_a, lon_a, lat_b, lon_b))
    if length <= 0.0:
        return haversine(lat_a, lon_a, lat, lon)

    along = along_track_distance(lat, lon, lat_a, lon_a, lat_b, lon_b)
    cross = np.abs(cross_track_distance(lat, lon, lat_a, lon_a, lat_b, lon_b))
    to_a = haversine(lat_a, lon_a, lat, lon)
    to_b = haversine(lat_b, lon_b, lat, lon)
    return np.where(along < 0.0, to_a, np.where(along > length, to_b, cross))
