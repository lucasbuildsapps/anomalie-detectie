"""Run a detector against the scenario suite and report what it can find.

The output of this module is the number that makes a null result credible.
"No significant anomaly detected" is only worth reading if it comes with
"and here is the smallest thing we would have caught".

A detector is any callable ``(pd.Series) -> pd.Series[bool]`` indexed the same
way as its input. That deliberately narrow contract lets the harness score v1
detectors, v2 tests, and anything built later without knowing their internals
— and keeps the harness from growing a dependency on the thing it measures.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from sentinel.eval.episodes import MatchResult, match_episodes, to_episodes
from sentinel.eval.synthetic.scenarios import Scenario, ScenarioGenerator

__all__ = [
    "Detector",
    "PowerCurve",
    "ScenarioScore",
    "detection_floor",
    "power_curve",
    "run_scenario",
    "run_suite",
]

#: ``(series) -> boolean series``. Nothing else. A detector that needs more
#: context than the series is not comparable across scenarios anyway.
Detector = Callable[[pd.Series], pd.Series]


@dataclass
class ScenarioScore:
    scenario_kind: str
    magnitude: float
    match: MatchResult
    is_negative_control: bool
    is_ambiguous: bool = False

    @property
    def outcome(self) -> str:
        """PASS, FAIL, or PENDING.

        PENDING exists for scenarios that are numerically indistinguishable
        from a real event. There, firing is the correct behaviour and the
        open question is whether the alert carries the right caveat — which
        this harness cannot see. Recording that as PASS would overstate what
        was checked; recording it as FAIL would reward blindness.
        """
        if self.is_ambiguous:
            return "PENDING" if self.match.n_predicted else "FAIL"
        if self.is_negative_control:
            return "PASS" if self.match.false_alarms == 0 else "FAIL"
        return "PASS" if self.match.hits > 0 else "FAIL"

    @property
    def passed(self) -> bool:
        return self.outcome == "PASS"

    def summary(self) -> str:
        if self.is_ambiguous:
            label = f"{self.scenario_kind} (ambiguous)"
        elif self.is_negative_control:
            label = f"{self.scenario_kind} (control)"
        else:
            label = f"{self.scenario_kind} x{self.magnitude:g}"
        note = ""
        if self.outcome == "PENDING":
            note = ("  <- fired, as it should; caveat requirement untestable "
                    "until the evidence layer lands")
        return f"[{self.outcome}] {label}: {self.match.summary()}{note}"


def _apply(detector: Detector, scenario: Scenario) -> pd.Series:
    """Run a detector, then neutralise flags on unobserved periods.

    A collection gap is not an observation, so a detector cannot be right or
    wrong about it. Counting flags there would penalise detectors that
    correctly treat missing data as unknown — which is the behaviour we want
    to encourage, not punish.
    """
    flags = pd.Series(detector(scenario.series)).fillna(False).astype(bool)
    flags = flags.reindex(scenario.series.index, fill_value=False)
    if scenario.unobserved is not None:
        flags = flags & ~scenario.unobserved.reindex(
            flags.index, fill_value=False)
    return flags


def run_scenario(detector: Detector, scenario: Scenario,
                 tolerance_periods: int = 1, max_gap: int = 1,
                 ) -> ScenarioScore:
    """Score one detector against one scenario."""
    flags = _apply(detector, scenario)
    predicted = to_episodes(flags, kind="predicted", max_gap=max_gap)
    match = match_episodes(scenario.truth, predicted,
                           tolerance_periods=tolerance_periods)
    return ScenarioScore(
        scenario_kind=scenario.kind,
        magnitude=scenario.magnitude,
        match=match,
        is_negative_control=scenario.is_negative_control,
        is_ambiguous=scenario.is_ambiguous,
    )


def run_suite(detector: Detector,
              scenarios: Iterable[Scenario] | None = None,
              **kwargs) -> list[ScenarioScore]:
    """Score a detector across the standard suite (or a supplied set)."""
    scenarios = scenarios if scenarios is not None else ScenarioGenerator().suite()
    return [run_scenario(detector, s, **kwargs) for s in scenarios]


@dataclass
class PowerCurve:
    """Recall as a function of effect size, calibrated against chance.

    Raw recall is not enough. A detector that fires eighteen times on a quiet
    series will overlap any injected window by luck, and a naive curve then
    reports a perfect detection floor for a detector that is simply loud.
    Measured on v1's STL detector this is not hypothetical: 17.9 false-alarm
    episodes per quiet series and an apparent floor of x1.25 on every
    scenario.

    So each point is measured twice: once on the injected series, and once on
    the *same baseline without the injection*. The second is the chance rate.
    `net_recalls` is the difference — detection actually attributable to the
    event — and the floor is computed from that.
    """

    kind: str
    magnitudes: list[float]
    recalls: list[float]
    chance_rates: list[float]
    n_repeats: int
    threshold: float

    @property
    def net_recalls(self) -> list[float]:
        return [max(0.0, r - c) for r, c in zip(self.recalls,
                                                self.chance_rates,
                                                strict=True)]

    @property
    def floor(self) -> float:
        """Smallest magnitude whose *attributable* recall meets `threshold`."""
        for magnitude, net in zip(self.magnitudes, self.net_recalls,
                                  strict=True):
            if net >= self.threshold:
                return magnitude
        return float("nan")

    @property
    def floor_is_uncertain(self) -> bool:
        """True when one more draw could move the floor.

        Found by measurement, not by reasoning: the spike floor for the
        shipped `strike_tempo_spike` configuration reads 5x at 8 repeats and
        3x at 16, because the net recall at 3x lands on the 0.8 decision
        boundary (0.75 / 0.81 / 0.88 at n = 8 / 16 / 24). The floor was
        flipping with sample size and the output said nothing about it.

        A proportion estimated from `n` draws has standard error
        `sqrt(p(1-p)/n)`. If the deciding magnitude clears the threshold by
        less than that, or the magnitude below it falls short by less, then
        the floor is not resolved and quoting it as exact overstates what was
        measured.
        """
        if self.is_confounded or not np.isfinite(self.floor):
            return False
        nets = self.net_recalls
        index = self.magnitudes.index(self.floor)

        def _se(p: float) -> float:
            return math.sqrt(max(p * (1.0 - p), 0.0) / max(self.n_repeats, 1))

        if nets[index] - self.threshold < _se(nets[index]):
            return True
        if index > 0:
            below = nets[index - 1]
            if self.threshold - below < _se(below):
                return True
        return False

    @property
    def is_confounded(self) -> bool:
        """True when chance overlap alone would satisfy the threshold.

        At that point the detector is not detecting, it is covering, and no
        floor derived from it should be quoted to an analyst.
        """
        return any(c >= self.threshold for c in self.chance_rates)

    def describe(self) -> str:
        if self.is_confounded:
            worst = max(self.chance_rates)
            return (
                f"{self.kind}: detection floor not measurable — this detector "
                f"flags the same window on an uninjected series {worst:.0%} of "
                f"the time. It is too noisy for a null result to mean "
                f"anything."
            )
        if not np.isfinite(self.floor):
            return (f"{self.kind}: not reliably detected at any tested "
                    f"magnitude (up to x{max(self.magnitudes):g}). "
                    f"A null result for this scenario type means little.")
        text = (f"{self.kind}: reliably detected from x{self.floor:g} "
                f"({self.threshold:.0%} attributable hit rate over "
                f"{self.n_repeats} runs). Smaller effects fall below the "
                f"detection floor.")
        if self.floor_is_uncertain:
            text += (f" This floor is not resolved at {self.n_repeats} runs — "
                     f"the deciding magnitude sits within sampling noise of "
                     f"the threshold, so more repeats may move it.")
        return text

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"magnitude": self.magnitudes,
                             "recall": self.recalls,
                             "chance": self.chance_rates,
                             "net_recall": self.net_recalls})


def power_curve(detector: Detector, kind: str = "sustained_increase",
                magnitudes: tuple[float, ...] = (1.25, 1.5, 2.0, 3.0, 5.0),
                n_repeats: int = 5, threshold: float = 0.8,
                base_seed: int = 1000, **scenario_kwargs) -> PowerCurve:
    """Measure recall vs. effect size, averaged over repeated draws.

    Repeats matter: a single draw conflates "the detector found it" with
    "that particular noise realisation happened to help". Without them the
    floor moves whenever the seed does, and a moving floor is not a number
    you can put in front of an analyst.
    """
    recalls: list[float] = []
    chance_rates: list[float] = []
    for magnitude in magnitudes:
        hits = 0
        chance_hits = 0
        for repeat in range(n_repeats):
            generator = ScenarioGenerator(seed=base_seed + repeat)
            scenario = generator.build(kind, magnitude=magnitude,
                                       **scenario_kwargs)
            if run_scenario(detector, scenario).match.hits > 0:
                hits += 1

            # Same seed, so the same baseline noise without the injection.
            # Any hit on the identical window here is coincidence, and that
            # is precisely what must be subtracted.
            null = Scenario(kind="noise", series=generator.baseline(),
                            truth=scenario.truth, params={})
            if run_scenario(detector, null).match.hits > 0:
                chance_hits += 1

        recalls.append(hits / n_repeats)
        chance_rates.append(chance_hits / n_repeats)
    return PowerCurve(kind=kind, magnitudes=list(magnitudes), recalls=recalls,
                      chance_rates=chance_rates, n_repeats=n_repeats,
                      threshold=threshold)


def detection_floor(detector: Detector, kind: str = "sustained_increase",
                    **kwargs) -> float:
    """Convenience: just the floor, for embedding in a null-result message."""
    return power_curve(detector, kind=kind, **kwargs).floor


def false_alarm_rate(detector: Detector, n_repeats: int = 20,
                     base_seed: int = 2000) -> float:
    """Episodes per quiet series — the other half of a trustworthy null.

    Detection power without this is meaningless: a detector that always fires
    has perfect recall and is useless. This is also the measurement that sets
    thresholds against the agreed alert budget.
    """
    total = 0
    for repeat in range(n_repeats):
        scenario = ScenarioGenerator(seed=base_seed + repeat).noise()
        total += run_scenario(detector, scenario).match.false_alarms
    return total / n_repeats
