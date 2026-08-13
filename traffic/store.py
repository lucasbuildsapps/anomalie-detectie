"""Opslag van de probe: ruw eerst, geparst daarna.

Twee regels die de hele keten door gelden:

1. **De ruwe respons gaat als eerste naar schijf.** Elk afgeleid getal
   moet terug te voeren zijn op een bewaard antwoord; lukt het parsen
   niet, dan is het bewijs er nog steeds.
2. **Append-only en hervatbaar.** Een run schrijft in een map per
   run-label; opnieuw starten voegt toe in plaats van te overschrijven, en
   `voltooide_rondes()` vertelt waar hij was.

Nog geen SQLite: dat is Fase 1 en die wacht op de uitkomst van de poort.
NDJSON is hier genoeg en leest zonder schema-migratie terug.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic.config import DATA_DIR
from traffic.providers.base import Sample


@dataclass
class ProbeStore:
    """Bestanden van één probe-run."""

    label: str
    wortel: Path = DATA_DIR

    @property
    def map(self) -> Path:
        return self.wortel / self.label

    @property
    def ruw_pad(self) -> Path:
        return self.map / "raw.ndjson"

    @property
    def samples_pad(self) -> Path:
        return self.map / "samples.ndjson"

    @property
    def kosten_pad(self) -> Path:
        return self.map / "cost.ndjson"

    @property
    def staat_pad(self) -> Path:
        return self.map / "state.json"

    def bereid_voor(self) -> None:
        self.map.mkdir(parents=True, exist_ok=True)

    # --- schrijven ---

    def schrijf_ruw(self, sample: Sample, ronde: int) -> None:
        """Ruwe respons bewaren. Gebeurt vóór het wegschrijven van de sample."""
        self.bereid_voor()
        rij = {
            "ts_utc": sample.ts_utc, "ronde": ronde, "provider": sample.provider,
            "segment_id": sample.segment_id, "http_status": sample.http_status,
            "raw": sample.raw,
        }
        with self.ruw_pad.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rij, ensure_ascii=False, default=str) + "\n")

    def schrijf_sample(self, sample: Sample, ronde: int) -> None:
        self.bereid_voor()
        rij = sample.to_row()
        rij["ronde"] = ronde
        with self.samples_pad.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rij, ensure_ascii=False, default=str) + "\n")

    def schrijf_staat(self, **velden: Any) -> None:
        self.bereid_voor()
        staat = self.lees_staat()
        staat.update(velden)
        staat["bijgewerkt_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.staat_pad.write_text(json.dumps(staat, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    # --- lezen ---

    def lees_staat(self) -> dict:
        if not self.staat_pad.exists():
            return {}
        try:
            return json.loads(self.staat_pad.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def lees_samples(self) -> list[dict]:
        if not self.samples_pad.exists():
            return []
        uit = []
        for regel in self.samples_pad.read_text(encoding="utf-8").splitlines():
            if not regel.strip():
                continue
            try:
                uit.append(json.loads(regel))
            except json.JSONDecodeError:
                continue          # kapotte regel overslaan, niet raden
        return uit

    def voltooide_rondes(self) -> int:
        """Hoogste rondenummer dat al op schijf staat (0 = nog niets)."""
        rondes = [int(r.get("ronde") or 0) for r in self.lees_samples()]
        return max(rondes, default=0)
