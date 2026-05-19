"""Genereert een test-Excel met fictieve drone-waarnemingen rond
Nederlandse militaire bases. Bevat ingebouwde anomalieën om detectie te valideren.

Draaien:
    python scripts/generate_test_data.py
"""
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


BASES = [
    {"name": "Vliegbasis Volkel",        "lat": 51.6573, "lon": 5.6889, "baseline": 3},
    {"name": "Vliegbasis Leeuwarden",    "lat": 53.2289, "lon": 5.7556, "baseline": 2},
    {"name": "Marinevliegkamp De Kooy",  "lat": 52.9233, "lon": 4.7806, "baseline": 4},
    {"name": "Vliegbasis Woensdrecht",   "lat": 51.4489, "lon": 4.3422, "baseline": 2},
    {"name": "Vliegbasis Eindhoven",     "lat": 51.4500, "lon": 5.3744, "baseline": 3},
]

DRONE_TYPES = ["Onbekend", "Quadcopter", "Vleugel-drone", "Mini-drone"]
SOURCES = ["Radar", "Visueel", "Akoestisch", "Meervoudig"]


def generate(start_date="2025-01-01", days=180, seed=42) -> pd.DataFrame:
    random.seed(seed)
    start = datetime.fromisoformat(start_date)
    rows = []
    for d in range(days):
        date = start + timedelta(days=d)
        is_weekend = date.weekday() >= 5
        for base in BASES:
            baseline = base["baseline"] + (1 if is_weekend else 0)
            count = max(0, int(round(random.gauss(baseline, 1.2))))

            # Geïnjecteerde anomalieën
            if base["name"] == "Vliegbasis Volkel" and d == 45:
                count = 30  # losse spike
            if base["name"] == "Vliegbasis Leeuwarden" and 100 <= d <= 103:
                count += 8  # cluster
            if base["name"] == "Marinevliegkamp De Kooy" and d >= 130:
                count += 5  # blijvende niveaustijging (change-point)
            if base["name"] == "Vliegbasis Eindhoven" and d == 160:
                count = 20  # losse spike

            if count == 0:
                continue

            rows.append({
                "Datum": date.date(),
                "Locatie": base["name"],
                "Aantal_waarnemingen": count,
                "Type": random.choice(DRONE_TYPES),
                "Latitude": base["lat"],
                "Longitude": base["lon"],
                "Gem_hoogte_m": round(random.uniform(50, 300), 1),
                "Bron": random.choice(SOURCES),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate()
    out = Path(__file__).resolve().parent.parent / "data" / "test_drone_waarnemingen.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False)
    print(f"{len(df)} rijen geschreven naar {out}")
