"""Triage: bevindingen beoordelen en meten of de tool zijn plek verdient.

Deze pagina bestaat om twee redenen:
1. De bevindingen uit het detectie-ensemble hebben een eigen werkplek nodig
   (ze stonden verstopt in tabbladen op de normbeeld-pagina).
2. Zonder terugkoppeling weet niemand of de signalen nuttig zijn. Elke
   beoordeling ('bevestigd' / 'vals alarm') voedt de prestatiemeting
   bovenaan deze pagina.
"""
from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from core import annotations as anno
from core import storage
from core.auto_pilot import build_findings, detector_agreement
from i18n.nl import t
from ui.cache import cached_analysis
from ui.components import (
    _render_annotation_widget,
    _render_empty_state,
    render_topbar,
)
from ui.theme import P


def _render_performance(dataset_id: int):
    """Prestatiemeting: wat leverden de signalen op, volgens de analist."""
    perf = anno.performance(dataset_id, window_days=90)
    verdict = anno.performance_verdict(perf)

    if not perf["reliable"]:
        color, label = P["text_muted"], "ONVOLDOENDE BEOORDEELD"
    elif perf["precision"] >= 0.7:
        color, label = P["ok"], f"{perf['precision'] * 100:.0f}% RAAK"
    elif perf["precision"] >= 0.4:
        color, label = P["mid"], f"{perf['precision'] * 100:.0f}% RAAK"
    else:
        color, label = P["high"], f"{perf['precision'] * 100:.0f}% RAAK"

    st.markdown(
        f"""
        <div class="finding-card" style="--card-color: {color};">
            <div class="finding-header">
                <span class="severity-pill" style="background: {color};">
                    {label}</span>
                <span class="finding-loc">Prestatie laatste 90 dagen</span>
            </div>
            <div class="finding-stat">{_html.escape(verdict)}</div>
            <div class="finding-meta">
                {perf['counts']['bevestigd']} bevestigd ·
                {perf['counts']['geescaleerd']} geëscaleerd ·
                {perf['counts']['vals_alarm']} vals alarm ·
                {perf['counts']['onderzocht']} onderzocht ·
                {perf['counts']['open']} nog open
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_findings(result, ds: dict):
    findings = build_findings(result, top_n=40)
    strong = [f for f in findings if f["severity"] in ("hoog", "midden")]
    weak = [f for f in findings if f["severity"] == "laag"]

    if not strong and not weak:
        st.success("Geen bevindingen boven de drempel in deze dataset.")
        return

    if strong:
        st.markdown("<div class='section-label'>Ter beoordeling</div>",
                    unsafe_allow_html=True)
        st.caption(
            f"Gevoeligheid automatisch afgesteld op "
            f"'{result.sensitivity_used}' ({result.iterations} iteratie(s)). "
            f"Dit is een triage-lijst: de meest opvallende punten van déze "
            f"dataset, geen bewijs van significantie."
        )
        sev_color = {"hoog": P["high"], "midden": P["mid"]}
        for i, f in enumerate(strong[:12]):
            exp = f["explanation"]
            st.markdown(
                f"""
                <div class="finding-card"
                     style="--card-color: {sev_color[f['severity']]};">
                    <div class="finding-header">
                        <span class="severity-pill severity-{f['severity']}">
                            {f['severity'].upper()}</span>
                        <span class="finding-loc">
                            {_html.escape(str(f['locatie']))}</span>
                        <span class="finding-date">
                            {pd.Timestamp(f['datum']).strftime('%d-%m-%Y')}</span>
                    </div>
                    <div class="finding-stat">
                        {_html.escape(exp['observation'])}</div>
                    <div class="finding-meta">
                        {f['stemmen']}/{f['totaal_methodes']} algoritmes:
                        {_html.escape(', '.join(f['methodes_aan']))}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            _render_annotation_widget(ds["id"], f["datum"],
                                      str(f["locatie"]), key_suffix=f"tri_{i}")

    if weak:
        with st.expander(
            f"Laag-vertrouwen signalen ({len(weak)}) — 2 algoritmes, "
            f"mogelijk vals alarm"
        ):
            rows = "".join(
                f"<div class='alert-row'>"
                f"{pd.Timestamp(f['datum']).strftime('%d-%m-%Y')} · "
                f"{_html.escape(str(f['locatie']))} · {f['waarde']}</div>"
                for f in weak[:30]
            )
            st.markdown(rows, unsafe_allow_html=True)


def _render_agreement(result):
    agree = detector_agreement(getattr(result, "method_outputs", None))
    if agree is None:
        return
    n, n_eff = agree["n_detectors"], agree["n_effective"]
    ratio = n_eff / n
    if ratio >= 0.8:
        verdict = ("De algoritmes kijken grotendeels naar verschillende "
                   "dingen — stemmen tellen bijna vol mee.")
    elif ratio >= 0.5:
        verdict = ("De algoritmes overlappen deels — lees een meerderheid "
                   "als sterke aanwijzing, niet als onafhankelijke "
                   "bevestiging.")
    else:
        verdict = ("De algoritmes zeggen grotendeels hetzelfde — behandel "
                   "een meerderheid als één waarneming.")
    with st.expander(
        f"Hoe zelfstandig zijn deze stemmen? "
        f"({n_eff:.1f} van {n} tellen echt afzonderlijk)"
    ):
        st.markdown(f"**{verdict}**")
        rows = sorted(agree["pairs"], key=lambda p: -p["jaccard"])[:10]
        st.dataframe(
            pd.DataFrame([{
                "Algoritme A": r["a"], "Algoritme B": r["b"],
                "Overlap": f"{r['jaccard'] * 100:.0f}%",
                "Samen gemarkeerd": r["n_both"],
            } for r in rows]),
            use_container_width=True, hide_index=True,
        )


def page_triage():
    render_topbar(t("nav_triage"))
    st.caption(
        "Beoordeel de signalen. Elke beoordeling meet mee of deze tool "
        "waarde toevoegt — zonder terugkoppeling blijft dat een aanname."
    )

    datasets = storage.list_datasets()
    if not datasets:
        _render_empty_state()
        return

    by_id = {d["id"]: d for d in datasets}
    ids = list(by_id.keys())
    if st.session_state.active_dataset_id not in ids:
        st.session_state.active_dataset_id = ids[0]
    chosen = st.selectbox(
        t("ds_dataset"), ids,
        format_func=lambda i: by_id[i]["name"],
        index=ids.index(st.session_state.active_dataset_id),
        key="tri_ds_select",
    )
    if chosen != st.session_state.active_dataset_id:
        st.session_state.active_dataset_id = chosen
        st.rerun()

    ds = by_id[chosen]
    _render_performance(ds["id"])

    gap_policy = (ds["column_mapping"] or {}).get("gap_policy", "zero")
    cached = cached_analysis(
        ds["id"], storage.dataset_data_hash(ds["id"]),
        st.session_state.horizon_days, st.session_state.aggregation,
        "auto", gap_policy,
    )
    if cached is None:
        st.warning("Dataset is leeg.")
        return
    _, _, result, _, alerts, _ = cached

    triage = anno.triage_counts(ds["id"], alerts)
    if triage["total"]:
        st.caption(
            f"{triage['unhandled']} van {triage['total']} recente afwijkingen "
            f"nog onbehandeld."
        )

    _render_findings(result, ds)
    _render_agreement(result)
