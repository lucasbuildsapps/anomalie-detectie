"""Dekkingsoordeel: levert dit kanaal in deze stad een levend signaal?

De valkuil die deze module moet dichtzetten: een geldig antwoord is geen
levend signaal. Alle drie de providers antwoorden netjes zonder
realtime-invoer — Google geeft dan `duration == staticDuration`, TomTom
geeft `currentSpeed == freeFlowSpeed`. Wie op "HTTP 200" afgaat, bouwt een
keten op een dood signaal.

Daarom twee eisen voor het oordeel *live*:

1. **Divergentie** — de waargenomen reistijd wijkt af van de reistijd
   zonder verkeer (|ratio − 1| > `EPS_DIVERGENTIE`).
2. **Beweging over tijd** — die afwijking verandert tussen de rondes. Een
   constante afwijking is even goed te verklaren door een modelverschil
   tussen de twee velden als door verkeer.

Zonder eis 2 zou een vaste opslag van 3% op elke route als "live"
doorgaan. Vandaar het tussenoordeel `divergent-maar-stil`: er is een
verschil, maar niets beweegt — dat is geen bruikbaar meetsignaal en het
verdient een eigen naam in plaats van een optimistische afronding.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from traffic.segments import by_id

#: Kleiner dan dit verschil noemen we geen divergentie (afronding).
EPS_DIVERGENTIE = 0.005
#: Spreiding van de ratio waaronder we 'geen beweging over tijd' zeggen.
EPS_BEWEGING = 0.005
#: Minimaal aantal geslaagde samples voor een uitspraak over beweging.
MIN_SAMPLES_VOOR_BEWEGING = 3

LIVE = "live"
DIVERGENT_STIL = "divergent-maar-stil"
ALLEEN_STATISCH = "alleen-statisch"
LEEG = "leeg"
GEEN_KEY = "geen-key"
ONBEREIKBAAR = "onbereikbaar"
TE_WEINIG = "te-weinig-samples"


@dataclass
class Oordeel:
    """Dekkingsoordeel voor één (stad, kanaal) of één (segment, kanaal)."""

    stad: str
    provider: str
    verdict: str
    n_samples: int = 0
    n_ok: int = 0
    n_geen_key: int = 0
    n_onbereikbaar: int = 0
    n_leeg: int = 0
    ratio_mediaan: float | None = None
    ratio_max: float | None = None
    ratio_spreiding: float | None = None
    n_unieke_ratio: int = 0
    n_unieke_polyline: int = 0
    afstand_spreiding_pct: float | None = None
    voorbeeldfout: str | None = None
    segmenten: list[str] = field(default_factory=list)

    @property
    def bruikbaar(self) -> bool:
        return self.verdict == LIVE


def _ratio(rij: dict) -> float | None:
    waarde = rij.get("ratio")
    return float(waarde) if isinstance(waarde, int | float) else None


def beoordeel(rijen: list[dict], stad: str, provider: str) -> Oordeel:
    """Één oordeel uit een verzameling samples van hetzelfde kanaal."""
    o = Oordeel(stad=stad, provider=provider, verdict=TE_WEINIG, n_samples=len(rijen))
    if not rijen:
        return o

    o.segmenten = sorted({r.get("segment_id", "?") for r in rijen})
    ok = [r for r in rijen if r.get("ok")]
    o.n_ok = len(ok)
    o.n_geen_key = sum(1 for r in rijen if r.get("error_kind") == "no_key")
    o.n_onbereikbaar = sum(1 for r in rijen if r.get("error_kind") in ("egress", "http", "parse"))
    o.n_leeg = sum(1 for r in rijen if r.get("error_kind") == "empty")
    fouten = [r.get("error") for r in rijen if r.get("error")]
    o.voorbeeldfout = fouten[0] if fouten else None

    if not ok:
        # Rangorde: een ontbrekende key is een ander probleem dan een
        # onbereikbaar endpoint, en dat weer een ander dan een leeg antwoord.
        if o.n_geen_key == len(rijen):
            o.verdict = GEEN_KEY
        elif o.n_onbereikbaar:
            o.verdict = ONBEREIKBAAR
        else:
            o.verdict = LEEG
        return o

    ratios = [r for r in (_ratio(x) for x in ok) if r is not None]
    if ratios:
        o.ratio_mediaan = statistics.median(ratios)
        o.ratio_max = max(ratios)
        o.ratio_spreiding = statistics.pstdev(ratios) if len(ratios) > 1 else 0.0
        # Afronden voordat we uniek tellen: providers leveren reistijd in
        # hele seconden, dus 1.0000001-verschillen zijn ruis.
        o.n_unieke_ratio = len({round(r, 4) for r in ratios})

    polylines = {r.get("polyline") for r in ok if r.get("polyline")}
    o.n_unieke_polyline = len(polylines)

    afstanden = [float(r["distance_m"]) for r in ok
                 if isinstance(r.get("distance_m"), int | float)]
    if afstanden and min(afstanden) > 0:
        o.afstand_spreiding_pct = (max(afstanden) - min(afstanden)) / min(afstanden) * 100

    if not ratios:
        o.verdict = LEEG
        return o

    divergent = any(abs(r - 1.0) > EPS_DIVERGENTIE for r in ratios)
    if not divergent:
        o.verdict = ALLEEN_STATISCH
        return o
    if len(ratios) < MIN_SAMPLES_VOOR_BEWEGING:
        o.verdict = TE_WEINIG
        return o
    beweegt = (o.ratio_spreiding or 0.0) > EPS_BEWEGING or o.n_unieke_ratio >= 3
    o.verdict = LIVE if beweegt else DIVERGENT_STIL
    return o


def per_stad_kanaal(samples: list[dict]) -> list[Oordeel]:
    """Dekkingstabel: één oordeel per (stad, kanaal)."""
    groepen: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rij in samples:
        try:
            stad = by_id(rij.get("segment_id", "")).city
        except KeyError:
            stad = "?"
        groepen[(stad, rij.get("provider", "?"))].append(rij)
    return [beoordeel(rijen, stad, prov)
            for (stad, prov), rijen in sorted(groepen.items())]


def per_segment_kanaal(samples: list[dict]) -> list[Oordeel]:
    """Detailtabel: één oordeel per (segment, kanaal)."""
    groepen: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rij in samples:
        groepen[(rij.get("segment_id", "?"), rij.get("provider", "?"))].append(rij)
    uit = []
    for (seg_id, prov), rijen in sorted(groepen.items()):
        try:
            stad = by_id(seg_id).city
        except KeyError:
            stad = "?"
        o = beoordeel(rijen, stad, prov)
        o.segmenten = [seg_id]
        uit.append(o)
    return uit


def render_dekkingstabel(oordelen: list[Oordeel]) -> str:
    """De tabel waar de poort van Fase 0 op wordt beoordeeld."""
    if not oordelen:
        return "(geen samples)"
    steden = sorted({o.stad for o in oordelen})
    kanalen = sorted({o.provider for o in oordelen})
    index = {(o.stad, o.provider): o for o in oordelen}

    breedte = max(12, max(len(s) for s in steden) + 1)
    # Ook de verdicts meenemen in de kolombreedte: 'alleen-statisch' is
    # langer dan 'tomtom' en mag niet tegen de volgende kolom aanplakken.
    kolom = max(14, max(len(k) for k in kanalen) + 2,
                max(len(o.verdict) for o in oordelen) + 2)
    kop = "stad".ljust(breedte) + "".join(k.ljust(kolom) for k in kanalen)
    regels = [kop, "-" * len(kop)]
    for stad in steden:
        rij = stad.ljust(breedte)
        for kanaal in kanalen:
            o = index.get((stad, kanaal))
            rij += (o.verdict if o else "—").ljust(kolom)
        regels.append(rij)
    return "\n".join(regels)


def render_detail(oordelen: list[Oordeel]) -> str:
    kop = (f"{'segment':<26}{'kanaal':<15}{'verdict':<22}"
           f"{'n':>4}{'ok':>4}{'ratio~':>9}{'max':>8}{'sd':>8}{'#uniek':>7}"
           f"{'#geom':>7}{'afst%':>7}")
    regels = [kop, "-" * len(kop)]
    for o in oordelen:
        seg = o.segmenten[0] if o.segmenten else "?"

        def num(waarde, formaat: str, breedte: int) -> str:
            return format(waarde, formaat).rjust(breedte) if waarde is not None \
                else "—".rjust(breedte)

        regels.append(
            f"{seg:<26}{o.provider:<15}{o.verdict:<22}{o.n_samples:>4}{o.n_ok:>4}"
            + num(o.ratio_mediaan, ".3f", 9) + num(o.ratio_max, ".3f", 8)
            + num(o.ratio_spreiding, ".4f", 8) + f"{o.n_unieke_ratio:>7}"
            + f"{o.n_unieke_polyline:>7}" + num(o.afstand_spreiding_pct, ".2f", 7)
        )
    return "\n".join(regels)


def poort(oordelen: list[Oordeel]) -> tuple[bool, str]:
    """De poort: mag Fase 1 beginnen?

    Voorwaarde: minstens één Oekraïense stad met minstens één kanaal op
    `live`. Externe controles tellen niet mee — Warschau werkt vast wel,
    en daar gaat het onderzoek niet over.
    """
    ua_steden = {"Kyiv", "Lviv", "Odesa", "Kharkiv", "Dnipro"}
    live = [o for o in oordelen if o.verdict == LIVE and o.stad in ua_steden]
    if live:
        steden = sorted({o.stad for o in live})
        kanalen = sorted({o.provider for o in live})
        return True, (f"Poort open: levend signaal in {', '.join(steden)} "
                      f"via {', '.join(kanalen)}.")

    stil = [o for o in oordelen if o.verdict == DIVERGENT_STIL and o.stad in ua_steden]
    if stil:
        return False, (
            "Poort dicht. Er is divergentie tussen waargenomen en statische "
            "reistijd, maar die beweegt niet over tijd — dat is niet te "
            "onderscheiden van een vast modelverschil. Meer rondes over een "
            "bredere spits nodig voordat hier een uitspraak op mag."
        )
    if all(o.verdict == GEEN_KEY for o in oordelen):
        return False, ("Poort niet getest: geen enkele key aanwezig. "
                       "De probe is gebouwd maar heeft niets kunnen meten.")
    return False, (
        "Poort dicht: geen enkel kanaal levert een levend, over tijd bewegend "
        "signaal voor een Oekraïense stad. Geen keten bouwen op een dood "
        "signaal, en niet terugvallen op het typisch-verkeer-model alsof dat "
        "een waarneming is."
    )
