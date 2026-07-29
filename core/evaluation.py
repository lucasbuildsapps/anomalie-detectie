"""Evaluatie-harnas: meet detectoren tegen bekende incidenten.

De rest van de tool zegt *hoeveel* methodes iets markeren. Dit zegt of ze
**gelijk hadden**. Zonder deze meting blijft "5 detectie-algoritmes" een
bewering; met labels wordt het een cijfer per dataset.

Werkwijze: geef een lijst bekende incidenten (datum of datumbereik, en
optioneel een regio). Elke detector draait over de historie en wordt
vergeleken met die labels:

- **recall**    — welk deel van de incidenten is opgemerkt? (missers zijn
                  in inlichtingenwerk meestal duurder dan vals alarm)
- **precisie**  — welk deel van de meldingen was raak?
- **F1**        — harmonisch gemiddelde, één getal om op te sorteren

Een melding telt als treffer wanneer die binnen `tolerance_periods` van
een incident valt: een aanval die op de 3e wordt gedetecteerd terwijl het
label de 2e noemt, is geen misser maar een rapportageverschil.

Belangrijke beperking: dit meet alleen wat gelabeld is. Een detector die
iets echts vindt dat niet in de lijst staat, telt hier als vals alarm.
Labels zijn dus zelf een bron van vertekening — houd ze bij als data, niet
als waarheid.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.registry import get_detectors


@dataclass
class Incident:
    """Eén bekend voorval om tegen te toetsen."""

    start: pd.Timestamp
    end: pd.Timestamp | None = None
    location: str | None = None
    label: str = ""

    def covers(self, ts: pd.Timestamp, tolerance: pd.Timedelta) -> bool:
        start = self.start - tolerance
        end = (self.end or self.start) + tolerance
        return start <= ts <= end


@dataclass
class DetectorScore:
    detector: str
    hits: int              # incidenten opgemerkt
    misses: int            # incidenten gemist
    false_alarms: int      # meldingen zonder incident
    n_flagged: int
    recall: float
    precision: float
    f1: float


def incidents_from_frame(df: pd.DataFrame) -> list[Incident]:
    """Lees incidenten uit een DataFrame met kolommen:
    start (verplicht), end, location, label."""
    out: list[Incident] = []
    for _, row in df.iterrows():
        start = pd.to_datetime(row.get("start"), errors="coerce")
        if pd.isna(start):
            continue
        end = pd.to_datetime(row.get("end"), errors="coerce")
        loc = row.get("location")
        out.append(Incident(
            start=start,
            end=None if pd.isna(end) else end,
            location=None if (loc is None or pd.isna(loc)) else str(loc),
            label=str(row.get("label") or ""),
        ))
    return out


def incidents_from_annotations(dataset_id: int) -> list[Incident]:
    """Gebruik door analisten bevestigde bevindingen als labels.

    Zo ontstaan labels als bijproduct van normaal werk, in plaats van een
    apart annotatie-project. Alleen 'bevestigd' en 'geëscaleerd' tellen —
    'vals alarm' is per definitie geen incident.
    """
    from core import annotations as anno
    from core import storage

    rows = storage.list_annotation_rows(dataset_id) or {}
    confirmed_keys = {
        key for key, entry in rows.items()
        if (entry or {}).get("status") in ("bevestigd", "geescaleerd")
    }
    if not confirmed_keys:
        return []

    # De finding_key is een hash; we kunnen datum/locatie niet terugrekenen.
    # Daarom reconstrueren we de sleutel voor elke waarneming en kijken we
    # welke voorkomen in de bevestigde set.
    df = storage.load_observations(dataset_id)
    if df.empty:
        return []
    out: list[Incident] = []
    seen: set = set()
    for _, row in df.iterrows():
        ts = pd.Timestamp(row["timestamp"])
        loc = str(row.get("location_name") or "")
        key = anno.finding_key(ts.date().isoformat(), loc, None)
        if key in confirmed_keys and key not in seen:
            seen.add(key)
            out.append(Incident(start=ts.normalize(), location=loc or None,
                                label="bevestigd door analist"))
    return out


def evaluate_detectors(
    df: pd.DataFrame,
    incidents: list[Incident],
    tolerance_periods: int = 1,
    freq: str = "D",
    detectors: list[str] | None = None,
) -> list[DetectorScore]:
    """Score elke detector tegen de bekende incidenten.

    Returnt een lijst gesorteerd op F1 (hoog naar laag). Lege lijst als er
    geen incidenten of geen bruikbare data zijn.
    """
    if df is None or df.empty or not incidents:
        return []

    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"])
    tolerance = pd.Timedelta(days=tolerance_periods) if freq == "D" \
        else pd.Timedelta(weeks=tolerance_periods)

    available = get_detectors()
    names = [n for n in (detectors or available) if n in available]

    scores: list[DetectorScore] = []
    for name in names:
        try:
            result = available[name].detect(work, "timestamp", "value")
        except Exception:
            continue
        if "is_anomaly" not in result.columns:
            continue

        flagged = result[result["is_anomaly"].astype(bool)]
        flags = [
            (pd.Timestamp(r["timestamp"]).normalize(),
             str(r.get("location_name") or "") or None)
            for _, r in flagged.iterrows()
        ]

        matched_incidents: set[int] = set()
        matched_flags: set[int] = set()
        for i, inc in enumerate(incidents):
            for j, (ts, loc) in enumerate(flags):
                if inc.location and loc and inc.location != loc:
                    continue
                if inc.covers(ts, tolerance):
                    matched_incidents.add(i)
                    matched_flags.add(j)

        hits = len(matched_incidents)
        misses = len(incidents) - hits
        false_alarms = len(flags) - len(matched_flags)
        recall = hits / len(incidents) if incidents else 0.0
        precision = (len(matched_flags) / len(flags)) if flags else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)

        scores.append(DetectorScore(
            detector=name, hits=hits, misses=misses,
            false_alarms=false_alarms, n_flagged=len(flags),
            recall=recall, precision=precision, f1=f1,
        ))

    scores.sort(key=lambda s: -s.f1)
    return scores


def summarize(scores: list[DetectorScore]) -> str:
    """Eén leesbare conclusie over welke detector hier werkt."""
    if not scores:
        return ("Geen evaluatie mogelijk: er zijn geen gelabelde incidenten. "
                "Markeer bevindingen als 'bevestigd' om labels op te bouwen.")
    best = scores[0]
    if best.f1 == 0:
        return ("Geen enkele detector vond de bekende incidenten terug. "
                "Controleer of de labels bij deze dataset en tijdschaal "
                "horen voordat je conclusies trekt over de methodes.")
    line = (f"**{best.detector}** presteert hier het best: "
            f"{best.hits} van {best.hits + best.misses} incidenten gevonden "
            f"(recall {best.recall * 100:.0f}%), "
            f"precisie {best.precision * 100:.0f}%.")
    if best.recall < 0.5:
        line += (" Meer dan de helft van de bekende incidenten wordt gemist — "
                 "op deze data is de detectie geen vangnet.")
    return line


def to_frame(scores: list[DetectorScore]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Detector": s.detector,
        "Gevonden": s.hits,
        "Gemist": s.misses,
        "Vals alarm": s.false_alarms,
        "Recall": f"{s.recall * 100:.0f}%",
        "Precisie": f"{s.precision * 100:.0f}%",
        "F1": round(s.f1, 2),
    } for s in scores])


__all__ = [
    "DetectorScore",
    "Incident",
    "evaluate_detectors",
    "incidents_from_annotations",
    "incidents_from_frame",
    "summarize",
    "to_frame",
]
