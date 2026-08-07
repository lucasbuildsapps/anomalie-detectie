"""Event-level scoring: episodes, not rows.

Why this exists
---------------
v1 scored detection per observation row. On a dataset with 100 rows per day,
one flagged day counted as 100 flags, so precision was computed against a
denominator that had nothing to do with what an analyst reviews. Worse, it
made a detector that fires on a dense day look catastrophically imprecise and
one that fires on a sparse day look excellent, for reasons unrelated to
whether either was right.

An analyst reviews *episodes*: "something was off in this region between the
3rd and the 9th". So that is the unit of account here. A run of consecutive
flags is one episode, one alert, one thing to be right or wrong about.

Matching rules
--------------
- A truth episode is **hit** if any predicted episode overlaps it within
  tolerance. One hit per truth episode, no matter how many predictions
  overlap — three alerts for one event is one detection, not three.
- Extra predictions overlapping an already-matched truth episode are
  **duplicates**: not false alarms (they are about something real), but not
  free either — they are the noise that erodes trust, so they are counted
  and reported separately.
- A predicted episode overlapping no truth episode is a **false alarm**.
- Recall = hits / truth episodes. Precision = matched predictions / all
  predictions. Both at episode level.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "Episode",
    "MatchResult",
    "match_episodes",
    "to_episodes",
]


@dataclass(frozen=True)
class Episode:
    """A contiguous period during which something was true.

    Used for both ground truth ("a sustained increase ran from here to here")
    and predictions ("the system flagged this stretch"). Same type on both
    sides so matching cannot accidentally compare unlike things.
    """

    start: pd.Timestamp
    end: pd.Timestamp
    kind: str = ""
    magnitude: float = float("nan")

    def __post_init__(self) -> None:
        if pd.Timestamp(self.end) < pd.Timestamp(self.start):
            raise ValueError(f"episode ends ({self.end}) before it starts "
                             f"({self.start})")

    @property
    def length(self) -> pd.Timedelta:
        return pd.Timestamp(self.end) - pd.Timestamp(self.start)

    def overlaps(self, other: Episode, tolerance: pd.Timedelta) -> bool:
        """True if the two periods touch, allowing `tolerance` slack.

        Slack matters because a report dated the 3rd for an event on the 2nd
        is a reporting difference, not a miss.
        """
        return (pd.Timestamp(self.start) - tolerance <= pd.Timestamp(other.end)
                and pd.Timestamp(other.start) - tolerance
                <= pd.Timestamp(self.end))


def to_episodes(flags: pd.Series, kind: str = "predicted",
                max_gap: int = 0) -> list[Episode]:
    """Collapse a boolean series indexed by time into episodes.

    `max_gap` bridges short interruptions: with `max_gap=1`, a pattern of
    flag-gap-flag is one episode rather than two. Intermittent flagging
    during a single developing event is the normal case, not two events, and
    counting it as two would understate precision for no good reason.
    """
    if flags is None or len(flags) == 0:
        return []
    values = pd.Series(flags).fillna(False).astype(bool)
    index = pd.DatetimeIndex(values.index)
    positions = np.flatnonzero(values.to_numpy())
    if positions.size == 0:
        return []

    episodes: list[Episode] = []
    run_start = positions[0]
    previous = positions[0]
    for pos in positions[1:]:
        if pos - previous - 1 > max_gap:
            episodes.append(Episode(index[run_start], index[previous], kind))
            run_start = pos
        previous = pos
    episodes.append(Episode(index[run_start], index[previous], kind))
    return episodes


@dataclass
class MatchResult:
    """Outcome of comparing predictions against ground truth."""

    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    duplicates: int = 0
    n_truth: int = 0
    n_predicted: int = 0
    #: Periods between a truth episode's start and its first detection.
    #: Negative would mean detection before onset, which signals leakage.
    lead_times: list[float] = field(default_factory=list)
    missed_episodes: list[Episode] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.hits / self.n_truth if self.n_truth else float("nan")

    @property
    def precision(self) -> float:
        if not self.n_predicted:
            # No predictions on a quiet dataset is correct behaviour, not a
            # precision failure. Reporting 0.0 here would punish the very
            # thing a warning system is supposed to do.
            return float("nan") if self.n_truth else 1.0
        return (self.n_predicted - self.false_alarms) / self.n_predicted

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if not np.isfinite(p) or not np.isfinite(r) or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)

    @property
    def median_lead_time(self) -> float:
        return float(np.median(self.lead_times)) if self.lead_times else float("nan")

    def summary(self) -> str:
        if self.n_truth == 0:
            return (f"No events to find. {self.false_alarms} false alarm(s) "
                    f"from {self.n_predicted} episode(s).")
        return (
            f"{self.hits}/{self.n_truth} found (recall {self.recall:.0%}), "
            f"{self.false_alarms} false alarm(s), "
            f"{self.duplicates} duplicate(s), "
            f"median detection delay {self.median_lead_time:.0f} period(s)."
        )


def match_episodes(truth: list[Episode], predicted: list[Episode],
                   tolerance_periods: int = 1,
                   period: pd.Timedelta | None = None) -> MatchResult:
    """Score predictions against ground truth at episode level.

    `tolerance_periods` is expressed in periods and converted using `period`
    (default one day), so the same call works for daily, weekly or hourly
    series without the caller doing timedelta arithmetic.
    """
    period = period or pd.Timedelta(days=1)
    tolerance = period * tolerance_periods

    result = MatchResult(n_truth=len(truth), n_predicted=len(predicted))
    matched_predictions: set[int] = set()

    for episode in truth:
        overlapping = [
            i for i, pred in enumerate(predicted)
            if episode.overlaps(pred, tolerance)
        ]
        if not overlapping:
            result.misses += 1
            result.missed_episodes.append(episode)
            continue

        result.hits += 1
        # Duplicates: every prediction beyond the first for this event.
        result.duplicates += len(overlapping) - 1
        matched_predictions.update(overlapping)

        earliest = min(pd.Timestamp(predicted[i].start) for i in overlapping)
        delay = (earliest - pd.Timestamp(episode.start)) / period
        result.lead_times.append(float(delay))

    result.false_alarms = len(predicted) - len(matched_predictions)
    return result
