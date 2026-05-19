import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from .base import Detector, ParameterSpec


class STLResidualDetector(Detector):
    name = "STL residual"
    short_description = (
        "Splitst de tijdreeks in trend + seizoen + rest, en zoekt afwijkingen "
        "in de rest-component."
    )
    plain_explanation = (
        "Filtert bekende patronen weg — bijvoorbeeld dat het altijd drukker "
        "is op zaterdag — en kijkt naar wat er dan nog overblijft. Markeert "
        "waarden waarbij die 'rest' onverklaarbaar groot is."
    )
    long_description = """
**STL decomposition + residual analysis**

*Seasonal-Trend decomposition using LOESS* (STL) verdeelt een tijdreeks in
drie componenten:

- **Trend**: lange-termijn ontwikkeling
- **Seizoen**: terugkerend patroon (bv. weekend-effect bij periode = 7)
- **Residual** (rest): wat overblijft, het 'onverwachte' deel

Op de residual wordt vervolgens een robuuste Z-score (MAD) berekend. Waarden
met `|z| > drempel` worden als afwijking gemarkeerd.

**Wanneer geschikt**
- Sterk seizoensgebonden data — bv. data die elke week of elke dag herhaalt.
- Voldoende historie (minimaal 2× de periode).

**Niet geschikt voor**
- Zeer korte tijdreeksen.
- Data zonder duidelijk periodiek patroon (gebruik dan Z-score of Rolling).
"""

    parameters = {
        "period": ParameterSpec(
            label="Seizoen-periode (dagen)",
            type="int",
            default=7,
            min=2,
            max=365,
            step=1,
            help="Lengte van het terugkerende patroon. 7 = wekelijks, 30 ~ maandelijks.",
        ),
        "threshold": ParameterSpec(
            label="Drempelwaarde",
            type="float",
            default=3.5,
            min=1.0,
            max=10.0,
            step=0.1,
            help="Aantal modified Z-scores op de residual.",
        ),
    }

    def detect(self, df, time_col, value_col, period=7, threshold=3.5):
        out = df.copy().sort_values(time_col).reset_index(drop=True)
        out[time_col] = pd.to_datetime(out[time_col])

        daily = (
            out.set_index(time_col)[value_col]
            .resample("D")
            .sum()
            .fillna(0)
        )

        if len(daily) < 2 * period + 1:
            out["anomaly_score"] = 0.0
            out["is_anomaly"] = False
            return out

        stl = STL(daily, period=period, robust=True).fit()
        resid = stl.resid

        median = np.median(resid)
        mad = np.median(np.abs(resid - median))
        if mad == 0:
            score = pd.Series(np.zeros(len(resid)), index=resid.index)
        else:
            score = 0.6745 * (resid - median) / mad

        day_idx = out[time_col].dt.floor("D")
        out["anomaly_score"] = day_idx.map(score).fillna(0).values
        out["is_anomaly"] = out["anomaly_score"].abs() > threshold
        return out
