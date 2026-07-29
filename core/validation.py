"""Validatie van geïmporteerde data, vóór opslag.

Twee niveaus:
- errors:   blokkerend — de import gaat niet door.
- warnings: informatief — de import gaat door, maar de analist ziet wat
            er opvalt (stil doorlaten van rommel is erger dan een melding).

Bewust dependency-licht (geen pandera): de checks zijn domein-specifiek
en in gewone pandas beter leesbaar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Harde grenzen
MAX_ROWS = 1_000_000
TS_MIN = pd.Timestamp("1970-01-01")
# Waarnemingen uit de toekomst zijn vrijwel altijd een parse-fout
TS_FUTURE_SLACK_DAYS = 366

# Waarschuw-drempels
WARN_DROP_FRACTION = 0.10     # >10% rijen gedropt bij import
WARN_DUPLICATE_FRACTION = 0.25


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_mapped(df: pd.DataFrame, import_stats: dict | None = None) -> ValidationReport:
    """Valideer een al-gemapte DataFrame (kolommen: timestamp, value, ...).

    `import_stats` is de drop-statistiek van apply_mapping (optioneel).
    """
    rep = ValidationReport()
    n = len(df)
    rep.stats["rows"] = n

    if n == 0:
        rep.errors.append(
            "Geen bruikbare rijen na kolom-mapping. Controleer of de "
            "tijd-kolom echte datums bevat."
        )
        return rep
    if n > MAX_ROWS:
        rep.errors.append(
            f"Dataset te groot ({n:,} rijen; maximum {MAX_ROWS:,}). "
            "Splits het bestand of aggregeer vooraf."
        )
        return rep

    # --- Tijdstempels ---
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    n_bad_ts = int(ts.isna().sum())
    if n_bad_ts == n:
        rep.errors.append("Geen enkele geldige datum in de tijd-kolom.")
        return rep

    now = pd.Timestamp.now()
    future_limit = now + pd.Timedelta(days=TS_FUTURE_SLACK_DAYS)
    n_ancient = int((ts < TS_MIN).sum())
    n_future = int((ts > future_limit).sum())
    if n_ancient:
        rep.warnings.append(
            f"{n_ancient} rijen met datum vóór 1970 — vrijwel zeker een "
            "parse-fout (Excel-serienummers?). Deze rijen vervallen."
        )
    if n_future:
        rep.warnings.append(
            f"{n_future} rijen met datum meer dan een jaar in de toekomst — "
            "controleer het datumformaat (dag/maand-verwisseling?)."
        )
    rep.stats["ts_range"] = (str(ts.min()), str(ts.max()))

    # --- Waarden ---
    if "value" in df.columns:
        vals = pd.to_numeric(df["value"], errors="coerce")
        n_bad_val = int(vals.isna().sum())
        if n_bad_val == n:
            rep.errors.append("Geen enkele numerieke waarde in de waarde-kolom.")
            return rep
        if n_bad_val / n > WARN_DROP_FRACTION:
            rep.warnings.append(
                f"{n_bad_val} van {n} waarden zijn niet-numeriek en tellen "
                "niet mee."
            )
        finite = vals.dropna()
        if len(finite) >= 10 and finite.nunique() == 1:
            rep.warnings.append(
                "Alle waarden zijn identiek — is de juiste kolom als "
                "'waarde' gekozen?"
            )
        if (finite < 0).any():
            rep.warnings.append(
                f"{int((finite < 0).sum())} negatieve waarden. Voor tellingen "
                "is dat onverwacht; voor delta's/temperaturen is dit OK."
            )

    # --- Coördinaten ---
    for col, lo, hi in (("lat", -90, 90), ("lon", -180, 180)):
        if col in df.columns:
            c = pd.to_numeric(df[col], errors="coerce").dropna()
            n_out = int(((c < lo) | (c > hi)).sum())
            if n_out:
                rep.warnings.append(
                    f"{n_out} rijen met {col} buiten [{lo}, {hi}] — deze "
                    "punten verschijnen niet op de kaart."
                )

    # --- Duplicaten ---
    dup_cols = [c for c in ("timestamp", "value", "location_name", "category")
                if c in df.columns]
    if dup_cols:
        n_dup = int(df.duplicated(subset=dup_cols).sum())
        rep.stats["duplicates"] = n_dup
        if n and n_dup / n > WARN_DUPLICATE_FRACTION:
            rep.warnings.append(
                f"{n_dup} van {n} rijen zijn duplicaten (zelfde tijd/waarde/"
                "locatie). Dubbele rijen worden bij opslag éénmalig geteld."
            )

    # --- Drop-statistiek van de mapping-stap ---
    if import_stats:
        dropped = import_stats.get("dropped_total", 0)
        n_input = max(import_stats.get("input_rows", 1), 1)
        if dropped / n_input > WARN_DROP_FRACTION:
            rep.warnings.append(
                f"{dropped} van {n_input} bronrijen zijn gedropt "
                f"({dropped / n_input * 100:.0f}%) — vrijwel altijd een "
                "datumformaat-probleem. Controleer de tijd-kolom."
            )

    return rep
