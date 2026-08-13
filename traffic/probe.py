"""Fase 0: de haalbaarheidsprobe.

Wat deze probe moet beantwoorden, en niets meer: **is er voor Oekraïense
steden een verkeerssignaal te krijgen dat leeft en beweegt?** Google zette
de live-verkeerslaag in februari 2022 uit; dat de Routes API intern nog
met verkeer rekent is een hypothese. Deze probe test hem.

De poort komt daarna (`coverage.poort`). Blijft die dicht, dan wordt er
geen keten gebouwd — en al helemaal niet teruggevallen op het
typisch-verkeer-model als vervanging voor een meting.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from traffic import providers as prov_mod
from traffic.config import ProbeConfig
from traffic.cost import BudgetExceeded, CostLedger
from traffic.pricing import schat_kosten
from traffic.providers.base import Sample, TrafficProvider, http_call
from traffic.segments import Segment, actieve_segmenten
from traffic.store import ProbeStore

#: Host per kanaal, voor de bereikbaarheidscontrole in de preflight.
HOSTS = {
    "google_routes": "https://routes.googleapis.com/",
    "tomtom": "https://api.tomtom.com/",
    "here": "https://data.traffic.hereapi.com/",
    "waze": "https://www.waze.com/",
}


def bereikbaar(url: str, timeout: int = 10) -> tuple[bool, str]:
    """Kan deze host bereikt worden?

    Elke HTTP-status geldt als bereikbaar — ook 403 en 404. Die komen van
    de provider en bewijzen dus dat het verzoek is aangekomen. Alleen een
    verbindingsfout (of een 403 van de egress-proxy op de CONNECT-tunnel)
    betekent geblokkeerd.
    """
    try:
        status, _ = http_call(url, timeout=timeout)
        return True, f"HTTP {status}"
    except Exception as e:
        from traffic.providers.base import classify_error
        soort, boodschap = classify_error(e)
        return False, f"{soort}: {boodschap[:120]}"


@dataclass
class ProbeRunner:
    cfg: ProbeConfig
    store: ProbeStore
    ledger: CostLedger = field(init=False)

    def __post_init__(self) -> None:
        self.store.bereid_voor()
        self.ledger = CostLedger(budget_usd=self.cfg.budget_usd, pad=self.store.kosten_pad)

    # ------------------------------------------------------------------ preflight

    def preflight(self) -> str:
        """Wat werkt er, nog vóór er één betaalde aanroep gedaan is."""
        namen = list(self.cfg.providers) + (["waze"] if self.cfg.include_waze else [])
        segmenten = actieve_segmenten(self.cfg.include_optional_controls)
        regels = [
            "PREFLIGHT",
            f"  segmenten:        {len(segmenten)} "
            f"({len({s.city for s in segmenten})} steden, "
            f"{sum(1 for s in segmenten if s.is_external_control)} externe controle)",
            f"  rondes:           {self.cfg.n_rondes} "
            f"(elke {self.cfg.interval_min} min, {self.cfg.duration_min} min totaal)",
            f"  budgetplafond:    ${self.cfg.budget_usd:.2f}",
            "",
            f"  {'kanaal':<15}{'key':<12}{'host':<14}{'detail'}",
            f"  {'-' * 70}",
        ]
        schatting = schat_kosten(len(segmenten), self.cfg.n_rondes,
                                 [n for n in namen if n != "waze"])
        for provider in prov_mod.maak(namen):
            ontbreekt = provider.missing_config()
            key_status = "ONTBREEKT" if ontbreekt else ("n.v.t." if not provider.requires_env
                                                        else "aanwezig")
            ok, detail = bereikbaar(HOSTS.get(provider.name, ""))
            regels.append(f"  {provider.name:<15}{key_status:<12}"
                          f"{'bereikbaar' if ok else 'GEBLOKKEERD':<14}{detail}")
            if ontbreekt:
                regels.append(f"  {'':<15}→ zet {', '.join(ontbreekt)} in .env")
        regels += [
            "",
            "  Geschatte bovengrens van de kosten van een volledige run:",
        ]
        for naam, bedrag in schatting.items():
            regels.append(f"    {naam:<16} ${bedrag:>8.2f}")
        return "\n".join(regels)

    # ------------------------------------------------------------------ meten

    def _sample(self, provider: TrafficProvider, segment: Segment, ronde: int) -> Sample:
        """Één aanroep: budget checken, boeken, ruw bewaren, dan parsen."""
        kosten = provider.unit_cost_usd
        if kosten > 0 and not provider.missing_config():
            self.ledger.check(kosten)          # gooit BudgetExceeded vóór de aanroep
        sample = provider.sample(segment)
        # Alleen boeken wat werkelijk aan de provider is gestuurd: een
        # ontbrekende key kost niets.
        if sample.error_kind != "no_key" and kosten > 0:
            self.ledger.boek(provider.name, provider.sku, kosten, segment.id)
            sample.cost_usd = kosten
        else:
            sample.cost_usd = 0.0
        self.store.schrijf_ruw(sample, ronde)   # ruw eerst, altijd
        self.store.schrijf_sample(sample, ronde)
        return sample

    def ronde(self, providers: list[TrafficProvider], segmenten: list[Segment],
              nummer: int) -> list[Sample]:
        uit: list[Sample] = []
        for provider in providers:
            if getattr(provider, "alleen_eenmalig", False) and nummer > 1:
                continue
            for segment in segmenten:
                uit.append(self._sample(provider, segment, nummer))
        return uit

    def run(self, log=print, sleep=time.sleep) -> dict:
        """Alle rondes. Hervat waar een eerdere run gebleven was."""
        namen = list(self.cfg.providers) + (["waze"] if self.cfg.include_waze else [])
        providers = prov_mod.maak(namen)
        segmenten = actieve_segmenten(self.cfg.include_optional_controls)

        gedaan = self.store.voltooide_rondes()
        if gedaan:
            log(f"hervatten: ronde 1–{gedaan} staat al op schijf")
        self.store.schrijf_staat(label=self.cfg.label, providers=namen,
                                 n_segmenten=len(segmenten), n_rondes=self.cfg.n_rondes,
                                 budget_usd=self.cfg.budget_usd)

        gestopt: str | None = None
        for nummer in range(gedaan + 1, self.cfg.n_rondes + 1):
            try:
                samples = self.ronde(providers, segmenten, nummer)
            except BudgetExceeded as e:
                gestopt = str(e)
                log(f"gestopt op budget: {e}")
                break
            n_ok = sum(1 for s in samples if s.ok)
            log(f"ronde {nummer}/{self.cfg.n_rondes}: {n_ok}/{len(samples)} geslaagd, "
                f"${self.ledger.besteed_usd:.4f} besteed")
            self.store.schrijf_staat(laatste_ronde=nummer,
                                     besteed_usd=round(self.ledger.besteed_usd, 6))
            if nummer < self.cfg.n_rondes:
                sleep(self.cfg.interval_min * 60)

        self.store.schrijf_staat(afgerond=gestopt is None, gestopt_om=gestopt,
                                 besteed_usd=round(self.ledger.besteed_usd, 6))
        return {"besteed_usd": self.ledger.besteed_usd, "gestopt_om": gestopt,
                "n_calls": self.ledger.n_calls}
