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
    sig_threshold: float = 1.0    # 95%-drempel voor max|corr| onder H0
    significant: bool = False     # |best_corr| boven die drempel?


def cross_correlation_lag(
    series_a: pd.Series,
    series_b: pd.Series,
    aggregation: str,
    max_lag: int | None = None,
) -> LagResult | None:
    """Cross-correlatie tussen twee reeksen over een gemeenschappelijke
    tijd-as. Positieve lag = B volgt A met die vertraging.

    Belangrijk: we correleren de EERSTE VERSCHILLEN (dag-op-dag verandering),
    niet de niveaus. Twee reeksen die allebei groeien over de tijd zouden in
    niveaus altijd "sterk gecorreleerd" lijken — ook zonder enig echt verband
    (spurious correlation). Verschillen meten of de *bewegingen* samenhangen,
    wat de vraag is die de analist stelt ("volgt B op A?").
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

    # Eerste verschillen; val terug op niveaus als een reeks (vrijwel)
    # lineair is en de verschillen dus geen variantie hebben.
    da = np.diff(a.values)
    db = np.diff(b.values)
    if da.std() > 1e-9 and db.std() > 1e-9:
        az = _z(da)
        bz = _z(db)
        n = len(az)
    else:
        az = _z(a.values)
        bz = _z(b.values)

    lags = list(range(-max_lag, max_lag + 1))

    def _corr_profile(x_arr: np.ndarray, y_arr: np.ndarray) -> list[float]:
        out: list[float] = []
        for lag in lags:
            if lag >= 0:
                x = x_arr[: n - lag] if lag > 0 else x_arr
                y = y_arr[lag:] if lag > 0 else y_arr
            else:
                x = x_arr[-lag:]
                y = y_arr[: n + lag]
            if len(x) < 8:
                out.append(0.0)
                continue
            denom = np.sqrt((x * x).sum() * (y * y).sum())
            out.append(float((x * y).sum() / denom) if denom > 0 else 0.0)
        return out

    corrs = _corr_profile(az, bz)
    best_i = int(np.argmax(corrs))

    # Significantie via permutatietest. We nemen het MAXIMUM over alle lags:
    # wie over ~61 lags de hoogste correlatie kiest, vindt óók in pure ruis
    # een "beste" lag — de drempel moet dat selectie-effect meerekenen
    # (klassieke t.o.v.-één-lag-drempels zoals 2/sqrt(n) zijn hier te laag).
    rng = np.random.default_rng(0)
    n_perm = 200
    max_abs = np.empty(n_perm)
    for p in range(n_perm):
        perm_profile = _corr_profile(az, rng.permutation(bz))
        max_abs[p] = float(np.max(np.abs(perm_profile)))
    sig_threshold = float(np.quantile(max_abs, 0.95))

    return LagResult(
        best_lag=lags[best_i],
        best_corr=corrs[best_i],
        lags=lags,
        corrs=corrs,
        unit=AGGREGATIONS[aggregation][1],
        n_overlap=n,
        sig_threshold=sig_threshold,
        significant=abs(corrs[best_i]) > sig_threshold,
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

    # Drempel schaalt met de reekslengte: bij honderden kandidaat-posities
    # vuurt een vaste t=2.0 gegarandeerd op ruis. De statistiek heeft
    # Student-t-staarten (df = 2w-2, dus zwaarder dan normaal bij kleine w);
    # we nemen de Bonferroni-gecorrigeerde t-quantile over het effectieve
    # aantal onafhankelijke vensters (overlappende vensters correleren over
    # ~w posities). Vloer van 2.0 voor korte reeksen.
    from scipy import stats as _st
    n_eff = max((n - 2 * w) // w, 3)
    threshold = max(2.0, float(
        _st.t.ppf(1.0 - 0.05 / (2 * n_eff), df=2 * w - 2)
    ))
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
    """Gemiddelde per uur (hourly), weekdag (daily) of maand (weekly/monthly).
    Returnt {labels, values, peak, trough} of None als niet zinvol."""
    if len(series) < 14:
        return None
    if aggregation == "hourly":
        grp = pd.Series(series.values, index=series.index).groupby(
            series.index.hour
        ).mean()
        labels = [f"{int(h):02d}u" for h in grp.index]
    elif aggregation == "daily":
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
