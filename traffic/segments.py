"""Meetsegmenten: herkomst/bestemming-paren per stad.

Selectiecriterium: 5–20 km, en het traject moet over een **enkel
faalpunt** lopen — een brug, een ringweg-tak, een grote kruising. Juist
daar verraadt een omleiding zich in de routegeometrie, ook 's nachts als
de reistijd op lege wegen nauwelijks verandert.

Warschau is de externe controle: die absorbeert continent-brede schokken
(weer, feestdagen, wijzigingen aan de providerkant). Chisinau en Krakau
staan erbij als optionele extra controles (`optional=True`, standaard
uit) — ze kosten geld en zijn voor Fase 0 niet nodig.

Coördinaten zijn benaderingen van de bedoelde knooppunten; de provider
snapt ze zelf naar het dichtstbijzijnde wegvak. Wat telt is dat het paar
consistent hetzelfde traject beschrijft, niet dat de punten exact op de
stoeprand liggen.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    """Eén meetsegment.

    `crossing` legt vast *waarom* dit segment is gekozen: welk faalpunt
    het bevat. Dat is later nodig om een geometriewijziging te kunnen
    interpreteren (brug weg? dan zien we een omweg, niet vertraging).
    """

    id: str
    label: str
    city: str
    oblast: str
    country: str
    origin: tuple[float, float]
    destination: tuple[float, float]
    crossing: str
    road_class: str
    is_external_control: bool = False
    optional: bool = False


SEGMENTS: tuple[Segment, ...] = (
    # ---------------- Kyiv: de Dnipro-oevers hangen aan een handvol bruggen
    Segment("kyiv_metro_bridge", "Livoberezjna → Kontraktova plosjtsja",
            "Kyiv", "Kyiv City", "UA", (50.4522, 30.5990), (50.4645, 30.5195),
            crossing="Dnipro-brug (Metro/Havana-corridor)", road_class="urban_arterial"),
    Segment("kyiv_paton_bridge", "Vydubytsji → Osokorky",
            "Kyiv", "Kyiv City", "UA", (50.4028, 30.5686), (50.3948, 30.6096),
            crossing="Dnipro-brug (Paton/Zuidbrug-corridor)", road_class="urban_arterial"),
    Segment("kyiv_m03_east", "Charkivska → Velyka Oleksandrivka (M03)",
            "Kyiv", "Kyiv Oblast", "UA", (50.4008, 30.6520), (50.3628, 30.7757),
            crossing="M03-uitvalsweg oost + ringweg-aansluiting", road_class="trunk"),
    Segment("kyiv_zhytomyr_bypass", "Zjuljany → Bojarka (Zjytomyr-corridor)",
            "Kyiv", "Kyiv Oblast", "UA", (50.4300, 30.4400), (50.3860, 30.3600),
            crossing="westelijke ringweg-tak + spoorviaduct", road_class="trunk"),

    # ---------------- Lviv: geen grote rivier, dus knooppunten en de ring
    Segment("lviv_airport", "Centrum → luchthaven Lviv",
            "Lviv", "Lviv Oblast", "UA", (49.8419, 24.0315), (49.8125, 23.9561),
            crossing="zuidwestelijke invalsweg + ringaansluiting", road_class="urban_arterial"),
    Segment("lviv_h02_east", "Rynok → Vynnyky (H02 oost)",
            "Lviv", "Lviv Oblast", "UA", (49.8425, 24.0322), (49.8290, 24.1290),
            crossing="H02-uitvalsweg oost", road_class="trunk"),
    Segment("lviv_north_south", "Zamarstynivska → Sychiv",
            "Lviv", "Lviv Oblast", "UA", (49.8620, 24.0230), (49.7860, 24.0290),
            crossing="noord-zuid-as door het centrum, spoorkruisingen",
            road_class="urban_arterial"),

    # ---------------- Odesa: Peresyp is een smalle strook met viaducten
    Segment("odesa_peresyp", "Prymorska → Peresyp/haven noord",
            "Odesa", "Odesa Oblast", "UA", (46.4870, 30.7400), (46.5450, 30.7550),
            crossing="Peresyp-flessenhals + spoorviaducten", road_class="urban_arterial"),
    Segment("odesa_airport", "Centrum → luchthaven Odesa",
            "Odesa", "Odesa Oblast", "UA", (46.4825, 30.7233), (46.4268, 30.6764),
            crossing="zuidwestelijke invalsweg", road_class="urban_arterial"),
    Segment("odesa_kotovskoho", "Centrum → Kotovskoho (noord)",
            "Odesa", "Odesa Oblast", "UA", (46.4825, 30.7233), (46.5730, 30.7620),
            crossing="Mykolajiv-corridor over Peresyp", road_class="trunk"),

    # ---------------- Charkiv
    Segment("kharkiv_airport", "Plosjtsja Konstytutsii → luchthaven Charkiv",
            "Kharkiv", "Kharkiv Oblast", "UA", (49.9915, 36.2310), (49.9210, 36.2930),
            crossing="zuidelijke invalsweg + spoorkruising", road_class="urban_arterial"),
    Segment("kharkiv_kholodna_hora", "Centrum → Cholodna Hora (west)",
            "Kharkiv", "Kharkiv Oblast", "UA", (49.9915, 36.2310), (49.9880, 36.1750),
            crossing="westelijke as over het spooremplacement", road_class="urban_arterial"),
    Segment("kharkiv_m03_east", "Centrum → Rohan (M03 oost)",
            "Kharkiv", "Kharkiv Oblast", "UA", (49.9915, 36.2310), (49.9420, 36.4030),
            crossing="M03-uitvalsweg oost + ringaansluiting", road_class="trunk"),

    # ---------------- Dnipro: opnieuw bruggen over de Dnipro
    Segment("dnipro_left_bank", "Centrum → Livoberezjnyj (linkeroever)",
            "Dnipro", "Dnipropetrovsk Oblast", "UA", (48.4647, 35.0462), (48.4880, 35.1080),
            crossing="Dnipro-brug (Amurskyj/Nieuwe brug)", road_class="urban_arterial"),
    Segment("dnipro_airport", "Centrum → luchthaven Dnipro",
            "Dnipro", "Dnipropetrovsk Oblast", "UA", (48.4647, 35.0462), (48.3572, 35.1006),
            crossing="zuidoostelijke invalsweg", road_class="trunk"),
    Segment("dnipro_prydniprovsk", "Centrum → Prydniprovsk",
            "Dnipro", "Dnipropetrovsk Oblast", "UA", (48.4647, 35.0462), (48.3980, 35.1330),
            crossing="zuidoever-corridor langs de rivier", road_class="urban_arterial"),

    # ---------------- Externe controle: Warschau
    Segment("warsaw_vistula", "Centrum → Praga (over de Wisła)",
            "Warsaw", "Mazowieckie", "PL", (52.2297, 21.0122), (52.2530, 21.0530),
            crossing="Wisła-brug", road_class="urban_arterial", is_external_control=True),
    Segment("warsaw_airport", "Centrum → Chopin-luchthaven",
            "Warsaw", "Mazowieckie", "PL", (52.2297, 21.0122), (52.1672, 20.9679),
            crossing="zuidelijke invalsweg", road_class="urban_arterial",
            is_external_control=True),
    Segment("warsaw_mokotow_wola", "Mokotów → Wola",
            "Warsaw", "Mazowieckie", "PL", (52.1900, 21.0400), (52.2350, 20.9700),
            crossing="binnenring west", road_class="urban_arterial", is_external_control=True),

    # ---------------- Optionele extra controles (standaard uit)
    Segment("chisinau_center_airport", "Centrum → luchthaven Chisinau",
            "Chisinau", "Chisinau", "MD", (47.0245, 28.8323), (46.9316, 28.9308),
            crossing="zuidoostelijke invalsweg", road_class="urban_arterial",
            is_external_control=True, optional=True),
    Segment("krakow_center_airport", "Centrum → luchthaven Kraków",
            "Krakow", "Malopolskie", "PL", (50.0614, 19.9366), (50.0777, 19.7848),
            crossing="westelijke invalsweg over de Rudawa", road_class="urban_arterial",
            is_external_control=True, optional=True),
)


def actieve_segmenten(include_optional: bool = False) -> list[Segment]:
    return [s for s in SEGMENTS if include_optional or not s.optional]


def per_stad(include_optional: bool = False) -> dict[str, list[Segment]]:
    uit: dict[str, list[Segment]] = {}
    for s in actieve_segmenten(include_optional):
        uit.setdefault(s.city, []).append(s)
    return uit


def by_id(segment_id: str) -> Segment:
    for s in SEGMENTS:
        if s.id == segment_id:
            return s
    raise KeyError(f"onbekend segment: {segment_id}")
