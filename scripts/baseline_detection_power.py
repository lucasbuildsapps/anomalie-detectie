#!/usr/bin/env python
"""Measure the v1 detectors against the synthetic suite.

Run: python scripts/baseline_detection_power.py

This is the "before" picture. Its purpose is to make the v2 rebuild
falsifiable: every claim that a new baseline or test is better has to beat
these numbers on the same scenarios, and regressions become visible instead
of arguable.

Read the false-alarm column first. Recall is easy to buy by lowering a
threshold; the pair of (what it finds, what it invents) is the only honest
summary, which is why the negative controls carry equal weight here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.registry import get_detectors  # noqa: E402
from sentinel.eval import (  # noqa: E402
    SCENARIO_KINDS,
    ScenarioGenerator,
    false_alarm_rate,
    power_curve,
    run_scenario,
)

DETECTORS = ("Z-score (MAD)", "Rolling mean ± N·std", "STL residual")
#: The v2 dual baseline, measured on identical scenarios so the comparison
#: is like for like.
V2_LABEL = "v2 dual baseline"


def v2_detector():
    from sentinel.core.baseline import divergence_detector

    return divergence_detector(reference_periods=180)


def as_callable(name: str):
    """Adapt a v1 detector to the harness contract: series -> bool series."""
    detector = get_detectors()[name]

    def run(series: pd.Series) -> pd.Series:
        frame = pd.DataFrame({"timestamp": series.index,
                              "value": series.to_numpy()})
        out = detector.detect(frame, "timestamp", "value")
        return pd.Series(out["is_anomaly"].to_numpy(), index=series.index)

    return run


def _all_detectors() -> dict:
    out = {name: as_callable(name) for name in DETECTORS}
    out[V2_LABEL] = v2_detector()
    return out


def scenario_table() -> pd.DataFrame:
    generator = ScenarioGenerator()
    rows = []
    for name, detector in _all_detectors().items():
        for kind in SCENARIO_KINDS:
            scenario = generator.build(kind)
            score = run_scenario(detector, scenario)
            rows.append({
                "detector": name,
                "scenario": kind,
                "control": scenario.is_negative_control,
                "found": score.match.hits,
                "of": score.match.n_truth,
                "false_alarms": score.match.false_alarms,
                "delay": score.match.median_lead_time,
                "verdict": score.outcome,
            })
    return pd.DataFrame(rows)


def power_table() -> pd.DataFrame:
    rows = []
    for name, detector in _all_detectors().items():
        for kind in ("sustained_increase", "gradual_escalation",
                     "adaptation_failure"):
            curve = power_curve(detector, kind=kind, n_repeats=8)
            rows.append({
                "detector": name,
                "scenario": kind,
                **{f"x{m:g}": f"{r:.1f}/{c:.1f}"
                   for m, r, c in zip(curve.magnitudes, curve.recalls,
                                      curve.chance_rates, strict=True)},
                "floor": "confounded" if curve.is_confounded
                         else f"{curve.floor:g}",
            })
    return pd.DataFrame(rows)


def main() -> None:
    pd.set_option("display.width", 120)

    print("=" * 78)
    print("SCENARIO SUITE — v1 detectors")
    print("=" * 78)
    table = scenario_table()
    for name, group in table.groupby("detector", sort=False):
        print(f"\n{name}")
        print(group.drop(columns=["detector"]).to_string(index=False))

    print("\n" + "=" * 78)
    print("FALSE ALARMS ON PURE NOISE (episodes per quiet series, 20 draws)")
    print("=" * 78)
    for name, detector in _all_detectors().items():
        rate = false_alarm_rate(detector, n_repeats=20)
        print(f"  {name:24s} {rate:6.2f}")

    print("\n" + "=" * 78)
    print("DETECTION POWER — recall vs. effect size")
    print("=" * 78)
    print(power_table().to_string(index=False))

    print("\nCells show recall/chance: the hit rate on the injected series "
          "and on the\nsame baseline WITHOUT the injection. Only the "
          "difference is attributable\ndetection. 'confounded' means the "
          "detector flags that window so often on\nquiet data that no "
          "detection floor can be quoted.")


if __name__ == "__main__":
    main()
