"""Synthetic scenarios with known ground truth.

Why synthetic comes first
-------------------------
v1's evaluation drew its labels from analyst-confirmed findings. Analysts
only ever see what the system surfaced, so an event the system never flagged
could not become a label, could not count as a miss, and recall was biased
toward 1 by construction. The metric that existed to prove the system worked
was structurally incapable of showing that it did not.

Injection has the opposite property: we decide what is there, so a miss is
observable. That is the whole point. It also lets us answer the question a
null result depends on — *how large would something have to be before we
caught it?* — which no amount of analyst feedback can answer.

What is deliberately included
-----------------------------
Three scenarios inject **nothing real**: pure noise, a data gap, and a source
change. A harness with only positive cases rewards a detector for firing
constantly, so these are how "a quiet environment produces quiet output"
becomes measurable rather than aspirational.

The three are not equivalent, and treating them alike was a mistake in the
first version of this file. Noise and a collection gap **must** produce
silence: flagging either is unambiguously wrong. A source change must not,
because it is numerically identical to a real level shift — the same series
with a real cause would be a miss. Demanding silence there can only be
satisfied by a detector too blunt to see real shifts of that size, which is
exactly what v1's Z-score did: it "passed" this control while missing the
identical real event. So a source change is scored PENDING: firing is
correct, and the requirement that an alternative explanation be attached
cannot be checked until provenance reaches the judgement layer.

All generators are seeded. Two runs with the same seed produce identical
series, so a change in a score is a change in the code, never in the dice.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sentinel.eval.episodes import Episode

__all__ = ["Scenario", "ScenarioGenerator", "SCENARIO_KINDS"]

#: Every scenario type the harness knows how to build. The three marked
#: "negative" must produce no detections; they are controls, not targets.
SCENARIO_KINDS = (
    "spike",              # isolated outlier
    "drop",               # isolated collapse
    "sustained_increase",  # step up, held
    "gradual_escalation",  # slow ramp  <- v1's documented blind spot
    "adaptation_failure",  # long escalation, scored only at the far end
    "regime_change",      # permanent level shift
    "silence",            # activity stops where it normally is not zero
    "noise",              # negative control: nothing happens
    "missing_data",       # negative control: collection gap, not an event
    "source_change",      # negative control: reporting artefact, not an event
)

#: Must produce silence. Anything flagged here is unambiguously a false alarm.
_NEGATIVE_CONTROLS = frozenset({"noise", "missing_data"})

#: Numerically indistinguishable from a real event, by construction. Silence
#: here is not a virtue: the same series with a real cause would be a miss.
#: What the product owes the analyst is the alternative explanation attached
#: to the alert, which needs provenance the detector does not see. Scored as
#: PENDING rather than PASS or FAIL until the evidence layer exists.
_AMBIGUOUS = frozenset({"source_change"})


@dataclass(frozen=True)
class Scenario:
    """A generated series together with what was actually done to it."""

    kind: str
    series: pd.Series
    truth: list[Episode] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    #: Periods that carry no information (collection gaps). A detector must
    #: not be penalised for staying silent here, nor rewarded for firing.
    unobserved: pd.Series | None = None

    @property
    def is_negative_control(self) -> bool:
        return self.kind in _NEGATIVE_CONTROLS

    @property
    def is_ambiguous(self) -> bool:
        """Firing is correct; the caveat is what matters and is untestable
        until provenance reaches the judgement layer."""
        return self.kind in _AMBIGUOUS

    @property
    def injects_nothing(self) -> bool:
        return not self.truth

    @property
    def magnitude(self) -> float:
        return float(self.params.get("magnitude", float("nan")))

    def describe(self) -> str:
        if not self.truth:
            return f"{self.kind}: nothing injected ({len(self.series)} periods)"
        first = self.truth[0]
        return (f"{self.kind}: magnitude {self.magnitude:g} from "
                f"{first.start.date()} to {first.end.date()} "
                f"({len(self.series)} periods)")


class ScenarioGenerator:
    """Builds count-like series with a controlled, known perturbation.

    The baseline imitates the data SENTINEL actually handles: non-negative
    integer counts with weekly structure and Poisson-ish dispersion. Using a
    Gaussian baseline here would flatter every detector, because the band
    models assume counts.
    """

    def __init__(self, seed: int = 42, start: str = "2024-01-01",
                 n_periods: int = 365, level: float = 20.0,
                 weekly_amplitude: float = 0.25) -> None:
        self.seed = seed
        self.start = start
        self.n_periods = n_periods
        self.level = level
        self.weekly_amplitude = weekly_amplitude

    # -- baseline --------------------------------------------------------
    def _index(self, n: int | None = None) -> pd.DatetimeIndex:
        return pd.date_range(self.start, periods=n or self.n_periods, freq="D")

    def baseline(self, seed_offset: int = 0) -> pd.Series:
        """A quiet series: weekly rhythm, Poisson noise, no events."""
        rng = np.random.default_rng(self.seed + seed_offset)
        index = self._index()
        phase = np.arange(len(index))
        seasonal = 1.0 + self.weekly_amplitude * np.sin(2 * np.pi * phase / 7)
        expected = self.level * seasonal
        return pd.Series(rng.poisson(expected).astype(float), index=index)

    # -- positive scenarios ---------------------------------------------
    def spike(self, magnitude: float = 5.0, at: int | None = None) -> Scenario:
        """One period multiplied by `magnitude`. The easy case."""
        series = self.baseline()
        at = at if at is not None else int(len(series) * 0.8)
        series.iloc[at] = float(round(series.iloc[at] * magnitude))
        stamp = series.index[at]
        return Scenario("spike", series,
                        [Episode(stamp, stamp, "spike", magnitude)],
                        {"magnitude": magnitude, "at": at})

    def drop(self, magnitude: float = 0.1, at: int | None = None) -> Scenario:
        """One period scaled down. Under-detection here is a classic gap:
        most detectors are tuned for excursions upward."""
        series = self.baseline()
        at = at if at is not None else int(len(series) * 0.8)
        series.iloc[at] = float(round(series.iloc[at] * magnitude))
        stamp = series.index[at]
        return Scenario("drop", series,
                        [Episode(stamp, stamp, "drop", magnitude)],
                        {"magnitude": magnitude, "at": at})

    def sustained_increase(self, magnitude: float = 2.0, duration: int = 30,
                           ) -> Scenario:
        """A step up, held to the end of the series.

        This is the scenario v1 fails: the adaptive expectation follows the
        new level, so after a few periods the escalation stops looking
        abnormal. Measured on v1, a 5x sustained rise produced 5 flags in 30
        days while confidence stayed "high".
        """
        series = self.baseline()
        onset = len(series) - duration
        series.iloc[onset:] = np.round(series.iloc[onset:] * magnitude)
        return Scenario(
            "sustained_increase", series,
            [Episode(series.index[onset], series.index[-1],
                     "sustained_increase", magnitude)],
            {"magnitude": magnitude, "duration": duration, "onset": onset},
        )

    def gradual_escalation(self, magnitude: float = 3.0, duration: int = 60,
                           ) -> Scenario:
        """A linear ramp from normal to `magnitude` x normal.

        The hardest and most operationally important case: no single period
        is anomalous, yet the trajectory is. A system that only asks "is
        today unusual" cannot see this at all.
        """
        series = self.baseline()
        onset = len(series) - duration
        ramp = np.linspace(1.0, magnitude, duration)
        series.iloc[onset:] = np.round(series.iloc[onset:].to_numpy() * ramp)
        return Scenario(
            "gradual_escalation", series,
            [Episode(series.index[onset], series.index[-1],
                     "gradual_escalation", magnitude)],
            {"magnitude": magnitude, "duration": duration, "onset": onset},
        )

    def adaptation_failure(self, magnitude: float = 3.0, duration: int = 150,
                           score_last: int = 30) -> Scenario:
        """A long escalation, scored **only at the far end**.

        Every other scenario rewards catching an onset. This one asks the
        question that actually matters for warning: months into a raised
        tempo, does the system still know it is raised?

        The injection runs for `duration` periods, but the truth episode
        covers only the final `score_last`. A detector that fires at the
        onset and then falls silent — which is exactly what a purely adaptive
        baseline does, and what v1 did — scores zero here while scoring
        perfectly on `sustained_increase`. That gap is the whole point of the
        scenario, and it is the reason the fixed reference baseline exists.
        """
        series = self.baseline()
        onset = len(series) - duration
        series.iloc[onset:] = np.round(series.iloc[onset:] * magnitude)
        scored_from = len(series) - score_last
        return Scenario(
            "adaptation_failure", series,
            [Episode(series.index[scored_from], series.index[-1],
                     "adaptation_failure", magnitude)],
            {"magnitude": magnitude, "duration": duration,
             "onset": onset, "score_last": score_last},
        )

    def regime_change(self, magnitude: float = 2.5, at_fraction: float = 0.5,
                      ) -> Scenario:
        """A permanent level shift partway through the series.

        Distinct from sustained_increase: there is enough post-shift history
        for a baseline to re-learn, so the question is whether the transition
        was reported at the time, not whether the new level looks odd now.
        """
        series = self.baseline()
        at = int(len(series) * at_fraction)
        series.iloc[at:] = np.round(series.iloc[at:] * magnitude)
        # The event is the transition, not the whole remaining series.
        end = series.index[min(at + 14, len(series) - 1)]
        return Scenario(
            "regime_change", series,
            [Episode(series.index[at], end, "regime_change", magnitude)],
            {"magnitude": magnitude, "at": at},
        )

    def silence(self, duration: int = 10) -> Scenario:
        """Activity stops where it is normally present.

        Absence is a classic warning signal and a systematic weakness of
        tools built around peak-finding. Note this is *reported* silence —
        genuine zeros, not missing data. The distinction is the whole point
        of the `missing_data` control below.
        """
        series = self.baseline()
        onset = len(series) - duration - 5
        series.iloc[onset:onset + duration] = 0.0
        return Scenario(
            "silence", series,
            [Episode(series.index[onset], series.index[onset + duration - 1],
                     "silence", 0.0)],
            {"magnitude": 0.0, "duration": duration, "onset": onset},
        )

    # -- negative controls ----------------------------------------------
    def noise(self) -> Scenario:
        """Nothing happens. Any alert here is a false alarm.

        Measured on v1 this produced 13 findings including one "high", which
        is the single clearest demonstration that the quota design cannot
        express a null result.
        """
        return Scenario("noise", self.baseline(), [], {"magnitude": 0.0})

    def missing_data(self, duration: int = 10) -> Scenario:
        """A collection gap: we did not observe, rather than observed zero.

        Correct behaviour is silence. Flagging here means the system is
        reporting its own blind spot as an event in the world — the most
        corrosive false alarm there is, because it is unfalsifiable to the
        analyst reading it.
        """
        series = self.baseline()
        onset = len(series) - duration - 5
        unobserved = pd.Series(False, index=series.index)
        unobserved.iloc[onset:onset + duration] = True
        series.iloc[onset:onset + duration] = np.nan
        return Scenario("missing_data", series, [],
                        {"magnitude": 0.0, "duration": duration,
                         "onset": onset},
                        unobserved=unobserved)

    def source_change(self, magnitude: float = 1.6) -> Scenario:
        """Reporting changes, the world does not.

        Indistinguishable from a real level shift in the numbers alone; the
        difference lives in provenance. Included because a system that cannot
        be fooled here is not being honest about what it knows — the correct
        product behaviour is to raise it *with* the alternative explanation
        attached, which is what the evidence layer is for.
        """
        series = self.baseline()
        at = int(len(series) * 0.6)
        series.iloc[at:] = np.round(series.iloc[at:] * magnitude)
        return Scenario("source_change", series, [],
                        {"magnitude": magnitude, "at": at,
                         "note": "level shift with no real-world meaning"})

    # -- suites ----------------------------------------------------------
    def build(self, kind: str, **kwargs) -> Scenario:
        if kind not in SCENARIO_KINDS:
            raise ValueError(f"unknown scenario {kind!r}; "
                             f"choose from {SCENARIO_KINDS}")
        return getattr(self, kind)(**kwargs)

    def suite(self) -> Iterator[Scenario]:
        """One instance of every scenario, at default magnitude."""
        for kind in SCENARIO_KINDS:
            yield self.build(kind)

    def power_sweep(self, kind: str,
                    magnitudes: tuple[float, ...] = (1.25, 1.5, 2.0, 3.0, 5.0),
                    **kwargs) -> Iterator[Scenario]:
        """The same scenario across magnitudes, for a detection-power curve.

        This is what turns "no significant anomaly detected" into a claim
        with a number behind it: the smallest effect the configuration
        reliably catches.
        """
        for magnitude in magnitudes:
            yield self.build(kind, magnitude=magnitude, **kwargs)
