"""Detection power for entity behaviour, measured on a synthetic fleet.

The count-series harness scores a detector against an injected effect in a
time series. Entity behaviour is judged per vessel against its peers, so it
needs its own measurement — and it turns out to fail along a dimension the
series harness has no way to express.

Two floors, and the second is the dangerous one
-----------------------------------------------
**Duration.** How long must a behaviour last to be detected at all. This is a
clean threshold set by the primitive's own minimum: below it no event is
emitted, so nothing downstream can judge anything. Measured at 1.0 hour for
the default configuration.

**Prevalence.** How rare the behaviour must remain within its class for the
peer baseline to still flag it. This one is a cliff, not a slope. Once more
than `rare_participation` of a class exhibits a behaviour, it stops being
rare *by definition*, and the baseline flags nobody — including vessels that
are genuinely worth flagging. Measured: detection is complete at 9%
prevalence and zero at 12%.

That failure mode deserves stating plainly, because it is silent. The system
does not degrade gracefully or report reduced confidence; it returns a clean
null result while the thing it was built to catch becomes common. It is the
entity-layer form of the escalation that became the baseline, and it wants
the same remedy: a *declared* reference for what participation historically
was, so that a rise in participation is itself the signal rather than the
thing that hides it. That remedy is not built — this module exists so the
gap is measured rather than assumed away.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sentinel.core.contracts import DetectionPower
from sentinel.entity import PeerBaseline, PeerConfig, extract_events
from sentinel.eval.synthetic.vessels import build_fleet

__all__ = [
    "EntityPowerResult",
    "measure",
    "measure_duration_floor",
    "measure_prevalence_cliff",
]


def _recall(target_loiter_hours: float = 4.0, n_contaminating: int = 0,
            seed: int = 42, peer_config: PeerConfig | None = None,
            ) -> tuple[float, int]:
    """Fraction of injected targets flagged, and how many trawlers were not.

    Returns (recall, fishing_false_positives). Both matter: a configuration
    that finds every target while flagging the fishing fleet is unusable at
    North Sea scale, so recall alone would be a misleading number.
    """
    fleet = build_fleet(seed=seed, target_loiter_hours=target_loiter_hours,
                        n_contaminating=n_contaminating)
    events = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group))

    baseline = PeerBaseline.from_positions(fleet.positions, events,
                                           config=peer_config)
    flagged = {
        event.entity.key for event in events
        if (assessment := baseline.assess(event)) is not None
        and assessment.is_unusual
    }
    found = len(set(fleet.target_keys) & flagged)
    fishing = len([k for k in flagged if k.startswith("fish")])
    return found / max(len(fleet.target_keys), 1), fishing


@dataclass(frozen=True)
class EntityPowerResult:
    """Both floors, plus the false alarms each was measured alongside."""

    duration_floor_hours: float
    prevalence_cliff: float
    fishing_false_positives: int
    threshold: float
    n_repeats: int

    @property
    def is_quotable(self) -> bool:
        return (np.isfinite(self.duration_floor_hours)
                and self.fishing_false_positives == 0)

    def to_detection_power(self) -> DetectionPower:
        """As a contract object, for the catalogue and for null results."""
        return DetectionPower(
            scenario_kind="Loitering",
            floor_magnitude=(self.duration_floor_hours
                             if self.is_quotable else float("nan")),
            threshold=self.threshold,
            n_repeats=self.n_repeats,
            confounded=not self.is_quotable,
            unit="hours",
            caveat=(
                f"This holds only while the behaviour stays rarer than "
                f"{self.prevalence_cliff:.0%} of the vessel class; above "
                f"that it is no longer rare and nothing is flagged, "
                f"including genuine cases."),
        )

    def describe(self) -> str:
        if not np.isfinite(self.duration_floor_hours):
            return ("No loiter duration was reliably detected; this "
                    "configuration cannot support a null result.")
        return (
            f"A loiter of {self.duration_floor_hours:g} hours or longer is "
            f"detected in {self.threshold:.0%} of runs, provided the "
            f"behaviour stays rarer than {self.prevalence_cliff:.0%} of the "
            f"vessel class. Above that it is no longer rare, and the baseline "
            f"flags nothing — including genuine cases."
        )


def measure_duration_floor(
    durations: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 4.0, 8.0),
    threshold: float = 0.8, n_repeats: int = 3,
    peer_config: PeerConfig | None = None) -> float:
    """Shortest loiter reliably detected, in hours. NaN if none is."""
    for hours in durations:
        recalls = [
            _recall(target_loiter_hours=hours, seed=42 + i,
                    peer_config=peer_config)[0]
            for i in range(n_repeats)
        ]
        if float(np.mean(recalls)) >= threshold:
            return hours
    return float("nan")


def measure_prevalence_cliff(
    contamination: tuple[int, ...] = (0, 2, 4, 6, 10, 20),
    threshold: float = 0.8, n_repeats: int = 2,
    peer_config: PeerConfig | None = None) -> float:
    """Prevalence at which detection stops, as a fraction of the class.

    Reported as the *last prevalence that still worked*, not the first that
    failed: a floor an analyst can rely on has to be the conservative end of
    the measurement.
    """
    last_working = 0.0
    for n_also in contamination:
        recalls, prevalences = [], []
        for i in range(n_repeats):
            fleet = build_fleet(seed=42 + i, n_contaminating=n_also)
            recall, _ = _recall(seed=42 + i, n_contaminating=n_also,
                                peer_config=peer_config)
            recalls.append(recall)
            loitering = n_also + len(fleet.target_keys)
            prevalences.append(loitering / (60 + loitering))
        if float(np.mean(recalls)) >= threshold:
            last_working = float(np.mean(prevalences))
        else:
            break
    return last_working


def measure(threshold: float = 0.8, n_repeats: int = 3,
            peer_config: PeerConfig | None = None) -> EntityPowerResult:
    """Both floors together, with the false-alarm check alongside."""
    _, fishing = _recall(peer_config=peer_config)
    return EntityPowerResult(
        duration_floor_hours=measure_duration_floor(
            threshold=threshold, n_repeats=n_repeats,
            peer_config=peer_config),
        prevalence_cliff=measure_prevalence_cliff(
            threshold=threshold, peer_config=peer_config),
        fishing_false_positives=fishing,
        threshold=threshold,
        n_repeats=n_repeats,
    )
