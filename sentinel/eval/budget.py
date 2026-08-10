"""Alert budget: setting thresholds against a volume an analyst can absorb.

Decision #6 fixed the budget at 5–15 alerts per week per region, as the
calibration target that replaces v1's deleted quota. Getting the difference
right is the whole point of this module, so it is worth stating plainly.

The quota and the budget are not the same thing
-----------------------------------------------
v1's `run_auto_pilot()` loosened its sensitivity until it had found something
to show. That is a runtime loop: it guarantees findings, and it guarantees
them loudest when the data is quietest, because a calm week still has to fill
the list. Nothing here does that, and nothing here may.

**The budget is measured offline and spent in advance.** It sets a threshold
before the data arrives. At runtime the threshold is fixed, every indicator is
evaluated independently, and the number of alerts is whatever the world
produces. There is no ranking step, no "top N this week", and no suppression.
If a region generates thirty alerts one week because thirty things happened,
the analyst sees thirty.

Why the lower bound is not a target
-----------------------------------
This is the trap the quota fell into. "5–15 per week" reads like a range to
land in, and the obvious way to reach 5 is to loosen the threshold until noise
supplies the difference. That is the quota rebuilt with extra steps.

So the two bounds do different jobs here:

- **The upper bound binds.** A configuration whose *quiet-data* volume alone
  exceeds 15/week is misconfigured — it will drown the analyst before anything
  happens. That is measurable and it is enforced.
- **The lower bound diagnoses.** Producing under 5/week is not a failure and
  is never corrected by loosening. It means either the world is quiet or the
  detection floor is too high, and the floor — measured alongside — is what
  distinguishes those. A quiet region producing zero alerts is a correct
  result.

The cost that must be reported with the number
-----------------------------------------------
Tightening a threshold to fit the budget raises the detection floor. Every
option below therefore carries both, and `recommend()` refuses any threshold
whose floor is unquotable. A budget met by going blind is the same failure as
a negative control passed by blindness — which this project has already made
once, and measured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from sentinel.core.contracts import Indicator, IndicatorTest

__all__ = [
    "AlertBudget",
    "BudgetCalibration",
    "ThresholdOption",
    "calibrate_indicator",
    "sweep_thresholds",
]

#: A quiet synthetic series is 365 daily periods.
_WEEKS_PER_SERIES = 365 / 7.0


@dataclass(frozen=True)
class AlertBudget:
    """How many alerts a week a region's analyst can actually absorb."""

    min_per_week: float = 5.0
    max_per_week: float = 15.0

    def __post_init__(self) -> None:
        if self.min_per_week > self.max_per_week:
            raise ValueError("budget minimum above its maximum")
        if self.min_per_week < 0:
            raise ValueError("a negative alert budget is not a budget")

    def admits(self, quiet_per_week: float) -> bool:
        """Whether noise alone stays inside the budget.

        Only the upper bound is tested. Falling under the minimum on quiet
        data is the expected and desirable outcome — the alerts that make up
        an analyst's week should come from events, not from noise.
        """
        return quiet_per_week <= self.max_per_week


@dataclass(frozen=True)
class ThresholdOption:
    """One candidate threshold, with both of its consequences."""

    threshold: float
    #: False-alarm episodes per week on data where nothing is happening.
    quiet_per_week: float
    #: Smallest effect reliably detected here. NaN when none was.
    floor: float
    confounded: bool = False
    scenario_kind: str = ""
    #: False when the floor sat within sampling noise of the recall
    #: threshold. A recommendation resting on one of these can flip with the
    #: repeat count — which is exactly how this module first recommended
    #: changing a shipped threshold on the strength of four draws.
    floor_resolved: bool = True
    n_repeats: int = 0

    @property
    def floor_is_quotable(self) -> bool:
        return not self.confounded and math.isfinite(self.floor)

    def within(self, budget: AlertBudget) -> bool:
        return budget.admits(self.quiet_per_week)

    def describe(self) -> str:
        volume = f"{self.quiet_per_week:.1f} alerts/week on quiet data"
        if not self.floor_is_quotable:
            return (f"threshold {self.threshold:g}: {volume}, but no usable "
                    f"detection floor — this setting cannot support a null "
                    f"result at any sensitivity")
        text = (f"threshold {self.threshold:g}: {volume}, detects "
                f"{self.scenario_kind or 'effects'} from {self.floor:g}x")
        if not self.floor_resolved:
            text += "  (floor unresolved at this repeat count)"
        return text


@dataclass(frozen=True)
class BudgetCalibration:
    """Every candidate threshold for one configuration, and the pick."""

    indicator_key: str
    options: tuple[ThresholdOption, ...]
    budget: AlertBudget

    @property
    def affordable(self) -> tuple[ThresholdOption, ...]:
        """Options whose noise volume fits, and whose floor can be quoted."""
        return tuple(o for o in self.options
                     if o.within(self.budget) and o.floor_is_quotable)

    @property
    def recommended(self) -> ThresholdOption | None:
        """The most sensitive setting the budget can afford.

        Sensitivity is the objective; the budget is the constraint. Picking
        the option nearest the middle of the range would be optimising for a
        number of alerts, which is how a budget turns back into a quota.
        """
        if not self.affordable:
            return None
        return min(self.affordable, key=lambda o: (o.floor, o.quiet_per_week))

    def describe(self) -> str:
        lines = [f"{self.indicator_key}: {len(self.options)} threshold(s) "
                 f"measured against {self.budget.max_per_week:g} alerts/week."]
        for option in self.options:
            mark = "  ok " if option in self.affordable else "  -- "
            lines.append(mark + option.describe())

        pick = self.recommended
        if pick is None:
            lines.append(
                "No threshold both fits the budget and keeps a usable "
                "detection floor. Tightening further would buy quiet by going "
                "blind; this configuration needs rethinking, not retuning.")
        else:
            lines.append(
                f"Recommended: {pick.threshold:g} — the most sensitive "
                f"setting the budget affords ({pick.quiet_per_week:.1f}/week "
                f"on quiet data, floor {pick.floor:g}x).")
            if not pick.floor_resolved or any(
                    not o.floor_resolved for o in self.affordable):
                lines.append(
                    f"At least one floor here is unresolved at "
                    f"{pick.n_repeats} runs, and the recommendation is ranked "
                    f"on floors. Re-run with more repeats before changing a "
                    f"shipped threshold on the strength of it.")
            if pick.quiet_per_week < self.budget.min_per_week:
                lines.append(
                    f"Noise alone yields under {self.budget.min_per_week:g} "
                    f"alerts/week. That is the desired outcome, not a "
                    f"shortfall to be corrected by loosening — the rest of an "
                    f"analyst's week should come from events.")
        return "\n".join(lines)


def sweep_thresholds(detector_for, thresholds, *,
                     scenario_kind: str = "spike",
                     magnitudes: tuple[float, ...] = (1.5, 2.0, 3.0, 5.0, 8.0),
                     n_repeats: int = 5,
                     recall_threshold: float = 0.8,
                     ) -> tuple[ThresholdOption, ...]:
    """Measure volume and floor at each threshold.

    `detector_for` is ``threshold -> (series -> bool series)``, so this works
    for any test type that has a knob, and knows nothing about which one.
    """
    from sentinel.eval.harness import false_alarm_rate, power_curve

    options = []
    for threshold in thresholds:
        detector = detector_for(threshold)
        per_year = false_alarm_rate(detector, n_repeats=n_repeats)
        curve = power_curve(detector, kind=scenario_kind,
                            magnitudes=magnitudes, n_repeats=n_repeats,
                            threshold=recall_threshold)
        options.append(ThresholdOption(
            threshold=float(threshold),
            quiet_per_week=per_year / _WEEKS_PER_SERIES,
            floor=curve.floor,
            confounded=curve.is_confounded,
            scenario_kind=scenario_kind,
            floor_resolved=not curve.floor_is_uncertain,
            n_repeats=n_repeats,
        ))
    return tuple(options)


def calibrate_indicator(indicator: Indicator,
                        thresholds: tuple[float, ...] = (2.0, 2.5, 3.0, 3.5,
                                                         4.0, 5.0),
                        budget: AlertBudget | None = None,
                        n_repeats: int = 5) -> BudgetCalibration | None:
    """Sweep an indicator's threshold against the budget, or decline.

    Returns None for test types with no volume knob. A deterministic condition
    fires exactly when its rule holds — there is nothing to tune, and offering
    a threshold for it would invite someone to tune away a finding they simply
    did not want to see.
    """
    if indicator.test_type is not IndicatorTest.LEVEL_DEVIATION:
        return None

    from sentinel.core.detect.power import production_detector

    def detector_for(threshold: float):
        # Measured through the shipped detector, not a stand-in: v1's whole
        # evaluation described a system nobody ran.
        tuned = replace(indicator,
                        test_config={**dict(indicator.test_config),
                                     "threshold": float(threshold)})
        return production_detector(tuned)

    return BudgetCalibration(
        indicator_key=indicator.key,
        options=sweep_thresholds(detector_for, thresholds,
                                 n_repeats=n_repeats),
        budget=budget or AlertBudget(),
    )
