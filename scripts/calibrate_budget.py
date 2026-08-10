#!/usr/bin/env python
"""Sweep indicator thresholds against the agreed alert budget.

Run: python scripts/calibrate_budget.py [--region euro_atlantic]

This is the calibration that replaced v1's quota, and the difference is worth
restating because the two are easy to confuse.

v1's `run_auto_pilot()` loosened sensitivity at runtime until it had something
to show. That guarantees findings, and guarantees them loudest in the weeks
when least is happening. This script does the opposite: it measures, offline
and in advance, how many alerts each threshold produces on data where nothing
is happening — and then that threshold is fixed. At runtime there is no
ranking, no top-N, and no suppression. If thirty things happen, thirty alerts
appear.

Reading the output
------------------
Two numbers per threshold, and neither is meaningful alone:

- **alerts/week on quiet data** — the noise floor. If this alone exceeds the
  budget the configuration is wrong, because it will drown the analyst before
  anything happens.
- **detection floor** — the smallest effect still caught. Tightening to fit
  the budget raises this, and a budget met by going blind is not a budget met.

The recommendation maximises sensitivity subject to the volume constraint. It
does *not* aim for the middle of the 5–15 range: aiming at an alert count is
how a budget turns back into a quota.

Cost: each threshold runs a false-alarm measurement plus a full power curve.
Expect a few minutes per indicator at the default repeat count.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.core.contracts import IndicatorTest  # noqa: E402
from sentinel.eval.budget import AlertBudget, calibrate_indicator  # noqa: E402
from sentinel.regions import REGIONS, get_region  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=None,
                        help="one region key; default is every region")
    parser.add_argument("--max-per-week", type=float, default=15.0)
    parser.add_argument("--min-per-week", type=float, default=5.0)
    parser.add_argument("--repeats", type=int, default=12,
                        help="draws per magnitude; low counts leave the "
                             "detection floor unresolved and the "
                             "recommendation unstable")
    parser.add_argument("--thresholds", type=float, nargs="+",
                        default=[2.0, 2.5, 3.0, 3.5, 4.0, 5.0])
    args = parser.parse_args()

    budget = AlertBudget(min_per_week=args.min_per_week,
                         max_per_week=args.max_per_week)
    regions = [get_region(args.region)] if args.region else list(REGIONS)

    tunable = [
        (region, indicator)
        for region in regions for indicator in region.indicators
        if indicator.test_type is IndicatorTest.LEVEL_DEVIATION
    ]
    if not tunable:
        print("No indicators with a tunable threshold. Deterministic "
              "conditions and sustained-divergence tests are not calibrated "
              "here — the first has nothing to tune, the second takes its "
              "knobs from DivergenceConfig.")
        return 0

    print(f"Budget: at most {budget.max_per_week:g} alerts/week per region "
          f"on quiet data.\n")

    unaffordable = 0
    for region, indicator in tunable:
        print(f"--- {region.name} / {indicator.key} "
              f"(currently {indicator.test_config.get('threshold', 3.5)})",
              flush=True)
        calibration = calibrate_indicator(
            indicator, thresholds=tuple(args.thresholds), budget=budget,
            n_repeats=args.repeats)
        print(calibration.describe())

        pick = calibration.recommended
        if pick is None:
            unaffordable += 1
        elif pick.threshold != indicator.test_config.get("threshold"):
            print(f"  -> differs from the shipped threshold "
                  f"({indicator.test_config.get('threshold')}). Changing it "
                  f"means re-running scripts/precompute_power.py, because the "
                  f"detection floor is a property of the configuration.")
        print()

    if unaffordable:
        print(f"{unaffordable} indicator(s) have no threshold that both fits "
              f"the budget and keeps a usable floor.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
