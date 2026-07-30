"""Vingerafdruk van de analyse-code, voor cache-sleutels.

Waarom dit bestaat: Streamlit's `@st.cache_data` verwerkt wél een
wijziging in de gecachte functie zelf, maar niet in de functies die
dié aanroept. De cache-wrappers in ui/cache.py zijn dun — het echte werk
zit in core/normbeeld.py. Verandert daar de bandberekening, dan blijft
Streamlit het oude resultaat serveren zolang de data gelijk blijft.

Dat is geen theoretisch risico: na een correctie aan de tolerantieband
bleef de app maandenlang de oude, veel te brede band tonen, terwijl de
code al klopte. Een analist ziet dan een grafiek die niet meer bij de
methode hoort, zonder enige aanwijzing dat er iets mis is.

Door de broncode van de analyse-modules te hashen en die hash in de
cache-sleutel te stoppen, vervalt de cache automatisch zodra de methode
verandert. Niemand hoeft een versienummer bij te werken — dat wordt toch
vergeten.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

#: Modules waarvan een wijziging de uitkomst van een analyse kan veranderen.
_ANALYSIS_MODULES = (
    "normbeeld.py",
    "comparison.py",
    "auto_pilot.py",
    "signals.py",
    "profiler.py",
    "indicators.py",
    "estimative.py",
)


@lru_cache(maxsize=1)
def analysis_fingerprint() -> str:
    """Korte hash over de broncode van de analyse-kern.

    Wordt één keer per proces berekend. Bij een wijziging in een van de
    modules verandert de hash, waardoor elke cache-sleutel die hem bevat
    vanzelf ongeldig wordt.
    """
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in sorted(_ANALYSIS_MODULES):
        path = here / name
        try:
            digest.update(path.read_bytes())
        except OSError:
            # Ontbrekende module: neem de naam mee, zodat het verschil
            # tussen 'bestaat niet' en 'leeg' zichtbaar blijft.
            digest.update(name.encode())
    # Ook de detectoren tellen mee: die bepalen de bevindingen.
    detectors = here.parent / "detectors"
    for path in sorted(detectors.glob("*.py")):
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]
