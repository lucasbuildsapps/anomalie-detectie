"""Divergence between the adaptive and reference baselines.

The single most important idea in v2's detection design, and the direct
answer to the failure the review measured: a fivefold sustained escalation
that produced five flags in thirty days at high confidence, because the
adaptive expectation climbed along with it.

Two baselines, asked two different questions about the same period:

- adaptive:  is this unusual *for how things have been lately*?
- reference: is this unusual *for what we declared normal*?

Neither alone is sufficient, and the interesting information is in the
disagreement:

| adaptive | reference | state                  | meaning                        |
|----------|-----------|------------------------|--------------------------------|
| quiet    | quiet     | QUIET                  | nothing to report              |
| **loud** | quiet     | INCIDENT               | a spike against a normal level |
| quiet    | **loud**  | **NORMALISED_ESCALATION** | the rise became the baseline |
| loud     | loud      | ACUTE                  | a spike on top of a raised level |

The third row is the one v1 could not express at all. It is not an absence of
signal; it is a specific, nameable condition — *we have stopped noticing
because we got used to it* — and it is what a warning system exists to catch.

Detecting it needs accumulation, not a threshold. A single period at 1.3x
normal is unremarkable; ninety consecutive periods at 1.3x is a different
world. That is what the CUSUM below is for: it integrates small, persistent,
one-sided deviations that no per-period test can see.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from sentinel.core.baseline.causal import BaselineFit

__all__ = [
    "DivergenceConfig",
    "DivergenceResult",
    "DivergenceState",
    "assess_divergence",
    "cusum",
    "divergence_detector",
]


class DivergenceState(StrEnum):
    """What the two baselines jointly say about one period."""

    QUIET = "quiet"
    INCIDENT = "incident"
    NORMALISED_ESCALATION = "normalised_escalation"
    ACUTE = "acute"
    UNTESTED = "untested"

    @property
    def is_notable(self) -> bool:
        return self in (DivergenceState.INCIDENT,
                        DivergenceState.NORMALISED_ESCALATION,
                        DivergenceState.ACUTE)

    @property
    def label(self) -> str:
        return {
            DivergenceState.QUIET: "no significant deviation",
            DivergenceState.INCIDENT: "isolated deviation against a normal level",
            DivergenceState.NORMALISED_ESCALATION:
                "sustained shift absorbed into the recent baseline",
            DivergenceState.ACUTE: "deviation on top of an already raised level",
            DivergenceState.UNTESTED: "not enough history to judge",
        }[self]


@dataclass(frozen=True)
class DivergenceConfig:
    """Thresholds for the joint test.

    Set conservatively. The harness measures false alarms on quiet data
    alongside detection power, and a configuration that fires on noise
    produces no quotable detection floor at all — so loosening these to buy
    recall is self-defeating, not a trade.
    """

    #: Per-period deviation to call a period loud. 3.5 rather than 3.0 is a
    #: measured choice, not a convention: at 3.0 the count-aware residuals
    #: produce 1.2 false-alarm episodes per quiet year and the noise control
    #: fails outright; at 3.5 that falls to 0.3 with no loss of detection
    #: floor on any sustained scenario. Above 4.0 isolated drops start being
    #: missed. See scripts/baseline_detection_power.py.
    adaptive_threshold: float = 3.5
    reference_threshold: float = 3.5
    #: CUSUM slack, in scale units. Deviations smaller than this accumulate
    #: nothing, which is what keeps ordinary noise from drifting the statistic.
    cusum_slack: float = 0.5
    #: Bound on each period's CUSUM contribution, in scale units. Keeps one
    #: spike from impersonating a sustained shift; see `cusum`.
    cusum_clip: float = 2.0
    #: CUSUM decision level. Crossing it means a persistent one-sided shift.
    cusum_threshold: float = 8.0
    #: Minimum consecutive periods above the reference before escalation is
    #: called. A shift is only sustained once it has been sustained.
    min_sustained_periods: int = 10

    def __post_init__(self) -> None:
        if self.cusum_slack < 0:
            raise ValueError("cusum_slack cannot be negative")
        if self.cusum_threshold <= 0:
            raise ValueError("cusum_threshold must be positive")


def cusum(standardised: pd.Series, slack: float = 0.5,
          clip: float = 2.0) -> tuple[pd.Series, pd.Series]:
    """Two-sided cumulative sum of standardised deviations.

    Returns (upper, lower), both non-negative. The upper arm accumulates
    persistent positive deviation, the lower arm persistent negative. Each
    resets to zero whenever the evidence turns.

    `clip` bounds each period's contribution, and it is not optional in
    practice. A CUSUM exists to detect *small persistent* shifts; without a
    bound, one extreme value injects tens of units at once and the statistic
    stays above its decision level for weeks afterwards. An isolated spike
    then reads as a sustained escalation — which is both wrong and precisely
    the confusion the two states are meant to separate.

    Causal by construction: the value at *t* depends only on values up to *t*.
    """
    values = standardised.to_numpy(dtype=float)
    upper = np.zeros(len(values))
    lower = np.zeros(len(values))
    hi = lo = 0.0
    for i, value in enumerate(values):
        if not np.isfinite(value):
            # A gap contributes no evidence in either direction. Treating it
            # as zero deviation would quietly dilute a real accumulation.
            upper[i], lower[i] = hi, lo
            continue
        bounded = float(np.clip(value, -clip, clip))
        hi = max(0.0, hi + bounded - slack)
        lo = max(0.0, lo - bounded - slack)
        upper[i], lower[i] = hi, lo
    return (pd.Series(upper, index=standardised.index),
            pd.Series(lower, index=standardised.index))


@dataclass(frozen=True)
class DivergenceResult:
    """Per-period joint assessment of the two baselines."""

    state: pd.Series                 # DivergenceState per period
    adaptive_deviation: pd.Series
    reference_deviation: pd.Series
    cusum_upper: pd.Series
    cusum_lower: pd.Series
    sustained_run: pd.Series         # consecutive periods beyond reference
    config: DivergenceConfig

    @property
    def notable(self) -> pd.Series:
        """Boolean mask of periods worth an analyst's attention."""
        return self.state.map(lambda s: DivergenceState(s).is_notable)

    @property
    def escalation_mask(self) -> pd.Series:
        return self.state == DivergenceState.NORMALISED_ESCALATION

    def latest_state(self) -> DivergenceState:
        testable = self.state[self.state != DivergenceState.UNTESTED]
        if testable.empty:
            return DivergenceState.UNTESTED
        return DivergenceState(testable.iloc[-1])

    def summary(self) -> str:
        state = self.latest_state()
        if state is DivergenceState.UNTESTED:
            return "Not enough history to assess this series."
        if state is DivergenceState.QUIET:
            return "Both the recent and the declared baseline are satisfied."
        if state is DivergenceState.NORMALISED_ESCALATION:
            run = int(self.sustained_run.iloc[-1])
            return (
                f"Activity has run above the declared reference for {run} "
                f"periods and the recent baseline has adapted to it. The "
                f"absence of a per-period alert here reflects habituation, "
                f"not a return to normal."
            )
        if state is DivergenceState.ACUTE:
            return ("A deviation on top of an already elevated level: both "
                    "baselines are exceeded.")
        return "An isolated deviation against an otherwise normal level."


def assess_divergence(actual: pd.Series, adaptive: BaselineFit,
                      reference: BaselineFit | None = None,
                      config: DivergenceConfig | None = None,
                      ) -> DivergenceResult:
    """Combine both baselines into a per-period state.

    With no reference baseline the result degrades gracefully to
    INCIDENT/QUIET only — which is honest, since without a declared normal
    there is no way to tell a normalised escalation from a quiet stretch.
    That degradation is exactly why the reference is not optional in
    practice, and why `Indicator` refuses to build a SUSTAINED_DIVERGENCE
    test without one.
    """
    config = config or DivergenceConfig()
    index = adaptive.expected.index
    aligned = actual.reindex(index)

    adaptive_dev = adaptive.deviation(aligned)
    usable = adaptive.is_usable

    if reference is not None:
        reference_dev = reference.deviation(aligned)
        ref_usable = reference.is_usable.reindex(index, fill_value=False)
    else:
        reference_dev = pd.Series(np.nan, index=index)
        ref_usable = pd.Series(False, index=index)

    upper, lower = cusum(reference_dev, slack=config.cusum_slack,
                         clip=config.cusum_clip)

    # Consecutive periods on the same side of the reference. A shift only
    # counts as sustained once it has actually been sustained.
    run = np.zeros(len(index), dtype=int)
    streak = 0
    ref_values = reference_dev.to_numpy(dtype=float)
    for i, value in enumerate(ref_values):
        if np.isfinite(value) and abs(value) > config.cusum_slack:
            streak += 1
        else:
            streak = 0
        run[i] = streak
    sustained_run = pd.Series(run, index=index)

    adaptive_loud = adaptive_dev.abs() > config.adaptive_threshold
    reference_loud = (
        (reference_dev.abs() > config.reference_threshold)
        | ((upper > config.cusum_threshold) | (lower > config.cusum_threshold))
    ) & (sustained_run >= config.min_sustained_periods)

    states: list[str] = []
    for i in range(len(index)):
        if not bool(usable.iloc[i]):
            states.append(DivergenceState.UNTESTED)
            continue
        a = bool(adaptive_loud.iloc[i]) and np.isfinite(adaptive_dev.iloc[i])
        r = bool(reference_loud.iloc[i]) and bool(ref_usable.iloc[i])
        if a and r:
            states.append(DivergenceState.ACUTE)
        elif a:
            states.append(DivergenceState.INCIDENT)
        elif r:
            states.append(DivergenceState.NORMALISED_ESCALATION)
        else:
            states.append(DivergenceState.QUIET)

    return DivergenceResult(
        state=pd.Series(states, index=index),
        adaptive_deviation=adaptive_dev,
        reference_deviation=reference_dev,
        cusum_upper=upper,
        cusum_lower=lower,
        sustained_run=sustained_run,
        config=config,
    )


def divergence_detector(reference_periods: int = 180,
                        config: DivergenceConfig | None = None,
                        baseline_config=None):
    """Adapt the dual baseline to the harness contract: series -> bool series.

    `reference_periods` declares the opening stretch of the series as the
    reference window, standing in for an analyst writing down "this is what
    normal looked like". Declaring it from the *start* matters: taking a
    recent window would fold whatever is currently happening into the
    definition of normal, which is the failure the reference exists to avoid.

    Returns notable periods only — warm-up and the reference window itself are
    excluded, because neither is a judgement.
    """
    from sentinel.core.baseline.causal import (
        BaselineConfig,
        fit_adaptive,
        fit_reference,
    )
    from sentinel.core.contracts import DateRange

    baseline_config = baseline_config or BaselineConfig()

    def detect(series: pd.Series) -> pd.Series:
        index = pd.DatetimeIndex(series.index)
        if len(index) < reference_periods + baseline_config.min_history:
            return pd.Series(False, index=index)

        adaptive = fit_adaptive(series, baseline_config)
        window = DateRange(index[0].to_pydatetime(),
                           index[reference_periods - 1].to_pydatetime())
        try:
            reference = fit_reference(series, window, baseline_config)
        except ValueError:
            reference = None

        result = assess_divergence(series, adaptive, reference, config)
        return result.notable.astype(bool)

    return detect
