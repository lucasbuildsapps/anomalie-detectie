"""Basis-interface voor data-connectors."""
from __future__ import annotations

from datetime import datetime

import pandas as pd


class Connector:
    """Eén externe databron.

    Verplichte klasse-attributen:
    - name:             unieke naam (toont in logs, ingest_runs, UI)
    - dataset_name:     doel-dataset in de opslag (wordt aangemaakt indien nodig)
    - schedule_minutes: gewenste pull-interval
    - enabled:          alleen enabled connectors draait de worker

    fetch(since) MOET een DataFrame teruggeven in het interne schema:
    kolommen 'timestamp' en 'value' verplicht; 'category',
    'location_name', 'lat', 'lon' en extra kolommen optioneel.
    Dedupe gebeurt downstream (row-hash), dus overlappende pulls zijn
    onschadelijk — lever gerust een ruime window opnieuw aan.
    """

    name: str = "?"
    dataset_name: str = "?"
    description: str = ""
    schedule_minutes: int = 60
    enabled: bool = False

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        raise NotImplementedError


def get_connectors() -> dict[str, Connector]:
    """Auto-discovery van connector-plug-ins (zelfde patroon als detectors)."""
    import importlib
    import pkgutil

    import connectors as pkg

    out: dict[str, Connector] = {}
    for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
        if mod_name.startswith("_") or mod_name == "base":
            continue
        module = importlib.import_module(f"connectors.{mod_name}")
        for attr in vars(module).values():
            if (isinstance(attr, type) and issubclass(attr, Connector)
                    and attr is not Connector):
                inst = attr()
                out[inst.name] = inst
    return out
