import numpy as np

from .base import Detector, ParameterSpec


class ZScoreDetector(Detector):
    name = "Z-score (MAD)"
    short_description = (
        "Markeert waarden die meer dan N robuuste standaarddeviaties "
        "van de mediaan afwijken."
    )
    plain_explanation = (
        "Kijkt naar het normale niveau van alle waarnemingen en markeert "
        "getallen die opvallend ver van dat normale liggen. Snel en simpel; "
        "vindt vooral losse uitschieters."
    )
    long_description = """
**Modified Z-score met Median Absolute Deviation (MAD)**

Voor elke waarde wordt de afstand tot de mediaan berekend, geschaald met de
*Median Absolute Deviation*. MAD is robuuster tegen uitschieters dan de
gewone standaarddeviatie en is daarom geschikt voor datasets met enkele
extreme waarden.

Formule: `z_i = 0.6745 * (x_i - mediaan) / MAD`

Een waarde wordt als afwijking gemarkeerd als `|z_i| > drempel`.

**Wanneer geschikt**
- Stabiele baseline zonder sterke trend of seizoenspatroon.
- Lage tot middelmatige hoeveelheid uitschieters in de historische data.

**Niet geschikt voor**
- Sterk seizoensgebonden data (gebruik dan STL).
- Trendmatige data (gebruik dan Change-point of Rolling).
"""

    parameters = {
        "threshold": ParameterSpec(
            label="Drempelwaarde",
            type="float",
            default=3.5,
            min=1.0,
            max=10.0,
            step=0.1,
            help="Aantal modified Z-scores waarboven een waarde als afwijking telt.",
        ),
    }

    def detect(self, df, time_col, value_col, threshold=3.5):
        out = df.copy().sort_values(time_col).reset_index(drop=True)
        x = out[value_col].astype(float).to_numpy()
        median = np.median(x)
        mad = np.median(np.abs(x - median))
        if mad == 0:
            score = np.zeros_like(x)
        else:
            score = 0.6745 * (x - median) / mad
        out["anomaly_score"] = score
        out["is_anomaly"] = np.abs(score) > threshold
        return out
