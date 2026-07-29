"""Vergelijken: twee reeksen overlay + lag-detectie + change-points."""
from __future__ import annotations

import streamlit as st

from core import storage
from core.comparison import build_series, cross_correlation_lag
from i18n.nl import t
from ui.cache import _cmp_load, _resolve_aggregation
from ui.components import (
    _event_markers,
    _render_empty_state,
    _render_markers_manager,
    render_topbar,
)
from visualizations.comparison_chart import render_lag_curve, render_overlay


def _series_picker_xds(by_id: dict, key_prefix: str, default_ds_id: int,
                       multi_dataset: bool):
    """Kies dataset (indien meerdere) + regio + categorieën voor één reeks.
    Returnt (series_df, region, categories, label) of None bij geen data."""
    ids = list(by_id.keys())
    if multi_dataset:
        ds_id = st.selectbox(
            "Dataset", ids,
            format_func=lambda i: by_id[i]["name"],
            index=ids.index(default_ds_id) if default_ds_id in ids else 0,
            key=f"{key_prefix}_ds",
        )
    else:
        ds_id = ids[0]
    df = _cmp_load(ds_id, storage.dataset_data_hash(ds_id))
    if df.empty or "location_name" not in df.columns \
            or df["location_name"].isna().all():
        st.info("Deze dataset heeft geen regio-kolom of is leeg.")
        return None

    regions = sorted(df["location_name"].dropna().unique(),
                     key=lambda s: str(s).lower())
    region = st.selectbox(
        "Regio", regions, key=f"{key_prefix}_region",
    )
    cats: list[str] = []
    if "category" in df.columns and df["category"].notna().any():
        avail = sorted(
            df[df["location_name"] == region]["category"].dropna().unique().tolist()
        )
        if avail:
            cats = st.multiselect(
                "Categorieën (leeg = alle samen)", avail, default=[],
                key=f"{key_prefix}_cats",
            )
    ds_name = by_id[ds_id]["name"]
    parts = [region] if not cats else [f"{region} ({', '.join(cats)})"]
    label = f"{ds_name} · {parts[0]}" if multi_dataset else parts[0]
    return df, region, cats, label


def page_compare():
    render_topbar(t("nav_compare"))
    st.caption(
        "Plot twee reeksen samen — uit dezelfde of uit verschillende datasets — "
        "en ontdek het verband: volgt de ene op de andere, en met hoeveel "
        "vertraging? (bv. RUS-aanvallen op UKR vs. UKR-aanvallen op RUS)."
    )

    datasets = storage.list_datasets()
    if not datasets:
        _render_empty_state()
        return

    by_id = {d["id"]: d for d in datasets}
    ids = list(by_id.keys())
    multi = len(ids) > 1
    if not multi:
        st.info(
            "Tip: voeg een tweede dataset toe (via Instellingen → Upload) om "
            "twee databronnen te vergelijken. Nu vergelijk je binnen één dataset."
        )

    # Aggregatie
    agg_options = ["hourly", "daily", "weekly", "monthly"]
    agg = st.selectbox(
        t("agg_label"), agg_options,
        format_func=lambda k: {"hourly": t("agg_hourly"),
                               "daily": t("agg_daily"), "weekly": t("agg_weekly"),
                               "monthly": t("agg_monthly")}[k],
        index=agg_options.index(
            _resolve_aggregation(_cmp_load(ids[0], storage.dataset_data_hash(ids[0])),
                                 st.session_state.aggregation)
        ),
        key="cmp_agg",
    )

    default_a = ids[0]
    default_b = ids[1] if len(ids) > 1 else ids[0]

    st.markdown("<div class='section-label'>Twee reeksen kiezen</div>",
                unsafe_allow_html=True)
    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Reeks A**")
        pick_a = _series_picker_xds(by_id, "cmp_a", default_a, multi)
    with cB:
        st.markdown("**Reeks B**")
        pick_b = _series_picker_xds(by_id, "cmp_b", default_b, multi)

    if pick_a is None or pick_b is None:
        return
    df_a, reg_a, cats_a, label_a = pick_a
    df_b, reg_b, cats_b, label_b = pick_b

    series_a = build_series(df_a, reg_a, cats_a, agg)
    series_b = build_series(df_b, reg_b, cats_b, agg)
    if series_a.empty or series_b.empty:
        st.warning("Eén van de reeksen heeft geen data.")
        return

    lag = cross_correlation_lag(series_a, series_b, agg)

    # Lag-bevinding in mensentaal
    align = st.checkbox(
        "Reeks B uitlijnen op de gevonden vertraging", value=False,
        key="cmp_align",
    )
    shift = lag.best_lag if (lag and align and lag.best_lag > 0) else 0

    st.markdown("<div class='section-label'>Overlay</div>",
                unsafe_allow_html=True)
    render_overlay(
        series_a, series_b, label_a, label_b,
        theme=st.session_state.ui_theme, shift_b_by=shift,
        markers=_event_markers(),
    )

    # Eigen markeringen ook hier beheren (bv. staakt-het-vuren-datum)
    _render_markers_manager(key_prefix="cmp")

    if lag is not None:
        unit = lag.unit
        if not lag.significant:
            verdict = (
                f"**Geen aantoonbaar verband** tussen {label_a} en {label_b} — "
                f"de hoogste correlatie ({lag.best_corr:.2f}) blijft onder de "
                f"significantie-drempel ({lag.sig_threshold:.2f}, permutatietest "
                f"over alle geteste vertragingen). De beste lag hieronder is "
                f"puur indicatief."
            )
        elif lag.best_lag > 0:
            verdict = (
                f"**{label_b} volgt {label_a}** met ongeveer "
                f"**{lag.best_lag} {unit}{'en' if lag.best_lag != 1 else ''}** "
                f"vertraging (correlatie {lag.best_corr:.2f})."
            )
        elif lag.best_lag < 0:
            verdict = (
                f"**{label_a} volgt {label_b}** met ongeveer "
                f"**{abs(lag.best_lag)} {unit}{'en' if abs(lag.best_lag) != 1 else ''}** "
                f"vertraging (correlatie {lag.best_corr:.2f})."
            )
        else:
            verdict = (
                f"**{label_a} en {label_b} bewegen gelijktijdig** "
                f"(correlatie {lag.best_corr:.2f}, geen vertraging)."
            )
        st.markdown(verdict)
        st.caption(
            "Cross-correlatie: voor elke mogelijke vertraging meten we hoe "
            "sterk de twee reeksen samenhangen. De hoogste balk is de meest "
            "waarschijnlijke vertraging. Let op: correlatie is geen bewijs "
            "van oorzaak."
        )
        render_lag_curve(lag, label_a, label_b, theme=st.session_state.ui_theme)
    else:
        st.info("Te weinig overlappende data voor een betrouwbare lag-analyse.")
