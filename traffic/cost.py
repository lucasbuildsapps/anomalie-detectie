"""Kostenlogboek met hard plafond.

Elke aanroep wordt geboekt *voordat* hij gedaan wordt, en een aanroep die
het plafond zou doorbreken gaat niet door. Zo is de rekening auditeerbaar
en kan een probe niet stilletjes uit de hand lopen.

De gratis staffels (Google: 5.000 Pro-events/maand, TomTom: 2.500 non-tile
requests/dag, HERE: 250.000 transacties/maand) worden hier bewust *niet*
verrekend: die zijn accountbreed en dit proces weet niet wat er elders al
verbruikt is. De geboekte bedragen zijn dus een bovengrens, niet een
factuur.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


class BudgetExceeded(RuntimeError):
    """Het plafond is bereikt; de aanroep is niet gedaan."""


@dataclass
class CostLedger:
    budget_usd: float
    pad: Path | None = None
    besteed_usd: float = 0.0
    n_calls: int = 0
    per_provider: dict[str, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Hervatten: een bestaand logboek telt mee, zodat een herstart na
        # een crash niet opnieuw het volle budget mag uitgeven.
        if self.pad and self.pad.exists():
            for regel in self.pad.read_text(encoding="utf-8").splitlines():
                if not regel.strip():
                    continue
                try:
                    rij = json.loads(regel)
                except json.JSONDecodeError:
                    continue
                self._tel(rij.get("provider", "?"), float(rij.get("cost_usd") or 0.0))

    def _tel(self, provider: str, bedrag: float) -> None:
        self.besteed_usd += bedrag
        self.n_calls += 1
        vak = self.per_provider.setdefault(provider, {"n": 0, "usd": 0.0})
        vak["n"] += 1
        vak["usd"] += bedrag

    @property
    def resterend_usd(self) -> float:
        return max(0.0, self.budget_usd - self.besteed_usd)

    def check(self, bedrag: float) -> None:
        """Gooit BudgetExceeded als deze aanroep het plafond zou breken."""
        if self.besteed_usd + bedrag > self.budget_usd + 1e-9:
            raise BudgetExceeded(
                f"plafond ${self.budget_usd:.2f} bereikt "
                f"(besteed ${self.besteed_usd:.4f} in {self.n_calls} aanroepen); "
                f"aanroep van ${bedrag:.4f} niet gedaan"
            )

    def boek(self, provider: str, sku: str, bedrag: float, segment_id: str = "") -> None:
        self._tel(provider, bedrag)
        if self.pad is None:
            return
        self.pad.parent.mkdir(parents=True, exist_ok=True)
        rij = {
            "ts_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": provider, "sku": sku, "segment_id": segment_id,
            "cost_usd": bedrag, "cumulatief_usd": round(self.besteed_usd, 6),
        }
        with self.pad.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rij, ensure_ascii=False) + "\n")

    def samenvatting(self) -> str:
        regels = [f"Kosten: ${self.besteed_usd:.4f} van ${self.budget_usd:.2f} "
                  f"({self.n_calls} aanroepen)"]
        for naam, vak in sorted(self.per_provider.items()):
            regels.append(f"  {naam:<16} {int(vak['n']):>5} aanroepen  ${vak['usd']:.4f}")
        return "\n".join(regels)
