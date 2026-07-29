"""Overzicht: executive statusbeeld over alle datasets."""
from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from core import annotations as anno
from core import storage
from i18n.nl import t
from ui.cache import cached_analysis
from ui.components import _render_empty_state, render_topbar


def _dataset_status(n_unhandled: int, quality: dict) -> tuple[str, str]:
    """(kleur, label) op basis van ONBEHANDELDE afwijkingen en datakwaliteit.

    Behandelde afwijkingen (status onderzocht/vals alarm/bevestigd/
    geëscaleerd) tellen niet meer mee — anders blijft een dataset rood
    terwijl de analist alles al beoordeeld heeft (alert-moeheid).
    """
    stale = quality.get("staleness_days")
    cov = quality.get("coverage")
    if n_unhandled >= 3 or (stale is not None and stale > 60):
        return ("#c53030", "aandacht vereist")
    if n_unhandled >= 1 or (cov is not None and cov < 0.7) \
            or (stale is not None and stale > 30):
        return ("#c05621", "let op")
    return ("#2e8b57", "normaal beeld")


def page_overview():
    render_topbar(t("nav_overview"))
    st.caption(
        "Statusbeeld over alle datasets: recente afwijkingen, datakwaliteit "
        "en verwachting in één oogopslag."
    )
    datasets = storage.list_datasets()
    if not datasets:
        _render_empty_state()
        return

    from core.normbeeld import data_quality
    for ds in datasets:
        meta = ds["column_mapping"] or {}
        gap_policy = meta.get("gap_policy", "zero")
        try:
            data_hash = storage.dataset_data_hash(ds["id"])
            cached = cached_analysis(
                ds["id"], data_hash, st.session_state.horizon_days,
                "auto", "auto", gap_policy,
            )
        except Exception as e:
            st.warning(f"{ds['name']}: analyse mislukt ({e})")
            continue
        if cached is None:
            st.caption(f"{ds['name']} — leeg")
            continue
        df_raw, _, _, normbeelds, alerts, effective_agg = cached
        q = data_quality(df_raw, effective_agg)
        triage = anno.triage_counts(ds["id"], alerts)
        color, label = _dataset_status(triage["unhandled"], q)

        rel = meta.get("source_reliability") or ""
        cred = meta.get("info_credibility") or ""
        bron = f" · bron {rel}{cred}" if (rel or cred) else ""
        stale = (f" · laatste data {q['staleness_days']}d geleden"
                 if q.get("staleness_days") is not None else "")
        cov = (f" · dekking {q['coverage'] * 100:.0f}%"
               if q.get("coverage") is not None else "")

        st.markdown(
            f"""
            <div class="finding-card" style="--card-color: {color};">
                <div class="finding-header">
                    <span class="severity-pill"
                          style="background: {color};">{label.upper()}</span>
                    <span class="finding-loc">{_html.escape(ds['name'])}</span>
                </div>
                <div class="finding-meta">
                    {triage['unhandled']} onbehandelde /
                    {triage['total']} recente afwijking(en)
                    {('· ' + str(triage['escalated']) + ' geëscaleerd')
                     if triage['escalated'] else ''} ·
                    {len(normbeelds)} regio's · {q['n_rows']} rijen
                    {bron}{stale}{cov}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if alerts:
            top = alerts[:3]
            rows = "".join(
                f"<div class='alert-row'>"
                f"{pd.Timestamp(a['datum']).strftime('%d-%m-%Y')} · "
                f"{_html.escape(str(a['locatie']))} · {a['waarde']} "
                f"({'boven' if a['richting'] == 'boven' else 'onder'} band)"
                f"</div>"
                for a in top
            )
            st.markdown(rows, unsafe_allow_html=True)
        if st.button("Open in Normbeeld →", key=f"ov_open_{ds['id']}",
                     type="secondary"):
            st.session_state.active_dataset_id = ds["id"]
            st.session_state.active_page = t("nav_normbeeld")
            st.rerun()
