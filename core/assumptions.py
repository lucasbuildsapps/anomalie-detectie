"""Aannameregister: welke keuzes zitten er onder dit normbeeld?

ICD 203 vraagt om onderscheid tussen de onderliggende gegevens en de
aannames en oordelen van de analist. Deze tool maakt een reeks keuzes die
de uitkomst wezenlijk sturen — gap-beleid, tijdschaal, methodekeuze,
bandmodel — en die stonden nergens bij elkaar. Ze waren daarmee feitelijk
onzichtbare aannames: de analist erft ze, zonder ze te zien.

Het gevaarlijkste voorbeeld is het gap-beleid. Standaard geldt "geen
rapport = geen activiteit". Valt de collectie uit, dan leest de tool dat
als echte stilte, en meldt hij keurig "onder de band". De uitkomst is dan
niet fout gerekend maar fout aangenomen — en dat is aan het getal niet te
zien.

Elke aanname krijgt daarom drie dingen mee:
- **wat** er is aangenomen,
- **waarop** dat berust (standaardwaarde, meting, of keuze van de
  gebruiker) — dat onderscheid is precies wat de standaard vraagt,
- **wat het betekent als de aanname niet klopt.**
"""
from __future__ import annotations

from dataclasses import dataclass

#: Waar een aanname vandaan komt. 'gemeten' is sterker dan 'standaard':
#: bij het eerste heeft de tool het uitgezocht, bij het tweede niet.
BASIS_DEFAULT = "standaardwaarde"
BASIS_MEASURED = "gemeten"
BASIS_USER = "keuze van de gebruiker"


@dataclass
class Assumption:
    """Eén expliciete aanname onder een analyse."""

    topic: str            # waar het over gaat
    statement: str        # wat er is aangenomen
    basis: str            # standaardwaarde / gemeten / keuze van de gebruiker
    if_wrong: str         # gevolg als de aanname niet klopt
    critical: bool = False

    def as_line(self) -> str:
        mark = "⚠ " if self.critical else ""
        return (f"{mark}**{self.topic}** — {self.statement} "
                f"({self.basis}). Als dit niet klopt: {self.if_wrong}")


def collect(normbeeld, *, gap_policy: str = "zero",
            aggregation_choice: str = "auto",
            methods_override=None,
            sensitivity: str | None = None,
            source_reliability: str | None = None) -> list[Assumption]:
    """Verzamel de aannames onder één normbeeld.

    `normbeeld` mag None zijn; dan blijven de dataset-brede aannames over.
    """
    out: list[Assumption] = []

    # --- Gap-beleid: de meest consequentierijke aanname ---
    gap_texts = {
        "zero": (
            "een periode zonder waarnemingen betekent dat er niets gebeurde",
            "een uitval in de collectie leest als echte stilte; de tool "
            "meldt dan 'onder de band' terwijl er niets bekend is",
            True,
        ),
        "interpolate": (
            "een periode zonder waarnemingen is collectie-uitval; de "
            "activiteit liep door",
            "echte stilte wordt opgevuld met geschatte activiteit, waardoor "
            "een daling onzichtbaar blijft",
            True,
        ),
        "mask": (
            "een periode zonder waarnemingen is onbekend en telt niet mee",
            "bewust geen aanname; wel minder data voor de band",
            False,
        ),
    }
    statement, consequence, critical = gap_texts.get(
        gap_policy, gap_texts["zero"])
    out.append(Assumption(
        topic="Gap-beleid", statement=statement,
        basis=BASIS_DEFAULT if gap_policy == "zero" else BASIS_USER,
        if_wrong=consequence, critical=critical,
    ))

    # --- Tijdschaal ---
    if normbeeld is not None:
        chosen = normbeeld.aggregation
        out.append(Assumption(
            topic="Tijdschaal",
            statement=f"activiteit wordt per {chosen} beoordeeld",
            basis=BASIS_DEFAULT if aggregation_choice == "auto" else BASIS_USER,
            if_wrong=("op een andere tijdschaal kunnen andere perioden "
                      "opvallen; een piek binnen één dag verdwijnt in een "
                      "weektotaal"),
        ))

        # --- Methodekeuze ---
        methods = ", ".join(normbeeld.methods_used)
        out.append(Assumption(
            topic="Voorspelmethode",
            statement=f"het normbeeld komt van: {methods}",
            basis=(BASIS_MEASURED if normbeeld.backtest_scores
                   else BASIS_DEFAULT) if methods_override is None
            else BASIS_USER,
            if_wrong=("een andere methode geeft een andere verwachting en "
                      "dus andere afwijkingen"),
        ))

        # --- Bandmodel ---
        model_texts = {
            "poisson": "spreiding volgt een Poisson-verdeling (tellingen)",
            "negbin": "spreiding volgt een negatief-binomiale verdeling "
                      "(tellingen met clustering)",
            "quantile": "spreiding komt uit de quantiles van de residuen",
        }
        out.append(Assumption(
            topic="Bandmodel",
            statement=model_texts.get(normbeeld.band_model,
                                      normbeeld.band_model),
            basis=BASIS_MEASURED,
            if_wrong=("de bandbreedte klopt dan niet; de gemeten dekking "
                      "hiernaast laat zien of dat speelt"),
        ))

        # --- Kalibratie als expliciete controle ---
        if normbeeld.band_coverage is not None and normbeeld.band_alpha:
            target = 1.0 - 2.0 * normbeeld.band_alpha
            gap = abs(normbeeld.band_coverage - target)
            out.append(Assumption(
                topic="Kalibratie",
                statement=(f"de band dekt feitelijk "
                           f"{normbeeld.band_coverage * 100:.0f}% van de "
                           f"historie (doel {target * 100:.0f}%)"),
                basis=BASIS_MEASURED,
                if_wrong=("bij een groot verschil is het aantal gemelde "
                          "afwijkingen structureel te hoog of te laag"),
                critical=gap > 0.10,
            ))

    # --- Gevoeligheid van het detectie-ensemble ---
    if sensitivity:
        out.append(Assumption(
            topic="Gevoeligheid",
            statement=(f"het ensemble is afgesteld op '{sensitivity}' om een "
                       f"werkbare lijst op te leveren"),
            basis=BASIS_MEASURED,
            if_wrong=("de lijst is quota-gestuurd: er verschijnen altijd "
                      "bevindingen, ook als de dataset statistisch "
                      "onopvallend is"),
            critical=True,
        ))

    # --- Bronbetrouwbaarheid ---
    if source_reliability:
        letter = str(source_reliability).strip().upper()[:1]
        out.append(Assumption(
            topic="Bron",
            statement=f"betrouwbaarheid {letter} (Admiraliteitsschaal)",
            basis=BASIS_USER,
            if_wrong=("bij een lagere betrouwbaarheid zegt een afwijking "
                      "meer over de rapportage dan over de werkelijkheid"),
            critical=letter in {"D", "E", "F"},
        ))
    else:
        out.append(Assumption(
            topic="Bron",
            statement="geen betrouwbaarheid vastgelegd",
            basis=BASIS_DEFAULT,
            if_wrong=("zonder bronoordeel is niet te zeggen of een afwijking "
                      "over de werkelijkheid of over de rapportage gaat"),
        ))

    return out


def critical_only(assumptions: list[Assumption]) -> list[Assumption]:
    return [a for a in assumptions if a.critical]


def summarise(assumptions: list[Assumption]) -> str:
    """Eén regel: waar moet de lezer vooral op letten."""
    if not assumptions:
        return "Geen aannames vastgelegd."
    crit = critical_only(assumptions)
    if not crit:
        return (f"{len(assumptions)} aannames onder deze analyse; geen "
                f"daarvan is bijzonder risicovol.")
    onderwerpen = ", ".join(a.topic for a in crit)
    return (f"{len(assumptions)} aannames, waarvan {len(crit)} met een "
            f"wezenlijk effect op de uitkomst: {onderwerpen}.")
