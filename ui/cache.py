"""Cache-wrappers rond de analyse-kern (st.cache_data, keyed op data-hash)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import storage
from core.auto_pilot import run_auto_pilot
from core.normbeeld import (
    AGGREGATIONS,
    _suggest_best_aggregation,
    compute_all_normbeelds,
    compute_normbeeld,
    detect_recent_alerts,
    recommend_timescale,
)


@st.cache_data(show_spinner="Tijdschalen vergelijken...")
def cached_timescale_advice(dataset_id: int, data_hash: str):
    """Backtest per tijdschaal is duur; cachen op de data-hash."""
    df = storage.load_observations(dataset_id)
    if df.empty:
        return None
    return recommend_timescale(df)


def _aggregate_df(df: pd.DataFrame, aggregation: str) -> pd.DataFrame:
    """Resample observaties naar week/maand. Houdt locatie/categorie intact."""
    if aggregation == "daily" or df.empty:
        return df
    freq = AGGREGATIONS[aggregation][0]
    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"])
    group_cols = [
        c for c in ["location_name", "category"]
        if c in work.columns and work[c].notna().any()
    ]
    if group_cols:
        work["__bucket"] = work["timestamp"].dt.to_period(
            "M" if aggregation == "monthly" else "W"
        ).dt.start_time
        agg_dict = {"value": "sum"}
        for col in ("lat", "lon"):
            if col in work.columns:
                agg_dict[col] = "first"
        result = (
            work.groupby(["__bucket"] + group_cols, dropna=False)
            .agg(agg_dict).reset_index()
        )
        result = result.rename(columns={"__bucket": "timestamp"})
    else:
        s = work.set_index("timestamp")["value"].resample(freq).sum()
        result = s.reset_index()
    return result


def _resolve_aggregation(df: pd.DataFrame, choice: str) -> str:
    if choice == "auto":
        return _suggest_best_aggregation(df)
    return choice


def _dataset_meta(dataset_id: int) -> dict:
    """Mapping + metadata (gap-policy, betrouwbaarheid) van één dataset."""
    for d in storage.list_datasets():
        if d["id"] == dataset_id:
            return d["column_mapping"] or {}
    return {}


@st.cache_data(show_spinner="Backtest draait... (eenmalig per locatie)")
def cached_detail_normbeeld(
    dataset_id: int, data_hash: str, location: str,
    category, horizon: int, methods_key: str, aggregation: str,
    gap_policy: str = "zero",
):
    """Detail-normbeeld voor één locatie, met backtest-gestuurde
    methode-selectie als de gebruiker niets heeft gekozen. `category` mag
    None, een string of een tuple van categorieën zijn (hashbaar voor cache)."""
    df = storage.load_observations(dataset_id)
    if df.empty:
        return None
    methods = None if methods_key == "auto" else methods_key.split(",")
    cat = list(category) if isinstance(category, tuple) else category
    return compute_normbeeld(
        df, location=location, category=cat,
        horizon_days=horizon, methods=methods,
        aggregation=aggregation, select="backtest", gap_policy=gap_policy,
    )


@st.cache_data(show_spinner="Analyseren... (eerste keer ~10-30 sec voor grote datasets)")
def cached_analysis(
    dataset_id: int, data_hash: str, horizon: int,
    aggregation: str, methods_key: str, gap_policy: str = "zero",
):
    df_raw = storage.load_observations(dataset_id)
    if df_raw.empty:
        return None

    effective_agg = _resolve_aggregation(df_raw, aggregation)
    df = _aggregate_df(df_raw, effective_agg)

    group_col = (
        "location_name" if "location_name" in df.columns
        and df["location_name"].notna().any() else None
    )
    result = run_auto_pilot(df, group_col=group_col)
    result.log.callbacks.clear()

    methods = None if methods_key == "auto" else methods_key.split(",")
    normbeelds = compute_all_normbeelds(
        df_raw, horizon_days=horizon, methods=methods,
        aggregation=effective_agg, gap_policy=gap_policy,
    )
    alerts = detect_recent_alerts(normbeelds, aggregation=effective_agg)
    return df_raw, df, result, normbeelds, alerts, effective_agg


@st.cache_data(show_spinner=False)
def _cmp_load(dataset_id: int, data_hash: str):
    return storage.load_observations(dataset_id)


@st.cache_data(show_spinner="Regio's vergelijken...")
def cached_comovement(dataset_id: int, data_hash: str, aggregation: str):
    """Peer-groep-analyse over alle regio's van een dataset."""
    from core.comparison import region_comovement
    df = storage.load_observations(dataset_id)
    if df.empty:
        return None, []
    return region_comovement(df, aggregation)
