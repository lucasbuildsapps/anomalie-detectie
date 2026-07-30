"""Watchboard: vooraf gedefinieerde indicatoren (I&W-tradecraft).

De rest van de tool werkt **inductief**: hier is de data, wat valt op?
Dat is nuttig, maar het heeft een bekende zwakte — achteraf is altijd wel
een verhaal te maken bij wat de detector oplichtte, en niemand kan
controleren of dat verhaal vooraf zou zijn bedacht.

Warning intelligence werkt daarom óók **deductief**: je bepaalt vooraf
welke waarneembare dingen ertoe zouden doen (indicatoren), en houdt bij
welke daarvan 'actief' worden. Dat draait de bewijslast om. Een actieve
indicator is navolgbaar ("we hadden opgeschreven dat dit ertoe doet"), en
een indicator die nooit afgaat is óók informatie.

Een indicator is bewust smal en toetsbaar:
- waar kijken we (dataset, regio, categorie),
- welke voorwaarde,
- hoeveel perioden achtereen,
- en wat betekent het als hij afgaat (de `betekenis`, in gewone taal).

**Afwezigheid telt mee.** `stilte` is een volwaardige voorwaarde: geen
activiteit waar die normaal wél is, is in waarschuwingswerk een klassiek
signaal — en precies wat een tool die alleen naar pieken kijkt mist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

CONDITIONS: dict[str, str] = {
    "boven_band": "Waarde boven het normbeeld",
    "onder_band": "Waarde onder het normbeeld",
    "drempel_boven": "Waarde boven een vaste drempel",
    "drempel_onder": "Waarde onder een vaste drempel",
    "stilte": "Geen activiteit (waarde 0)",
    "stijging_pct": "Niveau minstens X% hoger dan het normbeeld",
}

#: Voorwaarden die een getal nodig hebben.
NEEDS_THRESHOLD = frozenset({"drempel_boven", "drempel_onder", "stijging_pct"})


@dataclass
class Indicator:
    """Eén vooraf gedefinieerd waarnemingspunt."""

    name: str
    dataset_id: int
    condition: str
    location: str | None = None
    category: str | None = None
    threshold: float | None = None
    periods: int = 1               # hoeveel perioden achtereen
    meaning: str = ""              # wat het zou betekenen als dit afgaat
    enabled: bool = True
    id: int | None = None

    def describe(self) -> str:
        waar = self.location or "alle regio's"
        if self.category:
            waar += f" / {self.category}"
        basis = CONDITIONS.get(self.condition, self.condition)
        if self.condition in NEEDS_THRESHOLD and self.threshold is not None:
            basis += f" ({self.threshold:g})"
        duur = f", {self.periods} perioden achtereen" if self.periods > 1 else ""
        return f"{waar}: {basis}{duur}"


@dataclass
class IndicatorState:
    """Uitkomst van één toetsing."""

    indicator: Indicator
    active: bool
    since: pd.Timestamp | None = None      # sinds wanneer actief
    streak: int = 0                        # aantal perioden dat voldoet
    latest_value: float | None = None
    expected: float | None = None
    evidence: str = ""
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


def _condition_mask(hist: pd.DataFrame, indicator: Indicator) -> np.ndarray:
    """Per periode: voldoet die aan de voorwaarde?"""
    actual = hist["actual"].to_numpy(dtype=float)
    cond = indicator.condition
    thr = indicator.threshold

    if cond == "boven_band":
        return hist["status"].to_numpy() == "boven"
    if cond == "onder_band":
        return hist["status"].to_numpy() == "onder"
    if cond == "drempel_boven":
        return actual > (thr if thr is not None else np.inf)
    if cond == "drempel_onder":
        return actual < (thr if thr is not None else -np.inf)
    if cond == "stilte":
        # Alleen echte nullen; ontbrekende perioden (NaN) zijn onbekend,
        # niet stil. Dat onderscheid is hier wezenlijk: 'geen rapport' en
        # 'gerapporteerd dat er niets was' zijn verschillende dingen.
        return np.nan_to_num(actual, nan=-1.0) == 0.0
    if cond == "stijging_pct":
        # Bewust NIET vergeleken met de verwachting: die past zich aan.
        # Blijft het niveau maandenlang verhoogd, dan zakt de 'stijging'
        # t.o.v. de verwachting naar nul en gaat de indicator uit —
        # precies omdát de situatie aanhoudt. Dat is de omgekeerde wereld
        # voor een watchboard.
        #
        # Daarom afgezet tegen een stabiel referentieniveau: de mediaan
        # van de oudere historie. Een aanhoudende verhoging blijft dan
        # branden tot het niveau echt terugzakt.
        reference = _reference_level(actual)
        pct = (actual - reference) / max(reference, 0.1) * 100.0
        return pct >= (thr if thr is not None else np.inf)
    return np.zeros(len(hist), dtype=bool)


def _reference_level(actual: np.ndarray, recent: int = 30) -> float:
    """Stabiel 'normaal niveau': mediaan van de historie zónder de meest
    recente perioden, zodat een aanhoudende verschuiving het referentie-
    punt niet zelf omhoog trekt."""
    older = actual[:-recent] if len(actual) > recent * 2 else actual
    ref = float(np.nanmedian(older)) if len(older) else 0.0
    return ref if np.isfinite(ref) else 0.0


def evaluate(indicator: Indicator, normbeeld) -> IndicatorState:
    """Toets één indicator tegen een normbeeld.

    Actief betekent: de voorwaarde geldt voor de láátste `periods`
    perioden achtereen. Een indicator die ooit afging maar nu niet meer,
    is dormant — dat is het punt van een watchboard: hij beschrijft de
    huidige stand, niet de geschiedenis.
    """
    hist = getattr(normbeeld, "historical", None)
    if hist is None or hist.empty:
        return IndicatorState(indicator, False, evidence="geen data")

    mask = _condition_mask(hist, indicator)
    need = max(1, int(indicator.periods))
    if len(mask) < need:
        return IndicatorState(indicator, False,
                              evidence=f"te weinig perioden ({len(mask)})")

    active = bool(mask[-need:].all())

    # Hoe lang loopt de huidige reeks al?
    streak = 0
    for flag in reversed(mask):
        if not flag:
            break
        streak += 1

    since = None
    if streak:
        since = pd.Timestamp(hist["date"].iloc[len(mask) - streak])

    latest = float(hist["actual"].iloc[-1])
    expected = float(hist["expected"].iloc[-1])

    if active:
        evidence = (f"{streak} periode(n) achtereen; laatst {latest:.0f} "
                    f"(verwacht {expected:.1f})")
    elif streak:
        evidence = (f"{streak} van {need} benodigde perioden — nog niet "
                    f"actief")
    else:
        evidence = f"niet van toepassing; laatst {latest:.0f}"

    return IndicatorState(indicator, active, since=since, streak=streak,
                          latest_value=latest, expected=expected,
                          evidence=evidence)


def evaluate_all(indicators: list[Indicator], normbeelds: dict,
                 ) -> list[IndicatorState]:
    """Toets een lijst indicatoren tegen de normbeelden per regio.

    Indicatoren zonder regio worden getoetst tegen elke regio; de
    indicator geldt als actief zodra één regio voldoet (met die regio in
    het bewijs). Zo hoeft een analist niet per regio een kopie te maken.
    """
    out: list[IndicatorState] = []
    for ind in indicators:
        if not ind.enabled:
            continue
        if ind.location:
            nb = normbeelds.get(ind.location)
            out.append(evaluate(ind, nb) if nb is not None
                       else IndicatorState(ind, False,
                                           evidence="regio niet in data"))
            continue

        states = []
        for loc, nb in normbeelds.items():
            state = evaluate(ind, nb)
            state.evidence = f"{loc}: {state.evidence}"
            states.append(state)
        if not states:
            out.append(IndicatorState(ind, False, evidence="geen regio's"))
            continue
        # Actief wint van dormant; daarbinnen de langste reeks — dat is de
        # regio waar dit het duidelijkst speelt.
        out.append(max(states, key=lambda s: (s.active, s.streak)))

    # Actieve indicatoren eerst, langste reeks bovenaan: dat is de
    # volgorde waarin een analist ze wil zien.
    out.sort(key=lambda s: (not s.active, -s.streak))
    return out


def summarise(states: list[IndicatorState]) -> str:
    """Eén regel over de stand van het watchboard."""
    if not states:
        return ("Geen indicatoren gedefinieerd. Een watchboard dwingt je "
                "vooraf op te schrijven wát ertoe doet — dat maakt een "
                "waarschuwing achteraf navolgbaar.")
    active = [s for s in states if s.active]
    if not active:
        return (f"Geen van de {len(states)} indicatoren staat aan. Dat is "
                f"zelf ook informatie: de dingen die je vooraf belangrijk "
                f"vond, gebeuren nu niet.")
    namen = ", ".join(s.indicator.name for s in active[:4])
    meer = f" (+{len(active) - 4})" if len(active) > 4 else ""
    return f"{len(active)} van {len(states)} indicatoren actief: {namen}{meer}."
