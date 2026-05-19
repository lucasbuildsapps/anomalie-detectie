import numpy as np
import pandas as pd

from .base import Detector, ParameterSpec


class RollingDetector(Detector):
    name = "Rolling mean ± N·std"
    short_description = (
        "Vergelijkt elke dagwaarde met het voortschrijdend gemiddelde over een "
        "venster, en markeert grote afwijkingen."
    )
    plain_explanation = (
        "Past de definitie van 'normaal' aan op de recente periode (bv. de "
        "laatste week). Markeert waarden die opvallend afwijken van dat "
        "recente gemiddelde. Werkt goed als het niveau over de tijd "
        "geleidelijk verandert."
    )
    long_description = """
**Rolling mean & standard deviation**

Voor elke dag wordt het gemiddelde en de standaarddeviatie berekend over de
voorgaande N dagen. Een waarde wordt als afwijking gemarkeerd als deze meer
dan *drempel × std* afwijkt van het rollende gemiddelde.

Formule: `z_t = (x_t - rolling_mean_t) / rolling_std_t`

**Wanneer geschikt**
- Korte-termijn afwijkingen ten opzichte van de recente baseline.
- Data zonder sterk seizoenspatroon.

**Niet geschikt voor**
- Sterk seizoensgebonden data (gebruik STL).
- Hele kleine datasets (< 2× venstergrootte).

De data wordt eerst per dag geaggregeerd (sommatie). De score wordt
teruggekoppeld aan de oorspronkelijke rijen op basis van datum.
"""

    parameters = {
        "window": ParameterSpec(
            label="Venstergrootte (dagen)",
            type="int",
            default=7,
            min=2,
            max=90,
            step=1,
            help="Lengte van het voortschrijdende venster.",
        ),
        "threshold": ParameterSpec(
            label="Drempelwaarde (× std)",
            type="float",
            default=3.0,
            min=1.0,
            max=10.0,
            step=0.1,
            help="Aantal standaarddeviaties voor markering.",
        ),
    }

    def detect(self, df, time_col, value_col, window=7, threshold=3.0):
        out = df.copy().sort_values(time_col).reset_index(drop=True)
        out[time_col] = pd.to_datetime(out[time_col])

        daily = (
            out.set_index(time_col)[value_col]
            .resample("D")
            .sum()
        )
        roll_mean = daily.rolling(window=window, min_periods=2).mean()
        roll_std = daily.rolling(window=window, min_periods=2).std().replace(0, np.nan)
        score = ((daily - roll_mean) / roll_std).fillna(0)

        day_idx = out[time_col].dt.floor("D")
        out["anomaly_score"] = day_idx.map(score).fillna(0).values
        out["is_anomaly"] = out["anomaly_score"].abs() > threshold
        return out
