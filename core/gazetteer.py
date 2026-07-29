"""Gazetteer: regionaam → coördinaten, zonder externe dienst.

Waarom dit bestaat: veel bronnen (ook de demo-dataset) leveren wél een
regionaam maar géén lat/lon. Zonder coördinaten blijft de kaart leeg,
terwijl de tool de locaties prima kent. Deze tabel vult dat gat voor de
gebieden waar de tool voor bedoeld is.

Bewust een statische tabel en geen geocoding-API: geen netwerk-afhankelijkheid
tijdens analyse, geen data die naar buiten lekt (voor gevoelig werk een
harde eis), en reproduceerbare resultaten.

Uitbreiden = een regel toevoegen. Namen worden genormaliseerd (kleine
letters, zonder 'oblast'/'region'-suffix), dus 'Kharkiv', 'kharkiv oblast'
en 'Kharkiv Oblast' vinden alle drie hetzelfde punt.
"""
from __future__ import annotations

import re

# regio (genormaliseerd) -> (lat, lon)
_COORDS: dict[str, tuple[float, float]] = {
    # --- Oekraïne: oblasten + veelgebruikte aanduidingen ---
    "kyiv": (50.45, 30.52), "kiev": (50.45, 30.52),
    "kharkiv": (49.99, 36.23), "kharkov": (49.99, 36.23),
    "odesa": (46.48, 30.73), "odessa": (46.48, 30.73),
    "dnipropetrovsk": (48.46, 35.04), "dnipro": (48.46, 35.04),
    "donetsk": (48.02, 37.80), "luhansk": (48.57, 39.31),
    "zaporizhzhia": (47.84, 35.14), "zaporizhia": (47.84, 35.14),
    "kherson": (46.64, 32.61), "mykolaiv": (46.98, 31.99),
    "lviv": (49.84, 24.03), "vinnytsia": (49.23, 28.47),
    "poltava": (49.59, 34.55), "sumy": (50.91, 34.80),
    "chernihiv": (51.49, 31.29), "zhytomyr": (50.25, 28.66),
    "cherkasy": (49.44, 32.06), "kirovohrad": (48.51, 32.26),
    "khmelnytskyi": (49.42, 26.99), "rivne": (50.62, 26.25),
    "volyn": (50.75, 25.32), "ternopil": (49.55, 25.59),
    "ivano-frankivsk": (48.92, 24.71), "chernivtsi": (48.29, 25.94),
    "zakarpattia": (48.62, 22.29), "transcarpathia": (48.62, 22.29),
    "crimea": (45.34, 34.49), "sevastopol": (44.62, 33.53),
    "ukraine": (48.38, 31.17),

    # --- Oekraïense steden die als doel worden gerapporteerd ---
    "kryvyi rih": (47.91, 33.39), "kramatorsk": (48.72, 37.56),
    "starokostiantyniv": (49.75, 27.20), "kolomyia": (48.53, 25.04),
    "ochakiv": (46.61, 31.55), "snake island": (45.26, 30.20),
    "zmiinyi": (45.26, 30.20), "mariupol": (47.10, 37.54),
    "bakhmut": (48.60, 38.00), "sloviansk": (48.85, 37.61),
    "nikopol": (47.57, 34.39), "izmail": (45.35, 28.84),

    # --- Grove windstreken (de demo-dataset gebruikt deze) ---
    "north": (51.00, 31.00), "south": (46.50, 33.00),
    "east": (48.50, 38.00), "west": (49.50, 24.50),
    "center": (49.30, 31.50), "centre": (49.30, 31.50),
    "north-east": (50.20, 36.00), "south-east": (47.50, 37.00),
    "north-west": (50.80, 26.50), "south-west": (47.50, 27.50),
    "front line": (48.00, 37.00), "frontline": (48.00, 37.00),
    "noord": (51.00, 31.00), "zuid": (46.50, 33.00),
    "oost": (48.50, 38.00), "west-oekraine": (49.50, 24.50),

    # --- Rusland/Belarus: grensregio's die in conflictdata voorkomen ---
    "moscow": (55.75, 37.62), "belgorod": (50.60, 36.59),
    "kursk": (51.73, 36.19), "bryansk": (53.24, 34.36),
    "rostov": (47.24, 39.71), "voronezh": (51.67, 39.19),
    "krasnodar": (45.04, 38.98), "belarus": (53.71, 27.95),
    "russia": (55.75, 37.62),

    # --- Andere aandachtsgebieden (voor GDELT/ACLED-datasets) ---
    "israel": (31.05, 34.85), "gaza": (31.42, 34.38),
    "west bank": (31.95, 35.30), "lebanon": (33.85, 35.86),
    "syria": (34.80, 38.997), "iraq": (33.22, 43.68),
    "iran": (32.43, 53.69), "yemen": (15.55, 48.52),
    "mali": (17.57, -4.00), "burkina faso": (12.24, -1.56),
    "niger": (17.61, 8.08), "nigeria": (9.08, 8.68),
    "sudan": (12.86, 30.22), "somalia": (5.15, 46.20),
    "ethiopia": (9.15, 40.49), "libya": (26.34, 17.23),
    "taiwan": (23.70, 120.96), "south china sea": (13.00, 114.00),
    "north korea": (40.34, 127.51), "afghanistan": (33.94, 67.71),
    "pakistan": (30.38, 69.35), "myanmar": (21.91, 95.96),
    "sahel": (15.00, 0.00), "midden-oosten": (31.00, 39.00),
    "oekraine": (48.38, 31.17), "rusland": (55.75, 37.62),
    "taiwan-straat": (24.50, 119.50),
}

# Suffixen die niets toevoegen aan de identificatie van de plek.
_STRIP = re.compile(
    r"\b(oblast|oblasti|region|regio|province|governorate|raion|city|"
    r"district|state|prefecture)\b", re.IGNORECASE)


def normalize(name: str) -> str:
    """'Kharkiv Oblast ' -> 'kharkiv'."""
    s = _STRIP.sub(" ", str(name or ""))
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[^\w\s'-]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip().lower()


def lookup(name: str) -> tuple[float, float] | None:
    """Coördinaten van een regionaam, of None als onbekend."""
    key = normalize(name)
    if not key:
        return None
    if key in _COORDS:
        return _COORDS[key]
    # Meerdelige namen: probeer het eerste woord ('Kharkiv, Ukraine').
    first = key.split(",")[0].strip()
    if first in _COORDS:
        return _COORDS[first]
    head = first.split(" ")[0]
    return _COORDS.get(head)


def annotate(df, location_col: str = "location_name"):
    """Vul ontbrekende lat/lon aan op basis van de regionaam.

    Bestaande coördinaten blijven staan — een echte meting wint altijd van
    een tabel-benadering. Rijen met een onbekende regio blijven leeg en
    verdwijnen simpelweg van de kaart (geen verzonnen locatie).
    """
    import pandas as pd

    if df is None or df.empty or location_col not in df.columns:
        return df
    out = df.copy()
    if "lat" not in out.columns:
        out["lat"] = pd.NA
    if "lon" not in out.columns:
        out["lon"] = pd.NA

    missing = out["lat"].isna() | out["lon"].isna()
    if not missing.any():
        return out

    unique_names = out.loc[missing, location_col].dropna().unique()
    coords = {n: lookup(n) for n in unique_names}
    resolved = {n: c for n, c in coords.items() if c is not None}
    if not resolved:
        return out

    lat_map = {n: c[0] for n, c in resolved.items()}
    lon_map = {n: c[1] for n, c in resolved.items()}
    fill_lat = out.loc[missing, location_col].map(lat_map)
    fill_lon = out.loc[missing, location_col].map(lon_map)
    out.loc[missing, "lat"] = out.loc[missing, "lat"].fillna(fill_lat)
    out.loc[missing, "lon"] = out.loc[missing, "lon"].fillna(fill_lon)
    out["lat"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon"] = pd.to_numeric(out["lon"], errors="coerce")
    return out


def coverage(names) -> tuple[int, int]:
    """(herkend, totaal) — voor een eerlijke melding in de UI."""
    uniq = {str(n) for n in names if n is not None and str(n).strip()}
    hits = sum(1 for n in uniq if lookup(n) is not None)
    return hits, len(uniq)
