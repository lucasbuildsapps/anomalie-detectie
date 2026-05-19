"""Controleer of de voorspelmethoden in core/normbeeld.py de verwachte output
geven op synthetische data. Draaien:

    python scripts/verify_methods.py

Verwachte uitkomsten:
  - median:         vlakke voorspelling rond mediaan
  - rolling:        vlakke voorspelling rond recent gemiddelde
  - seasonal_naive: oscillatie met dezelfde periode als input
  - stl:            trend + seizoenscomponent zichtbaar
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.normbeeld import (  # noqa: E402
    PREDICTION_METHODS, compute_normbeeld,
)


def make_synthetic(
    days: int = 120, base: float = 3.0, weekend_bump: float = 1.0,
    trend_per_day: float = 0.01, noise: float = 0.7, seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-01")
    rows = []
    for d in range(days):
        date = start + pd.Timedelta(days=d)
        weekend = date.dayofweek >= 5
        value = max(0, round(
            base + (weekend_bump if weekend else 0)
            + trend_per_day * d + rng.normal(0, noise)
        ))
        rows.append({
            "timestamp": date,
            "value": value,
            "location_name": "TEST-A",
        })
    return pd.DataFrame(rows)


def describe(label: str, series: pd.Series):
    print(f"  {label:>18}: min={series.min():6.2f}  "
          f"max={series.max():6.2f}  "
          f"mean={series.mean():6.2f}  "
          f"std={series.std():6.2f}")


def main():
    df = make_synthetic()
    print(f"Synthetische data: {len(df)} dagen, weekend-bump=1.0, trend=+0.01/dag")
    print()

    methods = list(PREDICTION_METHODS.keys())
    for m in methods:
        nb = compute_normbeeld(
            df, location="TEST-A", horizon_days=14,
            methods=[m], aggregation="daily",
        )
        if nb is None:
            print(f"[{m}] NIET BEREKEND")
            continue
        print(f"[{m}] {PREDICTION_METHODS[m]}")
        print(f"  Methodes gevraagd: {nb.methods_requested}")
        print(f"  Methodes gelopen:  {nb.methods_used}")
        if nb.methods_skipped:
            print(f"  GESKIPT:           {nb.methods_skipped}")
        fc = nb.forecast["expected"]
        describe("forecast 14 dagen", fc)
        # Eerste 7 waarden
        print(f"  forecast eerste 7: {list(np.round(fc.values[:7], 2))}")
        print()

    print("Test: alle methodes tegelijk")
    nb = compute_normbeeld(
        df, location="TEST-A", horizon_days=14,
        methods=methods, aggregation="daily",
    )
    if nb is None:
        print("  niet berekend.")
        return
    print(f"  Gelopen: {nb.methods_used}")
    print(f"  Ensemble forecast eerste 7: "
          f"{list(np.round(nb.forecast['expected'].values[:7], 2))}")
    print()
    print("Per-methode forecast eerste 7 dagen:")
    for m_key, m_fc in nb.per_method_forecast.items():
        vals = list(np.round(m_fc["expected"].values[:7], 2))
        print(f"  {PREDICTION_METHODS[m_key]:<28}: {vals}")


if __name__ == "__main__":
    main()
