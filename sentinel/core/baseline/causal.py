"""Causal baselines: what was expected, using only what was known.

Two kinds, both first-class (ARCHITECTURE_V2.md §4.3):

- **Adaptive** — follows the recent regime. Answers "is today unusual for how
  things have been lately?"
- **Fixed reference** — fitted once over an analyst-declared period, then
  frozen. Answers "is the current level unusual for what we called normal?"

The second exists because the first has a fatal property on its own: a
developing threat becomes the new baseline. Measured on v1, a fivefold
sustained escalation produced five flags in thirty days while the expectation
tracked from 17.8 up to 87.5. The adaptive baseline was not wrong — it was
answering a question that stops mattering during an escalation.

Causality
---------
Every prediction at *t* is built from observations strictly before *t*. Not by
convention: the recursion computes `expected[t]` from state, and only then
folds `y[t]` into that state. There is no fitted-values step, no centred
smoothing, and no second pass. `tests/test_baseline_causal.py` proves it by
rewriting the future and checking the past does not move.

Adaptation damping
------------------
Even a causal adaptive baseline will absorb a slow escalation given time. So
when residuals stay one-sided — the model persistently under- or
over-predicting, which is what a developing shift looks like from the inside
— the level update is slowed. It is damped, never frozen: a genuine regime
change must still be learnable, or the baseline becomes a different kind of
wrong. The tension between those two is real, and both directions are tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from sentinel.core.contracts import Baseline, BaselineKind, DateRange

__all__ = [
    "BaselineConfig",
    "BaselineFit",
    "fit_adaptive",
    "fit_reference",
    "phases_for",
]


#: Median absolute deviation -> sigma, for a normal distribution.
_MAD_TO_SIGMA = 1.4826
#: **Mean** absolute deviation -> sigma. Different constant, different
#: estimator: E|X| = sigma*sqrt(2/pi), so sigma = 1.2533*E|X|. Using the MAD
#: constant on a running mean-absolute-deviation inflates every scale by ~18%,
#: which widens the band and quietly suppresses detection.
_MEAN_ABS_TO_SIGMA = 1.2533


def _half_life_to_alpha(half_life: float) -> float:
    """Smoothing factor for a given half-life, in periods."""
    if half_life <= 0:
        return 1.0
    return float(1.0 - np.exp(np.log(0.5) / half_life))


def phases_for(index: pd.DatetimeIndex, period: int,
               epoch: pd.Timestamp) -> np.ndarray:
    """Seasonal phase of each timestamp, relative to a fixed epoch.

    Derived from the timestamps rather than from position, so a reference
    baseline fitted on one window lines up correctly when applied to another.
    Position-based phase silently rotates the seasonal pattern whenever two
    windows start on different weekdays — a bug that produces a plausible
    baseline that is simply shifted.
    """
    if period <= 1:
        return np.zeros(len(index), dtype=int)
    offsets = (pd.DatetimeIndex(index) - epoch).days
    return np.asarray(offsets % period, dtype=int)


@dataclass(frozen=True)
class BaselineConfig:
    """Tuning for both baseline kinds.

    Defaults are deliberately conservative on false alarms. Detection power
    bought by widening sensitivity is not worth having: the harness measures
    both, and a detector that fires on quiet data has no quotable floor at
    all (see `sentinel.eval.harness.PowerCurve`).
    """

    half_life: float = 21.0
    seasonal_period: int | None = 7
    seasonal_half_life: float = 84.0
    min_history: int = 21
    #: Innovations beyond this many scale units are clipped before updating
    #: the level, so one spike cannot drag the baseline after it.
    robust_clip: float = 3.0
    #: Slow the level update when residuals stay one-sided.
    adaptation_damping: bool = True
    damping_strength: float = 4.0
    damping_threshold: float = 0.35
    #: Floor on the residual scale, as a fraction of the level. Prevents a
    #: quiet stretch from making every later wobble look infinitely extreme.
    min_scale_fraction: float = 0.15
    min_scale_absolute: float = 0.5

    def __post_init__(self) -> None:
        if self.half_life <= 0:
            raise ValueError("half_life must be positive")
        if self.min_history < 3:
            raise ValueError("min_history must be at least 3 periods")
        if self.seasonal_period is not None and self.seasonal_period < 2:
            raise ValueError("seasonal_period must be at least 2, or None")


@dataclass(frozen=True)
class BaselineFit:
    """A fitted baseline: what was expected, and how much spread to allow."""

    kind: BaselineKind
    expected: pd.Series
    scale: pd.Series
    warmup: int
    epoch: pd.Timestamp
    params: dict = field(default_factory=dict)
    reference_period: DateRange | None = None
    #: Per-period damping actually applied (adaptive only). 1.0 = undamped.
    damping: pd.Series | None = None
    #: Periods deliberately outside judgement, e.g. a reference baseline's own
    #: fitting window. Scoring the data that defined normal against itself
    #: would be circular and would make the reference look better than it is.
    excluded: pd.Series | None = None

    @property
    def is_usable(self) -> pd.Series:
        """Periods whose prediction rests on enough history to be trusted.

        Warm-up periods are not quiet and not anomalous — they are untested.
        Conflating the three is how a system reports confident nonsense in its
        first weeks.
        """
        usable = pd.Series(True, index=self.expected.index)
        if self.warmup:
            usable.iloc[:self.warmup] = False
        if self.excluded is not None:
            usable &= ~self.excluded.reindex(usable.index, fill_value=False)
        return usable

    def residuals(self, actual: pd.Series) -> pd.Series:
        aligned = actual.reindex(self.expected.index)
        return aligned - self.expected

    def deviation(self, actual: pd.Series) -> pd.Series:
        """Standardised residual: how far off, in units of local spread.

        For count-like data this uses a **quasi-Poisson deviance residual**
        rather than a plain z-score, because the Gaussian version is wrong at
        low counts and wrong asymmetrically. Observing 1 where 14.6 was
        expected has probability around 1e-5 under Poisson, but a z-score
        calls it -2.8 and waves it through. Drops are the systematic casualty:
        counts are bounded below by zero, so the left tail compresses exactly
        where a collapse in activity should be loudest.

        The empirical scale is retained as an overdispersion correction, so a
        genuinely noisy series is not treated as if it were clean Poisson.
        Continuous data keeps the ordinary z-score.
        """
        aligned = actual.reindex(self.expected.index)
        residual = aligned - self.expected
        scale = self.scale.replace(0, np.nan)
        if not self._is_count_like(aligned):
            return residual / scale

        y = aligned.to_numpy(dtype=float)
        mu = np.clip(self.expected.to_numpy(dtype=float), 0.1, None)
        with np.errstate(divide="ignore", invalid="ignore"):
            # Poisson deviance residual, signed.
            term = np.where(y > 0, y * np.log(np.maximum(y, 1e-12) / mu), 0.0)
            deviance = 2.0 * (term - (y - mu))
            signed = np.sign(y - mu) * np.sqrt(np.maximum(deviance, 0.0))
        # Quasi-Poisson: inflate by observed overdispersion, never deflate.
        dispersion = np.maximum((scale.to_numpy(dtype=float) ** 2) / mu, 1.0)
        out = signed / np.sqrt(dispersion)
        return pd.Series(np.where(np.isfinite(y), out, np.nan),
                         index=self.expected.index)

    @staticmethod
    def _is_count_like(values: pd.Series) -> bool:
        """Non-negative whole numbers. Same test v1 used, same reasoning."""
        arr = values.to_numpy(dtype=float)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0 or finite.min() < 0:
            return False
        return bool(np.allclose(finite, np.round(finite), atol=1e-9))

    def to_contract(self, as_of: datetime,
                    coverage_oos: float | None = None) -> Baseline:
        """Hand this fit to the rest of the system as a contract object."""
        return Baseline(
            kind=self.kind,
            as_of=as_of,
            params=dict(self.params),
            n_periods=int(self.is_usable.sum()),
            coverage_oos=coverage_oos,
            reference_period=self.reference_period,
        )


def _initial_state(values: np.ndarray, phase: np.ndarray, period: int,
                   min_history: int) -> tuple[float, np.ndarray, float]:
    """Seed level, seasonal offsets and scale from the warm-up window."""
    window = values[:min_history]
    finite = window[np.isfinite(window)]
    level = float(np.median(finite)) if finite.size else 0.0

    seasonal = np.zeros(max(period, 1), dtype=float)
    if period > 1:
        for ph in range(period):
            mask = (phase[:min_history] == ph) & np.isfinite(window)
            if mask.sum() >= 2:
                seasonal[ph] = float(np.median(window[mask]) - level)
        # Offsets must sum to zero, or they compete with the level.
        seasonal -= seasonal.mean()

    # Spread of residuals against the *seasonal* model. Measuring against the
    # bare level would fold the seasonal swing into the scale and start the
    # band far too wide, which takes many periods to shrink back.
    if finite.size:
        modelled = level + seasonal[phase[:min_history]]
        residuals = window - modelled
        residuals = residuals[np.isfinite(residuals)]
        spread = float(np.median(np.abs(residuals))) if residuals.size else 0.0
    else:
        spread = 0.0
    scale = max(spread * _MAD_TO_SIGMA, 1.0)
    return level, seasonal, scale


def fit_adaptive(series: pd.Series,
                 config: BaselineConfig | None = None) -> BaselineFit:
    """One-pass causal adaptive baseline.

    At each step the prediction is made from state built entirely from
    earlier observations; only afterwards is the current observation folded
    in. Missing values update nothing — a gap in collection is not evidence
    that the level changed.
    """
    config = config or BaselineConfig()
    values = series.to_numpy(dtype=float)
    n = len(values)
    index = pd.DatetimeIndex(series.index)
    epoch = index[0] if n else pd.Timestamp("1970-01-01")
    period = config.seasonal_period or 1
    phase = phases_for(index, period, epoch)

    if n <= config.min_history:
        # Not enough history to say anything. Returning a flat, wide baseline
        # is honest: every period lands inside it, and `is_usable` is False
        # throughout, so nothing downstream will call it a verdict.
        level = float(np.nanmedian(values)) if n else 0.0
        return BaselineFit(
            kind=BaselineKind.ADAPTIVE,
            expected=pd.Series(np.full(n, level), index=index),
            scale=pd.Series(np.full(n, max(abs(level), 1.0)), index=index),
            warmup=n, epoch=epoch,
            params={"reason": "insufficient history", "n_periods": n},
        )

    alpha = _half_life_to_alpha(config.half_life)
    gamma = _half_life_to_alpha(config.seasonal_half_life)
    scale_alpha = _half_life_to_alpha(max(config.half_life, 10.0))

    level, seasonal, scale = _initial_state(values, phase, period,
                                            config.min_history)
    bias = 0.0
    expected = np.empty(n, dtype=float)
    scales = np.empty(n, dtype=float)
    damps = np.ones(n, dtype=float)

    for t in range(n):
        # --- predict from state that knows nothing about y[t] -------------
        prediction = level + seasonal[phase[t]]
        floor = max(config.min_scale_absolute,
                    config.min_scale_fraction * max(abs(level), 1.0))
        scale = max(scale, floor)
        expected[t] = prediction
        scales[t] = scale

        y = values[t]
        if not np.isfinite(y):
            # Unobserved: carry state forward untouched.
            continue

        residual = y - prediction
        standardised = residual / scale

        # --- damping: persistent one-sidedness means the model is wrong ---
        bias = (1 - scale_alpha) * bias + scale_alpha * np.clip(
            standardised, -3.0, 3.0)
        damp = 1.0
        if config.adaptation_damping:
            excess = max(0.0, abs(bias) - config.damping_threshold)
            damp = 1.0 / (1.0 + config.damping_strength * excess)
        damps[t] = damp

        # --- update ------------------------------------------------------
        # Clip the innovation so a single spike cannot drag the level.
        clipped = float(np.clip(residual, -config.robust_clip * scale,
                                config.robust_clip * scale))
        effective_alpha = alpha * damp
        new_level = level + effective_alpha * clipped

        if period > 1:
            seasonal_target = y - new_level
            updated = ((1 - gamma) * seasonal[phase[t]]
                       + gamma * seasonal_target)
            seasonal[phase[t]] = updated
            seasonal -= seasonal.mean() / period  # drift control

        level = new_level
        scale = ((1 - scale_alpha) * scale
                 + scale_alpha * abs(clipped) * _MEAN_ABS_TO_SIGMA)

    return BaselineFit(
        kind=BaselineKind.ADAPTIVE,
        expected=pd.Series(expected, index=index),
        scale=pd.Series(scales, index=index),
        warmup=config.min_history,
        epoch=epoch,
        params={
            "half_life": config.half_life,
            "seasonal_period": config.seasonal_period,
            "adaptation_damping": config.adaptation_damping,
        },
        damping=pd.Series(damps, index=index),
    )


def fit_reference(series: pd.Series, reference: DateRange,
                  config: BaselineConfig | None = None) -> BaselineFit:
    """Fit over a declared period, then freeze.

    The frozen level and seasonal shape are projected across the whole
    series. Because nothing updates, a sustained rise keeps producing
    residuals for as long as it lasts — which is the entire point. This is
    the baseline that refuses to be talked round.
    """
    config = config or BaselineConfig()
    index = pd.DatetimeIndex(series.index)
    epoch = index[0] if len(index) else pd.Timestamp("1970-01-01")
    period = config.seasonal_period or 1
    phase = phases_for(index, period, epoch)

    in_window = np.asarray(
        [reference.contains(ts.to_pydatetime()) for ts in index], dtype=bool)
    window_values = series.to_numpy(dtype=float)[in_window]
    finite = window_values[np.isfinite(window_values)]

    if finite.size < config.min_history:
        raise ValueError(
            f"reference period {reference} holds only {finite.size} usable "
            f"periods; at least {config.min_history} are needed for a "
            f"declared baseline to mean anything"
        )

    level = float(np.median(finite))
    seasonal = np.zeros(max(period, 1), dtype=float)
    if period > 1:
        window_phase = phase[in_window]
        for ph in range(period):
            mask = (window_phase == ph) & np.isfinite(window_values)
            if mask.sum() >= 2:
                seasonal[ph] = float(np.median(window_values[mask]) - level)
        seasonal -= seasonal.mean()

    expected = level + seasonal[phase]
    window_residuals = window_values - expected[in_window]
    finite_residuals = window_residuals[np.isfinite(window_residuals)]
    spread = (float(np.median(np.abs(finite_residuals))) * _MAD_TO_SIGMA
              if finite_residuals.size else 0.0)
    scale = max(spread,
                config.min_scale_fraction * max(abs(level), 1.0),
                config.min_scale_absolute)

    return BaselineFit(
        kind=BaselineKind.FIXED_REFERENCE,
        expected=pd.Series(expected, index=index),
        scale=pd.Series(np.full(len(index), scale), index=index),
        # A frozen baseline needs no warm-up: it was fitted elsewhere. What it
        # does need is to be kept away from its own fitting window, which the
        # exclusion mask below enforces.
        warmup=0,
        epoch=epoch,
        params={"level": level, "seasonal_period": config.seasonal_period,
                "n_reference_periods": int(finite.size)},
        reference_period=reference,
        excluded=pd.Series(in_window, index=index),
    )
