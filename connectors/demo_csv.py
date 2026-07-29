"""Voorbeeld-connector: leest de meegeleverde demo-CSV.

Dient als sjabloon voor echte bronnen (HTTP-API, share, database-link).
Standaard uitgeschakeld; zet enabled = True om hem door de worker te
laten draaien.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from connectors.base import Connector
from core.import_data import apply_mapping

_CSV = Path(__file__).resolve().parent.parent / "data" / "missile_attacks_demo.csv"

_MAPPING = {
    "time": "time_start",
    "value": "launched",
    "location_name": "target",
    "category": "model",
    "extras": ["time_end", "launch_place", "target_main",
               "destroyed", "not_reach_goal"],
}


class DemoCsvConnector(Connector):
    name = "demo-csv"
    dataset_name = "Demo - Russian missile attacks on Ukraine"
    description = "Sjabloon-connector: herleest de lokale demo-CSV."
    schedule_minutes = 60 * 24
    enabled = False  # sjabloon; bewust uit

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        raw = pd.read_csv(_CSV)
        normalized, _stats = apply_mapping(raw, _MAPPING)
        if since is not None:
            normalized = normalized[normalized["timestamp"] >= pd.Timestamp(since)]
        return normalized
