"""Basis-interface voor data-connectors."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd

from core.logging_setup import get_logger

_logger = get_logger("connectors")

USER_AGENT = "SENTINEL/1.0 (anomaliedetectie; research use)"


class ConnectorError(RuntimeError):
    """Nette fout van een connector (bron onbereikbaar, key ontbreekt, ...)."""


def http_json(url: str, timeout: int = 60, retries: int = 3,
              backoff: float = 5.0) -> dict | list:
    """GET met retry/backoff op tijdelijke fouten (429/5xx).

    Publieke onderzoeks-API's (GDELT, ACLED) rate-limiten agressief; zonder
    backoff faalt een geplande run bij de eerste drukte. Een 4xx anders dan
    429 is een echte fout (verkeerde key/query) en wordt niet herhaald.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise ConnectorError(f"HTTP {e.code} van {url}") from e
            wait = backoff * (2 ** attempt)
            _logger.warning("bron rate-limit/serverfout — opnieuw proberen",
                            extra={"ctx": {"url": url, "code": e.code,
                                           "wait_s": wait}})
            time.sleep(wait)
        except json.JSONDecodeError as e:
            raise ConnectorError(f"Bron gaf geen geldige JSON: {e}") from e
        except Exception as e:  # netwerk, timeout
            last = e
            time.sleep(backoff * (2 ** attempt))
    raise ConnectorError(f"Bron onbereikbaar na {retries} pogingen: {last}")


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

    #: Namen van vereiste env-vars (API-keys). Leeg = geen key nodig.
    requires_env: tuple[str, ...] = ()

    def fetch(self, since: datetime | None) -> pd.DataFrame:
        raise NotImplementedError

    def missing_config(self) -> list[str]:
        return [k for k in self.requires_env if not os.environ.get(k)]

    def self_test(self) -> tuple[bool, str]:
        """Live-check voor de UI-knop 'Verbinding testen'.

        Haalt een kleine hoeveelheid data op en rapporteert of de bron
        bereikbaar is. Gooit nooit — de UI toont de boodschap.
        """
        missing = self.missing_config()
        if missing:
            return False, (f"Ontbrekende instelling(en): {', '.join(missing)}. "
                           f"Zet die als secret of env-var.")
        try:
            df = self.fetch(None)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if df.empty:
            return True, "Bron bereikbaar, maar leverde 0 rijen terug."
        return True, (
            f"Bron bereikbaar — {len(df)} rijen, "
            f"{pd.Timestamp(df['timestamp'].min()):%d-%m-%Y} t/m "
            f"{pd.Timestamp(df['timestamp'].max()):%d-%m-%Y}."
        )


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
