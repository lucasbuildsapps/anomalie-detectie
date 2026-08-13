"""Prijzen per kanaal — met herkomst en verificatiedatum.

Prijzen worden hier *niet* uit het hoofd opgeschreven. Elke regel heeft
een bron en een datum waarop hij nagekeken is, en `staleness_waarschuwing()`
klaagt zodra dat te lang geleden is.

**Beperking van deze verificatieronde (13-08-2026).** De primaire
prijspagina's zijn vanuit deze omgeving niet op te vragen: het
egress-beleid blokkeert `developers.google.com`, `mapsplatform.google.com`,
`docs.tomtom.com` en `here.com`. De bedragen hieronder komen uit een
websearch over die pagina's, dus uit samenvattingen en secundaire bronnen.
`confidence` legt dat per regel vast. Voordat er echt geld wordt uitgegeven
moet iemand met open netwerk de primaire pagina's naast deze tabel leggen —
zie de URL's in `bron`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Na zoveel dagen is een prijs verdacht en moet hij opnieuw nagekeken.
MAX_LEEFTIJD_DAGEN = 45


@dataclass(frozen=True)
class Prijs:
    provider: str
    sku: str
    usd_per_1000: float
    gratis_staffel: str
    bron: str
    nagekeken_op: date
    confidence: str          # 'primair' | 'secundair' | 'onbekend'
    opmerking: str = ""

    @property
    def usd_per_call(self) -> float:
        return self.usd_per_1000 / 1000.0


NAGEKEKEN_OP = date(2026, 8, 13)

PRIJZEN: tuple[Prijs, ...] = (
    Prijs(
        provider="google_routes", sku="Compute Routes Pro",
        usd_per_1000=10.00,
        gratis_staffel="5.000 Pro-events per maand",
        bron="https://developers.google.com/maps/documentation/routes/usage-and-billing "
             "(primair, geblokkeerd) via websearch-samenvatting",
        nagekeken_op=NAGEKEKEN_OP, confidence="secundair",
        opmerking="TRAFFIC_AWARE en TRAFFIC_AWARE_OPTIMAL vallen in de Pro-SKU. "
                  "Zonder verkeersvoorkeur is het Essentials à $5,00/1.000 — "
                  "die variant is voor dit onderzoek waardeloos.",
    ),
    Prijs(
        provider="google_routes", sku="Compute Routes Essentials",
        usd_per_1000=5.00,
        gratis_staffel="10.000 Essentials-events per maand",
        bron="idem",
        nagekeken_op=NAGEKEKEN_OP, confidence="secundair",
        opmerking="Alleen ter vergelijking; geen verkeersafhankelijke reistijd.",
    ),
    Prijs(
        provider="tomtom", sku="Traffic Flow non-tile request",
        usd_per_1000=0.50,
        gratis_staffel="2.500 non-tile requests per dag (plus 50.000 tiles)",
        bron="https://docs.tomtom.com/pricing (primair, geblokkeerd) "
             "via websearch-samenvatting: 50k credits voor $25",
        nagekeken_op=NAGEKEKEN_OP, confidence="secundair",
        opmerking="Grotere pakketten zijn goedkoper per 1.000; $0,50 is de bovengrens.",
    ),
    Prijs(
        provider="here", sku="Traffic Flow transaction",
        usd_per_1000=1.00,
        gratis_staffel="250.000 transacties per maand",
        bron="https://www.here.com/get-started/pricing (primair, geblokkeerd) "
             "via websearch-samenvatting",
        nagekeken_op=NAGEKEKEN_OP, confidence="secundair",
        opmerking="HERE verhoogde tarieven ~6% per 1 april 2026 voor nieuwe "
                  "contracten en verlengingen; controleer je eigen contract.",
    ),
    Prijs(
        provider="waze", sku="geen (ongedocumenteerd endpoint)",
        usd_per_1000=0.0,
        gratis_staffel="niet van toepassing",
        bron="geen publieke prijslijst — geen product",
        nagekeken_op=NAGEKEKEN_OP, confidence="onbekend",
        opmerking="Gratis is hier geen argument: ongedocumenteerd kanaal, "
                  "alleen eenmalige dekkingscontrole.",
    ),
)


def voor(provider: str) -> Prijs | None:
    """Eerste (goedkoopste bruikbare) prijsregel voor een provider."""
    for p in PRIJZEN:
        if p.provider == provider:
            return p
    return None


def staleness_waarschuwing(vandaag: date | None = None) -> str | None:
    vandaag = vandaag or date.today()
    leeftijd = (vandaag - NAGEKEKEN_OP).days
    if leeftijd > MAX_LEEFTIJD_DAGEN:
        return (f"Prijzen zijn {leeftijd} dagen oud (nagekeken {NAGEKEKEN_OP}). "
                f"Opnieuw verifiëren voordat je een langere meetreeks start.")
    return None


def schat_kosten(n_segmenten: int, n_rondes: int, providers: list[str]) -> dict[str, float]:
    """Bovengrens van de kosten van een probe-run, per provider en totaal."""
    uit: dict[str, float] = {}
    for naam in providers:
        prijs = voor(naam)
        uit[naam] = (prijs.usd_per_call if prijs else 0.0) * n_segmenten * n_rondes
    uit["totaal"] = sum(uit.values())
    return uit


def tabel() -> str:
    kop = f"{'provider':<15} {'SKU':<30} {'$/1.000':>9}  {'zekerheid':<11} gratis staffel"
    regels = [kop, "-" * len(kop)]
    for p in PRIJZEN:
        regels.append(f"{p.provider:<15} {p.sku:<30} {p.usd_per_1000:>9.2f}  "
                      f"{p.confidence:<11} {p.gratis_staffel}")
    regels.append("")
    regels.append(f"Nagekeken op {NAGEKEKEN_OP}. Primaire prijspagina's zijn vanuit "
                  f"deze omgeving geblokkeerd; zie module-docstring.")
    waarschuwing = staleness_waarschuwing()
    if waarschuwing:
        regels.append(f"LET OP: {waarschuwing}")
    return "\n".join(regels)
