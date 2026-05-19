"""Profile a time series: length, seasonality, trend, stationarity.
Used by the auto-pilot to choose detection methods + thresholds."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class DataProfile:
    n_observations: int
    n_days: int
    seasonality_period: int | None  # 7 = weekly, 30 ~ monthly, None = none detected
    has_trend: bool
    trend_slope: float
    is_stationary: bool
    daily_mean: float
    daily_std: float

    def to_dict(self) -> dict:
        return {
            "Aantal observaties": self.n_observations,
            "Periode (dagen)": self.n_days,
            "Seizoensperiode": self.seasonality_period or "Geen",
            "Trend aanwezig": "Ja" if self.has_trend else "Nee",
            "Trend-helling": round(self.trend_slope, 4),
            "Stationair": "Ja" if self.is_stationary else "Nee",
            "Dagelijks gemiddelde": round(self.daily_mean, 2),
            "Dagelijkse std": round(self.daily_std, 2),
        }


def _aggregate_daily(df: pd.DataFrame, time_col: str, value_col: str) -> pd.Series:
    s = df.copy()
    s[time_col] = pd.to_datetime(s[time_col])
    return s.set_index(time_col)[value_col].resample("D").sum().fillna(0)


def _detect_seasonality(daily: pd.Series) -> int | None:
    """Detect dominant period via autocorrelation peaks."""
    n = len(daily)
    if n < 28:
        return None
    x = daily.values.astype(float) - daily.values.mean()
    if x.std() == 0:
        return None
    # Autocorrelation for lags 2..min(60, n//3)
    max_lag = min(60, n // 3)
    acf = []
    for lag in range(2, max_lag + 1):
        a = x[:-lag]
        b = x[lag:]
        denom = np.sqrt((a * a).sum() * (b * b).sum())
        if denom == 0:
            acf.append(0.0)
        else:
            acf.append(float((a * b).sum() / denom))
    if not acf:
        return None
    # Find peak above threshold
    arr = np.array(acf)
    peak_lag = int(np.argmax(arr)) + 2
    if arr[peak_lag - 2] > 0.25:
        return peak_lag
    return None


def _trend_slope(daily: pd.Series) -> float:
    if len(daily) < 5:
        return 0.0
    x = np.arange(len(daily))
    y = daily.values.astype(float)
    if y.std() == 0:
        return 0.0
    slope, _ = np.polyfit(x, y, 1)
    # Normalize slope by mean so it's a relative trend
    mean = float(np.mean(y))
    return float(slope / mean) if mean != 0 else float(slope)


def _is_stationary(daily: pd.Series) -> bool:
    """ADF test via statsmodels. Stationary if p-value < 0.05."""
    if len(daily) < 20:
        return True  # te kort om uitspraak te doen, neem aan ja
    try:
        from statsmodels.tsa.stattools import adfuller
        result = adfuller(daily.values, autolag="AIC")
        return bool(result[1] < 0.05)
    except Exception:
        return True


def profile_data(df: pd.DataFrame, time_col: str, value_col: str) -> DataProfile:
    daily = _aggregate_daily(df, time_col, value_col)
    seasonality = _detect_seasonality(daily)
    slope = _trend_slope(daily)
    return DataProfile(
        n_observations=len(df),
        n_days=len(daily),
        seasonality_period=seasonality,
        has_trend=abs(slope) > 0.005,  # > 0.5% per dag relatieve drift
        trend_slope=slope,
        is_stationary=_is_stationary(daily),
        daily_mean=float(daily.mean()),
        daily_std=float(daily.std()),
    )
