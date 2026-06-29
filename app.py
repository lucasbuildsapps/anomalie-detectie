"""Streamlit entry point. Run: streamlit run app.py"""
from __future__ import annotations

import sys
import traceback

# Vroege debug-output zodat we zien dat we het scripten beginnen.
print(">>> app.py STARTING (Python", sys.version_info[:2], ")", flush=True)

import html as _html
from pathlib import Path

try:
    import pandas as pd
    import streamlit as st
    print(">>> base imports OK", flush=True)
except Exception as e:
    print(f">>> CRASH in base imports: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    raise

try:
    from core import annotations as anno
    from core import storage
    from core.auth import check_password
    print(">>> core imports OK", flush=True)
except Exception as e:
    print(f">>> CRASH in core imports: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    raise
from core.auto_mapping import guess_mapping
from core.auto_pilot import build_findings, run_auto_pilot
from core.briefing import briefing_filename, build_briefing_pdf
from core.excel_export import build_excel_export, excel_filename
from core.import_data import apply_mapping, read_table
from core.normbeeld import (
    AGGREGATIONS, PREDICTION_METHOD_DETAILS, PREDICTION_METHODS,
    compute_all_normbeelds, compute_normbeeld,
    detect_recent_alerts, _suggest_best_aggregation,
)
from core.registry import get_detectors
from i18n.nl import t
from visualizations.normbeeld_chart import render_normbeeld_chart


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=t("app_title"),
    layout="wide",
    initial_sidebar_state="expanded",
)
print(">>> page config set", flush=True)

try:
    storage.init_db()
    print(">>> db init OK", flush=True)
except Exception as e:
    print(f">>> CRASH in init_db: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    st.error(f"Database-fout bij opstart: {e}")
    st.stop()

# Authenticatie (alleen actief als wachtwoord is ingesteld in secrets.toml of
# ANOMALY_PASSWORD env-var). Lokaal zonder secrets = open toegang.
if not check_password():
    st.stop()

_DEFAULTS = {
    "ui_theme": "light",
    "active_page": t("nav_normbeeld"),
    "active_dataset_id": None,
    "horizon_days": 14,
    "aggregation": "auto",
    "show_settings": False,
    "show_more_findings": False,
    "nb_selected_location": None,
    "nb_selected_category": "Alle categorieën",
    "nb_selected_categories": [],   # [] = alle categorieën
    "nb_methods_override": None,   # None = auto
    "nb_n_to_show": 5,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# Theme + CSS
# ---------------------------------------------------------------------------
PALETTES = {
    "light": {
        "bg":            "#fafbfc",
        "surface":       "#ffffff",
        "surface_alt":   "#f0f2f5",
        "border":        "#dde1e6",
        "border_soft":   "#eef0f3",
        "text":          "#0a1929",
        "text_muted":    "#56616e",
        "accent":        "#1a4d8c",
        "accent_text":   "#ffffff",
        "accent_dim":    "#5b7ba5",
        "high":          "#c53030",
        "mid":           "#c05621",
        "low":           "#975a16",
        "ok":            "#2e8b57",
    },
    "dark": {
        "bg":            "#0d1117",
        "surface":       "#161b22",
        "surface_alt":   "#1f2630",
        "border":        "#2a3038",
        "border_soft":   "#1c2129",
        "text":          "#e6edf3",
        "text_muted":    "#8b949e",
        "accent":        "#58a6ff",
        "accent_text":   "#0d1117",
        "accent_dim":    "#79b8ff",
        "high":          "#f87171",
        "mid":           "#fb923c",
        "low":           "#fbbf24",
        "ok":            "#4cda86",
    },
}


def _build_css(theme: str) -> str:
    p = PALETTES[theme]
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stHeader"] {{
    background: {p['bg']} !important;
    color: {p['text']} !important;
}}
[data-testid="stHeader"] {{ border-bottom: 1px solid {p['border_soft']} !important; }}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {{
    background: {p['surface']} !important;
    border-right: 1px solid {p['border']} !important;
}}
[data-testid="stSidebar"] *, .stApp p, .stApp label, .stApp span, .stApp div {{
    color: {p['text']};
}}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
.stApp [data-testid="stCaptionContainer"] p,
[data-testid="stWidgetLabel"] p {{ color: {p['text_muted']} !important; }}

[data-baseweb="select"] > div, [data-baseweb="input"] > div,
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input, [data-baseweb="popover"] {{
    background: {p['surface']} !important;
    color: {p['text']} !important;
    border-color: {p['border']} !important;
}}
[data-baseweb="popover"] li {{ color: {p['text']} !important; }}
[data-baseweb="popover"] li:hover {{ background: {p['surface_alt']} !important; }}
[data-baseweb="tag"] {{ background: {p['surface_alt']} !important; color: {p['text']} !important; }}

.main .block-container {{
    padding-top: 1rem; padding-bottom: 2rem; max-width: 1400px;
}}

h1, h2, h3, h4, h5 {{
    font-family: 'Inter', sans-serif; font-weight: 600;
    letter-spacing: -0.01em; color: {p['text']} !important;
}}
.stApp {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}

.section-label {{
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-size: 0.7rem;
    font-weight: 600;
    color: {p['accent']};
    margin: 1.25rem 0 0.5rem 0;
    padding-bottom: 4px;
    border-bottom: 1px solid {p['border_soft']};
}}

/* Metrics */
[data-testid="stMetric"] {{
    background: {p['surface']} !important;
    border: 1px solid {p['border']} !important;
    border-left: 3px solid {p['accent']} !important;
    padding: 12px 16px;
    border-radius: 2px;
}}
[data-testid="stMetricLabel"] p {{
    text-transform: uppercase; letter-spacing: 0.08em;
    font-size: 0.7rem; font-weight: 500;
    color: {p['text_muted']} !important;
}}
[data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.3rem; font-weight: 600;
    color: {p['text']} !important;
    white-space: nowrap;
}}

/* Finding cards */
.finding-card {{
    background: {p['surface']};
    border: 1px solid {p['border']};
    border-left: 3px solid var(--card-color);
    padding: 14px 18px;
    margin-bottom: 10px;
}}
.finding-header {{
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 8px; flex-wrap: wrap;
}}
.severity-pill {{
    display: inline-block; padding: 2px 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem; font-weight: 600;
    letter-spacing: 0.1em; color: white;
}}
.severity-hoog   {{ background: {p['high']}; }}
.severity-midden {{ background: {p['mid']}; }}
.severity-laag   {{ background: {p['low']}; color: #1a1a1a; }}
.finding-loc {{ font-weight: 600; color: {p['text']}; }}
.finding-date {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem; color: {p['text_muted']};
}}
.finding-stat {{ color: {p['text']}; font-size: 0.92rem; margin: 4px 0; }}
.finding-explain {{ color: {p['text']}; font-size: 0.92rem; line-height: 1.55; margin: 8px 0; }}
.finding-meta {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem; color: {p['text_muted']};
    padding-top: 8px; border-top: 1px solid {p['border_soft']};
    margin-top: 8px;
}}

/* Normbeeld kaart (compacter) */
.nb-card {{
    background: {p['surface']};
    border: 1px solid {p['border']};
    border-left: 3px solid {p['accent']};
    padding: 12px 14px; margin-bottom: 8px;
}}
.nb-card .name {{ font-weight: 600; color: {p['text']}; font-size: 1rem; }}
.nb-card .stat {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.88rem; color: {p['text']};
    margin-top: 4px;
}}
.nb-card .stat .label {{ color: {p['text_muted']}; }}
.nb-card.alert {{ border-left-color: {p['high']}; }}

/* Alert banner */
.alert-banner {{
    background: {p['surface']};
    border: 1px solid {p['high']};
    border-left: 4px solid {p['high']};
    padding: 14px 18px; margin: 10px 0 14px 0;
}}
.alert-banner .head {{
    color: {p['high']}; font-weight: 700;
    font-size: 0.9rem; letter-spacing: 0.03em;
    text-transform: uppercase; margin-bottom: 6px;
}}
.alert-banner .intro {{
    color: {p['text']}; font-size: 0.9rem;
    margin-bottom: 8px;
}}
.alert-row {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.88rem; color: {p['text']};
    padding: 3px 0;
}}

/* Severity explainer */
.explainer {{
    background: {p['surface_alt']};
    border-left: 2px solid {p['accent_dim']};
    padding: 10px 14px;
    font-size: 0.88rem; color: {p['text']};
    margin: 6px 0 12px 0;
    line-height: 1.55;
}}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0; background: transparent;
    border-bottom: 1px solid {p['border']};
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent !important;
    color: {p['text_muted']} !important;
    border-radius: 0;
    padding: 8px 16px; font-weight: 500;
}}
.stTabs [aria-selected="true"] {{
    color: {p['accent']} !important;
    border-bottom: 2px solid {p['accent']};
}}

/* ===== Primary button (forceer leesbare tekst) ===== */
button[kind="primary"],
button[kind="primary"] * {{
    background: {p['accent']} !important;
    color: {p['accent_text']} !important;
    border-color: {p['accent']} !important;
    border-radius: 2px !important;
    font-weight: 600 !important;
}}
button[kind="primary"]:hover {{
    background: {p['accent_dim']} !important;
    border-color: {p['accent_dim']} !important;
}}
button[kind="secondary"] {{
    background: {p['surface']} !important;
    border: 1px solid {p['border']} !important;
    color: {p['text']} !important;
    border-radius: 2px !important;
}}
button[kind="secondary"]:hover {{
    border-color: {p['accent']} !important;
}}

/* Cogwheel button (klein, rechtsboven) */
.cog-button button {{
    padding: 4px 12px !important;
    font-size: 0.8rem !important;
    min-height: 0 !important;
}}

/* Sidebar nav rows (vlak, met active-indicator) */
.sidebar-nav button {{
    width: 100%;
    text-align: left !important;
    padding: 10px 14px !important;
    border: 1px solid {p['border']} !important;
    background: {p['surface']} !important;
    color: {p['text']} !important;
    font-weight: 500 !important;
    border-radius: 2px !important;
}}
.sidebar-nav-active button {{
    border-left: 3px solid {p['accent']} !important;
    background: {p['surface_alt']} !important;
    color: {p['accent']} !important;
    font-weight: 600 !important;
}}

div[data-testid="stExpander"] {{
    background: {p['surface']} !important;
    border: 1px solid {p['border']} !important;
    border-radius: 2px !important;
}}
div[data-testid="stExpander"] summary {{ color: {p['text']} !important; }}

.stDataFrame, .stDataFrame > div {{
    background: {p['surface']} !important;
    border: 1px solid {p['border']} !important;
}}

[data-testid="stFileUploader"] section {{
    background: {p['surface']} !important;
    border: 1px dashed {p['border']} !important;
    color: {p['text']} !important;
}}

footer {{ visibility: hidden; }}

/* Forceer zijbalk altijd zichtbaar (sommige Streamlit-versies klappen 'm
   onzichtbaar in bij smal scherm of na een collapse) */
[data-testid="stSidebar"] {{
    display: block !important;
    visibility: visible !important;
    transform: none !important;
    min-width: 240px !important;
}}
[data-testid="collapsedControl"] {{
    display: flex !important;
    visibility: visible !important;
}}
</style>
"""


st.markdown(_build_css(st.session_state.ui_theme), unsafe_allow_html=True)
P = PALETTES[st.session_state.ui_theme]


# ---------------------------------------------------------------------------
# Cache wrapper
# ---------------------------------------------------------------------------
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


@st.cache_data(show_spinner="Backtest draait... (eenmalig per locatie)")
def cached_detail_normbeeld(
    dataset_id: int, data_hash: str, location: str,
    category, horizon: int, methods_key: str, aggregation: str,
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
        aggregation=aggregation, select="backtest",
    )


@st.cache_data(show_spinner="Analyseren... (eerste keer ~10-30 sec voor grote datasets)")
def cached_analysis(
    dataset_id: int, data_hash: str, horizon: int,
    aggregation: str, methods_key: str,
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
        aggregation=effective_agg,
    )
    alerts = detect_recent_alerts(normbeelds, aggregation=effective_agg)
    return df_raw, df, result, normbeelds, alerts, effective_agg


# ---------------------------------------------------------------------------
# Sidebar (Normbeeld bovenaan, geen banner, geen Instellingen-knop)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="padding: 4px 0 2px 0;">
            <div style="font-family: 'JetBrains Mono', monospace;
                        font-size: 1.5rem; font-weight: 700;
                        letter-spacing: 0.22em; color: {P['accent']};">
                {t('app_title')}
            </div>
            <div style="font-size: 0.72rem; color: {P['text_muted']};
                        letter-spacing: 0.04em; margin-top: 2px;">
                {t('app_subtitle')}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # Persistente DB actief? Toon dat; anders waarschuw voor ephemeral cloud-opslag.
    if storage.is_persistent():
        st.caption("Verbonden met gedeelde database.")
    elif Path("/mount/src").exists():
        st.caption(
            "⚠ Demo-omgeving: geüploade data kan bij een herstart "
            "gewist worden."
        )
    st.divider()

    nav_items = [t("nav_normbeeld"), t("nav_data"), t("nav_compare")]
    for label in nav_items:
        is_active = st.session_state.active_page == label
        wrapper_cls = "sidebar-nav sidebar-nav-active" if is_active else "sidebar-nav"
        st.markdown(f"<div class='{wrapper_cls}'>", unsafe_allow_html=True)
        if st.button(
            label, key=f"nav_{label}",
            use_container_width=True, type="secondary",
        ):
            st.session_state.active_page = label
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    theme_choice = st.radio(
        t("theme_label"),
        [t("theme_light"), t("theme_dark")],
        horizontal=True,
        index=0 if st.session_state.ui_theme == "light" else 1,
        key="theme_radio",
    )
    new_theme = "light" if theme_choice == t("theme_light") else "dark"
    if new_theme != st.session_state.ui_theme:
        st.session_state.ui_theme = new_theme
        st.rerun()


# ---------------------------------------------------------------------------
# Top-right cogwheel (Instellingen)
# ---------------------------------------------------------------------------
def render_topbar(title: str = ""):
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


# ---------------------------------------------------------------------------
# Settings overlay (datasets, upload, expert, weergave)
# ---------------------------------------------------------------------------
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
    ])
    with tabs[0]:
        _settings_datasets()
    with tabs[1]:
        _settings_upload()
    with tabs[2]:
        _settings_expert()
    with tabs[3]:
        _settings_theme()


def _settings_datasets():
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


def _settings_upload():
    # Demo-knop bovenaan
    st.markdown(
        "**Snel beginnen:** laad de meegeleverde demo-dataset "
        "(open-source data: Russian missile/drone attacks op Oekraïne, 2022-2026)."
    )
    if st.button("Laad demo-dataset", type="secondary", key="load_demo_settings"):
        if _try_load_demo_dataset():
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
            dataset_id = storage.create_dataset(name.strip(), desc, mapping)
            n = storage.insert_observations(dataset_id, normalized)
            msg = t("msg_saved", n=n)
            if stats["dropped_total"] > 0:
                msg += (
                    f"  ({stats['dropped_total']} rijen overgeslagen: "
                    f"{stats['dropped_bad_time']} met ongeldige timestamp)"
                )
                if stats["dropped_total"] > 0.1 * stats["input_rows"]:
                    st.warning(
                        f"Let op: {stats['dropped_total']}/{stats['input_rows']} "
                        f"rijen ({100*stats['dropped_total']/stats['input_rows']:.0f}%) "
                        f"zijn overgeslagen. Controleer de kolom-koppeling, "
                        f"vooral de tijd-kolom."
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


# ---------------------------------------------------------------------------
# Data-specifics pagina
# ---------------------------------------------------------------------------
DEMO_DATASET_NAME = "Demo - Russian missile attacks on Ukraine"


def _try_load_demo_dataset() -> bool:
    """Importeer publieke demo: Russian missile/drone attacks op Oekraïne.
    Bron: open-source data uit kpszsu/PvKPivden Telegram-kanalen."""
    csv_path = Path(__file__).parent / "data" / "missile_attacks_demo.csv"
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
        if st.button("Laad demo-dataset", type="primary",
                     use_container_width=True, key="load_demo_empty"):
            if _try_load_demo_dataset():
                st.rerun()


def page_data():
    render_topbar()  # geen "Data-specifics" kop meer
    datasets = storage.list_datasets()
    if not datasets:
        _render_empty_state()
        return

    # Dataset-keuze
    by_id = {d["id"]: d for d in datasets}
    ids = list(by_id.keys())
    if st.session_state.active_dataset_id not in ids:
        st.session_state.active_dataset_id = ids[0]

    chosen = st.selectbox(
        t("ds_dataset"), ids,
        format_func=lambda i: by_id[i]["name"],
        index=ids.index(st.session_state.active_dataset_id),
        key="ds_pick",
    )
    if chosen != st.session_state.active_dataset_id:
        st.session_state.active_dataset_id = chosen
        st.rerun()

    ds = by_id[chosen]
    methods_key = (
        "auto" if st.session_state.nb_methods_override is None
        else ",".join(st.session_state.nb_methods_override)
    )
    data_hash = storage.dataset_data_hash(ds["id"])
    cached = cached_analysis(
        ds["id"], data_hash, st.session_state.horizon_days,
        st.session_state.aggregation, methods_key,
    )
    if cached is None:
        st.warning("Dataset is leeg.")
        return
    df_raw, df, result, normbeelds, alerts, effective_agg = cached
    res = result.results

    # Aggregatie-keuze
    c1, c2 = st.columns([2, 3])
    with c1:
        agg_options = ["auto", "daily", "weekly", "monthly"]
        agg_labels = {
            "auto":    f"Auto (aanbevolen: {AGGREGATIONS[effective_agg][1]})",
            "daily":   t("agg_daily"),
            "weekly":  t("agg_weekly"),
            "monthly": t("agg_monthly"),
        }
        new_agg = st.selectbox(
            t("agg_label"), agg_options,
            format_func=lambda k: agg_labels[k],
            index=agg_options.index(st.session_state.aggregation),
            key="agg_pick",
        )
        if new_agg != st.session_state.aggregation:
            st.session_state.aggregation = new_agg
            st.rerun()

    # Alerts (prominent) — keuze tussen recent en historische top
    from core.normbeeld import recent_window_label
    window_lbl = recent_window_label(effective_agg)

    def _fmt_date(iso: str) -> str:
        try:
            return pd.Timestamp(iso).strftime("%d-%m-%Y")
        except Exception:
            return iso

    # Verzamel historische top-deviaties (grootste afstand tot band)
    all_hist_dev = []
    for loc, nb in normbeelds.items():
        sub = nb.historical[nb.historical["status"] != "normaal"].copy()
        if sub.empty:
            continue
        sub["locatie"] = loc
        sub["magnitude"] = (sub["actual"] - sub["expected"]).abs()
        all_hist_dev.append(sub)
    if all_hist_dev:
        hist_dev_df = pd.concat(all_hist_dev, ignore_index=True)
        hist_dev_df = hist_dev_df.sort_values("magnitude", ascending=False).head(15)
    else:
        hist_dev_df = pd.DataFrame()

    if alerts or not hist_dev_df.empty:
        st.markdown(
            f"""
            <div class="alert-banner">
                <div class="head">{t('alerts_title')}</div>
                <div class="intro">{_html.escape(t('alerts_intro'))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        view_mode = st.radio(
            "Welke afwijkingen tonen?",
            [f"Recent ({window_lbl})", "Historische top (15 grootste ooit)"],
            horizontal=True, key="alert_view_mode",
            label_visibility="collapsed",
        )

        if view_mode.startswith("Recent"):
            if alerts:
                st.markdown(
                    f"<div style='color:{P['text_muted']}; font-size:0.85rem; "
                    f"margin-bottom:8px;'>"
                    f"{len(alerts)} waarnemingen buiten normbeeld in de "
                    f"laatste {window_lbl}</div>",
                    unsafe_allow_html=True,
                )
                rows = ""
                for a in alerts[:10]:
                    arrow = "boven band" if a["richting"] == "boven" else "onder band"
                    factor = (
                        f" — {a['waarde'] / max(a['upper'], 0.5):.1f}x bovengrens"
                        if a["richting"] == "boven" and a["upper"] > 0
                        else ""
                    )
                    rows += (
                        f"<div class='alert-row'>{_fmt_date(a['datum'])} · "
                        f"{_html.escape(str(a['locatie']))} · "
                        f"{a['waarde']} ({arrow}, verwacht "
                        f"{a['lower']:.0f}-{a['upper']:.0f}){factor}</div>"
                    )
                st.markdown(rows, unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<div style='color:{P['text_muted']}; font-size:0.85rem;'>"
                    f"Geen recente afwijkingen in de laatste {window_lbl}."
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            # Historische top
            if not hist_dev_df.empty:
                st.markdown(
                    f"<div style='color:{P['text_muted']}; font-size:0.85rem; "
                    f"margin-bottom:8px;'>"
                    f"De 15 grootste afwijkingen in de hele dataset, "
                    f"gerangschikt op afstand tot het verwachte normbeeld.</div>",
                    unsafe_allow_html=True,
                )
                rows = ""
                for _, h in hist_dev_df.iterrows():
                    direction = "boven band" if h["status"] == "boven" else "onder band"
                    factor_txt = ""
                    if h["status"] == "boven" and h["upper"] > 0:
                        f_ = h["actual"] / max(h["upper"], 0.5)
                        factor_txt = f" — {f_:.1f}x bovengrens"
                    rows += (
                        f"<div class='alert-row'>"
                        f"{pd.Timestamp(h['date']).strftime('%d-%m-%Y')} · "
                        f"{_html.escape(str(h['locatie']))} · "
                        f"{int(h['actual'])} ({direction}, verwacht "
                        f"{h['lower']:.0f}-{h['upper']:.0f}){factor_txt}</div>"
                    )
                st.markdown(rows, unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<div style='color:{P['text_muted']}; font-size:0.85rem;'>"
                    f"Geen historische afwijkingen in deze dataset.</div>",
                    unsafe_allow_html=True,
                )

    # Overzicht
    st.markdown(f"<div class='section-label'>{t('results_title')}</div>",
                unsafe_allow_html=True)
    n_obs = len(df_raw)
    n_loc = (
        df_raw["location_name"].nunique()
        if "location_name" in df_raw.columns
        and df_raw["location_name"].notna().any() else 0
    )
    period_str = "—"
    if "timestamp" in df_raw.columns and not df_raw["timestamp"].empty:
        ts = pd.to_datetime(df_raw["timestamp"])
        # Compact format om afkapping te voorkomen
        period_str = f"{ts.min().strftime('%d-%m-%Y')} t/m {ts.max().strftime('%d-%m-%Y')}"
    n_hoog = int((res["severity"] == "hoog").sum())
    n_mid = int((res["severity"] == "midden").sum())
    n_laag = int((res["severity"] == "laag").sum())
    n_anom = n_hoog + n_mid + n_laag

    c1, c2 = st.columns(2)
    c1.metric(t("results_observations"), f"{n_obs:,}".replace(",", "."))
    c2.metric(t("results_period"), period_str)
    c3, c4 = st.columns(2)
    c3.metric(t("results_locations"), n_loc if n_loc else "—")
    c4.metric(t("results_anomalies_total"),
              f"{n_anom} ({n_hoog} hoog)")

    # Data-viewer — toont alleen de recentste N rijen in de editor (browser
    # wordt traag bij duizenden rijen). Bij opslaan blijven de niet-getoonde
    # rijen onaangetast.
    with st.expander(t("ds_show_data")):
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
            editable,
            use_container_width=True,
            num_rows="dynamic",
            key=f"editor_{ds['id']}",
            hide_index=True,
        )
        if st.button(t("ds_save_changes"), type="primary",
                     key=f"save_data_{ds['id']}"):
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

    # Export
    st.markdown(f"<div class='section-label'>Export</div>",
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        try:
            pdf_bytes = build_briefing_pdf(
                result, ds["name"], ds["description"],
                normbeelds=normbeelds,
            )
            st.download_button(
                t("export_pdf"), data=pdf_bytes,
                file_name=briefing_filename(ds["name"]),
                mime="application/pdf",
                use_container_width=True, type="secondary",
            )
        except Exception as e:
            st.error(f"PDF: {e}")
    with c2:
        try:
            xlsx_bytes = build_excel_export(
                result, normbeelds, ds["name"], ds["description"]
            )
            st.download_button(
                t("export_excel"), data=xlsx_bytes,
                file_name=excel_filename(ds["name"]),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, type="secondary",
            )
        except Exception as e:
            st.error(f"Excel: {e}")

    # Severity-uitleg
    with st.expander(t("severity_explainer_title")):
        st.markdown(t("severity_explainer"))

    # Tabs (gereduceerd)
    has_geo = (
        "lat" in res.columns and "lon" in res.columns
        and res["lat"].notna().any() and res["lon"].notna().any()
    )
    labels = [t("tab_findings")]
    if has_geo:
        labels.append(t("tab_map"))
    labels.append(t("tab_timeline"))
    tabs = st.tabs(labels)

    i = 0
    with tabs[i]:
        _render_findings(result, ds["id"])
    i += 1
    if has_geo:
        with tabs[i]:
            _render_geomap(res)
        i += 1
    with tabs[i]:
        _render_timeseries(res)


def _render_findings(result, dataset_id: int):
    all_findings = build_findings(result, top_n=100)
    if not all_findings:
        st.info(t("findings_empty"))
        return

    # Hoog + midden zijn het echte signaal; laag-vertrouwen gaat ingeklapt
    # onderaan (alert-moeheid voorkomen).
    findings = [f for f in all_findings if f["severity"] in ("hoog", "midden")]
    low_findings = [f for f in all_findings if f["severity"] == "laag"]

    sev_color = {"hoog": P["high"], "midden": P["mid"], "laag": P["low"]}
    notes_map = anno.list_annotations(dataset_id)

    if not findings:
        st.info(
            "Geen afwijkingen met hoog of midden vertrouwen. "
            f"Er zijn wel {len(low_findings)} laag-vertrouwen signalen "
            "(zie onderaan)."
            if low_findings else t("findings_empty")
        )

    # Top 3 + expand
    show_n = len(findings) if st.session_state.show_more_findings else min(3, len(findings))

    if findings:
        st.markdown(
            f"<div class='section-label'>"
            f"{t('findings_top_initial')} ({show_n}/{len(findings)})</div>",
            unsafe_allow_html=True,
        )

    for i, f in enumerate(findings[:show_n], start=1):
        sev = f["severity"]
        color = sev_color.get(sev, "#999")
        exp = f["explanation"]
        key = anno.finding_key(f["datum"], str(f["locatie"]), None)
        existing = notes_map.get(key, {})

        body_lines = []
        if exp.get("baseline"):
            body_lines.append(_html.escape(exp["baseline"]))
        if exp.get("factor"):
            body_lines.append(_html.escape(exp["factor"]))
        if exp.get("weekday_context"):
            body_lines.append(_html.escape(exp["weekday_context"]))
        body_html = "<br>".join(body_lines)
        flagged_str = ", ".join(f["methodes_aan"]) if f["methodes_aan"] else "—"
        not_flagged_str = (
            ", ".join(f["methodes_uit"]) if f.get("methodes_uit") else ""
        )
        not_flagged_html = (
            f"<br><span style='color: {P['text_muted']};'>"
            f"Niet aangeslagen: {_html.escape(not_flagged_str)}</span>"
            if not_flagged_str else ""
        )

        st.markdown(
            f"""
            <div class="finding-card" style="--card-color: {color};">
                <div class="finding-header">
                    <span class="severity-pill severity-{sev}">#{i} · {sev.upper()}</span>
                    <span class="finding-loc">{_html.escape(str(f["locatie"]))}</span>
                    <span class="finding-date">{pd.Timestamp(f["datum"]).strftime("%d-%m-%Y")}</span>
                </div>
                <div class="finding-stat">{_html.escape(exp['observation'])}</div>
                <div class="finding-explain">{body_html}</div>
                <div class="finding-meta">
                    <strong>{f["stemmen"]}/{f["totaal_methodes"]}</strong> methodes bevestigen:
                    {_html.escape(flagged_str)}
                    {not_flagged_html}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander(
            f"Notitie {'(bestaand)' if existing else ''} — {f['locatie']} {f['datum']}"
        ):
            status_opts = list(anno.STATUS_LABELS.keys())
            cur_status = existing.get("status", "open")
            cur_idx = status_opts.index(cur_status) if cur_status in status_opts else 0
            c1, c2 = st.columns([1, 2])
            with c1:
                new_status = st.selectbox(
                    t("anno_status"), status_opts,
                    format_func=lambda s: anno.STATUS_LABELS[s],
                    index=cur_idx, key=f"anno_st_{key}",
                )
            with c2:
                new_note = st.text_area(
                    t("anno_note"), value=existing.get("note", ""),
                    key=f"anno_nt_{key}", height=70,
                )
            if st.button(t("anno_save"), key=f"anno_sv_{key}",
                         use_container_width=True, type="secondary"):
                anno.save_annotation(dataset_id, key, new_note, new_status)
                st.success(t("anno_saved"))

    # Show more / less
    if len(findings) > 3:
        if st.session_state.show_more_findings:
            if st.button(t("findings_show_less"), key="show_less",
                         use_container_width=True):
                st.session_state.show_more_findings = False
                st.rerun()
        else:
            if st.button(t("findings_show_more"), key="show_more",
                         use_container_width=True):
                st.session_state.show_more_findings = True
                st.rerun()

    # Laag-vertrouwen signalen: standaard ingeklapt, compacte regels
    if low_findings:
        with st.expander(
            f"Laag-vertrouwen signalen ({len(low_findings)}) — "
            f"2 methodes eens, mogelijk vals alarm"
        ):
            rows = ""
            for f in low_findings[:40]:
                rows += (
                    f"<div class='alert-row'>"
                    f"{pd.Timestamp(f['datum']).strftime('%d-%m-%Y')} · "
                    f"{_html.escape(str(f['locatie']))} · "
                    f"{f['waarde']} waarnemingen · "
                    f"{f['stemmen']}/{f['totaal_methodes']} stemmen</div>"
                )
            st.markdown(rows, unsafe_allow_html=True)


def _render_geomap(res: pd.DataFrame):
    from core.registry import get_visualizations
    for name, v in get_visualizations().items():
        if "kaart" in name.lower():
            v.render(res, time_col="timestamp", value_col="value")
            return
    st.warning("Geen kaart-visualisatie beschikbaar.")


def _render_timeseries(res: pd.DataFrame):
    from core.registry import get_visualizations
    for name, v in get_visualizations().items():
        if "tijdreeks" in name.lower():
            cat_col = "location_name" if "location_name" in res.columns else (
                "category" if "category" in res.columns else None
            )
            v.render(
                res, time_col="timestamp", value_col="value",
                category_col=cat_col, theme=st.session_state.ui_theme,
            )
            return
    st.warning("Geen tijdreeks-visualisatie beschikbaar.")


# ---------------------------------------------------------------------------
# Normbeeld pagina
# ---------------------------------------------------------------------------
def page_normbeeld():
    render_topbar(t("nb_title"))
    st.caption(t("nb_subtitle"))

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
    methods_key = (
        "auto" if st.session_state.nb_methods_override is None
        else ",".join(st.session_state.nb_methods_override)
    )
    data_hash = storage.dataset_data_hash(ds["id"])
    cached = cached_analysis(
        ds["id"], data_hash, st.session_state.horizon_days,
        st.session_state.aggregation, methods_key,
    )
    if cached is None:
        st.warning("Dataset is leeg.")
        return
    df_raw, df, result, normbeelds, alerts, effective_agg = cached

    # Aggregatie-toggle ook op normbeeld-pagina (zodat hij ook hier werkt)
    agg_options = ["auto", "daily", "weekly", "monthly"]
    agg_labels = {
        "auto":    f"Auto (aanbevolen: {AGGREGATIONS[effective_agg][1]})",
        "daily":   t("agg_daily"),
        "weekly":  t("agg_weekly"),
        "monthly": t("agg_monthly"),
    }
    new_agg = st.selectbox(
        t("agg_label"), agg_options,
        format_func=lambda k: agg_labels[k],
        index=agg_options.index(st.session_state.aggregation),
        key="nb_agg_pick",
    )
    if new_agg != st.session_state.aggregation:
        st.session_state.aggregation = new_agg
        st.rerun()

    if not normbeelds:
        st.warning(t("nb_no_data"))
        return

    unit = AGGREGATIONS[effective_agg][1]  # 'dag' / 'week' / 'maand'
    locs = list(normbeelds.keys())
    # Sorteer op aantal recente afwijkingen (alerts eerst)
    locs_sorted = sorted(locs, key=lambda l: -normbeelds[l].n_recent_deviations)

    # ----- Regio direct selecteerbaar (geen doorklik-stap) -----
    if st.session_state.nb_selected_location not in locs_sorted:
        st.session_state.nb_selected_location = locs_sorted[0]
    selected = st.selectbox(
        t("nb_region"),
        locs_sorted,
        index=locs_sorted.index(st.session_state.nb_selected_location),
        format_func=lambda l: (
            f"{l}  ·  {normbeelds[l].n_recent_deviations} recente afwijking(en)"
        ),
        key="nb_detail_pick",
    )
    if selected != st.session_state.nb_selected_location:
        st.session_state.nb_selected_location = selected
        st.rerun()

    _render_normbeeld_detail(
        df_raw, normbeelds[selected], selected, ds["id"], unit, effective_agg,
    )

    # ----- Overzichtstabel alle regio's (onderaan, inklapbaar) -----
    st.divider()
    with st.expander(f"{t('nb_overview')} ({len(locs_sorted)} regio's)"):
        table_rows = []
        for loc in locs_sorted:
            nb = normbeelds[loc]
            table_rows.append({
                "Regio": loc,
                f"Verwacht /{unit}": f"{nb.expected_value:.1f}",
                "Band": f"{nb.lower_band:.0f} – {nb.upper_band:.0f}",
                "Recente afwijkingen": nb.n_recent_deviations,
                "Vertrouwen": nb.confidence,
            })
        st.dataframe(
            pd.DataFrame(table_rows),
            use_container_width=True, hide_index=True,
        )


def _render_nb_card_compact(loc: str, nb):
    is_active = st.session_state.nb_selected_location == loc
    alert_cls = "alert" if nb.n_recent_deviations > 0 else ""
    border = f"border: 2px solid {P['accent']};" if is_active else ""
    dev_text = (
        f"<strong style='color:{P['high']};'>{nb.n_recent_deviations}</strong>"
        if nb.n_recent_deviations > 0
        else f"<strong>0</strong>"
    )
    st.markdown(
        f"""
        <div class="nb-card {alert_cls}" style="{border}">
            <div class="name">{_html.escape(loc)}</div>
            <div class="stat">
                <span class="label">{t('nb_expected')}:</span>
                <strong>{nb.expected_value:.1f}</strong>
                · <span class="label">{t('nb_recent_dev')}:</span> {dev_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    label = "Geselecteerd" if is_active else "Bekijken"
    if st.button(label, key=f"sel_{loc}",
                 use_container_width=True,
                 type="primary" if is_active else "secondary"):
        st.session_state.nb_selected_location = loc
        st.rerun()


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
        method_keys = list(PREDICTION_METHODS.keys())
        method_labels = [PREDICTION_METHODS[k] for k in method_keys]
        current = st.session_state.nb_methods_override
        if current is None:
            current_labels = [PREDICTION_METHODS[m] for m in nb.methods_used]
        else:
            current_labels = [PREDICTION_METHODS[m] for m in current if m in PREDICTION_METHODS]
        picked = st.multiselect(
            "Voorspelmethodes (combineerbaar)",
            method_labels, default=current_labels,
            help=(
                "Voorspelmethodes proberen de waarde voor toekomstige periodes "
                "in te schatten. Anders dan detectiemethodes (Z-score, "
                "Isolation Forest, Change-point) — die markeren historische "
                "afwijkingen maar voorspellen niet. Selecteer meerdere voor "
                "een ensemble-gemiddelde."
            ),
            key="nb_methods_pick",
        )
        picked_keys = [method_keys[method_labels.index(l)] for l in picked]
        new_override = picked_keys if picked_keys else None
        if new_override != st.session_state.nb_methods_override:
            st.session_state.nb_methods_override = new_override
            st.cache_data.clear()
            st.rerun()

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
    )
    if nb_view is None:
        st.warning(t("nb_no_data"))
        return

    # Statistieken met expliciete tijdseenheid
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Verwacht per {unit}", f"{nb_view.expected_value:.1f}")
    c2.metric(f"Band (per {unit})",
              f"{nb_view.lower_band:.0f} – {nb_view.upper_band:.0f}")
    c3.metric(f"Recente afwijkingen", nb_view.n_recent_deviations)

    st.caption(t("nb_band_explained"))
    render_normbeeld_chart(nb_view, theme=st.session_state.ui_theme, height=520)

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


APP_VERSION = "0.6.0-alpha"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
if st.session_state.show_settings:
    page_settings()
elif st.session_state.active_page == t("nav_normbeeld"):
    page_normbeeld()
else:
    page_data()


# Versie-footer
st.markdown(
    f"""
    <div style='margin-top: 3rem; padding-top: 1rem;
                border-top: 1px solid {P["border_soft"]};
                color: {P["text_muted"]}; font-size: 0.75rem;
                font-family: JetBrains Mono, monospace;
                display: flex; justify-content: space-between;'>
        <span>Anomalie-detectie · v{APP_VERSION}</span>
        <span>Interne tool - software in ontwikkeling</span>
    </div>
    """,
    unsafe_allow_html=True,
)
