"""Instellingen-overlay: datasets, upload + mapping, expert, weergave."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import storage
from core.auto_mapping import guess_mapping
from core.import_data import apply_mapping, read_table
from core.registry import get_detectors
from core.validation import validate_mapped
from i18n.nl import t
from ui.components import _try_load_demo_dataset


def page_settings():
    c1, c2 = st.columns([5, 1])
    with c1:
        st.markdown("## " + t("settings_title"))
    with c2:
        if st.button(t("settings_close"), key="close_settings",
                     use_container_width=True, type="primary"):
            st.session_state.show_settings = False
            st.rerun()

    tabs = st.tabs([
        t("settings_tab_datasets"),
        t("settings_tab_upload"),
        t("settings_tab_expert"),
        t("settings_tab_theme"),
        t("settings_tab_bronnen"),
        t("settings_tab_admin"),
    ])
    with tabs[0]:
        _settings_datasets()
    with tabs[1]:
        _settings_upload()
    with tabs[2]:
        _settings_expert()
    with tabs[3]:
        _settings_theme()
    with tabs[4]:
        _settings_bronnen()
    with tabs[5]:
        _settings_admin()


def _settings_bronnen():
    """Automatische bronnen: testen, handmatig draaien, status bekijken.

    Zonder deze knoppen is de connector-laag onzichtbaar: je kunt niet
    controleren of een bron werkt zonder de worker te starten.
    """
    from connectors.base import get_connectors
    from core.ingest import run_connector

    st.markdown("**Automatische data-inwinning**")
    st.caption(
        "Elke bron is een plug-in in `connectors/`. Test hem hier, draai "
        "hem handmatig, en zet `enabled = True` in het bestand zodra hij "
        "werkt — dan pakt de geplande inwinning hem op."
    )

    try:
        conns = get_connectors()
    except Exception as e:
        st.error(f"Connectors laden mislukt: {e}")
        return
    if not conns:
        st.info("Geen connectors gevonden.")
        return

    for name, c in sorted(conns.items()):
        status = "actief (gepland)" if c.enabled else "uit"
        with st.expander(f"{name} — {status}"):
            st.caption(c.description or "(geen omschrijving)")
            st.caption(
                f"Doel-dataset: **{c.dataset_name}** · interval: "
                f"{c.schedule_minutes} min"
            )
            missing = c.missing_config()
            if missing:
                st.warning(
                    f"Nog niet bruikbaar — ontbrekende instelling(en): "
                    f"`{'`, `'.join(missing)}`. Zet die als secret "
                    f"(Streamlit Cloud → Settings → Secrets) of env-var."
                )

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Verbinding testen", key=f"cs_test_{name}",
                             use_container_width=True):
                    with st.spinner("Bron benaderen..."):
                        ok, msg = c.self_test()
                    (st.success if ok else st.error)(msg)
            with c2:
                if st.button("Nu ophalen", key=f"cs_run_{name}",
                             type="primary", use_container_width=True):
                    with st.spinner("Ophalen en opslaan..."):
                        summary = run_connector(c)
                    if summary["status"] == "ok":
                        st.success(
                            f"{summary['rows_added']} nieuwe rijen "
                            f"({summary['rows_offered']} aangeboden, rest was "
                            f"al bekend)."
                        )
                        st.cache_data.clear()
                    else:
                        st.error(summary["error"])

            runs = storage.list_ingest_runs(name, limit=5)
            if runs:
                st.dataframe(
                    pd.DataFrame(runs)[["started_at", "status", "rows_added",
                                        "error"]],
                    use_container_width=True, hide_index=True,
                )


def _render_source_health():
    """Gezondheid van geautomatiseerde bronnen: laatste run, status, rijen."""
    try:
        health = storage.source_health()
    except Exception:
        health = []
    if not health:
        return
    st.markdown("**Automatische bronnen**")
    rows = []
    for h in health:
        status = "OK" if h.get("last_status") == "ok" else "FOUT"
        rows.append({
            "Bron": h["source"],
            "Status": status,
            "Laatste run": str(h.get("last_run") or "-"),
            "Nieuwe rijen": h.get("last_rows_added"),
            "Fout": (h.get("last_error") or "")[:120],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.divider()



def _settings_admin():
    """Beheer: audit-trail en inwinning-runs (wie deed wat, draait alles?)."""
    st.markdown("**Audit-trail**")
    st.caption(
        "Elke muterende actie en login-poging. Identiteit komt van de "
        "SSO-proxy (X-Forwarded-User); zonder proxy staat er 'onbekend'."
    )
    try:
        audit = storage.list_audit(limit=500)
    except Exception as e:
        st.error(f"Audit-log lezen mislukt: {e}")
        audit = []
    if not audit:
        st.info("Nog geen audit-regels.")
    else:
        adf = pd.DataFrame(audit)
        actions = sorted(adf["action"].unique())
        pick = st.multiselect(
            "Filter op actie", actions, default=[], key="adm_act",
            help="Leeg = alles tonen.",
        )
        if pick:
            adf = adf[adf["action"].isin(pick)]
        show = adf[["ts", "username", "action", "object_type",
                    "object_id", "detail", "client"]].rename(columns={
            "ts": "Tijd (UTC)", "username": "Gebruiker", "action": "Actie",
            "object_type": "Type", "object_id": "Object",
            "detail": "Detail", "client": "Client",
        })
        st.dataframe(show, use_container_width=True, hide_index=True,
                     height=380)

    st.divider()
    st.markdown("**Analyse-momentopnames**")
    st.caption(
        "Wat zei de tool wanneer? Elke geslaagde inwinning legt de stand "
        "vast (afwijkingen + normbeeld per regio), zodat een eerder oordeel "
        "achteraf te verantwoorden is."
    )
    try:
        snaps = storage.list_snapshots(limit=100)
    except Exception:
        snaps = []
    if not snaps:
        st.caption("Nog geen momentopnames.")
    else:
        sdf = pd.DataFrame(snaps)[
            ["created_at", "dataset_id", "label", "n_alerts", "n_rows",
             "aggregation", "created_by"]
        ].rename(columns={
            "created_at": "Moment (UTC)", "dataset_id": "Dataset",
            "label": "Aanleiding", "n_alerts": "Afwijkingen",
            "n_rows": "Rijen", "aggregation": "Tijdschaal",
            "created_by": "Door",
        })
        st.dataframe(sdf, use_container_width=True, hide_index=True)
        pick = st.number_input(
            "Momentopname openen (id)", min_value=0, value=0, step=1,
            key="adm_snap_id",
            help="Id uit de tabel hierboven; 0 = niets openen.",
        )
        if pick:
            snap = storage.get_snapshot(int(pick))
            if snap is None:
                st.warning("Geen momentopname met dat id.")
            else:
                st.json(snap["payload"], expanded=False)

    st.divider()
    st.markdown("**Inwinning-runs (automatische bronnen)**")
    try:
        runs = storage.list_ingest_runs(limit=100)
    except Exception:
        runs = []
    if not runs:
        st.caption(
            "Nog geen runs. Connectors leven in connectors/ ; de worker "
            "(ingest_worker.py) draait ze op schema."
        )
    else:
        rdf = pd.DataFrame(runs)[
            ["started_at", "source", "status", "rows_offered",
             "rows_added", "error"]
        ].rename(columns={
            "started_at": "Gestart (UTC)", "source": "Bron",
            "status": "Status", "rows_offered": "Aangeboden",
            "rows_added": "Nieuw", "error": "Fout",
        })
        st.dataframe(rdf, use_container_width=True, hide_index=True)

def _settings_datasets():
    _render_source_health()
    datasets = storage.list_datasets()
    if not datasets:
        st.info("Geen datasets aanwezig. Gebruik tab Upload.")
        return
    for ds in datasets:
        with st.expander(ds["name"]):
            st.caption(f"Aangemaakt: {ds['created_at']}")
            st.write(ds["description"] or "Geen omschrijving.")
            st.json(ds["column_mapping"], expanded=False)
            c1, c2 = st.columns(2)
            with c1:
                upd = st.file_uploader(
                    "Bijwerken (Excel/CSV)",
                    type=["xlsx", "xls", "csv"],
                    key=f"upd_{ds['id']}",
                )
                if upd is not None and st.button(
                    "Toevoegen", key=f"updbtn_{ds['id']}",
                    use_container_width=True,
                ):
                    try:
                        full_df = read_table(upd)
                        normalized, stats = apply_mapping(full_df, ds["column_mapping"])
                        n = storage.insert_observations(ds["id"], normalized)
                        msg = f"{n} nieuwe rijen toegevoegd."
                        if stats["dropped_total"] > 0:
                            msg += f" ({stats['dropped_total']} rijen overgeslagen — ongeldige timestamps)"
                        st.success(msg)
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Bijwerken mislukt: {e}")
            with c2:
                if st.button(t("btn_delete"), key=f"del_{ds['id']}",
                             use_container_width=True):
                    storage.delete_dataset(ds["id"])
                    st.cache_data.clear()
                    st.success(t("msg_deleted"))
                    st.rerun()

            # --- Metadata: gap-policy + bron-betrouwbaarheid ---
            st.markdown("---")
            st.markdown("**Data-interpretatie & bron**")
            meta = dict(ds["column_mapping"] or {})
            from core.normbeeld import GAP_POLICIES
            gp_keys = list(GAP_POLICIES.keys())
            cur_gp = meta.get("gap_policy", "zero")
            gp = st.selectbox(
                "Wat betekent een periode zonder waarnemingen?",
                gp_keys,
                format_func=lambda k: GAP_POLICIES[k],
                index=gp_keys.index(cur_gp) if cur_gp in gp_keys else 0,
                key=f"gp_{ds['id']}",
                help="Bepaalt hoe gaten in de data de baseline beïnvloeden. "
                     "'0' past bij event-meldingen (geen melding = niets "
                     "gebeurd); 'masker' past bij collectie-uitval (we weten "
                     "het simpelweg niet).",
            )
            c1, c2 = st.columns(2)
            with c1:
                rel_opts = ["", "A", "B", "C", "D", "E", "F"]
                rel_labels = {
                    "": "(niet gezet)", "A": "A — betrouwbaar",
                    "B": "B — meestal betrouwbaar",
                    "C": "C — redelijk betrouwbaar",
                    "D": "D — meestal niet betrouwbaar",
                    "E": "E — onbetrouwbaar", "F": "F — niet te beoordelen",
                }
                cur_rel = meta.get("source_reliability", "")
                rel = st.selectbox(
                    "Bron-betrouwbaarheid", rel_opts,
                    format_func=lambda k, _m=rel_labels: _m[k],
                    index=rel_opts.index(cur_rel) if cur_rel in rel_opts else 0,
                    key=f"rel_{ds['id']}",
                )
            with c2:
                cred_opts = ["", "1", "2", "3", "4", "5", "6"]
                cred_labels = {
                    "": "(niet gezet)", "1": "1 — bevestigd",
                    "2": "2 — waarschijnlijk juist", "3": "3 — mogelijk juist",
                    "4": "4 — twijfelachtig", "5": "5 — onwaarschijnlijk",
                    "6": "6 — niet te beoordelen",
                }
                cur_cred = meta.get("info_credibility", "")
                cred = st.selectbox(
                    "Informatie-geloofwaardigheid", cred_opts,
                    format_func=lambda k, _m=cred_labels: _m[k],
                    index=cred_opts.index(cur_cred) if cur_cred in cred_opts else 0,
                    key=f"cred_{ds['id']}",
                )
            if st.button("Metadata opslaan", key=f"meta_sv_{ds['id']}",
                         type="secondary"):
                meta["gap_policy"] = gp
                meta["source_reliability"] = rel
                meta["info_credibility"] = cred
                storage.update_dataset_mapping(ds["id"], meta)
                st.cache_data.clear()
                st.success("Metadata opgeslagen.")
                st.rerun()

            st.markdown("---")
            st.markdown("**Ruwe data bekijken / bewerken**")
            _render_data_editor(ds)


def _settings_upload():
    # Demo-knop bovenaan
    st.markdown(
        "**Snel beginnen:** laad de meegeleverde demo-dataset "
        "(open-source data: Russian missile/drone attacks op Oekraïne, 2022-2026)."
    )
    if (st.button("Laad demo-dataset", type="secondary",
                  key="load_demo_settings") and _try_load_demo_dataset()):
        st.rerun()
    st.divider()

    uploaded = st.file_uploader(
        "Bron-bestand", type=["xlsx", "xls", "csv"],
        key="upload_settings",
    )
    if not uploaded:
        return
    try:
        full_df = read_table(uploaded)
    except Exception as e:
        st.error(f"Lezen mislukt: {e}")
        return
    st.caption(f"{len(full_df)} rijen · {len(full_df.columns)} kolommen")
    st.dataframe(full_df.head(6), use_container_width=True, hide_index=True)
    _inline_mapping_form(full_df, uploaded.name)


def _inline_mapping_form(full_df: pd.DataFrame, filename: str):
    columns = list(full_df.columns)
    none = t("none_option")
    opt = [none] + columns
    suggested = guess_mapping(full_df)

    def _idx(value): return opt.index(value) if value in opt else 0
    def _req_idx(value): return columns.index(value) if value in columns else 0

    c1, c2 = st.columns(2)
    with c1:
        time_col = st.selectbox(t("field_time"), columns,
                                index=_req_idx(suggested.get("time")), key="m_t")
        category_col = st.selectbox(t("field_category"), opt,
                                    index=_idx(suggested.get("category")), key="m_c")
        lat_col = st.selectbox(t("field_lat"), opt,
                               index=_idx(suggested.get("lat")), key="m_la")
    with c2:
        value_col = st.selectbox(t("field_value"), columns,
                                 index=_req_idx(suggested.get("value")), key="m_v")
        location_col = st.selectbox(t("field_location_name"), opt,
                                    index=_idx(suggested.get("location_name")), key="m_l")
        lon_col = st.selectbox(t("field_lon"), opt,
                               index=_idx(suggested.get("lon")), key="m_lo")

    chosen = {time_col, value_col, category_col, location_col, lat_col, lon_col}
    chosen.discard(none)
    extras = st.multiselect(
        t("field_extras"),
        [c for c in columns if c not in chosen],
        default=[e for e in (suggested.get("extras") or []) if e not in chosen],
        key="m_e",
    )

    c1, c2 = st.columns([2, 3])
    with c1:
        default_name = filename.rsplit(".", 1)[0]
        name = st.text_input(t("dataset_name"), value=default_name, key="ds_n")
    with c2:
        desc = st.text_input(t("dataset_description"), key="ds_d")

    if st.button(t("btn_save"), type="primary", key="save_ds",
                 use_container_width=True):
        if not name.strip():
            st.error(t("msg_need_name"))
            return
        mapping = {
            "time": time_col, "value": value_col,
            "category": None if category_col == none else category_col,
            "location_name": None if location_col == none else location_col,
            "lat": None if lat_col == none else lat_col,
            "lon": None if lon_col == none else lon_col,
            "extras": extras,
        }
        try:
            normalized, stats = apply_mapping(full_df, mapping)

            # Validatie vóór opslag: fouten blokkeren, waarschuwingen tonen.
            report = validate_mapped(normalized, stats)
            for w in report.warnings:
                st.warning(w)
            if not report.ok:
                for err in report.errors:
                    st.error(err)
                return

            dataset_id = storage.create_dataset(name.strip(), desc, mapping)
            n = storage.insert_observations(dataset_id, normalized)
            msg = t("msg_saved", n=n)
            if stats["dropped_total"] > 0:
                msg += (
                    f"  ({stats['dropped_total']} rijen overgeslagen: "
                    f"{stats['dropped_bad_time']} met ongeldige timestamp)"
                )
            st.success(msg)
            st.session_state.active_dataset_id = dataset_id
            st.session_state.show_settings = False
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Opslaan mislukt: {e}")


def _settings_expert():
    st.caption("Handmatige methode en parameters.")
    datasets = storage.list_datasets()
    if not datasets:
        st.info("Geen datasets.")
        return
    by_name = {d["name"]: d for d in datasets}
    ds = by_name[st.selectbox("Dataset", list(by_name.keys()), key="exp_ds")]
    df = storage.load_observations(ds["id"])
    if df.empty:
        return
    detectors = get_detectors()
    det = detectors[st.selectbox("Methode", list(detectors.keys()), key="exp_d")]
    st.caption(det.plain_explanation)
    params = {}
    for pname, spec in det.parameters.items():
        if spec.type == "float":
            params[pname] = st.number_input(
                spec.label, value=float(spec.default), key=f"ex_{pname}",
            )
        elif spec.type == "int":
            params[pname] = st.number_input(
                spec.label, value=int(spec.default), key=f"ex_{pname}",
            )
    if st.button("Run", type="primary", key="exp_run"):
        results = det.detect(df, "timestamp", "value", **params)
        n_anom = int(results["is_anomaly"].sum())
        st.success(f"{n_anom} afwijkingen.")
        with st.expander("Resultaten"):
            st.dataframe(
                results[results["is_anomaly"]].sort_values(
                    "anomaly_score", key=abs, ascending=False),
                use_container_width=True,
            )


def _settings_theme():
    theme = st.radio(
        t("theme_label"),
        [t("theme_light"), t("theme_dark")],
        index=0 if st.session_state.ui_theme == "light" else 1,
        horizontal=True, key="theme_pick",
    )
    new = "light" if theme == t("theme_light") else "dark"
    if new != st.session_state.ui_theme:
        st.session_state.ui_theme = new
        st.rerun()


def _render_data_editor(ds: dict):
    """Bekijk/bewerk de ruwe data van een dataset (gebruikt in Instellingen)."""
    df_raw = storage.load_observations(ds["id"])
    if df_raw.empty:
        st.caption("Deze dataset bevat nog geen rijen.")
        return
    st.caption(t("ds_data_help"))
    full = df_raw.copy()
    if "timestamp" in full.columns:
        full["timestamp"] = pd.to_datetime(full["timestamp"])
        full = full.sort_values("timestamp").reset_index(drop=True)
    max_n = len(full)
    slice_n = st.number_input(
        "Bewerk laatste N rijen",
        min_value=min(50, max_n), max_value=max_n,
        value=min(500, max_n), step=50,
        key=f"editor_n_{ds['id']}",
        help="Oudere rijen blijven bij opslaan ongewijzigd staan.",
    )
    hidden = full.iloc[:max_n - int(slice_n)]
    editable = full.iloc[max_n - int(slice_n):]
    if len(hidden):
        st.caption(
            f"{len(hidden)} oudere rijen verborgen — die blijven bij "
            f"opslaan ongewijzigd."
        )
    edited = st.data_editor(
        editable, use_container_width=True, num_rows="dynamic",
        key=f"editor_{ds['id']}", hide_index=True,
    )
    if st.button(t("ds_save_changes"), type="primary", key=f"save_data_{ds['id']}"):
        try:
            combined = pd.concat([hidden, edited], ignore_index=True)
            storage.clear_observations(ds["id"])
            if not combined.empty:
                storage.insert_observations(ds["id"], combined)
            st.cache_data.clear()
            st.success("Opgeslagen.")
            st.rerun()
        except Exception as e:
            st.error(f"Opslaan mislukt: {e}")
