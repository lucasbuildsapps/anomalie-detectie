import numpy as np
import pandas as pd

from .base import Detector, ParameterSpec


class ChangePointDetector(Detector):
    name = "Change-point (windowed t-test)"
    short_description = (
        "Detecteert structurele breekpunten in de tijdreeks waar het "
        "gemiddelde permanent verandert."
    )
    plain_explanation = (
        "Zoekt momenten waarop het niveau structureel verandert — niet 'er "
        "was even een piek', maar 'vanaf deze datum is het permanent anders'. "
        "Vindt regime-shifts en gedragsveranderingen, geen losse uitschieters."
    )
    long_description = """
**Change-point detectie via windowed t-statistiek**

In tegenstelling tot Z-score / Rolling die naar losse uitschieters zoeken,
vindt deze methode **blijvende niveaustijgingen of -dalingen**.

Voor elk tijdstip `i` wordt het gemiddelde van het venster ervoor vergeleken
met het venster erna. Het verschil wordt geschaald met de gepoolde
standaarddeviatie (t-achtige statistiek):

`t_i = |mean(after) - mean(before)| / sqrt(s_pooled² · 2/n)`

Punten met `t > drempel` zijn kandidaat-breekpunten. Tussen nabij gelegen
kandidaten wordt non-maximum suppression toegepast, zodat alleen de
sterkste punten overblijven.

**Wanneer geschikt**
- Zoeken naar trendbreuken, gedragsveranderingen, regime-shifts.
- Bijvoorbeeld: vanaf een bepaalde datum is het aantal waarnemingen
  structureel hoger.

**Niet geschikt voor**
- Losse spikes (gebruik Z-score of Isolation Forest).
- Vooral seizoensgebonden patronen (gebruik STL).

**Noot**: pure NumPy-implementatie, geen externe build-dependencies. Voor
zwaardere gevallen kun je later `ruptures` (PELT-algoritme) installeren en
deze detector vervangen.
"""

    parameters = {
        "window": ParameterSpec(
            label="Venstergrootte (dagen)",
            type="int",
            default=7,
            min=3,
            max=60,
            step=1,
            help="Lengte van het vergelijkings-venster voor en na elk punt.",
        ),
        "threshold": ParameterSpec(
            label="Drempelwaarde (t-statistiek)",
            type="float",
            default=2.5,
            min=1.0,
            max=10.0,
            step=0.1,
            help="Hoger = strenger, vindt minder breekpunten.",
        ),
    }

    def detect(self, df, time_col, value_col, window=7, threshold=2.5):
        out = df.copy().sort_values(time_col).reset_index(drop=True)
        out[time_col] = pd.to_datetime(out[time_col])

        daily = (
            out.set_index(time_col)[value_col]
            .resample("D")
            .sum()
            .fillna(0)
        )

        w = int(window)
        thr = float(threshold)
        signal = daily.values.astype(float)
        n = len(signal)

        if n < 2 * w + 1:
            out["anomaly_score"] = 0.0
            out["is_anomaly"] = False
            return out

        scores = np.zeros(n)
        for i in range(w, n - w):
            before = signal[i - w:i]
            after = signal[i:i + w]
            mean_diff = abs(after.mean() - before.mean())
            pooled_var = (before.var(ddof=1) + after.var(ddof=1)) / 2.0
            if pooled_var <= 0:
                continue
            scores[i] = mean_diff / np.sqrt(pooled_var * 2.0 / w)

        # Non-maximum suppression: pak hoogste scores eerst, onderdruk
        # andere kandidaten binnen 'window' dagen.
        candidates = [(i, scores[i]) for i in range(n) if scores[i] > thr]
        candidates.sort(key=lambda p: -p[1])
        selected_idx: list[int] = []
        for idx, _ in candidates:
            if all(abs(idx - s) >= w for s in selected_idx):
                selected_idx.append(idx)

        change_dates = {daily.index[i]: float(scores[i]) for i in selected_idx}

        day_idx = out[time_col].dt.floor("D")
        out["anomaly_score"] = day_idx.map(
            lambda d: change_dates.get(pd.Timestamp(d), 0.0)
        ).astype(float).values
        out["is_anomaly"] = day_idx.isin(change_dates.keys()).values
        return out
