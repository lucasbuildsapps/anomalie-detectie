import pandas as pd

from .base import Detector, ParameterSpec


def _other_detector_names() -> list[str]:
    # Lazy import to avoid registry circularity at module load.
    from core.registry import get_detectors
    return [n for n in get_detectors().keys() if n != "Ensemble (stemming)"]


class EnsembleDetector(Detector):
    name = "Ensemble (stemming)"
    short_description = (
        "Voert meerdere detectiemethoden uit en markeert een waarde als "
        "afwijking wanneer minstens N van hen het eens zijn."
    )
    plain_explanation = (
        "Combineert meerdere methodes en markeert pas iets als afwijkend als "
        "meerdere het eens zijn. Vermindert vals-alarmen ten koste van "
        "gemiste subtiele signalen."
    )
    long_description = """
**Ensemble via majority voting**

Robuuster dan een enkele methode: deze ensemble draait meerdere detectoren
parallel op dezelfde data en combineert hun uitkomsten via een stemmechanisme.

- Elke deelmethode geeft per rij een **stem** (afwijkend / niet afwijkend).
- Een rij wordt als afwijkend gemarkeerd wanneer het aantal stemmen ≥
  *Minimum aantal stemmen*.
- De score per rij is het aandeel stemmen (0,0 — 1,0).

**Wanneer geschikt**
- Wanneer geen enkele methode op zichzelf overtuigt.
- Voor robuustheid: bv. eis dat 2-van-3 onafhankelijke methoden het eens zijn.

**Tip**: kies methoden die *verschillende* dingen detecteren (bv. Z-score
voor spikes, Change-point voor niveauverschuivingen, STL voor
seizoens-afwijkingen). Drie soortgelijke methoden geven schijn-robuustheid.

Per deelmethode worden de standaard-parameters gebruikt. Wil je per methode
fijnregelen, draai ze dan eerst los en bekijk de resultaten naast elkaar.
"""

    parameters = {
        "methods": ParameterSpec(
            label="Te combineren methodes",
            type="multiselect",
            default=[],
            options=_other_detector_names,
            help="Selecteer 2 of meer detectiemethoden.",
        ),
        "min_votes": ParameterSpec(
            label="Minimum aantal stemmen",
            type="int",
            default=2,
            min=1,
            max=10,
            step=1,
            help="Hoeveel methodes moeten een rij als afwijkend markeren.",
        ),
    }

    def detect(self, df, time_col, value_col, methods=None, min_votes=2):
        from core.registry import get_detectors
        all_detectors = get_detectors()

        if not methods:
            methods = [n for n in all_detectors.keys() if n != self.name][:3]

        out = df.copy().sort_values(time_col).reset_index(drop=True)
        votes = pd.Series(0, index=out.index, dtype=int)
        n_used = 0
        for m in methods:
            if m == self.name or m not in all_detectors:
                continue
            try:
                sub = all_detectors[m].detect(df, time_col, value_col)
            except Exception:
                continue
            sub = sub.sort_values(time_col).reset_index(drop=True)
            if len(sub) == len(out):
                votes = votes.add(sub["is_anomaly"].astype(int).values, fill_value=0)
                n_used += 1

        if n_used == 0:
            out["anomaly_score"] = 0.0
            out["is_anomaly"] = False
            return out

        out["anomaly_score"] = votes.astype(float).values / n_used
        out["is_anomaly"] = votes.values >= int(min_votes)
        return out
