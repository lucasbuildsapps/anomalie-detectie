"""Plain-language verklaringen bij bevindingen. Geen jargon."""
from __future__ import annotations

import pandas as pd

from core.profiler import DataProfile


DAY_NAMES = [
    "maandag", "dinsdag", "woensdag", "donderdag",
    "vrijdag", "zaterdag", "zondag",
]


def _safe_baseline(results: pd.DataFrame, key_col: str) -> dict:
    """Mediane waarde per groep, gebaseerd op niet-afwijkende rijen.
    Zo zit de spike zelf niet in z'n eigen baseline."""
    if key_col not in results.columns:
        return {}
    baseline_rows = results[~results["is_anomaly"]] if "is_anomaly" in results.columns else results
    if baseline_rows.empty:
        baseline_rows = results
    grp = baseline_rows.groupby(key_col)["value"].median()
    return grp.to_dict()


def explain_finding(
    row: pd.Series,
    results: pd.DataFrame,
    profile: DataProfile,
    methods_flagged: list[str],
    methods_not_flagged: list[str],
) -> dict:
    """Bouwt een gestructureerde uitleg per bevinding.

    Returnt dict met:
      header, observation, baseline, factor, weekday, votes, not_flagged
    """
    key_col = (
        "location_name" if "location_name" in results.columns
        and results["location_name"].notna().any() else
        ("category" if "category" in results.columns
         and results["category"].notna().any() else None)
    )
    location = (
        row.get(key_col) if key_col and pd.notna(row.get(key_col)) else "—"
    )
    date = pd.Timestamp(row["timestamp"]).date()
    value = row["value"]
    weekday = DAY_NAMES[date.weekday()]

    baselines = _safe_baseline(results, key_col) if key_col else {}
    base_value = baselines.get(location) if key_col else None

    parts = {
        "header": f"{location} — {date.isoformat()}",
        "observation": (
            f"Op {weekday} {date.strftime('%d-%m-%Y')} werden "
            f"{int(value)} waarnemingen geregistreerd bij {location}."
        ),
        "baseline": None,
        "factor": None,
        "weekday_context": None,
        "votes": (
            f"{len(methods_flagged)} van de "
            f"{len(methods_flagged) + len(methods_not_flagged)} algoritmes "
            f"markeren deze waarde als afwijkend."
        ),
        "not_flagged": None,
    }

    if base_value and base_value > 0:
        factor = float(value) / float(base_value)
        parts["baseline"] = (
            f"Het gebruikelijke niveau voor {location} is ongeveer "
            f"{base_value:.1f} per dag."
        )
        if factor >= 1.5:
            parts["factor"] = (
                f"Deze waarde is {factor:.1f}× zo hoog als gebruikelijk."
            )
        elif factor <= 0.5:
            parts["factor"] = (
                f"Deze waarde is slechts {factor:.1f}× het gebruikelijke "
                f"niveau (sterk lager)."
            )

    if profile.seasonality_period == 7:
        is_weekend = date.weekday() >= 5
        parts["weekday_context"] = (
            f"Er is een wekelijks patroon in de data; "
            f"{weekday}en zijn doorgaans "
            f"{'iets drukker (weekend)' if is_weekend else 'rustiger (doordeweeks)'}. "
            f"De huidige waarde wijkt ook ten opzichte daarvan af."
        )

    if methods_not_flagged:
        parts["not_flagged"] = (
            "Niet aangeslagen: " + ", ".join(methods_not_flagged) + "."
        )

    return parts


def explanation_to_markdown(exp: dict) -> str:
    """Compose to a single multiline string for UI rendering."""
    lines = [exp["observation"]]
    if exp.get("baseline"):
        lines.append(exp["baseline"])
    if exp.get("factor"):
        lines.append(exp["factor"])
    if exp.get("weekday_context"):
        lines.append(exp["weekday_context"])
    lines.append("")
    lines.append(exp["votes"])
    if exp.get("not_flagged"):
        lines.append(exp["not_flagged"])
    return "\n\n".join(lines)
