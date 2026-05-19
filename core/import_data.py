"""Excel/CSV import + column mapping to internal schema."""
from __future__ import annotations

import pandas as pd


def read_table(file) -> pd.DataFrame:
    """Reads Excel (.xlsx/.xls) or CSV based on filename."""
    name = getattr(file, "name", "").lower()
    if name.endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


# Backwards-compatible alias
read_excel = read_table


def parse_datetime_robust(series: pd.Series) -> pd.Series:
    """Robuust datums parsen, ook bij gemengde formaten in één kolom.

    Pandas 3.0 NaT't stilzwijgend rijen waar het formaat niet kan worden
    afgeleid uit één enkel formaat. format='mixed' lost dat op.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    # Eerste poging: mixed (haalt date + datetime door elkaar)
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed",
                                dayfirst=False)
        if parsed.notna().sum() >= series.notna().sum() * 0.5:
            return parsed
    except Exception:
        pass
    # Tweede poging: dayfirst (Europees)
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed",
                                dayfirst=True)
        if parsed.notna().sum() >= series.notna().sum() * 0.5:
            return parsed
    except Exception:
        pass
    # Fallback: dateutil per element
    return pd.to_datetime(series, errors="coerce")


def apply_mapping(df: pd.DataFrame, mapping: dict) -> tuple[pd.DataFrame, dict]:
    """Map source columns to internal schema. Returns (normalized_df, stats).

    stats geeft inzicht in hoeveel rijen gedropt zijn en waarom — voorkomt
    stille data-loss.
    """
    out = pd.DataFrame()

    time_col = mapping.get("time")
    value_col = mapping.get("value")
    if not time_col or not value_col:
        raise ValueError("Tijd-kolom en waarde-kolom zijn verplicht.")

    n_input = len(df)

    parsed_time = parse_datetime_robust(df[time_col])
    out["timestamp"] = parsed_time
    out["value"] = pd.to_numeric(df[value_col], errors="coerce")

    for std in ("category", "location_name"):
        src = mapping.get(std)
        if src and src in df.columns:
            out[std] = df[src].astype(str)

    for std in ("lat", "lon"):
        src = mapping.get(std)
        if src and src in df.columns:
            out[std] = pd.to_numeric(df[src], errors="coerce")

    for extra in mapping.get("extras") or []:
        if extra in df.columns and extra not in out.columns:
            out[extra] = df[extra]

    # Diagnostiek: welke rijen drop ik en waarom
    n_bad_time = int(parsed_time.isna().sum())
    n_bad_value = int(out["value"].isna().sum())
    n_both_bad = int((parsed_time.isna() & out["value"].isna()).sum())

    # We droppen alleen op timestamp (value mag NaN zijn; resampling vult op)
    final = out.dropna(subset=["timestamp"]).reset_index(drop=True)

    stats = {
        "input_rows": n_input,
        "output_rows": len(final),
        "dropped_bad_time": n_bad_time,
        "dropped_bad_value": n_bad_value,
        "dropped_both": n_both_bad,
        "dropped_total": n_input - len(final),
    }
    return final, stats
