"""Guess which columns in an uploaded file map to which internal role.

Robuust voor messy real-world data: weigert JSON/struct-text als
categorie of locatie, en doet strikte lat/lon-detectie."""
from __future__ import annotations

import pandas as pd


TIME_KEYWORDS = (
    "datum", "date", "time", "tijd", "tijdstip", "timestamp", "moment",
    "dag", "day",
)
VALUE_KEYWORDS = (
    "aantal", "count", "value", "waarde", "totaal", "metingen",
    "observaties", "waarnemingen", "amount", "launched", "fired",
    "afgevuurd", "gemeld", "incidents", "events", "frequency",
    "mag", "magnitude", "intensity", "intensiteit", "score",
    "depth", "diepte", "level", "niveau",
)
LOCATION_KEYWORDS = (
    "locatie", "location", "plaats", "basis", "site", "place",
    "vliegbasis", "haven", "regio", "region", "area", "gebied",
    "country", "land", "oblast", "stad", "city", "target", "doel",
    "bestemming", "destination", "where",
)
CATEGORY_KEYWORDS = (
    "type", "categorie", "category", "soort", "klasse", "class",
    "kind", "label", "model", "groep", "group", "magtype",
)
LAT_KEYWORDS = ("lat", "latitude", "breedte", "noorderbreedte", "ycoord")
LON_KEYWORDS = ("lon", "lng", "longitude", "lengte", "oosterlengte", "xcoord")


def _name_matches(name: str, keywords: tuple[str, ...]) -> bool:
    if not isinstance(name, str):
        return False
    n = name.lower()
    return any(kw in n for kw in keywords)


def _name_match_strength(name: str, keywords: tuple[str, ...]) -> float:
    """0 = geen match, 1 = substring match, 2 = exact match, 3 = exact + short."""
    if not isinstance(name, str):
        return 0.0
    n = name.lower().strip()
    # Exact match wint altijd
    for kw in keywords:
        if n == kw.lower():
            # Korte exact matches (mag, place) zijn sterkste signalen
            return 3.0 if len(kw) <= 6 else 2.0
    for kw in keywords:
        if kw in n:
            return 1.0
    return 0.0


def _is_date_column(s: pd.Series) -> float:
    """Fractie van waarden die als datum kan worden gelezen."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return 1.0
    sample = s.dropna().head(50)
    if sample.empty:
        return 0.0
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        return float(parsed.notna().sum() / len(sample))
    except Exception:
        return 0.0


def _is_numeric(s: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(s):
        return True
    try:
        pd.to_numeric(s.dropna().head(50))
        return True
    except Exception:
        return False


def _is_json_or_struct_text(s: pd.Series) -> bool:
    """Detecteer kolommen waarvan een groot deel JSON/dict/list-achtige
    strings bevat. Dat zijn nooit goede categorieën of locaties."""
    if pd.api.types.is_numeric_dtype(s):
        return False
    sample = s.dropna().astype(str).head(50)
    if sample.empty:
        return False
    has_struct = sample.str.contains(r"[\{\}\[\]]", regex=True, na=False)
    return bool(has_struct.mean() > 0.2)


def _looks_like_coordinate(s: pd.Series, lo: float, hi: float) -> bool:
    """Strikte lat/lon-detectie: niet alleen binnen range, maar ook
    decimalen aanwezig en niet alles 0."""
    nums = pd.to_numeric(s, errors="coerce").dropna()
    if len(nums) < 5:
        return False
    if not (nums.between(lo, hi).mean() > 0.95):
        return False
    # Coordinaten zijn vrijwel altijd float met decimalen
    has_decimals = (nums % 1 != 0).mean()
    if has_decimals < 0.5:
        return False
    # Niet alles dezelfde waarde, en niet allemaal 0
    if nums.std() < 0.001 or nums.abs().mean() < 0.01:
        return False
    return True


def _cardinality(s: pd.Series) -> int:
    return int(s.dropna().nunique())


def guess_mapping(df: pd.DataFrame) -> dict:
    """Return best-guess role-to-column mapping."""
    cols = list(df.columns)
    if not cols:
        return {}

    mapping: dict = {
        "time": None, "value": None,
        "category": None, "location_name": None,
        "lat": None, "lon": None, "extras": [],
    }

    # ----- Time: hoogste date-parse score + naam-bonus -----
    time_scores = []
    for c in cols:
        score = _is_date_column(df[c])
        if _name_matches(c, TIME_KEYWORDS):
            score += 0.5
        time_scores.append((c, score))
    time_scores.sort(key=lambda p: -p[1])
    if time_scores and time_scores[0][1] >= 0.6:
        mapping["time"] = time_scores[0][0]

    # ----- Lat / lon: strikte detectie -----
    for c in cols:
        if c == mapping["time"]:
            continue
        if _looks_like_coordinate(df[c], -90, 90) and _name_matches(c, LAT_KEYWORDS):
            mapping["lat"] = c
        elif _looks_like_coordinate(df[c], -180, 180) and _name_matches(c, LON_KEYWORDS):
            mapping["lon"] = c
    # Fallback puur op range + decimalen, maar alleen als naam niet contra-indicatief
    if mapping["lat"] is None:
        for c in cols:
            if c in {mapping["time"], mapping["lon"]}:
                continue
            if _name_matches(c, VALUE_KEYWORDS + LOCATION_KEYWORDS + CATEGORY_KEYWORDS):
                continue
            if _looks_like_coordinate(df[c], -90, 90):
                mapping["lat"] = c
                break
    if mapping["lon"] is None:
        for c in cols:
            if c in {mapping["time"], mapping["lat"]}:
                continue
            if _name_matches(c, VALUE_KEYWORDS + LOCATION_KEYWORDS + CATEGORY_KEYWORDS):
                continue
            if _looks_like_coordinate(df[c], -180, 180):
                nums = pd.to_numeric(df[c], errors="coerce").dropna()
                # Lon-onderscheid: enkele waarden buiten [-90,90]
                if (nums.abs() > 90).any():
                    mapping["lon"] = c
                    break

    # ----- Value: numeriek + niet tijd/lat/lon, naam-match prioriteit -----
    reserved = {mapping["time"], mapping["lat"], mapping["lon"]}
    value_candidates = []
    for c in cols:
        if c in reserved:
            continue
        if _is_numeric(df[c]):
            nums = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(nums) < 3:
                continue
            score = float(nums.std() / (abs(nums.mean()) + 1e-6))
            name_str = _name_match_strength(c, VALUE_KEYWORDS)
            score += name_str * 20  # 0/20/40/60 voor geen/substring/exact/exact-kort
            if (nums == nums.astype(int)).all() and nums.min() >= 0:
                score += 1.0
            value_candidates.append((c, score))
    value_candidates.sort(key=lambda p: -p[1])
    if value_candidates:
        mapping["value"] = value_candidates[0][0]
    reserved.add(mapping["value"])

    # ----- Location: string kolom, GEEN JSON. Naam-match wint van cardinaliteit. -----
    loc_candidates = []
    for c in cols:
        if c in reserved:
            continue
        if _is_numeric(df[c]):
            continue
        if _is_json_or_struct_text(df[c]):
            continue
        card = _cardinality(df[c])
        total = len(df[c].dropna())
        if total == 0:
            continue
        ratio = card / total
        name_str = _name_match_strength(c, LOCATION_KEYWORDS)

        # Bij sterke naam-match (exact, short) accepteren we hoge cardinaliteit
        # (zoals "place" met duizenden unieke values). Bij zwakke match (substring)
        # geldt de strengere cardinaliteits-grens.
        if name_str >= 2.0:  # exact match
            score = 50.0 + name_str * 5 + (1 - min(ratio, 1.0)) * 5
            loc_candidates.append((c, score))
        elif name_str >= 1.0 and 2 <= card <= 500:  # substring match
            score = 30.0 + (1 - min(ratio, 1.0)) * 5
            loc_candidates.append((c, score))
        elif 2 <= card <= 200 and ratio < 0.7:
            score = (1 - ratio) * 10
            loc_candidates.append((c, score))
    loc_candidates.sort(key=lambda p: -p[1])
    if loc_candidates:
        mapping["location_name"] = loc_candidates[0][0]
        reserved.add(mapping["location_name"])

    # ----- Category: string kolom met lage cardinaliteit, geen JSON -----
    cat_candidates = []
    for c in cols:
        if c in reserved:
            continue
        if _is_numeric(df[c]):
            continue
        if _is_json_or_struct_text(df[c]):
            continue
        card = _cardinality(df[c])
        if card < 2:
            continue
        # Categorieën hebben doorgaans <100 unieke waarden
        if card > 100:
            continue
        score = float(100 - card)
        if _name_matches(c, CATEGORY_KEYWORDS):
            score += 50.0
        cat_candidates.append((c, score))
    cat_candidates.sort(key=lambda p: -p[1])
    if cat_candidates:
        mapping["category"] = cat_candidates[0][0]
        reserved.add(mapping["category"])

    used = {v for k, v in mapping.items() if k != "extras" and v}
    mapping["extras"] = [c for c in cols if c not in used]

    return mapping
