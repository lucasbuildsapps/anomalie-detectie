"""Normbeeld-pagina: detail-normbeeld, afwijkingen, signalen, export."""
from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from core import storage
from core.auto_pilot import build_findings
from core.briefing import briefing_filename, build_briefing_pdf
from core.comparison import seasonality_profile
from core.excel_export import build_excel_export, excel_filename
from core.normbeeld import (
    AGGREGATIONS,
    PREDICTION_METHOD_DETAILS,
    PREDICTION_METHODS,
    data_quality,
)
from core.signals import collect_signals
from i18n.nl import t
from ui.cache import _dataset_meta, cached_analysis, cached_detail_normbeeld
from ui.components import (
    _event_markers,
    _fmt_num,
    _pctl_label,
    _render_annotation_widget,
    _render_empty_state,
    _render_markers_manager,
    _render_saved_views,
    render_topbar,
)
from ui.theme import P
from visualizations.normbeeld_chart import render_normbeeld_chart

# Voorspel-presets: elk combineert intern meerdere methodes. De gebruiker
# kiest één preset; de tool doet de combinatie. 'auto' laat de backtest de
# nauwkeurigste twee kiezen.
METHOD_PRESETS = {
    "auto":   ("Automatisch (aanbevolen)", None),
    "season": ("Seizoensgericht",          ["stl", "ets", "seasonal_naive"]),
    "trend":  ("Trend & stabiel",          ["ets", "rolling"]),
    "simple": ("Eenvoudig & robuust",      ["median", "rolling"]),
}
PRESET_HELP = {
    "auto": "Test alle methodes op jouw data (backtest) en kiest automatisch "
            "de twee nauwkeurigste. Beste keuze als je twijfelt.",
    "season": "Voor data met een duidelijk terugkerend patroon (per week of "
              "maand). Combineert STL + Holt-Winters + seasonal naive.",
    "trend": "Voor data met een trend maar zonder sterk seizoen. Combineert "
             "Holt-Winters + voortschrijdend gemiddelde.",
    "simple": "Voor korte of grillige reeksen waar modellen onbetrouwbaar "
              "zijn. Combineert mediaan + voortschrijdend gemiddelde.",
}


def _recommend_preset(nb) -> str:
    """Beveel een preset aan op basis van de data-eigenschappen."""
    try:
        hist = nb.historical.set_index("date")["actual"]
        seasonal = seasonality_profile(hist, nb.aggregation) is not None
    except Exception:
        seasonal = False
    n = nb.n_history_periods
    if n < 14:
        return "simple"
    if seasonal and n >= 21:
        return "season"
    return "trend"


def _render_exports(result, normbeelds, ds: dict, alerts=None):
    """Briefing + SITREP + Excel (gebruikt op de normbeeld-pagina)."""
    from core.briefing import build_sitrep_pdf, sitrep_filename
    from core.normbeeld import data_quality as _dq
    c1, c_sitrep, c2 = st.columns(3)
    with c1:
        try:
            pdf_bytes = build_briefing_pdf(
                result, ds["name"], ds["description"], normbeelds=normbeelds,
            )
            st.download_button(
                t("export_pdf"), data=pdf_bytes,
                file_name=briefing_filename(ds["name"]),
                mime="application/pdf",
                use_container_width=True, type="secondary",
            )
        except Exception as e:
            st.error(f"PDF: {e}")
    with c_sitrep:
        try:
            df_q = storage.load_observations(ds["id"])
            sitrep_bytes = build_sitrep_pdf(
                result, normbeelds, alerts or [], ds["name"],
                meta=ds.get("column_mapping") or {},
                quality=_dq(df_q),
            )
            st.download_button(
                "SITREP (PDF)", data=sitrep_bytes,
                file_name=sitrep_filename(ds["name"]),
                mime="application/pdf",
                use_container_width=True, type="secondary",
            )
        except Exception as e:
            st.error(f"SITREP: {e}")
    with c2:
        try:
            xlsx_bytes = build_excel_export(
                result, normbeelds, ds["name"], ds["description"],
            )
            st.download_button(
                t("export_excel"), data=xlsx_bytes,
                file_name=excel_filename(ds["name"]),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, type="secondary",
            )
        except Exception as e:
            st.error(f"Excel: {e}")


def page_normbeeld():
    render_topbar(t("nb_title"))
    st.caption(t("nb_subtitle"))
    _render_saved_views()

    datasets = storage.list_datasets()
    if not datasets:
        _render_empty_state()
        return

    by_id = {d["id"]: d for d in datasets}
    ids = list(by_id.keys())
    if st.session_state.active_dataset_id not in ids:
        st.session_state.active_dataset_id = ids[0]

    c1, c2 = st.columns([3, 1])
    with c1:
        chosen = st.selectbox(
            t("ds_dataset"), ids,
            format_func=lambda i: by_id[i]["name"],
            index=ids.index(st.session_state.active_dataset_id),
            key="nb_ds_select",
        )
        if chosen != st.session_state.active_dataset_id:
            st.session_state.active_dataset_id = chosen
            st.session_state.nb_selected_location = None
            st.rerun()
    with c2:
        horizon = st.number_input(
            t("nb_horizon"),
            min_value=1, max_value=60,
            value=st.session_state.horizon_days, step=1,
            key="nb_horizon_input",
        )
        if horizon != st.session_state.horizon_days:
            st.session_state.horizon_days = int(horizon)
            st.rerun()

    ds = by_id[chosen]
    ds_meta = ds["column_mapping"] or {}
    gap_policy = ds_meta.get("gap_policy", "zero")
    methods_key = (
        "auto" if st.session_state.nb_methods_override is None
        else ",".join(st.session_state.nb_methods_override)
    )
    data_hash = storage.dataset_data_hash(ds["id"])
    cached = cached_analysis(
        ds["id"], data_hash, st.session_state.horizon_days,
        st.session_state.aggregation, methods_key, gap_policy,
    )
    if cached is None:
        st.warning("Dataset is leeg.")
        return
    df_raw, df, result, normbeelds, alerts, effective_agg = cached

    # Bron-metadata + datakwaliteit (compacte regel onder de selector)
    q = data_quality(df_raw, effective_agg)
    meta_bits = []
    rel = ds_meta.get("source_reliability") or ""
    cred = ds_meta.get("info_credibility") or ""
    if rel or cred:
        meta_bits.append(f"Bron: {rel}{cred} (Admiraliteitsschaal)")
    if q["coverage"] is not None:
        cov_txt = f"dekking {q['coverage'] * 100:.0f}%"
        if q["coverage"] < 0.7:
            cov_txt += " ⚠"
        meta_bits.append(cov_txt)
    if q["staleness_days"] is not None:
        stale_txt = f"laatste waarneming {q['staleness_days']}d geleden"
        if q["staleness_days"] > 30:
            stale_txt += " ⚠"
        meta_bits.append(stale_txt)
    meta_bits.append(f"gap-policy: {gap_policy}")
    st.caption(" · ".join(meta_bits))

    # Aggregatie-toggle ook op normbeeld-pagina (zodat hij ook hier werkt)
    agg_options = ["auto", "hourly", "daily", "weekly", "monthly"]
    agg_labels = {
        "auto":    f"Auto (aanbevolen: {AGGREGATIONS[effective_agg][1]})",
        "hourly":  t("agg_hourly"),
        "daily":   t("agg_daily"),
        "weekly":  t("agg_weekly"),
        "monthly": t("agg_monthly"),
    }
    new_agg = st.selectbox(
        t("agg_label"), agg_options,
        format_func=lambda k: agg_labels[k],
        index=agg_options.index(st.session_state.aggregation)
        if st.session_state.aggregation in agg_options else 0,
        key="nb_agg_pick",
    )
    if new_agg != st.session_state.aggregation:
        st.session_state.aggregation = new_agg
        st.rerun()

    if not normbeelds:
        st.warning(t("nb_no_data"))
        return

    unit = AGGREGATIONS[effective_agg][1]  # 'dag' / 'week' / 'maand'
    # Regio's alfabetisch (voorspelbare volgorde voor de analist)
    locs_sorted = sorted(normbeelds.keys(), key=lambda s: s.lower())

    # ----- Regio direct selecteerbaar (geen doorklik-stap) -----
    if st.session_state.nb_selected_location not in locs_sorted:
        st.session_state.nb_selected_location = locs_sorted[0]
    selected = st.selectbox(
        t("nb_region"),
        locs_sorted,
        index=locs_sorted.index(st.session_state.nb_selected_location),
        format_func=lambda loc: (
            f"{loc}  ·  {normbeelds[loc].n_recent_deviations} recente afwijking(en)"
        ),
        key="nb_detail_pick",
    )
    if selected != st.session_state.nb_selected_location:
        st.session_state.nb_selected_location = selected
        st.rerun()

    nb_view = _render_normbeeld_detail(
        df_raw, normbeelds[selected], selected, ds["id"], unit, effective_agg,
    )

    # ----- Afwijkingen (kern-capability) -----
    if nb_view is not None:
        _render_afwijkingen_section(nb_view, result, alerts, ds,
                                    selected, unit)

    # ----- Export (briefing + SITREP + Excel) -----
    st.divider()
    st.markdown("<div class='section-label'>Export</div>",
                unsafe_allow_html=True)
    _render_exports(result, normbeelds, ds, alerts=alerts)


def _render_normbeeld_detail(df_raw, nb, location: str, dataset_id: int,
                             unit: str = "dag", aggregation: str = "daily"):
    st.markdown(
        f"<div class='section-label'>{t('nb_detail')}: {_html.escape(location)}</div>",
        unsafe_allow_html=True,
    )

    # Categorie (meerdere) + methode-selectie naast elkaar
    selected_cats: list[str] = []
    c1, c2 = st.columns([1, 2])
    with c1:
        if "category" in df_raw.columns and df_raw["category"].notna().any():
            avail_cats = sorted(
                df_raw[df_raw["location_name"] == location]["category"]
                .dropna().unique().tolist()
            )
            default_cats = [
                c for c in st.session_state.nb_selected_categories
                if c in avail_cats
            ]
            picked_cats = st.multiselect(
                t("nb_categories"), avail_cats, default=default_cats,
                help="Leeg = alle categorieën samen. Kies er één of meer om "
                     "het normbeeld tot die categorieën te beperken.",
                key="nb_cats_select",
            )
            if picked_cats != st.session_state.nb_selected_categories:
                st.session_state.nb_selected_categories = picked_cats
                st.rerun()
            selected_cats = picked_cats
    with c2:
        recommended = _recommend_preset(nb)
        preset_keys = list(METHOD_PRESETS.keys())

        def _preset_label(k: str) -> str:
            base = METHOD_PRESETS[k][0]
            return f"{base}  ·  aanbevolen" if k == recommended else base

        cur_preset = st.session_state.nb_preset
        if cur_preset not in preset_keys:
            cur_preset = "auto"
        preset = st.selectbox(
            "Voorspelmethode",
            preset_keys, index=preset_keys.index(cur_preset),
            format_func=_preset_label,
            help="Elke optie combineert intern meerdere voorspelmethodes — "
                 "je hoeft niets handmatig te mengen. De tool maakt de keuze; "
                 "je kunt 'm overrulen.",
            key="nb_preset_pick",
        )
        if preset != st.session_state.nb_preset:
            st.session_state.nb_preset = preset
            st.session_state.nb_methods_override = METHOD_PRESETS[preset][1]
            st.cache_data.clear()
            st.rerun()
        st.caption(PRESET_HELP[preset])
        if preset != recommended:
            rec_label = METHOD_PRESETS[recommended][0]
            st.caption(f"Tip: voor deze reeks ligt **{rec_label}** voor de hand.")

    # Volledige uitleg van de voorspelling + het normbeeld
    with st.expander("Hoe werkt de voorspelling en het normbeeld? (volledige uitleg)"):
        st.markdown(
            """
**In het kort**: de tool leert wat normaal is per regio, voorspelt het
verwachte niveau vooruit, en markeert wat daarbuiten valt. In vijf stappen:

**1. Aggregeren.** De ruwe waarnemingen worden opgeteld per dag, week of
maand (de tool kiest de schaal op basis van hoe lang je reeks loopt; je
kunt dit bovenaan overrulen). Een onvolledige laatste periode wordt
weggelaten, anders lijkt die kunstmatig laag.

**2. Methodes vergelijken via backtest.** Vijf voorspelmethodes worden
*eerlijk getest*: we houden recente periodes achter, laten elke methode die
voorspellen, en vergelijken met wat er écht gebeurde (rolling-origin
backtest). De fout per methode zie je in de tabel hieronder onder de
grafiek. Lager = beter.

**3. Beste combinatie kiezen.** De twee methodes met de laagste fout worden
gecombineerd tot het normbeeld (gemiddelde van hun voorspellingen). Je ziet
de losse methode-lijnen als stippellijnen in de grafiek — zo zie je waar ze
het eens of oneens zijn. Wil je zelf kiezen? Gebruik de methode-selector.

**4. Tolerantieband bepalen.** Rond de verwachte lijn ligt een band: het
*normale bereik*. Die is gebaseerd op hoe ver de werkelijkheid in het
verleden van de voorspelling afweek (quantiles van de residuen), waarbij
**recente** periodes zwaarder wegen. De band is asymmetrisch en hangt niet
zinloos op nul — hij volgt het huidige regime.

**5. Afwijkingen markeren.** Elke waarneming buiten de band wordt
gemarkeerd: rood = boven, blauw-driehoek = onder. Dát zijn de punten die
aandacht verdienen omdat ze afwijken van wat normaal is voor deze regio.

---

**De vijf voorspelmethodes:**
"""
        )
        for m_key, m_label in PREDICTION_METHODS.items():
            details = PREDICTION_METHOD_DETAILS.get(m_key, {})
            st.markdown(
                f"**{m_label}**  \n"
                f"{details.get('summary', '')}  \n"
                f"*Geschikt voor*: {details.get('good_for', '')}  \n"
                f"*Niet geschikt voor*: {details.get('not_good_for', '')}  \n"
                f"<span style='color: {P['text_muted']}; font-size: 0.85rem;'>"
                f"Technisch: {details.get('technical', '')}</span>",
                unsafe_allow_html=True,
            )
            st.markdown("")

    # Herbereken normbeeld met categorie-filter / nieuwe methodes.
    # Zonder handmatige keuze: backtest kiest de empirisch beste methodes.
    cat_filter = tuple(selected_cats) if selected_cats else None
    methods_for_view = st.session_state.nb_methods_override
    methods_key = (
        "auto" if methods_for_view is None else ",".join(methods_for_view)
    )
    nb_view = cached_detail_normbeeld(
        dataset_id, storage.dataset_data_hash(dataset_id),
        location, cat_filter,
        st.session_state.horizon_days, methods_key, aggregation,
        _dataset_meta(dataset_id).get("gap_policy", "zero"),
    )
    if nb_view is None:
        st.warning(t("nb_no_data"))
        return

    # Statistieken met expliciete tijdseenheid
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Verwacht per {unit}", f"{nb_view.expected_value:.1f}")
    c2.metric(f"Band (per {unit})",
              f"{nb_view.lower_band:.0f} – {nb_view.upper_band:.0f}")
    c3.metric("Recente afwijkingen", nb_view.n_recent_deviations)

    hist_series = nb_view.historical.set_index("date")["actual"]
    markers = _event_markers()

    st.caption(t("nb_band_explained"))
    render_normbeeld_chart(
        nb_view, theme=st.session_state.ui_theme, height=520,
        markers=markers,
        scenario_pct=float(st.session_state.get("nb_scenario", 0) or 0),
    )

    # Scenario-verkenning (what-if op de voorspelling)
    with st.expander("Scenario-verkenning (wat als de activiteit verandert?)"):
        st.slider(
            "Verwachte activiteit aanpassen (%)",
            min_value=-50, max_value=100, value=0, step=10,
            key="nb_scenario",
            help="Tekent een extra lijn in de grafiek: de voorspelling als "
                 "de activiteit met dit percentage verschuift. Puur "
                 "verkennend — geen modeluitspraak.",
        )

    # Signalen: patronen die je niet aan losse punten ziet
    signals = collect_signals(nb_view.historical, nb_view.aggregation)
    if signals:
        st.markdown("<div class='section-label'>Signalen</div>",
                    unsafe_allow_html=True)
        for sig in signals:
            if sig["type"] == "variability":
                icon_txt = "◆"
                if sig["richting"] == "grilliger":
                    txt = (f"**Activiteit is grilliger dan normaal** — de "
                           f"recente spreiding ({sig['recent_std']:.1f}) ligt "
                           f"boven {sig['pctl'] * 100:.0f}% van de historie "
                           f"(typisch {sig['typical_std']:.1f}).")
                else:
                    txt = (f"**Activiteit is opvallend vlak** — recente "
                           f"spreiding {sig['recent_std']:.1f} vs. typisch "
                           f"{sig['typical_std']:.1f}. Kan duiden op "
                           f"veranderde melding of rapportage.")
            elif sig["type"] == "persistence":
                icon_txt = "▶"
                txt = (f"**Al {sig['run']} periodes op rij "
                       f"{sig['richting']} de verwachting** (sinds "
                       f"{sig['sinds'].strftime('%d-%m-%Y')}; kans op toeval "
                       f"~{sig['p'] * 100:.1f}%). Mogelijk een blijvende "
                       f"verschuiving in plaats van losse uitschieters.")
            elif sig["type"] == "change":
                icon_txt = "▲" if sig["direction"] == "stijging" else "▼"
                txt = (f"**Structurele {sig['direction']} rond "
                       f"{pd.Timestamp(sig['date']).strftime('%d-%m-%Y')}** — "
                       f"niveau ging van ~{sig['before']:.0f} naar "
                       f"~{sig['after']:.0f} per periode.")
            elif sig["type"] == "similar":
                icon_txt = "≈"
                txt = (f"**De huidige situatie lijkt het meest op "
                       f"{sig['start'].strftime('%d-%m-%Y')} t/m "
                       f"{sig['end'].strftime('%d-%m-%Y')}** "
                       f"(vorm-correlatie {sig['corr']:.2f} over "
                       f"{sig['window']} periodes). Wat gebeurde er toen?")
            else:
                continue
            st.markdown(f"{icon_txt} {txt}")

    # Eigen markeringen beheren (bv. staakt-het-vuren, beleidswijziging)
    _render_markers_manager(key_prefix="nb")

    # Seizoens-indicatie in tekst
    season = seasonality_profile(hist_series, nb_view.aggregation)
    if season:
        st.markdown(
            f"**Seizoenspatroon** — drukst rond *{season['peak']}*, "
            f"rustigst rond *{season['trough']}* "
            f"(±{season['amplitude_pct']:.0f}% verschil)."
        )

    # Waarschuw als methodes zijn geskipt (mét reden)
    if nb_view.methods_skipped:
        reasons = "; ".join(
            f"{PREDICTION_METHODS.get(m, m)}: "
            f"{nb_view.skip_reasons.get(m, 'onbekend')}"
            for m in nb_view.methods_skipped
        )
        st.warning(f"Niet uitgevoerd — {reasons}")

    # Voorspelnauwkeurigheid (backtest) — eerlijkheid over hoe goed dit werkt
    if nb_view.backtest_scores:
        st.markdown(
            "<div class='section-label'>Voorspelnauwkeurigheid (backtest)</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Elke methode is getest door recente periodes achter te houden, "
            "te voorspellen en te vergelijken met de werkelijkheid. Lager = "
            "beter. Het normbeeld combineert de beste twee methodes."
        )
        bt_rows = [
            {
                "Methode": PREDICTION_METHODS.get(k, k),
                "Gem. voorspelfout": f"{v:.0f}%",
                "Gebruikt": "✓" if k in nb_view.methods_used else "",
            }
            for k, v in sorted(
                nb_view.backtest_scores.items(), key=lambda x: x[1]
            )
        ]
        st.dataframe(pd.DataFrame(bt_rows), use_container_width=True,
                     hide_index=True)

    st.markdown(
        f"<div style='margin-top: 0.5rem; font-size: 0.92rem;'>"
        f"<strong>Patroon:</strong> "
        f"{_html.escape(nb_view.pattern_description)}</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Actieve methodes: {nb_view.method_used} · "
        f"{nb_view.n_history_periods} {AGGREGATIONS[nb_view.aggregation][2]} historie"
    )
    # Betrouwbaarheids-laag: hoe goed past de band feitelijk op deze reeks?
    trust_bits = []
    if nb_view.band_coverage is not None and nb_view.band_alpha is not None:
        expected_cov = (1.0 - 2.0 * nb_view.band_alpha) * 100
        trust_bits.append(
            f"Banddekking: {nb_view.band_coverage * 100:.0f}% van de historie "
            f"binnen de band (doel ≈ {expected_cov:.0f}%)"
        )
    if nb_view.band_model == "poisson":
        trust_bits.append("band: Poisson-interval (schaarse telling-data)")
    elif nb_view.band_model == "negbin":
        disp = (f", dispersie {nb_view.dispersion:.1f}"
                if nb_view.dispersion else "")
        trust_bits.append(
            f"band: negatief-binomiaal (telling-data met clustering{disp})"
        )
    if nb_view.widening_source == "backtest":
        trust_bits.append(
            "voorspelband verbreedt met de horizon o.b.v. gemeten backtest-fout"
        )
    elif nb_view.widening_source == "default":
        trust_bits.append(
            "voorspelband verbreedt met de horizon (conservatieve default)"
        )
    if trust_bits:
        st.caption(" · ".join(trust_bits))
    return nb_view


def _render_afwijkingen_section(nb_view, result, alerts, ds: dict,
                                location: str, unit: str):
    """Eén samenhangende afwijkingen-sectie: (1) deze regio, (2) alle
    regio's incl. ensemble-bevindingen, (3) kaart bij geo-data."""
    st.divider()
    st.markdown("<div class='section-label'>Afwijkingen</div>",
                unsafe_allow_html=True)

    res = result.results
    has_geo = (
        "lat" in res.columns and "lon" in res.columns
        and res["lat"].notna().any() and res["lon"].notna().any()
    )
    labels = ["Deze regio", "Alle regio's"]
    if has_geo:
        labels.append("Kaart")
    tabs = st.tabs(labels)

    # --- Tab 1: afwijkingen van de geselecteerde regio (band-gebaseerd) ---
    with tabs[0]:
        dev = nb_view.historical[
            nb_view.historical["status"] != "normaal"
        ].sort_values("date", ascending=False)
        if dev.empty:
            st.caption("Geen afwijkingen van het normbeeld in deze regio.")
        else:
            st.caption(
                f"{len(dev)} waarnemingen buiten het normbeeld "
                f"(hele historie, meest recent eerst)."
            )
            for i, (_, row) in enumerate(dev.head(10).iterrows()):
                d_str = pd.Timestamp(row["date"]).strftime("%d-%m-%Y")
                richting = "boven band" if row["status"] == "boven" else "onder band"
                st.markdown(
                    f"<div class='alert-row'>{d_str} · "
                    f"<strong>{row['actual']:.0f}</strong> per {unit} "
                    f"({richting}, verwacht {_fmt_num(row['lower'])}–{_fmt_num(row['upper'])}) "
                    f"· {_pctl_label(row)}</div>",
                    unsafe_allow_html=True,
                )
                _render_annotation_widget(
                    ds["id"], pd.Timestamp(row["date"]).date().isoformat(),
                    location, key_suffix=f"reg_{i}",
                )

    # --- Tab 2: dataset-breed — recente alerts + ensemble-bevindingen ---
    with tabs[1]:
        if alerts:
            st.markdown("**Recente afwijkingen (alle regio's)**")
            rows = ""
            for a in alerts[:10]:
                arrow = "boven band" if a["richting"] == "boven" else "onder band"
                extr = a.get("extremer_dan")
                extr_txt = (f" · extremer dan {min(extr, 0.99)*100:.0f}% v.d. historie"
                            if extr is not None else "")
                rows += (
                    f"<div class='alert-row'>"
                    f"{pd.Timestamp(a['datum']).strftime('%d-%m-%Y')} · "
                    f"{_html.escape(str(a['locatie']))} · "
                    f"{a['waarde']} ({arrow}, verwacht "
                    f"{a['lower']:.0f}–{a['upper']:.0f}){extr_txt}</div>"
                )
            st.markdown(rows, unsafe_allow_html=True)
        else:
            st.caption("Geen recente afwijkingen in de dataset.")

        # Ensemble-bevindingen: 5 detectie-algoritmes stemmen
        findings = build_findings(result, top_n=40)
        strong = [f for f in findings if f["severity"] in ("hoog", "midden")]
        weak = [f for f in findings if f["severity"] == "laag"]
        if strong:
            st.markdown("**Bevestigd door meerdere detectie-algoritmes**")
            st.caption(
                "Naast het normbeeld draaien 5 detectie-"
                "algoritmes. Punten waar een meerderheid het over eens is. "
                f"Gevoeligheid automatisch afgesteld op '{result.sensitivity_used}' "
                f"({result.iterations} iteratie(s)) — de lijst toont de meest "
                "opvallende punten van déze dataset, geen absolute significantie."
            )
            sev_color = {"hoog": P["high"], "midden": P["mid"]}
            for i, f in enumerate(strong[:6]):
                exp = f["explanation"]
                st.markdown(
                    f"""
                    <div class="finding-card"
                         style="--card-color: {sev_color[f['severity']]};">
                        <div class="finding-header">
                            <span class="severity-pill severity-{f['severity']}">
                                {f['severity'].upper()}</span>
                            <span class="finding-loc">{_html.escape(str(f['locatie']))}</span>
                            <span class="finding-date">
                                {pd.Timestamp(f['datum']).strftime('%d-%m-%Y')}</span>
                        </div>
                        <div class="finding-stat">{_html.escape(exp['observation'])}</div>
                        <div class="finding-meta">
                            {f['stemmen']}/{f['totaal_methodes']} algoritmes:
                            {_html.escape(', '.join(f['methodes_aan']))}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                _render_annotation_widget(ds["id"], f["datum"],
                                          str(f["locatie"]),
                                          key_suffix=f"ens_{i}")
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

    # --- Tab 3: kaart (geomap + heatmap) ---
    if has_geo:
        with tabs[2]:
            from core.registry import get_visualizations
            vizs = get_visualizations()
            for name, v in vizs.items():
                if "kaart" in name.lower():
                    v.render(res, time_col="timestamp", value_col="value")
            for name, v in vizs.items():
                if "heatmap" in name.lower():
                    st.markdown(f"**{v.name}**")
                    v.render(res, time_col="timestamp", value_col="value")
