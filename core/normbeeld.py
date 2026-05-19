"""Normbeeld-berekening per locatie (en optioneel per categorie).

Functies:
- compute_normbeeld(df, location, category, horizon_days, methods, aggregation)
- compute_all_normbeelds(df, ...)
- detect_recent_alerts(...)

Het normbeeld is het centrale data-object van de tool: verwachte waarde per
periode + tolerantieband, gebouwd uit één of meerdere voorspelmethoden,
gladgestreken om individuele uitschieters niet te volgen.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


DAY_NAMES = [
    "maandag", "dinsdag", "woensdag", "donderdag",
    "vrijdag", "zaterdag", "zondag",
]

# Beschikbare voorspelmethoden voor in de UI.
PREDICTION_METHODS = {
    "stl":            "STL (trend + seizoen)",
    "ets":            "Exponential Smoothing (Holt-Winters)",
    "rolling":        "Voortschrijdend gemiddelde",
    "seasonal_naive": "Seasonal naive",
    "median":         "Mediaan (vlak)",
}

# Korte uitleg per methode — gebruikt in info-paneel naast keuze.
PREDICTION_METHOD_DETAILS = {
    "stl": {
        "summary": (
            "Splitst de data in trend, seizoenspatroon en rest. "
            "Voorspelt verder door trend en seizoen voorwaarts te projecteren."
        ),
        "good_for": (
            "Lange tijdreeksen (≥3 perioden) met duidelijk wekelijks of "
            "maandelijks patroon én een meebewegende trend."
        ),
        "not_good_for": (
            "Korte reeksen, of data zonder herhalend patroon. "
            "Kan instabiel zijn bij sterke uitschieters."
        ),
        "technical": "Seasonal-Trend decomposition using LOESS (Cleveland 1990).",
    },
    "ets": {
        "summary": (
            "Standaard forecasting in BI-tools. Geeft recent gewicht zwaarder "
            "dan oud, met optionele trend- en seizoens-componenten."
        ),
        "good_for": (
            "Bijna alle business-tijdreeksen. Robuust, weinig parameters, "
            "stabieler dan STL bij rumoerige data."
        ),
        "not_good_for": (
            "Heel korte reeksen (<10 punten). "
            "Mist scherpe events die ARIMA wel zou pakken."
        ),
        "technical": "Holt-Winters Exponential Smoothing (statsmodels).",
    },
    "rolling": {
        "summary": (
            "Voorspelt met het gemiddelde van de afgelopen N periodes. "
            "Volgt de recente werkelijkheid, geen trend-extrapolatie."
        ),
        "good_for": (
            "Stabiele reeksen zonder duidelijke trend of seizoen. "
            "Snel en zonder modelaannames."
        ),
        "not_good_for": (
            "Reeksen met seizoenspatroon (mist het) "
            "of sterke trend (loopt achter)."
        ),
        "technical": "Centered rolling mean, window ~7 perioden.",
    },
    "seasonal_naive": {
        "summary": (
            "Voorspelt door simpelweg dezelfde periode een seizoen terug "
            "te herhalen (bv. maandag = maandag-vorige-week)."
        ),
        "good_for": (
            "Sterk seizoensgebonden data zonder noemenswaardige trend. "
            "Verrassend goede baseline."
        ),
        "not_good_for": (
            "Data met trend (mist die volledig) "
            "of zonder herhalend patroon."
        ),
        "technical": "Naive forecast met seizoens-shift.",
    },
    "median": {
        "summary": (
            "Vlakke voorspelling op basis van de mediaan van alle data. "
            "Robuust voor uitschieters."
        ),
        "good_for": (
            "Hele korte reeksen waar geen andere methode betrouwbaar is, "
            "of als baseline-vergelijking."
        ),
        "not_good_for": (
            "Data met enige trend of seizoen — wordt volledig genegeerd."
        ),
        "technical": "Median + Median Absolute Deviation (MAD) band.",
    },
}

AGGREGATIONS = {
    "daily":   ("D",  "dag",   "dagen"),
    "weekly":  ("W",  "week",  "weken"),
    "monthly": ("MS", "maand", "maanden"),
}


@dataclass
class Normbeeld:
    location: str
    category: str | None
    aggregation: str                  # 'daily' / 'weekly' / 'monthly'
    n_history_periods: int
    expected_value: float
    lower_band: float
    upper_band: float
    confidence: str                   # 'hoog' / 'midden' / 'laag'
    pattern_description: str
    historical: pd.DataFrame          # date, actual, expected, lower, upper, status
    forecast: pd.DataFrame            # date, expected, lower, upper (ensemble)
    n_recent_deviations: int          # afwijkingen laatste 14 periodes
    methods_used: list[str]           # gebruikte methode-sleutels
    methods_requested: list[str]      # wat de gebruiker vroeg
    methods_skipped: list[str]        # gevraagd maar niet uitgevoerd (met reden)
    per_method_forecast: dict         # method_key -> DataFrame(date, expected)
    per_method_historical: dict       # method_key -> Series(expected) op hist-index

    @property
    def n_history_days(self) -> int:  # backward compat
        return self.n_history_periods

    @property
    def method_used(self) -> str:     # backward compat (één label)
        return ", ".join(PREDICTION_METHODS.get(m, m) for m in self.methods_used)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _aggregate(df: pd.DataFrame, freq: str) -> pd.Series:
    s = df.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"])
    return s.set_index("timestamp")["value"].resample(freq).sum().fillna(0)


def _detect_period(series: pd.Series, agg: str) -> int | None:
    """Periode-detectie via autocorrelatie. Voor weekly/monthly minder zinvol."""
    if agg != "daily":
        return None
    n = len(series)
    if n < 28:
        return None
    x = series.values.astype(float) - series.values.mean()
    if x.std() == 0:
        return None
    max_lag = min(60, n // 3)
    best_lag, best_corr = None, 0.0
    for lag in range(2, max_lag + 1):
        a = x[:-lag]
        b = x[lag:]
        denom = np.sqrt((a * a).sum() * (b * b).sum())
        if denom <= 0:
            continue
        corr = float((a * b).sum() / denom)
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    return best_lag if best_corr > 0.25 else None


def _describe_pattern(
    series: pd.Series, period: int | None, expected: float, agg: str
) -> str:
    unit = AGGREGATIONS[agg][1]
    parts: list[str] = []

    # 1. Bepaal trend EERST (overschrijft "stabiel" als er drift is)
    trend_phrase: str | None = None
    if len(series) >= 14:
        first_half = series.iloc[:len(series) // 2].mean()
        second_half = series.iloc[len(series) // 2:].mean()
        if first_half > max(0.5, second_half * 0.01):
            drift = (second_half - first_half) / first_half
            if drift > 5:
                trend_phrase = f"Sterk gegroeid (>{int(drift)}× over de periode)."
            elif drift > 0.5:
                trend_phrase = f"Sterk stijgend (+{drift * 100:.0f}% over de periode)."
            elif drift > 0.2:
                trend_phrase = f"Lichte stijging (+{drift * 100:.0f}%)."
            elif drift < -0.5:
                trend_phrase = "Sterk gedaald."
            elif drift < -0.2:
                trend_phrase = f"Lichte daling ({drift * 100:.0f}%)."
        elif first_half < 0.5 and second_half > 1:
            trend_phrase = "Van bijna nul naar regelmatige waarnemingen."

    # 2. Niveau-beschrijving (alleen "stabiel" als er GEEN trend is)
    if expected < 0.5:
        parts.append(f"Zeer rustig: gemiddeld <1 per {unit}.")
    elif trend_phrase is None:
        if expected < 2:
            parts.append(f"Rustig: gemiddeld {expected:.1f} per {unit}.")
        else:
            parts.append(f"Stabiel rond {expected:.1f} per {unit}.")
    else:
        # Bij een trend: noem het recente niveau, niet "stabiel"
        parts.append(f"Recent niveau ongeveer {expected:.1f} per {unit}.")

    # 3. Trend-zin (als aanwezig)
    if trend_phrase:
        parts.append(trend_phrase)

    # 4. Wekelijks patroon (alleen bij dagelijkse aggregatie + periode 7)
    if agg == "daily" and period == 7 and len(series) >= 14:
        dow = pd.Series(series.values, index=series.index).groupby(
            series.index.dayofweek
        ).mean()
        if len(dow) >= 7:
            highest = int(dow.idxmax())
            lowest = int(dow.idxmin())
            diff_pct = (dow.max() - dow.min()) / max(dow.mean(), 1e-6)
            if diff_pct > 0.2:
                parts.append(
                    f"Wekelijks patroon: {DAY_NAMES[highest]}en drukker, "
                    f"{DAY_NAMES[lowest]}en rustiger."
                )

    return " ".join(parts)


def _confidence(n_periods: int, period_detected: bool) -> str:
    if n_periods >= 60 and period_detected:
        return "hoog"
    if n_periods >= 30:
        return "midden"
    return "laag"


def _suggest_best_aggregation(df: pd.DataFrame) -> str:
    """Suggereer de beste aggregatie op basis van data-eigenschappen.

    Logica:
    - Korte tijdspanne (<60 dagen) of dichte data → daily
    - Lange tijdspanne (>365 dagen) en regelmatig → monthly
    - Anders → weekly
    """
    if df.empty or "timestamp" not in df.columns:
        return "daily"
    ts = pd.to_datetime(df["timestamp"])
    days = (ts.max() - ts.min()).days
    if days < 60:
        return "daily"
    if days > 365:
        return "monthly"
    if days > 120:
        return "weekly"
    return "daily"


# ---------------------------------------------------------------------------
# Forecast-methoden (returnen 4 reeksen per index: expected, lower, upper, std)
# ---------------------------------------------------------------------------
def _stl_forecast(series: pd.Series, period: int, horizon: int):
    from statsmodels.tsa.seasonal import STL
    stl = STL(series, period=period, robust=True).fit()
    trend = stl.trend
    seasonal = stl.seasonal
    resid = stl.resid
    expected_hist = (trend + seasonal).clip(lower=0).values
    std = float(np.std(resid))

    # Forecast
    look = min(14, len(trend))
    x = np.arange(look)
    y = trend.iloc[-look:].values
    if np.std(y) > 1e-6:
        slope, intercept = np.polyfit(x, y, 1)
    else:
        slope, intercept = 0.0, float(y[-1])
    seasonal_last = seasonal.iloc[-period:].values
    future_expected = np.maximum(
        intercept + slope * (np.arange(horizon) + look)
        + np.array([seasonal_last[(i + 1) % period] for i in range(horizon)]),
        0,
    )
    return expected_hist, future_expected, std


def _rolling_forecast(series: pd.Series, horizon: int):
    w = min(7, max(2, len(series) // 3))
    rolling_mean = series.rolling(window=w, min_periods=2).mean().bfill()
    std = float(series.std() or 0.0)
    expected_hist = rolling_mean.values
    future_expected = np.full(horizon, float(rolling_mean.iloc[-1]))
    return expected_hist, future_expected, std


def _seasonal_naive_forecast(series: pd.Series, period: int, horizon: int):
    # Verwachting per index = waarde één periode geleden, gemiddeld over n periodes.
    if len(series) < 2 * period:
        return None
    expected_hist = series.shift(period).bfill().values
    # Forecast: laatste periode herhalen
    last_period = series.iloc[-period:].values
    future_expected = np.array([last_period[i % period] for i in range(horizon)])
    resid = series.values[period:] - expected_hist[period:]
    std = float(np.std(resid))
    return expected_hist, future_expected, std


def _median_forecast(series: pd.Series, horizon: int):
    median = float(np.median(series.values))
    mad = float(np.median(np.abs(series.values - median)))
    std = max(1.5 * mad, 1.0)
    expected_hist = np.full(len(series), median)
    future_expected = np.full(horizon, median)
    return expected_hist, future_expected, std


def _ets_forecast(series: pd.Series, period: int, horizon: int):
    """Exponential Smoothing / Holt-Winters via statsmodels. Standaard
    forecasting-methode in de meeste BI-tools."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    # Statsmodels Holt-Winters vereist > 2 * period datapunten voor seasonal.
    n = len(series)
    use_seasonal = period and n >= 2 * period + 1
    try:
        model = ExponentialSmoothing(
            series.astype(float),
            trend="add",
            seasonal="add" if use_seasonal else None,
            seasonal_periods=period if use_seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
    except Exception:
        # Fallback zonder seasonal als optimalisatie faalt
        try:
            model = ExponentialSmoothing(
                series.astype(float), trend="add",
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True)
        except Exception:
            return None

    expected_hist = np.clip(fit.fittedvalues.values, 0, None)
    future_expected = np.clip(fit.forecast(horizon).values, 0, None)
    resid = series.values - expected_hist
    std = float(np.std(resid))
    return expected_hist, future_expected, std


# ---------------------------------------------------------------------------
# Combine + smooth
# ---------------------------------------------------------------------------
def _combine_predictions(predictions: list[tuple], smooth_window: int = 3):
    """Gemiddeld de verwachte waarden, neem de mediaan van de standaarddeviaties,
    en streek alles glad met een rolling mean."""
    if not predictions:
        return None

    expected_hists = np.array([p[0] for p in predictions])
    future_expecteds = np.array([p[1] for p in predictions])
    stds = np.array([p[2] for p in predictions])

    expected_hist = expected_hists.mean(axis=0)
    future_expected = future_expecteds.mean(axis=0)
    # Iets conservatievere band: max-std bij meerdere methodes vermindert misleiding
    combined_std = float(np.max(stds)) if len(stds) > 1 else float(stds[0])

    # Smoothing: rolling mean over de verwachte lijn
    if smooth_window > 1 and len(expected_hist) > smooth_window:
        kernel = np.ones(smooth_window) / smooth_window
        # 'same' size convolve met edge-padding
        padded = np.pad(expected_hist, (smooth_window // 2, smooth_window // 2),
                        mode="edge")
        expected_hist = np.convolve(padded, kernel, mode="valid")
        if len(expected_hist) > len(expected_hists[0]):
            expected_hist = expected_hist[:len(expected_hists[0])]
        elif len(expected_hist) < len(expected_hists[0]):
            expected_hist = np.pad(
                expected_hist,
                (0, len(expected_hists[0]) - len(expected_hist)),
                mode="edge",
            )

    return expected_hist, future_expected, combined_std


# ---------------------------------------------------------------------------
# Method selection
# ---------------------------------------------------------------------------
def _auto_select_methods(series: pd.Series, period: int | None) -> list[str]:
    """Default-keuze als de gebruiker niets specificeert.

    Logica gebaseerd op standaard forecasting-best-practices:
    - Lange seizoensgebonden reeks (>= 2 perioden): STL + ETS + seasonal naive
    - Lange niet-seizoensgebonden reeks: ETS + rolling
    - Korte reeks (>= 14): rolling + median
    - Heel korte reeks: median + rolling
    """
    n = len(series)
    methods: list[str] = []
    has_season = period and n >= 2 * period + 1

    if has_season and n >= 21:
        methods += ["stl", "ets", "seasonal_naive"]
    elif n >= 21:
        methods += ["ets", "rolling"]
    elif n >= 14:
        methods += ["rolling", "median"]
    else:
        methods += ["median"]
        if n >= 7:
            methods.append("rolling")
    return methods


# ---------------------------------------------------------------------------
# Hoofd-API
# ---------------------------------------------------------------------------
def compute_normbeeld(
    df: pd.DataFrame,
    location: str | None = None,
    category: str | None = None,
    horizon_days: int = 14,
    methods: list[str] | None = None,
    aggregation: str = "daily",
) -> Normbeeld | None:
    work = df.copy()
    if location is not None and "location_name" in work.columns:
        work = work[work["location_name"] == location]
    if category is not None and "category" in work.columns:
        work = work[work["category"] == category]
    if len(work) < 3:
        return None

    freq = AGGREGATIONS.get(aggregation, AGGREGATIONS["daily"])[0]
    series = _aggregate(work, freq)
    if len(series) < 5:
        return None

    period = _detect_period(series, aggregation)

    methods_requested: list[str] = []
    if methods is None:
        methods = _auto_select_methods(series, period)
    methods = [m for m in methods if m in PREDICTION_METHODS]
    if not methods:
        methods = _auto_select_methods(series, period)
    methods_requested = list(methods)

    # Fallback periode: als geen detectie maar gebruiker vraagt STL/seasonal naive,
    # gebruik 7 (dag-aggregatie) of 4 (week) of 12 (maand) als default-aanname.
    fallback_period = {
        "daily": 7, "weekly": 4, "monthly": 12,
    }.get(aggregation, 7)
    use_period = period if period else fallback_period

    predictions: list[tuple] = []
    used_methods: list[str] = []
    skipped: list[str] = []
    per_method_predictions: dict[str, tuple] = {}

    for m in methods:
        pred = None
        try:
            if m == "stl":
                if len(series) >= 2 * use_period + 1 and len(series) >= 14:
                    pred = _stl_forecast(series, use_period, horizon_days)
            elif m == "ets":
                if len(series) >= 10:
                    pred = _ets_forecast(series, use_period, horizon_days)
            elif m == "rolling":
                pred = _rolling_forecast(series, horizon_days)
            elif m == "seasonal_naive":
                if len(series) >= 2 * use_period:
                    pred = _seasonal_naive_forecast(series, use_period, horizon_days)
            elif m == "median":
                pred = _median_forecast(series, horizon_days)
        except Exception:
            pred = None
        if pred is not None:
            predictions.append(pred)
            used_methods.append(m)
            per_method_predictions[m] = pred
        else:
            skipped.append(m)

    if not predictions:
        pred = _median_forecast(series, horizon_days)
        predictions.append(pred)
        used_methods.append("median")
        per_method_predictions["median"] = pred

    combined = _combine_predictions(predictions, smooth_window=3)
    if combined is None:
        return None
    expected_hist, future_expected, std = combined

    # Smooth de band ook (band volgt minder de data)
    band = 2.0 * std

    hist = pd.DataFrame({
        "date":     series.index,
        "actual":   series.values,
        "expected": expected_hist,
        "lower":    np.clip(expected_hist - band, 0, None),
        "upper":    expected_hist + band,
    })

    # Future dates: zelfde freq als historie
    if aggregation == "monthly":
        future_idx = pd.date_range(
            start=series.index[-1] + pd.offsets.MonthBegin(1),
            periods=horizon_days, freq="MS",
        )
    elif aggregation == "weekly":
        future_idx = pd.date_range(
            start=series.index[-1] + pd.Timedelta(days=7),
            periods=horizon_days, freq="W",
        )
    else:
        future_idx = pd.date_range(
            start=series.index[-1] + pd.Timedelta(days=1),
            periods=horizon_days, freq="D",
        )

    forecast = pd.DataFrame({
        "date":     future_idx,
        "expected": future_expected,
        "lower":    np.clip(future_expected - band, 0, None),
        "upper":    future_expected + band,
    })

    # Per-methode reeksen voor visualisatie
    per_method_forecast: dict = {}
    per_method_historical: dict = {}
    for m, p in per_method_predictions.items():
        m_hist, m_future, _ = p
        per_method_forecast[m] = pd.DataFrame({
            "date":     future_idx,
            "expected": np.clip(m_future, 0, None),
        })
        per_method_historical[m] = pd.Series(
            np.clip(m_hist, 0, None), index=series.index,
        )

    hist["status"] = "normaal"
    hist.loc[hist["actual"] > hist["upper"], "status"] = "boven"
    hist.loc[hist["actual"] < hist["lower"], "status"] = "onder"

    # `expected_value` representeert het HUIDIGE normbeeld — niet het gemiddelde
    # over alle historie (dat is misleidend bij trends). Gebruik laatste 25%
    # van de historie, minimaal 3 periodes.
    tail_n = max(3, len(hist) // 4)
    expected_value = float(hist["expected"].tail(tail_n).mean())
    lower_band = float(hist["lower"].tail(tail_n).mean())
    upper_band = float(hist["upper"].tail(tail_n).mean())

    n_recent_dev = int((hist.tail(14)["status"] != "normaal").sum())

    return Normbeeld(
        location=location or "Alle locaties",
        category=category,
        aggregation=aggregation,
        n_history_periods=len(series),
        expected_value=expected_value,
        lower_band=lower_band,
        upper_band=upper_band,
        confidence=_confidence(len(series), period is not None),
        pattern_description=_describe_pattern(
            series, period, expected_value, aggregation
        ),
        historical=hist,
        forecast=forecast,
        n_recent_deviations=n_recent_dev,
        methods_used=used_methods,
        methods_requested=methods_requested,
        methods_skipped=skipped,
        per_method_forecast=per_method_forecast,
        per_method_historical=per_method_historical,
    )


def compute_all_normbeelds(
    df: pd.DataFrame,
    horizon_days: int = 14,
    methods: list[str] | None = None,
    aggregation: str = "daily",
    min_rows_per_location: int = 5,
    max_locations: int = 50,
) -> dict[str, Normbeeld]:
    """Bereken normbeelden voor elke locatie met genoeg data.

    Performance: skipt locaties met < min_rows_per_location waarnemingen
    (geen zinvol normbeeld mogelijk), en cap totaal aantal locaties op
    max_locations (top by row count). Dit voorkomt minuten wachten op
    grote datasets met veel one-off locaties.
    """
    if "location_name" not in df.columns or df["location_name"].isna().all():
        nb = compute_normbeeld(
            df, horizon_days=horizon_days,
            methods=methods, aggregation=aggregation,
        )
        return {"Alle locaties": nb} if nb else {}

    # Tel rijen per locatie, sorteer aflopend, neem top N met >= min_rows
    counts = df["location_name"].value_counts()
    counts = counts[counts >= min_rows_per_location].head(max_locations)
    locations = list(counts.index)

    out: dict[str, Normbeeld] = {}
    for loc in locations:
        nb = compute_normbeeld(
            df, location=loc, horizon_days=horizon_days,
            methods=methods, aggregation=aggregation,
        )
        if nb is not None:
            out[loc] = nb
    return out


_RECENT_WINDOW_DAYS = {"daily": 14, "weekly": 56, "monthly": 180}
_RECENT_WINDOW_LABEL = {
    "daily": "14 dagen", "weekly": "8 weken", "monthly": "6 maanden",
}


def recent_window_label(aggregation: str) -> str:
    return _RECENT_WINDOW_LABEL.get(aggregation, "14 dagen")


def detect_recent_alerts(
    normbeelds: dict[str, Normbeeld],
    aggregation: str = "daily",
) -> list[dict]:
    """Geef recente afwijkingen terug, op basis van het laatste datapunt in de
    dataset (niet 'vandaag'). Window-grootte schaalt met aggregatie."""
    days_back = _RECENT_WINDOW_DAYS.get(aggregation, 14)
    alerts: list[dict] = []
    for loc, nb in normbeelds.items():
        if nb.historical.empty:
            continue
        last_date = pd.Timestamp(nb.historical["date"].max())
        cutoff = last_date - pd.Timedelta(days=days_back)
        recent = nb.historical[nb.historical["date"] >= cutoff]
        for _, row in recent.iterrows():
            if row["status"] != "normaal":
                alerts.append({
                    "datum": pd.Timestamp(row["date"]).date().isoformat(),
                    "locatie": loc,
                    "waarde": int(row["actual"]),
                    "verwacht": float(row["expected"]),
                    "lower": float(row["lower"]),
                    "upper": float(row["upper"]),
                    "richting": row["status"],
                })
    alerts.sort(key=lambda a: a["datum"], reverse=True)
    return alerts
