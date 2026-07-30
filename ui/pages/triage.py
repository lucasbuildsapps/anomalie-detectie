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
from ui.cache import cached_analysis, cached_comovement, code_version
from ui.components import (
    _render_annotation_widget,
    _render_empty_state,
    deny_notice,
    may,
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


def _render_evaluation(dataset_id: int):
    """Meet de detectoren tegen bevestigde bevindingen.

    Dit is de stap van 'de tool produceert signalen' naar 'we weten welke
    methode op deze data werkt'. Labels komen uit het normale triage-werk:
    alles wat als bevestigd of geescaleerd is gemarkeerd.
    """
    from core.evaluation import (
        evaluate_detectors,
        incidents_from_annotations,
        summarize,
        to_frame,
    )

    incidents = incidents_from_annotations(dataset_id)
    with st.expander(
        f"Welke detector werkt op deze data? ({len(incidents)} bevestigde "
        f"incidenten als ijkpunt)"
    ):
        if len(incidents) < 3:
            st.caption(
                "Nog te weinig ijkpunten. Markeer bevindingen als "
                "'bevestigd' of 'geëscaleerd'; vanaf ongeveer drie kan hier "
                "per detector recall en precisie berekend worden."
            )
            return
        df = storage.load_observations(dataset_id)
        scores = evaluate_detectors(df, incidents)
        st.markdown(summarize(scores))
        st.dataframe(to_frame(scores), use_container_width=True,
                     hide_index=True)
        st.caption(
            "**Recall** = welk deel van de bekende incidenten is opgemerkt "
            "(missers zijn meestal duurder dan vals alarm). **Precisie** = "
            "welk deel van de meldingen was raak. Let op: alleen wat "
            "gelabeld is telt mee — een terechte melding zonder label "
            "verschijnt hier als vals alarm."
        )


def _render_changes(dataset_id: int):
    """Wat is er veranderd sinds de vorige beoordeling?

    Staat bovenaan omdat dit is wat een terugkerende analist als eerste
    wil weten. Zonder dit vergelijk je op geheugen, en daar vallen dingen
    weg. ICD 203 vraagt bovendien om wijzigingen t.o.v. eerdere oordelen
    expliciet te benoemen.
    """
    from core.changes import since_last, summarise

    try:
        changes, previous = since_last(dataset_id)
    except Exception:
        return
    if previous is None:
        return

    kleur = {"nieuw": P["high"], "niveau": P["mid"],
             "vertrouwen": P["mid"], "model": P["accent"],
             "verdwenen": P["text_muted"]}
    st.markdown("<div class='section-label'>Sinds de vorige beoordeling</div>",
                unsafe_allow_html=True)
    st.caption(summarise(changes, previous))
    for c in changes[:10]:
        st.markdown(
            f"<div class='alert-row'>"
            f"<span style='color:{kleur.get(c.kind, P['text_muted'])};'>"
            f"{'●' if c.important else '○'}</span> "
            f"<strong>{_html.escape(c.subject)}</strong> — "
            f"{_html.escape(c.description)}</div>",
            unsafe_allow_html=True,
        )


def _render_watchboard(ds: dict, normbeelds: dict):
    """Watchboard: wat hadden we vóóraf afgesproken in de gaten te houden?

    Dit staat bewust bovenaan de triage. De bevindingenlijst is inductief
    ('wat valt op?'); dit is deductief ('gebeurt waar we op letten?').
    Dat laatste is navolgbaar en daarmee verdedigbaar.
    """
    from core.indicators import (
        CONDITIONS,
        NEEDS_THRESHOLD,
        Indicator,
        evaluate_all,
        summarise,
    )

    try:
        rows = storage.list_indicators(ds["id"])
    except Exception as e:
        st.error(f"Indicatoren laden mislukt: {e}")
        return

    indicators = [
        Indicator(
            name=r["name"], dataset_id=r["dataset_id"], condition=r["condition"],
            location=r["location"], category=r["category"],
            threshold=r["threshold"], periods=r["periods"] or 1,
            meaning=r["meaning"] or "", enabled=bool(r["enabled"]), id=r["id"],
        )
        for r in rows
    ]
    states = evaluate_all(indicators, normbeelds or {})

    st.markdown("<div class='section-label'>Watchboard</div>",
                unsafe_allow_html=True)
    st.caption(summarise(states))

    for state in states:
        ind = state.indicator
        color = P["high"] if state.active else P["text_muted"]
        label = "ACTIEF" if state.active else "RUST"
        sinds = (f" · sinds {state.since:%d-%m-%Y}"
                 if state.active and state.since is not None else "")
        st.markdown(
            f"""
            <div class="finding-card" style="--card-color: {color};">
                <div class="finding-header">
                    <span class="severity-pill" style="background: {color};">
                        {label}</span>
                    <span class="finding-loc">{_html.escape(ind.name)}</span>
                    <span class="finding-date">{_html.escape(ind.describe())}
                        {sinds}</span>
                </div>
                <div class="finding-stat">{_html.escape(state.evidence)}</div>
                {f'<div class="finding-meta">Betekenis: '
                 f'{_html.escape(ind.meaning)}</div>' if ind.meaning else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Indicator toevoegen of beheren"):
        if not may("annotate"):
            deny_notice("annotate")
            return
        st.caption(
            "Schrijf vooraf op wát ertoe doet. Dat is de kern van "
            "waarschuwingswerk: achteraf is bij elke uitschieter wel een "
            "verhaal te bedenken, vooraf niet."
        )
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Naam", key="wb_name",
                                 placeholder="bv. Stilte rond Kyiv")
            cond_keys = list(CONDITIONS)
            condition = st.selectbox(
                "Voorwaarde", cond_keys,
                format_func=lambda k: CONDITIONS[k], key="wb_cond")
            periods = st.number_input("Perioden achtereen", min_value=1,
                                      max_value=60, value=1, key="wb_per")
        with c2:
            regios = sorted(normbeelds or {})
            regio = st.selectbox("Regio", ["(alle regio's)"] + regios,
                                 key="wb_loc")
            threshold = None
            if condition in NEEDS_THRESHOLD:
                threshold = st.number_input(
                    "Drempel", value=0.0, key="wb_thr",
                    help="Absolute waarde, of percentage bij 'stijging'.")
            meaning = st.text_input(
                "Betekenis als dit afgaat", key="wb_meaning",
                placeholder="bv. mogelijk hergroepering")

        if st.button("Indicator vastleggen", type="primary", key="wb_add"):
            if not name.strip():
                st.warning("Geef de indicator een naam.")
            else:
                storage.add_indicator(
                    ds["id"], name.strip(), condition,
                    location=None if regio == "(alle regio's)" else regio,
                    threshold=threshold, periods=int(periods),
                    meaning=meaning.strip(),
                )
                st.success("Vastgelegd — met datum en naam, zodat achteraf "
                           "aantoonbaar is dat dit vooraf is bepaald.")
                st.rerun()

        for r in rows:
            cc1, cc2 = st.columns([5, 1])
            with cc1:
                st.markdown(
                    f"{'✓' if r['enabled'] else '—'} **{_html.escape(r['name'])}**"
                    f" · {_html.escape(str(r['condition']))}"
                )
            with cc2:
                if st.button("Verwijder", key=f"wb_del_{r['id']}",
                             use_container_width=True):
                    storage.delete_indicator(r["id"])
                    st.rerun()


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


def _render_peer_deviations(dataset_id: int, aggregation: str):
    """Regio's die uit de pas lopen met hun eigen peer-groep.

    Ander signaal dan het normbeeld: stijgen alle regio's samen, dan is
    dat landelijk (of een rapportage-wijziging). Stijgt er één terwijl
    zijn peers vlak blijven, dan is dat lokaal — meestal interessanter.
    """
    corr, devs = cached_comovement(
        dataset_id, storage.dataset_data_hash(dataset_id), aggregation,
        code_version=code_version(),
    )
    if corr is None:
        return

    st.markdown("<div class='section-label'>Uit de pas met vergelijkbare "
                "regio's</div>", unsafe_allow_html=True)
    if not devs:
        st.caption(
            "Geen regio wijkt momenteel af van zijn peer-groep. Let op: als "
            "álle regio's tegelijk stijgen, verschijnt hier niets — dat is "
            "dan een landelijk patroon, geen lokaal signaal."
        )
    else:
        st.caption(
            f"{len(devs)} regio('s) bewegen anders dan de regio's waarmee ze "
            f"normaal meelopen."
        )
        for d in devs[:6]:
            color = P["high"] if d.direction == "boven" else P["accent"]
            peers = ", ".join(d.peers[:4])
            if len(d.peers) > 4:
                peers += f" (+{len(d.peers) - 4})"
            st.markdown(
                f"""
                <div class="finding-card" style="--card-color: {color};">
                    <div class="finding-header">
                        <span class="severity-pill" style="background: {color};">
                            {d.direction.upper()} PEERS</span>
                        <span class="finding-loc">
                            {_html.escape(d.region)}</span>
                        <span class="finding-date">
                            {abs(d.recent_z):.1f}σ</span>
                    </div>
                    <div class="finding-stat">
                        Recent {d.recent_value:.1f} per periode, terwijl
                        vergelijkbare regio's op {d.peer_expected:.1f} zitten.
                    </div>
                    <div class="finding-meta">
                        peer-groep: {_html.escape(peers)} ·
                        gem. samenhang {d.peer_correlation:.2f}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with st.expander("Samenhang tussen regio's (correlatiematrix)"):
        st.caption(
            "Hoe sterk bewegen regio's samen, over de hele historie "
            "(genormaliseerd, zodat een grote regio de matrix niet "
            "domineert). 1.00 = identiek patroon, 0 = geen samenhang."
        )
        st.dataframe(corr.round(2), use_container_width=True)


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
    _render_changes(ds["id"])
    _render_performance(ds["id"])
    _render_evaluation(ds["id"])

    gap_policy = (ds["column_mapping"] or {}).get("gap_policy", "zero")
    cached = cached_analysis(
        ds["id"], storage.dataset_data_hash(ds["id"]),
        st.session_state.horizon_days, st.session_state.aggregation,
        "auto", gap_policy, code_version=code_version(),
    )
    if cached is None:
        st.warning("Dataset is leeg.")
        return
    _, _, result, normbeelds, alerts, effective_agg = cached

    triage = anno.triage_counts(ds["id"], alerts)
    if triage["total"]:
        st.caption(
            f"{triage['unhandled']} van {triage['total']} recente afwijkingen "
            f"nog onbehandeld."
        )

    _render_watchboard(ds, normbeelds)
    _render_findings(result, ds)
    _render_agreement(result)
    _render_peer_deviations(ds["id"], effective_agg)
