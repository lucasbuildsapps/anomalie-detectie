import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .base import Detector, ParameterSpec


class IsolationForestDetector(Detector):
    name = "Isolation Forest"
    short_description = (
        "ML-methode die afwijkingen vindt door de data willekeurig in stukjes "
        "te knippen — afwijkende punten zijn sneller geïsoleerd."
    )
    plain_explanation = (
        "Machine learning techniek die kijkt welke datapunten 'eenzaam' "
        "staan — weinig vergelijkbare punten in de buurt. Werkt met meerdere "
        "eigenschappen tegelijk (waarde + dag-van-week + aantal observaties)."
    )
    long_description = """
**Isolation Forest (sklearn)**

Een unsupervised machine-learning methode die werkt door de data herhaaldelijk
willekeurig op te splitsen. Afwijkingen zijn punten die met **weinig
splitsingen** al uniek staan — normale punten zitten dicht bij elkaar en
vereisen meer splitsingen.

In tegenstelling tot statistische methoden maakt deze methode geen aanname
over de verdeling van de data en werkt zij goed in **meerdere dimensies**
(bv. waarde + dag-van-week + locatie).

In deze implementatie wordt per rij een feature-vector gebouwd uit:
- waarde
- dag-van-week (cyclisch)
- aantal observaties per dag

**Wanneer geschikt**
- Multidimensionale data zonder duidelijke statistische verdeling.
- Vergelijking met andere methodes als 'second opinion'.

**Niet geschikt voor**
- Heel kleine datasets (< 50 rijen).
- Wanneer interpretatie van *waarom* iets afwijkend is belangrijk is.
"""

    parameters = {
        "contamination": ParameterSpec(
            label="Verwachte fractie afwijkingen",
            type="float",
            default=0.05,
            min=0.01,
            max=0.5,
            step=0.01,
            help="Schatting hoeveel procent van de data afwijkend is.",
        ),
        "n_estimators": ParameterSpec(
            label="Aantal bomen",
            type="int",
            default=100,
            min=20,
            max=500,
            step=10,
            help="Meer bomen = stabieler resultaat, maar langzamer.",
        ),
    }

    def detect(self, df, time_col, value_col, contamination=0.05, n_estimators=100):
        out = df.copy().sort_values(time_col).reset_index(drop=True)
        out[time_col] = pd.to_datetime(out[time_col])

        if len(out) < 20:
            out["anomaly_score"] = 0.0
            out["is_anomaly"] = False
            return out

        dow = out[time_col].dt.dayofweek.to_numpy()
        sin_dow = np.sin(2 * np.pi * dow / 7)
        cos_dow = np.cos(2 * np.pi * dow / 7)

        daily_counts = (
            out.set_index(time_col)[value_col]
            .resample("D").count()
        )
        day_idx = out[time_col].dt.floor("D")
        n_per_day = day_idx.map(daily_counts).fillna(1).to_numpy()

        X = np.column_stack([
            out[value_col].astype(float).to_numpy(),
            sin_dow,
            cos_dow,
            n_per_day,
        ])

        model = IsolationForest(
            n_estimators=int(n_estimators),
            contamination=float(contamination),
            random_state=42,
        )
        model.fit(X)
        # decision_function: hoger = normaler, lager = afwijkender
        # Invert + shift naar 0 voor leesbaarheid.
        raw = model.decision_function(X)
        out["anomaly_score"] = (-raw).astype(float)
        out["is_anomaly"] = model.predict(X) == -1
        return out
