"""Pytest config: zorg dat de projectmap importeerbaar is en lever
gedeelde synthetische datasets."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def synthetic_daily() -> pd.DataFrame:
    """120 dagen, baseline 3/dag, weekend +1, lichte trend, 2 spikes."""
    rng = np.random.default_rng(42)
    start = pd.Timestamp("2025-01-01")
    rows = []
    for d in range(120):
        date = start + pd.Timedelta(days=d)
        weekend = date.dayofweek >= 5
        value = max(0, round(3 + (1 if weekend else 0) + 0.01 * d
                             + rng.normal(0, 0.7)))
        if d == 60:
            value = 25  # geïnjecteerde spike
        if d == 90:
            value = 30  # tweede spike
        rows.append({
            "timestamp": date,
            "value": float(value),
            "location_name": "BASIS-A",
        })
    return pd.DataFrame(rows)


@pytest.fixture
def mixed_format_dates() -> pd.Series:
    """Gemengde datum-formaten zoals in echte exports (de pandas 3.0 val)."""
    return pd.Series([
        "2026-05-16",
        "2026-05-16 18:00",
        "2026-05-15",
        "2026-05-14 09:30",
        "2026-05-13",
    ])
