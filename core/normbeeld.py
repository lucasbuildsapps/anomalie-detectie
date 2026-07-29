"""Normbeeld-berekening per locatie (en optioneel per categorie).

Publieke API:
- compute_normbeeld(df, location, category, horizon_days, methods, aggregation, select)
- compute_all_normbeelds(df, ...)
- backtest_all_methods(series, period, horizon)
- detect_recent_alerts(...)

Het normbeeld is het centrale data-object: verwachte waarde per periode +
tolerantieband. Banden zijn asymmetrisch en quantile-gebaseerd (recente
residuen wegen zwaarder), zodat de ondergrens niet zinloos op 0 hangt bij
scheve count-data. Methode-selectie kan heuristisch (snel, voor overzichten)
of via backtest (rigoureus, voor de detail-weergave).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.logging_setup import get_logger

_logger = get_logger("normbeeld")

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
    "hourly":  ("h",  "uur",   "uren"),
    "daily":   ("D",  "dag",   "dagen"),
    "weekly":  ("W",  "week",  "weken"),
    "monthly": ("MS", "maand", "maanden"),
}

# Gap-policy: wat betekent een periode zonder waarnemingen?
GAP_POLICIES = {
    "zero":        "Geen rapport = geen activiteit (waarde 0)",
    "interpolate": "Gat = ontbrekende collectie, schat tussenliggende waarde",
    "mask":        "Gat = onbekend; niet meenemen in baseline of afwijkingen",
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
    methods_skipped: list[str]        # gevraagd maar niet uitgevoerd
    per_method_forecast: dict         # method_key -> DataFrame(date, expected)
    per_method_historical: dict       # method_key -> Series(expected) op hist-index
    skip_reasons: dict = field(default_factory=dict)   # method_key -> reden
    backtest_scores: dict | None = None    # method_key -> gem. fout % (sMAPE)
    backtest_error: float | None = None    # fout % van beste methode (indicatie)
    band_alpha: float | None = None        # gebruikte quantile-tail (bv. 0.02)
    band_coverage: float | None = None     # fractie historie binnen de band
    widening_source: str | None = None     # 'backtest' of 'default'
    band_model: str = "quantile"           # 'quantile' / 'poisson' / 'negbin'
    dispersion: float | None = None        # Pearson-dispersie (count-band)

    @property
    def n_history_days(self) -> int:  # backward compat
        return self.n_history_periods

    @property
    def method_used(self) -> str:     # backward compat (één label)
        return ", ".join(PREDICTION_METHODS.get(m, m) for m in self.methods_used)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _aggregate(
    df: pd.DataFrame, freq: str, gap_policy: str = "zero",
) -> tuple[pd.Series, pd.Series]:
    """Aggregeer naar (waarde-reeks, observed-mask).

    `observed` geeft per bucket aan of er daadwerkelijk waarnemingen waren.
    Gap-policy bepaalt hoe lege buckets in de reeks terechtkomen:
    - zero: 0 (geen rapport = geen activiteit) — default voor event-data
    - interpolate: lineair geschat (gat = collectie-uitval, activiteit liep door)
    - mask: geschat vóór het modelfitten, maar uitgesloten van band-berekening
      en nooit als afwijking geflagd (waarheid onbekend)
    """
    s = df.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"])
    grouped = s.set_index("timestamp")["value"].resample(freq)
    sums = grouped.sum()
    observed = grouped.count() > 0

    if gap_policy in ("interpolate", "mask") and observed.any():
        out = sums.where(observed).interpolate(limit_direction="both").fillna(0)
    else:
        out = sums.fillna(0)

    # Drop incomplete trailing bucket bij week/maand-aggregatie: als de data
    # halverwege de periode stopt, lijkt de laatste bucket kunstmatig laag en
    # genereert hij valse "onder band"-afwijkingen.
    if len(out) >= 3:
        data_max = s["timestamp"].max()
        if freq == "MS":
            bucket_end = out.index[-1] + pd.offsets.MonthEnd(1)
            if data_max < bucket_end - pd.Timedelta(days=2):
                out = out.iloc[:-1]
        elif freq == "W":
            # 'W'-labels liggen op het einde van de week
            if data_max < out.index[-1] - pd.Timedelta(days=1):
                out = out.iloc[:-1]

    observed = observed.reindex(out.index).fillna(False)
    return out, observed


def _autocorr_at(series: pd.Series, lag: int) -> float:
    """Autocorrelatie op één specifieke lag (0 bij te weinig data)."""
    x = series.values.astype(float) - series.values.mean()
    if len(x) <= lag or x.std() == 0:
        return 0.0
    a, b = x[:-lag], x[lag:]
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def _detect_period(series: pd.Series, agg: str) -> int | None:
    """Periode-detectie via autocorrelatie, per aggregatie-niveau.

    - daily: vrije zoektocht (lags 2-60) — vindt week/maand-ritmes.
    - monthly: check specifiek lag 12 (jaarcyclus), vereist >= 25 maanden.
    - weekly: check lag 52 (jaarcyclus), vereist >= 110 weken.
    """
    n = len(series)
    if agg == "monthly":
        if n >= 25 and _autocorr_at(series, 12) > 0.3:
            return 12
        return None
    if agg == "weekly":
        if n >= 110 and _autocorr_at(series, 52) > 0.3:
            return 52
        return None
    if agg == "hourly":
        # Dag-ritme (24u) is verreweg het gangbaarst op uur-niveau
        if n >= 50 and _autocorr_at(series, 24) > 0.3:
            return 24
        return None

    if n < 28:
        return None
    x = series.values.astype(float) - series.values.mean()
    if x.std() == 0:
        return None
    max_lag = min(60, n // 3)
    best_lag, best_corr = None, 0.0
    for lag in range(2, max_lag + 1):
        corr = _autocorr_at(series, lag)
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
        parts.append(f"Recent niveau ongeveer {expected:.1f} per {unit}.")

    if trend_phrase:
        parts.append(trend_phrase)

    # 3. Wekelijks patroon (alleen bij dagelijkse aggregatie + periode 7)
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


def _weighted_quantile(values: np.ndarray, q: float, weights: np.ndarray) -> float:
    """Gewogen quantile via cumulatieve gewichten + interpolatie."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    sorter = np.argsort(values)
    v = values[sorter]
    w = weights[sorter]
    cw = np.cumsum(w)
    if cw[-1] <= 0:
        return float(np.quantile(values, q))
    cw = cw / cw[-1]
    return float(np.interp(q, cw, v))


# ---------------------------------------------------------------------------
# Forecast-methoden (returnen expected_hist, future_expected, std)
# ---------------------------------------------------------------------------
def _stl_forecast(series: pd.Series, period: int, horizon: int):
    from statsmodels.tsa.seasonal import STL
    stl = STL(series, period=period, robust=True).fit()
    trend = stl.trend
    seasonal = stl.seasonal
    resid = stl.resid
    expected_hist = (trend + seasonal).values
    std = float(np.std(resid))

    look = min(14, len(trend))
    x = np.arange(look)
    y = trend.iloc[-look:].values
    if np.std(y) > 1e-6:
        slope, intercept = np.polyfit(x, y, 1)
    else:
        slope, intercept = 0.0, float(y[-1])

    # Fase-uitlijning: seasonal_last[j] hoort bij absolute positie n-period+j,
    # dus fase (n+j) mod period. Toekomstige stap i (positie n+i) heeft
    # dezelfde fase als seasonal_last[i % period] — NIET (i+1) % period.
    seasonal_last = seasonal.iloc[-period:].values
    future_expected = (
        intercept + slope * (np.arange(horizon) + look)
        + np.array([seasonal_last[i % period] for i in range(horizon)])
    )
    return expected_hist, future_expected, std


def _ets_forecast(series: pd.Series, period: int, horizon: int):
    """Exponential Smoothing / Holt-Winters via statsmodels."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

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
        model = ExponentialSmoothing(
            series.astype(float), trend="add",
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)

    expected_hist = fit.fittedvalues.values
    future_expected = fit.forecast(horizon).values
    resid = series.values - expected_hist
    std = float(np.std(resid))
    return expected_hist, future_expected, std


def _rolling_forecast(series: pd.Series, horizon: int):
    w = min(7, max(2, len(series) // 3))
    # Verwachting op t gebruikt alleen data VÓÓR t (shift), anders dempt een
    # spike zijn eigen detectie (leakage).
    shifted = series.shift(1)
    rolling_mean = shifted.rolling(window=w, min_periods=1).mean()
    rolling_mean.iloc[0] = float(series.iloc[0])  # geen verleden op t=0
    std = float(series.std() or 0.0)
    expected_hist = rolling_mean.values
    # Forecast mag wél alle bekende data gebruiken (laatste w punten).
    future_expected = np.full(horizon, float(series.tail(w).mean()))
    return expected_hist, future_expected, std


def _seasonal_naive_forecast(series: pd.Series, period: int, horizon: int):
    expected_hist = series.shift(period).bfill().values
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


def _forecast_with(
    method: str, series: pd.Series, period: int, horizon: int
) -> tuple[tuple | None, str | None]:
    """Dispatcher. Returnt (prediction, None) of (None, reden-van-skip).

    Clipt voorspellingen op 0 alléén als de invoerdata niet-negatief is
    (tellingen). Bij data die negatief kan zijn (temperaturen, delta's)
    blijven negatieve voorspellingen geldig.
    """
    n = len(series)
    try:
        if method == "stl":
            if n < 2 * period + 1 or n < 14:
                return None, "te weinig data voor STL"
            pred = _stl_forecast(series, period, horizon)
        elif method == "ets":
            if n < 10:
                return None, "te weinig data voor Holt-Winters (<10 punten)"
            pred = _ets_forecast(series, period, horizon)
        elif method == "rolling":
            pred = _rolling_forecast(series, horizon)
        elif method == "seasonal_naive":
            if n < 2 * period:
                return None, "te weinig data voor seasonal naive (<2 perioden)"
            pred = _seasonal_naive_forecast(series, period, horizon)
        elif method == "median":
            pred = _median_forecast(series, horizon)
        else:
            return None, f"onbekende methode '{method}'"
    except Exception as e:
        return None, f"berekening faalde ({type(e).__name__})"

    if bool(series.min() >= 0):  # count-achtige data: geen negatieve forecast
        pred = (
            np.clip(np.asarray(pred[0], dtype=float), 0, None),
            np.clip(np.asarray(pred[1], dtype=float), 0, None),
            pred[2],
        )
    return pred, None


# ---------------------------------------------------------------------------
# Backtest (rolling origin)
# ---------------------------------------------------------------------------
def _backtest_method(
    series: pd.Series, method: str, period: int, horizon: int,
    n_folds: int = 4, max_points: int = 400,
    return_step_errors: bool = False,
):
    """Gemiddelde voorspelfout (%) van één methode via rolling-origin backtest.

    Houdt per fold `horizon` punten achter, traint op de rest, vergelijkt.
    Fout-metriek: |voorspeld - werkelijk| / max(|werkelijk|, 1) — robuust
    voor nullen, vergelijkbaar over locaties. Test op max. de laatste
    `max_points` punten zodat het recente regime telt én ETS snel blijft.

    4 folds (was 2): methode-selectie op 2 folds bleek instabiel — één
    afwijkende fold kon de 'beste' methode laten omslaan. Folds waarvoor
    te weinig historie overblijft (cutoff < 10) worden overgeslagen.

    Met `return_step_errors=True` returnt (score, per_stap_gem_abs_fout,
    per_stap_n) — de basis voor horizon-afhankelijke bandverbreding.
    """
    s = series.tail(max_points) if len(series) > max_points else series
    if len(s) < max(20, 2 * horizon + 10):
        return None
    errors: list[float] = []
    step_abs = np.zeros(horizon)
    step_n = np.zeros(horizon)
    for i in range(n_folds, 0, -1):
        cutoff = len(s) - i * horizon
        if cutoff < 10:
            continue
        train = s.iloc[:cutoff]
        actual = s.iloc[cutoff:cutoff + horizon].values.astype(float)
        pred, _ = _forecast_with(method, train, period, len(actual))
        if pred is None:
            return None
        future = np.asarray(pred[1], dtype=float)[:len(actual)]
        abs_err = np.abs(future - actual)
        denom = np.maximum(np.abs(actual), 1.0)
        errors.extend(abs_err / denom)
        k = len(actual)
        step_abs[:k] += abs_err
        step_n[:k] += 1
    if not errors:
        return None
    score = float(np.mean(errors) * 100)
    if not np.isfinite(score):
        return None
    if return_step_errors:
        steps = step_abs / np.maximum(step_n, 1)
        return score, steps, step_n
    return score


def backtest_all_methods(
    series: pd.Series, period: int, horizon: int,
) -> dict[str, float]:
    """Backtest alle voorspelmethoden; returnt {method_key: fout%}."""
    out: dict[str, float] = {}
    for m in PREDICTION_METHODS:
        err = _backtest_method(series, m, period, horizon)
        if err is not None:
            out[m] = err
    return out


def backtest_step_widening(
    series: pd.Series, methods: list[str], period: int, horizon: int,
) -> np.ndarray | None:
    """Horizon-afhankelijke bandverbredings-factoren uit de backtest.

    Voorspelonzekerheid groeit met de horizon; een band die op stap 14 even
    smal is als op stap 1 is te optimistisch. We meten dat empirisch: de
    gemiddelde absolute out-of-sample-fout per voorspelstap, genormaliseerd
    op stap 1. De factoren zijn monotoon niet-dalend gemaakt (cummax) en
    begrensd op 3x, zodat één rare fold de band niet opblaast.

    Returnt array met lengte `horizon` (allemaal >= 1), of None als geen
    enkele methode een bruikbaar stap-profiel oplevert.
    """
    profiles = []
    for m in methods:
        out = _backtest_method(series, m, period, horizon,
                               return_step_errors=True)
        if out is None:
            continue
        _score, steps, step_n = out
        if (step_n > 0).sum() < 2:
            continue
        base = max(float(steps[0]), 1e-9)
        prof = np.clip(steps / base, 1.0, 3.0)
        profiles.append(prof)
    if not profiles:
        return None
    w = np.mean(np.asarray(profiles), axis=0)
    return np.maximum.accumulate(np.clip(w, 1.0, 3.0))


# ---------------------------------------------------------------------------
# Combine + smooth
# ---------------------------------------------------------------------------
def _combine_predictions(predictions: list[tuple], smooth_window: int = 3,
                         weights: list[float] | None = None):
    """Combineer methode-voorspellingen tot één ensemble.

    Met `weights` (bv. 1/backtest-fout) telt een aantoonbaar betere methode
    zwaarder mee; zonder gewichten geldt het gewone gemiddelde.
    """
    if not predictions:
        return None
    expected_hists = np.array([p[0] for p in predictions])
    future_expecteds = np.array([p[1] for p in predictions])

    if weights is not None and len(weights) == len(predictions) \
            and np.sum(weights) > 0:
        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        expected_hist = (expected_hists * w[:, None]).sum(axis=0)
        future_expected = (future_expecteds * w[:, None]).sum(axis=0)
    else:
        expected_hist = expected_hists.mean(axis=0)
        future_expected = future_expecteds.mean(axis=0)

    if smooth_window > 1 and len(expected_hist) > smooth_window:
        kernel = np.ones(smooth_window) / smooth_window
        padded = np.pad(expected_hist, (smooth_window // 2, smooth_window // 2),
                        mode="edge")
        smoothed = np.convolve(padded, kernel, mode="valid")
        if len(smoothed) > len(expected_hist):
            smoothed = smoothed[:len(expected_hist)]
        elif len(smoothed) < len(expected_hist):
            smoothed = np.pad(
                smoothed, (0, len(expected_hist) - len(smoothed)), mode="edge"
            )
        expected_hist = smoothed

    return expected_hist, future_expected


def _quantile_band(
    series: pd.Series, expected_hist: np.ndarray,
    observed_mask: pd.Series | None = None,
) -> tuple[float, float, float]:
    """Asymmetrische band-offsets uit residual-quantiles met recency-weging.

    Returnt (q_lo, q_hi, alpha):
    - alpha schaalt met reekslengte: clip(5/n, 0.01, 0.10). Korte reeksen
      krijgen bredere tails (10%), lange reeksen smallere (1%), zodat het
      aantal historisch geflagde punten in beide gevallen werkbaar blijft.
    - Recente residuen wegen zwaarder (exponentieel, halfwaardetijd = n/3),
      zodat de band het huidige regime volgt en niet het hele verleden.
    """
    resid = series.values.astype(float) - np.asarray(expected_hist, dtype=float)
    n = len(resid)
    alpha = float(np.clip(5.0 / max(n, 1), 0.01, 0.10))

    half_life = max(10.0, n / 3.0)
    ages = np.arange(n, dtype=float)[::-1]  # 0 = nieuwste punt
    weights = np.power(0.5, ages / half_life)

    # Mask-policy: niet-geobserveerde buckets tellen niet mee in de band
    # (hun 'residu' is een artefact van interpolatie, geen werkelijkheid).
    if observed_mask is not None:
        mask_arr = np.asarray(observed_mask, dtype=float)
        if mask_arr.sum() >= 5:  # genoeg echte punten over
            weights = weights * mask_arr

    q_lo = _weighted_quantile(resid, alpha, weights)
    q_hi = _weighted_quantile(resid, 1.0 - alpha, weights)

    # Minimale bandbreedte: voorkom 0-brede band bij vlakke reeksen
    level = max(abs(float(np.median(series.values))), 1.0)
    min_width = max(1.0, 0.1 * level)
    if q_hi - q_lo < min_width:
        pad = (min_width - (q_hi - q_lo)) / 2.0
        q_lo -= pad
        q_hi += pad
    return q_lo, q_hi, alpha


def _is_low_count_series(series: pd.Series) -> bool:
    """True voor schaarse telling-data: niet-negatieve gehele aantallen met
    een laag typisch niveau. Voor zulke reeksen zijn residual-quantiles
    onbetrouwbaar (residuen nemen maar een handvol discrete waarden aan en
    de band wordt gedomineerd door een paar spikes)."""
    vals = series.values.astype(float)
    if len(vals) == 0 or np.nanmin(vals) < 0:
        return False
    if not np.allclose(vals, np.round(vals), atol=1e-9):
        return False
    return float(np.nanmedian(vals)) < 5.0


def _count_band(
    series: pd.Series, expected_hist: np.ndarray, alpha: float,
    observed_mask: pd.Series | None = None,
    phi_fixed: float | None = None,
) -> tuple[np.ndarray, np.ndarray, str, float]:
    """Discrete band voor telling-data: Poisson, of negatief-binomiaal bij
    overdispersie (variantie > gemiddelde, zoals bij geclusterde aanvallen).

    Per periode t met verwachting mu_t:
      lower_t = ppf(alpha, mu_t), upper_t = ppf(1 - alpha, mu_t)

    De dispersie phi wordt Pearson-geschat op de (recency-gewogen) historie:
      phi = gewogen gemiddelde van (y - mu)^2 / mu
    phi <= 1.3 -> Poisson; anders NB met var = phi * mu (r = mu / (phi - 1)).

    Returnt (lower, upper, model, phi) met arrays op de lengte van
    expected_hist. Zie METHODS.md §7.
    """
    from scipy import stats

    mu = np.clip(np.asarray(expected_hist, dtype=float), 0.1, None)
    y = series.values.astype(float)

    n = len(y)
    half_life = max(10.0, n / 3.0)
    ages = np.arange(n, dtype=float)[::-1]
    weights = np.power(0.5, ages / half_life)
    if observed_mask is not None:
        mask_arr = np.asarray(observed_mask, dtype=float)
        if mask_arr.sum() >= 5:
            weights = weights * mask_arr

    if phi_fixed is not None:
        # Forecast-pad: dispersie is op de historie gemeten en wordt hier
        # hergebruikt (op de voorspelling zelf zijn de residuen per
        # definitie nul, dus daar valt niets te schatten).
        phi = float(phi_fixed)
    else:
        pearson = (y - mu) ** 2 / mu
        phi = float(np.average(pearson, weights=weights)) if weights.sum() > 0 \
            else float(pearson.mean())
    phi = max(phi, 1.0)

    if phi <= 1.3:
        lower = stats.poisson.ppf(alpha, mu)
        upper = stats.poisson.ppf(1.0 - alpha, mu)
        model = "poisson"
    else:
        # NB2-parameterisatie: var = mu + mu^2/r = phi*mu  ->  r = mu/(phi-1)
        r = np.clip(mu / (phi - 1.0), 0.05, None)
        p = r / (r + mu)
        lower = stats.nbinom.ppf(alpha, r, p)
        upper = stats.nbinom.ppf(1.0 - alpha, r, p)
        model = "negbin"

    return lower.astype(float), np.maximum(upper, lower).astype(float), model, phi


# ---------------------------------------------------------------------------
# Method selection (heuristisch)
# ---------------------------------------------------------------------------
def _auto_select_methods(series: pd.Series, period: int | None) -> list[str]:
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
    select: str = "heuristic",  # 'heuristic' (snel) of 'backtest' (rigoureus)
    gap_policy: str = "zero",
) -> Normbeeld | None:
    work = df.copy()
    if location is not None and "location_name" in work.columns:
        work = work[work["location_name"] == location]
    if category is not None and "category" in work.columns:
        # category mag één waarde of een lijst (meerdere categorieën) zijn
        if isinstance(category, (list, tuple, set)):
            cats = list(category)
            if cats:
                work = work[work["category"].isin(cats)]
        else:
            work = work[work["category"] == category]
    if len(work) < 3:
        return None

    freq = AGGREGATIONS.get(aggregation, AGGREGATIONS["daily"])[0]
    series, observed = _aggregate(work, freq, gap_policy)
    if len(series) < 5:
        return None

    period = _detect_period(series, aggregation)
    fallback_period = {"hourly": 24, "daily": 7, "weekly": 4,
                       "monthly": 12}.get(aggregation, 7)
    use_period = period if period else fallback_period

    # --- Methode-selectie ---
    backtest_scores: dict[str, float] | None = None
    if methods is None and select == "backtest" and len(series) >= 20:
        bt_horizon = int(max(3, min(horizon_days, len(series) // 6)))
        scores = backtest_all_methods(series, use_period, bt_horizon)
        if scores:
            backtest_scores = scores
            methods = sorted(scores, key=scores.get)[:2]
    if methods is None:
        methods = _auto_select_methods(series, period)
    methods = [m for m in methods if m in PREDICTION_METHODS]
    if not methods:
        methods = _auto_select_methods(series, period)
    methods_requested = list(methods)

    # --- Voorspellen per methode ---
    predictions: list[tuple] = []
    used_methods: list[str] = []
    skipped: list[str] = []
    skip_reasons: dict[str, str] = {}
    per_method_predictions: dict[str, tuple] = {}

    for m in methods:
        pred, reason = _forecast_with(m, series, use_period, horizon_days)
        if pred is not None:
            predictions.append(pred)
            used_methods.append(m)
            per_method_predictions[m] = pred
        else:
            skipped.append(m)
            skip_reasons[m] = reason or "onbekend"

    if not predictions:
        pred, _ = _forecast_with("median", series, use_period, horizon_days)
        predictions.append(pred)
        used_methods.append("median")
        per_method_predictions["median"] = pred

    # Gewogen ensemble: bij backtest-scores telt een aantoonbaar betere
    # methode zwaarder mee (gewicht ~ 1/fout, +5pp demping tegen extremen).
    ens_weights = None
    if backtest_scores and all(m in backtest_scores for m in used_methods):
        ens_weights = [1.0 / (backtest_scores[m] + 5.0) for m in used_methods]

    combined = _combine_predictions(predictions, smooth_window=3,
                                    weights=ens_weights)
    if combined is None:
        return None
    expected_hist, future_expected = combined

    # Clip op 0 alleen voor niet-negatieve (count-achtige) data.
    nonneg = bool(series.min() >= 0)

    def _floor(arr):
        return np.clip(arr, 0, None) if nonneg else np.asarray(arr, dtype=float)

    # --- Band: discreet (telling-data) of quantile (continu/hoog niveau) ---
    # Voor schaarse gehele aantallen (mediaan < 5) zijn residual-quantiles
    # onbetrouwbaar; daar past een Poisson- of negatief-binomiaal-interval
    # rond de verwachting beter. Zie METHODS.md §7.
    obs_mask = observed if gap_policy == "mask" else None
    q_lo, q_hi, band_alpha = _quantile_band(series, expected_hist,
                                            observed_mask=obs_mask)
    band_model = "quantile"
    dispersion = None
    if _is_low_count_series(series):
        cb_lo, cb_hi, band_model, dispersion = _count_band(
            series, expected_hist, band_alpha, observed_mask=obs_mask,
        )
        hist_lower = cb_lo
        hist_upper = cb_hi
    else:
        # Invariant: upper >= lower, óók na flooring van alleen de ondergrens
        # (anders kan een pathologische fit de band ondersteboven zetten).
        hist_lower = _floor(expected_hist + q_lo)
        hist_upper = np.maximum(expected_hist + q_hi, hist_lower)

    hist = pd.DataFrame({
        "date":     series.index,
        "actual":   series.values,
        "expected": expected_hist,
        "lower":    hist_lower,
        "upper":    hist_upper,
    })

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

    # --- Horizon-verbreding: onzekerheid groeit met de voorspelafstand ---
    # Bij backtest-selectie meten we de groei empirisch (out-of-sample fout
    # per stap); anders een conservatieve default van +3% bandbreedte per
    # stap, gemaximeerd op 1.5x. Zonder verbreding zou de band op stap 14
    # even smal zijn als op stap 1 — aantoonbaar te optimistisch.
    widening = None
    widening_source = "default"
    if select == "backtest" and len(series) >= 20 and used_methods:
        widening = backtest_step_widening(
            series, used_methods, use_period, horizon_days
        )
        if widening is not None:
            widening_source = "backtest"
    if widening is None:
        widening = np.minimum(1.0 + 0.03 * np.arange(horizon_days), 1.5)

    if band_model in ("poisson", "negbin"):
        # Discreet interval per toekomstige verwachting; horizon-verbreding
        # schaalt de offsets rond mu (zelfde principe als bij quantiles).
        fb_lo, fb_hi, _, _ = _count_band(
            pd.Series(future_expected, index=future_idx),
            future_expected, band_alpha, phi_fixed=dispersion,
        )
        mu_f = np.asarray(future_expected, dtype=float)
        fc_lower = np.clip(mu_f - (mu_f - fb_lo) * widening, 0, None)
        fc_upper = np.maximum(mu_f + (fb_hi - mu_f) * widening, fc_lower)
    else:
        fc_lower = _floor(future_expected + q_lo * widening)
        fc_upper = np.maximum(future_expected + q_hi * widening, fc_lower)
    forecast = pd.DataFrame({
        "date":     future_idx,
        "expected": future_expected,
        "lower":    fc_lower,
        "upper":    fc_upper,
    })

    hist["status"] = "normaal"
    hist.loc[hist["actual"] > hist["upper"], "status"] = "boven"
    hist.loc[hist["actual"] < hist["lower"], "status"] = "onder"

    # Anomalie-percentiel: hoe extreem is dit punt t.o.v. de hele historie?
    # Empirische rang van het residu (0 = extreem laag, 1 = extreem hoog).
    # Voor 'boven'-punten lees je pctl, voor 'onder'-punten (1 - pctl).
    resid_all = series.values.astype(float) - np.asarray(expected_hist)
    ranks = np.argsort(np.argsort(resid_all))
    hist["resid_pctl"] = ranks / max(len(resid_all) - 1, 1)

    # Mask-policy: niet-geobserveerde buckets zijn geen afwijking maar
    # 'geen data' — de werkelijkheid daar is onbekend. In de grafiek tonen
    # we ze als gat (NaN) in plaats van een verzonnen waarde.
    if gap_policy == "mask" and (~observed).any():
        mask_idx = ~observed.values
        hist.loc[mask_idx, "status"] = "geen data"
        hist.loc[mask_idx, "actual"] = np.nan
        hist.loc[mask_idx, "resid_pctl"] = np.nan

    # Empirische banddekking: welk deel van de (geobserveerde) historie viel
    # binnen de band? Hoort dicht bij 1 - 2*alpha te liggen; een veel lagere
    # waarde betekent dat de band te smal is voor deze reeks.
    observed_hist = hist.dropna(subset=["actual"])
    band_coverage = None
    if len(observed_hist) >= 10:
        inside = (
            (observed_hist["actual"] >= observed_hist["lower"])
            & (observed_hist["actual"] <= observed_hist["upper"])
        )
        band_coverage = float(inside.mean())

    # `expected_value` = HUIDIG normbeeld (laatste 25% van historie)
    tail_n = max(3, len(hist) // 4)
    expected_value = float(hist["expected"].tail(tail_n).mean())
    lower_band = float(hist["lower"].tail(tail_n).mean())
    upper_band = float(hist["upper"].tail(tail_n).mean())

    # "Recent" schaalt met de aggregatie (consistent met detect_recent_alerts:
    # 48 uur / 14 dagen / 8 weken / 6 maanden), niet blind 14 periodes.
    recent_periods = {"hourly": 48, "daily": 14, "weekly": 8,
                      "monthly": 6}.get(aggregation, 14)
    n_recent_dev = int(
        hist.tail(recent_periods)["status"].isin(["boven", "onder"]).sum()
    )

    # Per-methode reeksen voor visualisatie (clip komt al uit _forecast_with)
    per_method_forecast: dict = {}
    per_method_historical: dict = {}
    for m, p in per_method_predictions.items():
        m_hist, m_future, _ = p
        per_method_forecast[m] = pd.DataFrame({
            "date":     future_idx,
            "expected": np.asarray(m_future, dtype=float),
        })
        per_method_historical[m] = pd.Series(
            np.asarray(m_hist, dtype=float), index=series.index,
        )

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
        skip_reasons=skip_reasons,
        backtest_scores=backtest_scores,
        backtest_error=(
            min(backtest_scores.values()) if backtest_scores else None
        ),
        band_alpha=band_alpha,
        band_coverage=band_coverage,
        widening_source=widening_source,
        band_model=band_model,
        dispersion=dispersion,
    )


def compute_all_normbeelds(
    df: pd.DataFrame,
    horizon_days: int = 14,
    methods: list[str] | None = None,
    aggregation: str = "daily",
    min_rows_per_location: int = 5,
    max_locations: int = 50,
    gap_policy: str = "zero",
) -> dict[str, Normbeeld]:
    """Normbeelden voor elke locatie met genoeg data (heuristische selectie,
    snel). Voor de rigoureuze backtest-variant: compute_normbeeld(select=
    'backtest') op één locatie in de detail-weergave."""
    if "location_name" not in df.columns or df["location_name"].isna().all():
        nb = compute_normbeeld(
            df, horizon_days=horizon_days,
            methods=methods, aggregation=aggregation, gap_policy=gap_policy,
        )
        return {"Alle locaties": nb} if nb else {}

    counts = df["location_name"].value_counts()
    counts = counts[counts >= min_rows_per_location].head(max_locations)
    locations = list(counts.index)

    out: dict[str, Normbeeld] = {}
    for loc in locations:
        # Eén pathologische locatie (rare data, edge-case in een model) mag
        # nooit de hele analyse laten crashen: skip met traceback in de logs.
        try:
            nb = compute_normbeeld(
                df, location=loc, horizon_days=horizon_days,
                methods=methods, aggregation=aggregation,
                gap_policy=gap_policy,
            )
        except Exception:
            _logger.exception("normbeeld faalde voor locatie",
                              extra={"ctx": {"location": str(loc)}})
            continue
        if nb is not None:
            out[loc] = nb
    return out


def data_quality(df: pd.DataFrame, aggregation: str = "daily") -> dict:
    """Datakwaliteits-indicatoren voor een dataset (of subset).

    - coverage: fractie van de periodes in de spanne mét waarnemingen
    - staleness_days: dagen tussen laatste waarneming en vandaag
    - n_rows, span_days
    """
    out = {"n_rows": len(df), "coverage": None,
           "staleness_days": None, "span_days": None}
    if df.empty or "timestamp" not in df.columns:
        return out
    ts = pd.to_datetime(df["timestamp"]).dropna()
    if ts.empty:
        return out
    freq = AGGREGATIONS.get(aggregation, AGGREGATIONS["daily"])[0]
    buckets = pd.Series(1, index=ts).resample(freq).count()
    out["coverage"] = float((buckets > 0).mean())
    out["span_days"] = int((ts.max() - ts.min()).days)
    out["staleness_days"] = int(
        (pd.Timestamp.now().normalize() - ts.max().normalize()).days
    )
    return out


_RECENT_WINDOW_DAYS = {"hourly": 2, "daily": 14, "weekly": 56, "monthly": 180}
_RECENT_WINDOW_LABEL = {
    "hourly": "48 uur", "daily": "14 dagen",
    "weekly": "8 weken", "monthly": "6 maanden",
}


def recent_window_label(aggregation: str) -> str:
    return _RECENT_WINDOW_LABEL.get(aggregation, "14 dagen")


def detect_recent_alerts(
    normbeelds: dict[str, Normbeeld],
    aggregation: str = "daily",
) -> list[dict]:
    """Recente afwijkingen op basis van het laatste datapunt in de dataset
    (niet 'vandaag'). Window-grootte schaalt met aggregatie."""
    days_back = _RECENT_WINDOW_DAYS.get(aggregation, 14)
    alerts: list[dict] = []
    for loc, nb in normbeelds.items():
        if nb.historical.empty:
            continue
        last_date = pd.Timestamp(nb.historical["date"].max())
        cutoff = last_date - pd.Timedelta(days=days_back)
        recent = nb.historical[nb.historical["date"] >= cutoff]
        for _, row in recent.iterrows():
            if row["status"] in ("boven", "onder"):
                pctl = float(row.get("resid_pctl", 0.5))
                extremer_dan = pctl if row["status"] == "boven" else 1.0 - pctl
                alerts.append({
                    "datum": pd.Timestamp(row["date"]).date().isoformat(),
                    "locatie": loc,
                    "waarde": int(row["actual"]),
                    "verwacht": float(row["expected"]),
                    "lower": float(row["lower"]),
                    "upper": float(row["upper"]),
                    "richting": row["status"],
                    "extremer_dan": extremer_dan,  # 0-1: aandeel historie dat minder extreem is
                })
    alerts.sort(key=lambda a: a["datum"], reverse=True)
    return alerts
