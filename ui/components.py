"""Gedeelde UI-componenten: topbar, opgeslagen weergaves, markeringen,
demo-loader, leeg-scherm, annotatie-widget."""
from __future__ import annotations

import contextlib
import html as _html
from pathlib import Path

import pandas as pd
import streamlit as st

from core import annotations as anno
from core import storage
from core.import_data import apply_mapping
from i18n.nl import t
from ui.theme import P


def _render_saved_views():
    """Analytische workflows opslaan/herladen: dataset + regio + categorieën
    + methode-preset + horizon + tijdschaal in één klik terug."""
    try:
        views = storage.list_views()
    except Exception:
        views = []
    title = f"Opgeslagen weergaves ({len(views)})" if views \
        else "Weergave opslaan"
    with st.expander(title):
        c1, c2 = st.columns([2, 1])
        with c1:
            name = st.text_input(
                "Naam voor huidige weergave", key="sv_name",
                placeholder="bv. Kyiv wekelijks — seizoensmodel",
            )
        with c2:
            st.write("")
            st.write("")
            if st.button("Opslaan", key="sv_save", use_container_width=True,
                         type="secondary"):
                if name.strip():
                    storage.save_view(name.strip(), {
                        "dataset_id": st.session_state.active_dataset_id,
                        "location": st.session_state.nb_selected_location,
                        "categories": st.session_state.nb_selected_categories,
                        "preset": st.session_state.nb_preset,
                        "methods": st.session_state.nb_methods_override,
                        "horizon": st.session_state.horizon_days,
                        "aggregation": st.session_state.aggregation,
                    })
                    st.success("Weergave opgeslagen.")
                    st.rerun()
                else:
                    st.warning("Geef een naam op.")
        for v in views:
            p = v["payload"]
            cc1, cc2, cc3 = st.columns([3, 1, 1])
            with cc1:
                st.markdown(_html.escape(v["name"]))
            with cc2:
                if st.button("Laden", key=f"sv_ld_{v['id']}",
                             use_container_width=True, type="secondary"):
                    st.session_state.active_dataset_id = p.get("dataset_id")
                    st.session_state.nb_selected_location = p.get("location")
                    st.session_state.nb_selected_categories = p.get(
                        "categories") or []
                    st.session_state.nb_preset = p.get("preset", "auto")
                    st.session_state.nb_methods_override = p.get("methods")
                    st.session_state.horizon_days = int(p.get("horizon", 14))
                    st.session_state.aggregation = p.get("aggregation", "auto")
                    # Widget-keys ook zetten: een al-geïnstantieerde widget
                    # negeert anders zijn index/default bij de rerun.
                    for wk, val in (
                        ("nb_ds_select", p.get("dataset_id")),
                        ("nb_detail_pick", p.get("location")),
                        ("nb_cats_select", p.get("categories") or []),
                        ("nb_preset_pick", p.get("preset", "auto")),
                        ("nb_horizon_input", int(p.get("horizon", 14))),
                        ("nb_agg_pick", p.get("aggregation", "auto")),
                    ):
                        with contextlib.suppress(Exception):
                            st.session_state[wk] = val
                    st.rerun()
            with cc3:
                if st.button("Verwijder", key=f"sv_del_{v['id']}",
                             use_container_width=True):
                    storage.delete_view(v["id"])
                    st.rerun()


def _event_markers() -> list[dict]:
    """Door de analist toegevoegde markeringen, klaar om te plotten."""
    out = []
    try:
        events = storage.list_events()
    except Exception:
        return []
    for e in events:
        try:
            out.append({"date": pd.Timestamp(e["event_date"]),
                        "label": e["label"]})
        except Exception:
            continue
    return out


def _render_markers_manager(key_prefix: str = "mk"):
    """Beheer eigen markeringen: voeg datum + label toe, of verwijder.
    Gedeeld over alle grafieken (een gebeurtenis geldt voor elke reeks)."""
    try:
        events = storage.list_events()
    except Exception:
        events = []
    title = (f"Eigen markeringen ({len(events)})" if events
             else "Eigen markeringen toevoegen")
    with st.expander(title):
        st.caption(
            "Markeer momenten die je zelf wilt tonen (bv. een staakt-het-vuren "
            "of beleidswijziging). Ze verschijnen als verticale lijn in de "
            "grafieken — handig om te zien wat er ná dat moment gebeurde."
        )
        c1, c2, c3 = st.columns([1.2, 2, 1])
        with c1:
            d = st.date_input("Datum", key=f"{key_prefix}_ev_date")
        with c2:
            lbl = st.text_input("Label", key=f"{key_prefix}_ev_label",
                                placeholder="bv. Staakt-het-vuren")
        with c3:
            st.write("")
            st.write("")
            if st.button("Toevoegen", key=f"{key_prefix}_ev_add",
                         use_container_width=True, type="secondary"):
                if lbl.strip():
                    storage.add_event(pd.Timestamp(d).date().isoformat(),
                                      lbl.strip())
                    st.rerun()
                else:
                    st.warning("Geef een label op.")
        for e in events:
            cc1, cc2 = st.columns([5, 1])
            with cc1:
                st.markdown(
                    f"{pd.Timestamp(e['event_date']).strftime('%d-%m-%Y')} — "
                    f"{_html.escape(e['label'])}"
                )
            with cc2:
                if st.button("Verwijder", key=f"{key_prefix}_ev_del_{e['id']}",
                             use_container_width=True):
                    storage.delete_event(e["id"])
                    st.rerun()


def render_classification_bar():
    """Statusstrip bovenaan: waar kijk je naar, hoe vers is het, is het
    afgeschermd. In operationele tools staat dit altijd in beeld, zodat
    niemand per ongeluk oude of open data voor actueel aanziet.
    """
    from core.auth import is_protected

    left = "Interne tool · software in ontwikkeling"

    bits = []
    try:
        datasets = storage.list_datasets()
    except Exception:
        datasets = []
    if datasets:
        active_id = st.session_state.get("active_dataset_id")
        active = next((d for d in datasets if d["id"] == active_id),
                      datasets[0])
        stale_txt, stale_cls = "", "status"
        try:
            df = storage.load_observations(active["id"])
            if not df.empty and "timestamp" in df.columns:
                last = pd.Timestamp(df["timestamp"].max())
                days = (pd.Timestamp.utcnow().tz_localize(None) - last).days
                stale_txt = f"data {days}d oud"
                if days > 30:
                    stale_cls = "warn"
        except Exception:
            pass
        bits.append(f"<span class='{stale_cls}'>{_html.escape(stale_txt)}</span>"
                    if stale_txt else "")
        bits.append(f"{len(datasets)} dataset(s)")

    from core.authz import ROLE_LABELS, current_identity
    ident = current_identity()
    if ident.source == "sso":
        bits.append(f"<span class='status'>{_html.escape(ident.username)} "
                    f"· {ident.role}</span>")
    else:
        bits.append(f"rol {ident.role} <span class='warn'>(geen SSO)</span>")
        _ = ROLE_LABELS
    bits.append("<span class='status'>afgeschermd</span>" if is_protected()
                else "<span class='warn'>open toegang</span>")

    right = " · ".join(b for b in bits if b)
    st.markdown(
        f"<div class='classification-bar'><span>{left}</span>"
        f"<span>{right}</span></div>",
        unsafe_allow_html=True,
    )


def render_topbar(title: str = ""):
    render_classification_bar()
    c1, c2 = st.columns([6, 1])
    with c1:
        if title:
            st.markdown(f"## {title}")
        else:
            st.write("")
    with c2:
        st.markdown("<div class='cog-button' style='text-align:right;'>",
                    unsafe_allow_html=True)
        st.write("")
        if st.button(t("btn_settings"), key="open_settings",
                     use_container_width=True, type="secondary"):
            st.session_state.show_settings = True
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


DEMO_DATASET_NAME = "Demo - Russian missile attacks on Ukraine"


def _try_load_demo_dataset() -> bool:
    """Importeer publieke demo: Russian missile/drone attacks op Oekraïne.
    Bron: open-source data uit kpszsu/PvKPivden Telegram-kanalen."""
    csv_path = Path(__file__).resolve().parent.parent / "data" / "missile_attacks_demo.csv"
    if not csv_path.exists():
        st.error(f"Demo-bestand niet gevonden op {csv_path}")
        return False

    # Check of dezelfde demo al bestaat — voorkom dubbele import
    existing = [d for d in storage.list_datasets()
                if d["name"] == DEMO_DATASET_NAME]
    if existing:
        st.session_state.active_dataset_id = existing[0]["id"]
        st.info("Demo-dataset is al geladen — geactiveerd.")
        return True

    try:
        # Direct via path (geen file-wrapper) — werkt overal
        full_df = pd.read_csv(str(csv_path))
        # Vaste mapping — bekend voor deze dataset
        mapping = {
            "time": "time_start",
            "value": "launched",
            "location_name": "target",
            "category": "model",
            "lat": None,
            "lon": None,
            "extras": ["time_end", "launch_place", "target_main",
                       "destroyed", "not_reach_goal"],
        }
        normalized, stats = apply_mapping(full_df, mapping)
        ds_id = storage.create_dataset(
            DEMO_DATASET_NAME,
            "Open-source data uit kpszsu/PvKPivden Telegram-kanalen "
            "(2022-2026). Per aanval-waarschuwing: tijdstip, doel-regio, "
            "wapen-type en aantal lanceringen.",
            mapping,
        )
        n = storage.insert_observations(ds_id, normalized)
        st.session_state.active_dataset_id = ds_id
        st.cache_data.clear()
        msg = f"Demo geladen ({n} rijen)."
        if stats.get("dropped_total"):
            msg += f" ({stats['dropped_total']} rijen overgeslagen)"
        st.success(msg)
        return True
    except Exception as e:
        st.error(f"Demo-laden mislukt: {type(e).__name__}: {e}")
        import traceback
        st.code(traceback.format_exc())
        return False


def _render_empty_state():
    """Welkomstscherm zonder datasets."""
    st.markdown(
        f"""
        <div style='padding: 40px 32px; text-align: center;
                    background: {P['surface']}; border: 1px solid {P['border']};
                    border-radius: 4px; margin-top: 2rem;'>
            <h2 style='margin: 0 0 8px 0; font-weight: 600;'>Welkom</h2>
            <p style='color: {P['text_muted']}; font-size: 0.95rem; max-width: 540px; margin: 0 auto 24px auto;'>
                Deze tool bouwt een <strong>normbeeld</strong> uit jouw data —
                wat is normaal voor elke locatie — en signaleert afwijkingen
                + voorspelt waar het naartoe gaat.
            </p>
            <p style='color: {P['text_muted']}; font-size: 0.9rem; margin-bottom: 0;'>
                Begin door een dataset te uploaden via <strong>Instellingen</strong>
                of laad de demo-dataset om de tool direct te zien werken.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        st.write("")
        if (st.button("Laad demo-dataset", type="primary",
                      use_container_width=True, key="load_demo_empty")
                and _try_load_demo_dataset()):
            st.rerun()


def _pctl_label(row) -> str:
    """'extremer dan X% van de historie' voor een afwijkings-rij.

    Cap op 99: '100% van de historie' is logisch onmogelijk (het punt zelf
    is deel van de historie) en ondermijnt het vertrouwen in de cijfers.
    """
    pctl = float(row.get("resid_pctl", 0.5))
    extremer = pctl if row["status"] == "boven" else 1.0 - pctl
    return f"extremer dan {min(extremer, 0.99) * 100:.0f}% van de historie"


def _fmt_num(x: float) -> str:
    """Compact getal-formaat: 1 decimaal onder de 10 (zodat een band van
    0.4 niet als '0' toont en de tekst zichzelf niet tegenspreekt)."""
    x = float(x)
    if abs(x) < 10 and abs(x - round(x)) >= 0.05:
        return f"{x:.1f}"
    return f"{x:.0f}"


def _render_annotation_widget(dataset_id: int, date_iso: str, location: str,
                              key_suffix: str):
    """Compacte notitie + status per afwijking (analist-oordeel)."""
    key = anno.finding_key(date_iso, location, None)
    existing = anno.get_annotation(dataset_id, key) or {}
    status_txt = anno.STATUS_LABELS.get(existing.get("status", ""), "")
    label = f"Notitie ({status_txt})" if existing else "Notitie toevoegen"
    with st.expander(label):
        status_opts = list(anno.STATUS_LABELS.keys())
        cur = existing.get("status", "open")
        c1, c2 = st.columns([1, 2])
        with c1:
            new_status = st.selectbox(
                t("anno_status"), status_opts,
                format_func=lambda s: anno.STATUS_LABELS[s],
                index=status_opts.index(cur) if cur in status_opts else 0,
                key=f"an_st_{key_suffix}",
            )
        with c2:
            new_note = st.text_area(
                t("anno_note"), value=existing.get("note", ""),
                key=f"an_nt_{key_suffix}", height=70,
            )
        if st.button(t("anno_save"), key=f"an_sv_{key_suffix}",
                     type="secondary", disabled=not may("annotate")):
            anno.save_annotation(dataset_id, key, new_note, new_status)
            st.success(t("anno_saved"))


def may(action: str) -> bool:
    """Mag de huidige gebruiker deze actie? Voor het uitschakelen van
    knoppen in de UI.

    Let op: dit is gemak, geen beveiliging. De echte controle staat in de
    API (core/authz.py + api/main.py). Een verborgen knop houdt niemand
    tegen die zelf een verzoek stuurt.
    """
    from core.authz import current_identity
    return current_identity().can(action)


def deny_notice(action: str) -> None:
    """Toon waarom een actie niet beschikbaar is."""
    from core.authz import PERMISSIONS, current_identity
    ident = current_identity()
    st.info(
        f"Hiervoor is de rol **{PERMISSIONS.get(action, '?')}** nodig; "
        f"jij hebt **{ident.role}**. Vraag een beheerder om je groep aan "
        f"te passen."
    )
