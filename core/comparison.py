"""Vergelijkings- en tijdlijn-analyse:

- build_series(): bouw een geaggregeerde tijdreeks voor een (regio, categorieën).
- cross_correlation_lag(): vind de vertraging waarbij twee reeksen het sterkst
  samenhangen ("B volgt gemiddeld ~X perioden na A").
- detect_change_points(): significante niveau-verschuivingen in een reeks
  (de 'significante momenten' om op de tijdlijn te markeren).
- seasonality_profile(): gemiddelde per weekdag / maand om seizoen te tonen.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.normbeeld import AGGREGATIONS

DAY_NAMES = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MONTH_NAMES = ["jan", "feb", "mrt", "apr", "mei", "jun",
               "jul", "aug", "sep", "okt", "nov", "dec"]


def build_series(
    df: pd.DataFrame,
    location: str | None,
    categories: list[str] | None,
    aggregation: str,
) -> pd.Series:
    """Geaggregeerde waarde-reeks voor een selectie. Lege index als geen data."""
    work = df.copy()
    if location is not None and "location_name" in work.columns:
        work = work[work["location_name"] == location]
    if categories and "category" in work.columns:
        work = work[work["category"].isin(categories)]
    if work.empty:
        return pd.Series(dtype=float)
    freq = AGGREGATIONS.get(aggregation, AGGREGATIONS["daily"])[0]
    work["timestamp"] = pd.to_datetime(work["timestamp"])
    return work.set_index("timestamp")["value"].resample(freq).sum().fillna(0)


@dataclass
class LagResult:
    best_lag: int                 # >0: B volgt A; <0: B loopt voor op A
    best_corr: float              # correlatie bij best_lag
    lags: list[int]               # alle geteste lags
    corrs: list[float]            # correlatie per lag
    unit: str                     # 'dag' / 'week' / 'maand'
    n_overlap: int                # aantal overlappende periodes


def cross_correlation_lag(
    series_a: pd.Series,
    series_b: pd.Series,
    aggregation: str,
    max_lag: int | None = None,
) -> LagResult | None:
    """Cross-correlatie tussen twee reeksen over een gemeenschappelijke
    tijd-as. Positieve lag = B volgt A met die vertraging.

    We z-scoren beide reeksen en berekenen Pearson-correlatie voor elke lag.
    De lag met de hoogste correlatie is de meest waarschijnlijke vertraging.
    """
    if series_a.empty or series_b.empty:
        return None

    # Gemeenschappelijke, regelmatige tijd-as
    idx = series_a.index.union(series_b.index)
    a = series_a.reindex(idx).fillna(0).astype(float)
    b = series_b.reindex(idx).fillna(0).astype(float)
    n = len(idx)
    if n < 12:
        return None

    if max_lag is None:
        max_lag = min(30, n // 3)
    max_lag = max(1, int(max_lag))

    def _z(x: np.ndarray) -> np.ndarray:
        s = x.std()
        return (x - x.mean()) / s if s > 1e-9 else x - x.mean()

    az = _z(a.values)
    bz = _z(b.values)

    lags = list(range(-max_lag, max_lag + 1))
    corrs: list[float] = []
    for lag in lags:
        if lag >= 0:
            x = az[: n - lag] if lag > 0 else az
            y = bz[lag:] if lag > 0 else bz
        else:
            x = az[-lag:]
            y = bz[: n + lag]
        if len(x) < 8:
            corrs.append(0.0)
            continue
        denom = np.sqrt((x * x).sum() * (y * y).sum())
        corrs.append(float((x * y).sum() / denom) if denom > 0 else 0.0)

    best_i = int(np.argmax(corrs))
    return LagResult(
        best_lag=lags[best_i],
        best_corr=corrs[best_i],
        lags=lags,
        corrs=corrs,
        unit=AGGREGATIONS[aggregation][1],
        n_overlap=n,
    )


def detect_change_points(
    series: pd.Series, max_points: int = 6, min_separation: int = 4,
) -> list[dict]:
    """Significante niveau-verschuivingen via windowed t-statistiek.

    Voor elk tijdstip vergelijken we het gemiddelde van het venster ervoor
    met dat erna; grote, statistisch sterke verschillen zijn change-points.
    Non-maximum suppression houdt alleen de sterkste, onderling gescheiden
    punten over. Returnt lijst van {date, before, after, direction, strength}.
    """
    if len(series) < 12:
        return []
    vals = series.values.astype(float)
    n = len(vals)
    w = max(3, min(8, n // 6))
    scores = np.zeros(n)
    for i in range(w, n - w):
        before = vals[i - w:i]
        after = vals[i:i + w]
        pooled = (before.var(ddof=1) + after.var(ddof=1)) / 2.0
        if pooled <= 0:
            continue
        scores[i] = abs(after.mean() - before.mean()) / np.sqrt(pooled * 2.0 / w)

    threshold = 2.0
    candidates = [(i, scores[i]) for i in range(n) if scores[i] > threshold]
    candidates.sort(key=lambda p: -p[1])
    chosen: list[int] = []
    for idx, _ in candidates:
        if all(abs(idx - c) >= min_separation for c in chosen):
            chosen.append(idx)
        if len(chosen) >= max_points:
            break

    out = []
    for i in sorted(chosen):
        before = float(vals[max(0, i - w):i].mean())
        after = float(vals[i:i + w].mean())
        out.append({
            "date": pd.Timestamp(series.index[i]),
            "before": before,
            "after": after,
            "direction": "stijging" if after > before else "daling",
            "strength": float(scores[i]),
        })
    return out


def seasonality_profile(series: pd.Series, aggregation: str) -> dict | None:
    """Gemiddelde per weekdag (daily) of per maand (weekly/monthly).
    Returnt {labels, values, peak, trough} of None als niet zinvol."""
    if len(series) < 14:
        return None
    if aggregation == "daily":
        grp = pd.Series(series.values, index=series.index).groupby(
            series.index.dayofweek
        ).mean()
        labels = [DAY_NAMES[i] for i in grp.index]
    else:
        grp = pd.Series(series.values, index=series.index).groupby(
            series.index.month
        ).mean()
        labels = [MONTH_NAMES[i - 1] for i in grp.index]
    if len(grp) < 3:
        return None
    values = [float(v) for v in grp.values]
    diff = (max(values) - min(values)) / max(np.mean(values), 1e-9)
    if diff < 0.15:
        return None  # te vlak om als seizoen te tonen
    peak_i = int(np.argmax(values))
    trough_i = int(np.argmin(values))
    return {
        "labels": labels,
        "values": values,
        "peak": labels[peak_i],
        "trough": labels[trough_i],
        "amplitude_pct": diff * 100,
    }
