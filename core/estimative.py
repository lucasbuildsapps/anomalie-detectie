"""Gestandaardiseerde onzekerheidstaal (ICD 203 / NATO).

Waarom dit bestaat: een analist moet zijn oordeel uiteindelijk opschrijven
in een product dat aan analytische standaarden voldoet. Als de tool
"afwijking" zegt en de analist "waarschijnlijk", zit daar een
vertaalslag tussen die niemand controleert. Door de tool in dezelfde taal
te laten spreken, is de stap van bevinding naar rapport navolgbaar.

Twee losse schalen, die nadrukkelijk **verschillende dingen** zeggen:

**Words of Estimative Probability (WEP)** — hoe waarschijnlijk is de
gebeurtenis? Bereiken volgens de NATO/FIRST-conventie:

| term | bereik |
|---|---|
| zeer onwaarschijnlijk | < 10% |
| onwaarschijnlijk | 10–40% |
| ongeveer even waarschijnlijk | 40–60% |
| waarschijnlijk | 60–90% |
| zeer waarschijnlijk | > 90% |

**Levels of Confidence in Assessment (LCA)** — hoe stevig staat het
oordeel, gegeven de kwaliteit van de onderliggende informatie?

- **hoog**: goede informatiekwaliteit, bevestiging uit meerdere bronnen
  of methoden, eenduidig te beoordelen
- **gemiddeld**: informatie is geloofwaardig maar mist bevestiging, of
  laat meerdere interpretaties toe
- **laag**: fragmentarische informatie, of uit een bron van twijfelachtige
  betrouwbaarheid

**De belangrijkste regel**: zet een waarschijnlijkheids- en een
vertrouwensterm nóóit in dezelfde zin. "Waarschijnlijk een aanval, met
hoge zekerheid" laat de lezer raden wát er onzeker is — de gebeurtenis of
het oordeel erover. `format_judgment()` dwingt die scheiding af en de
tests bewaken hem.

Belangrijke beperking: de WEP-term hier beschrijft een **frequentie onder
het normbeeld** ("hoe vaak komt een waarde als deze voor in vergelijkbare
perioden"), niet de kans dát er iets aan de hand is. Dat laatste vereist
een prior die deze tool niet heeft en niet moet verzinnen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# (bovengrens, term) — oplopend; het laatste bereik is de restcategorie.
WEP_SCALE: tuple[tuple[float, str], ...] = (
    (0.10, "zeer onwaarschijnlijk"),
    (0.40, "onwaarschijnlijk"),
    (0.60, "ongeveer even waarschijnlijk"),
    (0.90, "waarschijnlijk"),
    (1.01, "zeer waarschijnlijk"),
)

WEP_RANGES: dict[str, str] = {
    "zeer onwaarschijnlijk": "< 10%",
    "onwaarschijnlijk": "10–40%",
    "ongeveer even waarschijnlijk": "40–60%",
    "waarschijnlijk": "60–90%",
    "zeer waarschijnlijk": "> 90%",
}

LCA_LEVELS = ("laag", "gemiddeld", "hoog")

LCA_DEFINITIONS: dict[str, str] = {
    "hoog": ("goede informatiekwaliteit, bevestigd door meerdere methoden, "
             "eenduidig te beoordelen"),
    "gemiddeld": ("geloofwaardige informatie die bevestiging mist of "
                  "meerdere interpretaties toelaat"),
    "laag": ("fragmentarische informatie, of een bron van twijfelachtige "
             "betrouwbaarheid"),
}

#: Termen die niet samen in één zin mogen staan (ICD 203).
_WEP_TERMS = frozenset(WEP_RANGES)
_LCA_MARKERS = frozenset({
    "hoge zekerheid", "gemiddelde zekerheid", "lage zekerheid",
    "hoog vertrouwen", "gemiddeld vertrouwen", "laag vertrouwen",
})


def wep_term(probability: float) -> str:
    """Vertaal een kans (0–1) naar de standaard-term."""
    p = min(max(float(probability), 0.0), 1.0)
    for upper, term in WEP_SCALE:
        if p < upper:
            return term
    return WEP_SCALE[-1][1]


def wep_phrase(probability: float) -> str:
    """Term plus het bijbehorende bereik, zoals in een product hoort."""
    term = wep_term(probability)
    return f"{term} ({WEP_RANGES[term]})"


@dataclass
class Assessment:
    """Eén oordeel, met de twee schalen strikt gescheiden."""

    statement: str                 # wat er beoordeeld wordt
    probability: float | None      # kans onder het normbeeld
    confidence: str                # 'laag' / 'gemiddeld' / 'hoog'
    confidence_reasons: list[str] = field(default_factory=list)

    @property
    def likelihood_phrase(self) -> str | None:
        if self.probability is None:
            return None
        return wep_phrase(self.probability)


def assess_confidence(
    *,
    coverage: float | None = None,
    target_coverage: float | None = None,
    n_periods: int | None = None,
    data_coverage: float | None = None,
    staleness_days: int | None = None,
    source_reliability: str | None = None,
    corroborating_methods: int | None = None,
    effective_methods: float | None = None,
    regime_stable: bool | None = None,
) -> tuple[str, list[str]]:
    """Bepaal het vertrouwensniveau volgens de LCA-criteria.

    De drie criteria uit de standaard vertaald naar wat deze tool meet:

    - *informatiekwaliteit*: dekking van de reeks, versheid van de data en
      de bron-betrouwbaarheid (Admiraliteitsschaal);
    - *bevestiging*: hoeveel detectiemethoden het eens zijn, gecorrigeerd
      voor hun onderlinge afhankelijkheid;
    - *eenduidigheid*: klopt de band met zijn eigen doel, en is het regime
      stabiel genoeg om iets te kunnen zeggen.

    Returnt (niveau, redenen). De redenen zijn bedoeld om letterlijk in
    een product te kunnen citeren.
    """
    score = 0
    reasons: list[str] = []

    # --- Informatiekwaliteit ---
    if n_periods is not None:
        if n_periods >= 60:
            score += 1
        elif n_periods < 30:
            score -= 1
            reasons.append(f"korte reeks ({n_periods} perioden)")

    if data_coverage is not None and data_coverage < 0.7:
        score -= 1
        reasons.append(f"lage datadekking ({data_coverage * 100:.0f}%)")

    if staleness_days is not None and staleness_days > 30:
        score -= 1
        reasons.append(f"data {staleness_days} dagen oud")

    if source_reliability:
        letter = str(source_reliability).strip().upper()[:1]
        if letter in {"A", "B"}:
            score += 1
        elif letter in {"D", "E", "F"}:
            score -= 1
            reasons.append(f"bron-betrouwbaarheid {letter}")

    # --- Bevestiging ---
    if effective_methods is not None:
        if effective_methods >= 3:
            score += 1
        elif effective_methods < 2:
            score -= 1
            reasons.append(
                f"weinig zelfstandige bevestiging "
                f"({effective_methods:.1f} methoden)")
    elif corroborating_methods is not None and corroborating_methods >= 3:
        score += 1

    # --- Eenduidigheid ---
    if coverage is not None and target_coverage is not None:
        gap = abs(coverage - target_coverage)
        if gap > 0.10:
            score -= 1
            reasons.append(
                f"band dekt {coverage * 100:.0f}% waar "
                f"{target_coverage * 100:.0f}% bedoeld is")
        elif gap <= 0.03:
            score += 1

    level = "hoog" if score >= 2 else ("gemiddeld" if score >= 0 else "laag")

    # Een verse regimewissel is geen aftrekpost maar een plafond: alle
    # andere kwaliteitssignalen gaan over het óude regime. Hoe lang en
    # hoe schoon de reeks ook is, het normbeeld beschrijft niet wat er nú
    # gebeurt — dan past geen hoog vertrouwen.
    if regime_stable is False:
        reasons.append("recente regimewissel; normbeeld nog niet ingespeeld")
        if level == "hoog":
            level = "gemiddeld"
    if not reasons:
        reasons.append("geen bijzonderheden in kwaliteit, bevestiging of "
                       "kalibratie")
    return level, reasons


def format_judgment(assessment: Assessment) -> str:
    """Schrijf het oordeel uit met de schalen in **aparte zinnen**.

    Dit is geen opmaak-detail: door waarschijnlijkheid en zekerheid te
    scheiden blijft duidelijk wát er onzeker is — de gebeurtenis of het
    oordeel erover.
    """
    lines = []
    if assessment.probability is not None:
        lines.append(
            f"{assessment.statement} is {assessment.likelihood_phrase} "
            f"onder het huidige normbeeld."
        )
    else:
        lines.append(f"{assessment.statement}.")

    reasons = "; ".join(assessment.confidence_reasons)
    lines.append(
        f"Het vertrouwen in deze beoordeling is {assessment.confidence} "
        f"({LCA_DEFINITIONS[assessment.confidence]}). Grond: {reasons}."
    )
    return "\n".join(lines)


def violates_separation(sentence: str) -> bool:
    """True als een zin een waarschijnlijkheids- én een zekerheidsterm
    bevat — precies wat ICD 203 verbiedt."""
    low = sentence.lower()
    has_wep = any(term in low for term in _WEP_TERMS)
    has_lca = any(marker in low for marker in _LCA_MARKERS)
    return has_wep and has_lca


def exceedance_probability(percentile: float, direction: str) -> float:
    """Kans op een waarde minstens zo extreem, onder het normbeeld.

    `percentile` is de empirische rang van het residu (0–1). Voor een
    punt boven de band telt de rechterstaart, voor een punt eronder de
    linker. Bewust een frequentie-uitspraak over vergelijkbare perioden,
    geen uitspraak over de kans dát er iets aan de hand is.
    """
    p = min(max(float(percentile), 0.0), 1.0)
    return (1.0 - p) if direction == "boven" else p
