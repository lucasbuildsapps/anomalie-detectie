"""Synthetic AIS tracks with known behaviour.

The entity twin of `scenarios.py`, and built for the same reason: without
ground truth we cannot tell whether the behaviour detectors work, and no
amount of analyst feedback supplies it — an analyst only ever sees the
vessels the system surfaced.

Why the negative controls carry the weight here
-----------------------------------------------
Maritime behaviour is where a naive detector embarrasses itself. Loitering is
suspicious for a bulk carrier beside a cable route and completely normal for
a trawler on a fishing ground; an AIS gap can be sabotage or a cheap
transponder in bad weather; sitting still for six hours is a dark rendezvous
or a berth queue at Rotterdam.

So three of the seven scenarios are legitimate behaviour that *must not*
fire: fishing, anchorage waiting, and ordinary transit. A detector that
cannot pass those is not a detector, it is a vessel counter — and the North
Sea has thousands of vessels doing exactly these things every day, so a
false-positive rate that looks tolerable per vessel becomes unusable at
fleet scale.

Geometry is North Sea-shaped: a notional cable corridor, a fishing ground and
an anchorage, positioned so that "near infrastructure" and "normal fishing"
are genuinely separate places rather than the same coordinates with different
labels.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "CABLE_CORRIDOR",
    "VESSEL_SCENARIOS",
    "Fleet",
    "VesselScenario",
    "VesselTrackGenerator",
    "build_fleet",
]

#: A notional cable route across the southern North Sea: (lat, lon) endpoints.
CABLE_CORRIDOR = ((52.20, 3.20), (53.40, 4.60))

#: A fishing ground, well away from the corridor.
FISHING_GROUND = (54.30, 3.10)

#: An anchorage off a major port.
ANCHORAGE = (51.95, 3.95)

VESSEL_SCENARIOS = (
    "transit",              # control: ordinary passage
    "fishing",              # control: loiters legitimately, by trade
    "anchorage_wait",       # control: stationary for hours, legitimately
    "loiter_near_cable",    # a vessel class that should not linger, lingering
    "ais_gap",              # transmission stops mid-passage
    "route_deviation",      # departs the expected lane
    "identity_swap",        # broadcast identity changes mid-track
)

_CONTROLS = frozenset({"transit", "fishing", "anchorage_wait"})


@dataclass(frozen=True)
class VesselScenario:
    """Generated positions for one vessel, and what was done to it."""

    kind: str
    positions: pd.DataFrame          # entity_key, timestamp, lat, lon, sog
    vessel_class: str
    truth_start: pd.Timestamp | None = None
    truth_end: pd.Timestamp | None = None
    params: dict = field(default_factory=dict)

    @property
    def is_control(self) -> bool:
        """Legitimate behaviour that must not be flagged."""
        return self.kind in _CONTROLS

    @property
    def entity_key(self) -> str:
        return str(self.positions["entity_key"].iloc[0])

    def describe(self) -> str:
        if self.is_control:
            return (f"{self.kind} ({self.vessel_class}): legitimate, "
                    f"{len(self.positions)} positions")
        return (f"{self.kind} ({self.vessel_class}): "
                f"{self.truth_start} to {self.truth_end}")


class VesselTrackGenerator:
    """Builds AIS-like position streams with controlled behaviour.

    Reporting interval is a parameter because it matters: AIS transmits every
    few seconds under way and every few minutes at anchor, and a gap detector
    tuned on one cadence misfires on the other. Ten minutes is a deliberately
    coarse, satellite-like cadence — the pessimistic case for detection.
    """

    def __init__(self, seed: int = 42, start: str = "2024-06-01",
                 interval_minutes: int = 10, hours: float = 30.0) -> None:
        self.seed = seed
        self.start = pd.Timestamp(start)
        self.interval_minutes = interval_minutes
        self.hours = hours

    # -- helpers ---------------------------------------------------------
    def _timeline(self, n: int) -> pd.DatetimeIndex:
        return pd.date_range(self.start, periods=n,
                             freq=f"{self.interval_minutes}min")

    @property
    def _n_points(self) -> int:
        return int(self.hours * 60 / self.interval_minutes)

    def _frame(self, key: str, times, lat, lon, sog,
               vessel_class: str) -> pd.DataFrame:
        return pd.DataFrame({
            "entity_key": key,
            "timestamp": times,
            "lat": np.asarray(lat, dtype=float),
            "lon": np.asarray(lon, dtype=float),
            "sog": np.asarray(sog, dtype=float),
            "vessel_class": vessel_class,
        })

    def _jitter(self, rng, n: int, metres: float = 30.0) -> np.ndarray:
        """Positional noise. Real AIS positions wobble; a generator that
        produces perfectly smooth tracks makes every detector look good."""
        return rng.normal(0.0, metres / 111_000.0, n)

    def punch_gap(self, frame: pd.DataFrame, minutes: float,
                  at_fraction: float = 0.5) -> pd.DataFrame:
        """Remove reports so the track carries a silence of `minutes`.

        Deleting rows rather than marking them is the point: a receiver that
        heard nothing produces no row, and a detector that relies on a
        "missing" flag would be testing something the real feed never sends.
        """
        if minutes <= 0 or len(frame) < 3:
            return frame
        n_drop = int(round(minutes / self.interval_minutes)) - 1
        if n_drop < 1:
            return frame
        start = max(1, min(int(len(frame) * at_fraction),
                           len(frame) - n_drop - 1))
        keep = np.ones(len(frame), dtype=bool)
        keep[start:start + n_drop] = False
        return frame[keep].reset_index(drop=True)

    # -- controls --------------------------------------------------------
    def transit(self, vessel_class: str = "cargo") -> VesselScenario:
        """A straight passage at steady speed. Nothing to find."""
        rng = np.random.default_rng(self.seed)
        n = self._n_points
        lat = np.linspace(51.80, 54.60, n) + self._jitter(rng, n)
        lon = np.linspace(2.60, 5.20, n) + self._jitter(rng, n)
        sog = rng.normal(12.0, 0.6, n).clip(8.0, 16.0)
        return VesselScenario("transit", self._frame("v-transit",
                                                     self._timeline(n), lat,
                                                     lon, sog, vessel_class),
                              vessel_class)

    def fishing(self, vessel_class: str = "fishing") -> VesselScenario:
        """Slow, meandering work on a fishing ground.

        The hardest control. This vessel loiters for hours by definition, and
        a loiter detector that does not know about vessel class will flag it
        every single day.
        """
        rng = np.random.default_rng(self.seed + 1)
        n = self._n_points
        # Random walk around the ground, at trawling speed.
        lat = FISHING_GROUND[0] + np.cumsum(rng.normal(0, 0.0012, n))
        lon = FISHING_GROUND[1] + np.cumsum(rng.normal(0, 0.0018, n))
        sog = rng.normal(3.2, 0.8, n).clip(0.5, 6.0)
        return VesselScenario("fishing", self._frame("v-fishing",
                                                     self._timeline(n), lat,
                                                     lon, sog, vessel_class),
                              vessel_class)

    def anchorage_wait(self, vessel_class: str = "tanker") -> VesselScenario:
        """Stationary at an anchorage for many hours. Also legitimate."""
        rng = np.random.default_rng(self.seed + 2)
        n = self._n_points
        lat = ANCHORAGE[0] + self._jitter(rng, n, 80.0)
        lon = ANCHORAGE[1] + self._jitter(rng, n, 80.0)
        sog = np.abs(rng.normal(0.2, 0.15, n))
        return VesselScenario("anchorage_wait",
                              self._frame("v-anchor", self._timeline(n), lat,
                                          lon, sog, vessel_class),
                              vessel_class)

    # -- behaviours of interest -----------------------------------------
    def loiter_near_cable(self, vessel_class: str = "cargo",
                          hours_loitering: float = 4.0) -> VesselScenario:
        """Transit, then sit still astride the cable corridor, then continue.

        The behaviour that matters: not that the vessel stopped, but that a
        vessel of a class which does not normally stop, stopped *there*.
        """
        rng = np.random.default_rng(self.seed + 3)
        n = self._n_points
        stall = int(hours_loitering * 60 / self.interval_minutes)
        stall = min(stall, n - 4)
        before = (n - stall) // 2
        after = n - stall - before

        mid_lat = (CABLE_CORRIDOR[0][0] + CABLE_CORRIDOR[1][0]) / 2
        mid_lon = (CABLE_CORRIDOR[0][1] + CABLE_CORRIDOR[1][1]) / 2

        lat = np.concatenate([
            np.linspace(51.80, mid_lat, before),
            np.full(stall, mid_lat),
            np.linspace(mid_lat, 54.20, after),
        ]) + self._jitter(rng, n, 40.0)
        lon = np.concatenate([
            np.linspace(2.60, mid_lon, before),
            np.full(stall, mid_lon),
            np.linspace(mid_lon, 5.00, after),
        ]) + self._jitter(rng, n, 40.0)
        sog = np.concatenate([
            rng.normal(11.0, 0.5, before),
            np.abs(rng.normal(0.3, 0.2, stall)),
            rng.normal(11.0, 0.5, after),
        ]).clip(0.0, 16.0)

        times = self._timeline(n)
        return VesselScenario(
            "loiter_near_cable",
            self._frame("v-loiter", times, lat, lon, sog, vessel_class),
            vessel_class,
            truth_start=times[before], truth_end=times[before + stall - 1],
            params={"hours_loitering": hours_loitering},
        )

    def ais_gap(self, vessel_class: str = "cargo",
                hours_dark: float = 3.0) -> VesselScenario:
        """Transit with a stretch of missing reports.

        The positions are *absent*, not zeroed. A generator that emitted
        zeros would be testing a different thing entirely — the detector has
        to notice silence, not read a value.
        """
        rng = np.random.default_rng(self.seed + 4)
        n = self._n_points
        lat = np.linspace(51.80, 54.60, n) + self._jitter(rng, n)
        lon = np.linspace(2.60, 5.20, n) + self._jitter(rng, n)
        sog = rng.normal(12.0, 0.6, n).clip(8.0, 16.0)
        times = self._timeline(n)

        gap_points = int(hours_dark * 60 / self.interval_minutes)
        start = n // 3
        keep = np.ones(n, dtype=bool)
        keep[start:start + gap_points] = False

        frame = self._frame("v-dark", times, lat, lon, sog, vessel_class)
        return VesselScenario(
            "ais_gap", frame[keep].reset_index(drop=True), vessel_class,
            truth_start=times[start - 1], truth_end=times[start + gap_points],
            params={"hours_dark": hours_dark},
        )

    def route_deviation(self, vessel_class: str = "cargo",
                        offset_km: float = 25.0) -> VesselScenario:
        """A passage that departs the expected lane and comes back."""
        rng = np.random.default_rng(self.seed + 5)
        n = self._n_points
        lat = np.linspace(51.80, 54.60, n)
        lon = np.linspace(2.60, 5.20, n)

        start, stop = int(n * 0.40), int(n * 0.65)
        bulge = np.zeros(n)
        span = stop - start
        bulge[start:stop] = np.sin(np.linspace(0, np.pi, span)) * (
            offset_km / 111.0)
        lon = lon + bulge + self._jitter(rng, n)
        lat = lat + self._jitter(rng, n)
        sog = rng.normal(12.0, 0.6, n).clip(8.0, 16.0)

        times = self._timeline(n)
        return VesselScenario(
            "route_deviation",
            self._frame("v-deviate", times, lat, lon, sog, vessel_class),
            vessel_class,
            truth_start=times[start], truth_end=times[stop - 1],
            params={"offset_km": offset_km},
        )

    def identity_swap(self, vessel_class: str = "cargo") -> VesselScenario:
        """One physical track, two broadcast identities.

        Modelled as a single continuous set of positions whose `entity_key`
        changes partway. Whether that is reflagging or spoofing is not
        decidable from the track; the record simply shows the conflict.
        """
        rng = np.random.default_rng(self.seed + 6)
        n = self._n_points
        lat = np.linspace(51.80, 54.60, n) + self._jitter(rng, n)
        lon = np.linspace(2.60, 5.20, n) + self._jitter(rng, n)
        sog = rng.normal(12.0, 0.6, n).clip(8.0, 16.0)
        times = self._timeline(n)

        frame = self._frame("v-swap-a", times, lat, lon, sog, vessel_class)
        switch = n // 2
        frame.loc[switch:, "entity_key"] = "v-swap-b"
        return VesselScenario(
            "identity_swap", frame, vessel_class,
            truth_start=times[switch - 1], truth_end=times[switch],
            params={"switch_index": switch},
        )

    # -- suite -----------------------------------------------------------
    def build(self, kind: str, **kwargs) -> VesselScenario:
        if kind not in VESSEL_SCENARIOS:
            raise ValueError(f"unknown vessel scenario {kind!r}; "
                             f"choose from {VESSEL_SCENARIOS}")
        return getattr(self, kind)(**kwargs)

    def suite(self) -> Iterator[VesselScenario]:
        for kind in VESSEL_SCENARIOS:
            yield self.build(kind)


@dataclass(frozen=True)
class Fleet:
    """Many vessels at once, which is the only way to test a peer baseline.

    A single track cannot show whether a behaviour is unusual — that question
    only exists relative to comparable vessels. And the realistic difficulty
    is not detecting a loitering vessel; it is finding the one cargo vessel
    that stopped among hundreds of trawlers that stop constantly.
    """

    positions: pd.DataFrame
    target_keys: tuple[str, ...]
    n_vessels: int

    @property
    def control_keys(self) -> tuple[str, ...]:
        keys = self.positions["entity_key"].unique().tolist()
        return tuple(k for k in keys if k not in self.target_keys)


def build_fleet(seed: int = 42, n_fishing: int = 40, n_cargo: int = 60,
                n_targets: int = 2, hours: float = 30.0,
                target_loiter_hours: float = 4.0,
                n_contaminating: int = 0,
                target_gap_minutes: float = 0.0,
                background_gap_minutes: float = 60.0,
                background_gap_rate: float = 0.7) -> Fleet:
    """A day's traffic: trawlers that loiter by trade, cargo that does not,
    and a couple of cargo vessels that stop where they should not.

    The class mix matters. Make the fleet all cargo and the peer baseline has
    nothing to learn; make it all fishing and the target is invisible. The
    proportions here are not calibrated to real North Sea traffic — they are
    chosen so both failure modes are reachable.
    """
    frames: list[pd.DataFrame] = []
    targets: list[str] = []
    gap_rng = np.random.default_rng(seed + 9000)

    def _with_background_gap(frame, generator):
        """Ordinary reception dropouts, on a share of the fleet.

        Without these every gap is rare by construction and the peer baseline
        flags any silence at all — which would make the AIS-gap floor a
        measurement of a world that does not exist. Real AIS is patchy, so the
        question is never "did it go quiet" but "for longer than its class
        normally does".
        """
        if background_gap_minutes <= 0 or gap_rng.random() > background_gap_rate:
            return frame
        # Heavy-tailed, not uniform. Reception dropouts are mostly short with
        # an occasional long one — a terrestrial receiver going down, a vessel
        # crossing a coverage hole. An earlier version drew them uniformly,
        # which produced only three distinct durations after quantisation to
        # the reporting interval; against a distribution that tight, any extra
        # step reads as extreme and ordinary vessels get flagged. A detector
        # tested on that is tested against a world where all outages are the
        # same length.
        minutes = background_gap_minutes * float(gap_rng.lognormal(0.0, 0.6))
        return generator.punch_gap(frame, minutes,
                                   at_fraction=float(gap_rng.uniform(0.2, 0.7)))

    for i in range(n_fishing):
        generator = VesselTrackGenerator(seed=seed + 1000 + i, hours=hours)
        frame = generator.fishing().positions.copy()
        frame["entity_key"] = f"fish-{i:03d}"
        frames.append(_with_background_gap(frame, generator))

    for i in range(n_cargo):
        generator = VesselTrackGenerator(seed=seed + 2000 + i, hours=hours)
        frame = generator.transit().positions.copy()
        frame["entity_key"] = f"cargo-{i:03d}"
        frames.append(_with_background_gap(frame, generator))

    # Cargo vessels that also loiter, but are not targets. As their number
    # rises, loitering stops being rare for the class and the peer baseline
    # stops flagging it — the entity analogue of an escalation becoming the
    # baseline. `n_contaminating` exists so that degradation can be measured
    # rather than discovered in the field.
    for i in range(n_contaminating):
        generator = VesselTrackGenerator(seed=seed + 4000 + i, hours=hours)
        frame = generator.loiter_near_cable(
            hours_loitering=target_loiter_hours).positions.copy()
        frame["entity_key"] = f"cargo-also-{i:03d}"
        frames.append(frame)

    for i in range(n_targets):
        generator = VesselTrackGenerator(seed=seed + 3000 + i, hours=hours)
        frame = generator.loiter_near_cable(
            hours_loitering=target_loiter_hours).positions.copy()
        key = f"cargo-target-{i:02d}"
        frame["entity_key"] = key
        if target_gap_minutes > 0:
            frame = generator.punch_gap(frame, target_gap_minutes,
                                        at_fraction=0.75)
        targets.append(key)
        frames.append(frame)

    positions = pd.concat(frames, ignore_index=True)
    return Fleet(positions=positions, target_keys=tuple(targets),
                 n_vessels=n_fishing + n_cargo + n_targets + n_contaminating)
