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
from core.comparison import (
    build_series, cross_correlation_lag, seasonality_profile,
)
from core.registry import get_detectors
from i18n.nl import t
from visualizations.comparison_chart import render_lag_curve, render_overlay
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
    "nb_preset": "auto",
    "nb_n_to_show": 5,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


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
    # Let op: HTML voor st.markdown mag GEEN regels met 4+ inspringing hebben,
    # anders ziet Markdown het als code-blok en toont het de tags als tekst.
    _svg = (
        f'<svg viewBox="0 0 48 48" width="36" height="36" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">'
        f'<path d="M24 3 L41 9 V23 C41 34 33.5 41.5 24 45 C14.5 41.5 7 34 7 23 V9 Z" '
        f'stroke="{P["accent"]}" stroke-width="2.4" fill="{P["accent"]}11" '
        f'stroke-linejoin="round"/>'
        f'<circle cx="24" cy="22" r="7" stroke="{P["accent"]}" stroke-width="2.2"/>'
        f'<circle cx="24" cy="22" r="2.6" fill="{P["accent"]}"/>'
        f'<line x1="24" y1="22" x2="24" y2="6.5" stroke="{P["accent"]}" '
        f'stroke-width="1.4" stroke-dasharray="2 2"/></svg>'
    )
    _wordmark = (
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:1.45rem;'
        f'font-weight:700;letter-spacing:0.18em;color:{P["accent"]};'
        f'line-height:1.1;">{t("app_title")}</div>'
        f'<div style="font-size:0.68rem;color:{P["text_muted"]};'
        f'letter-spacing:0.03em;">{t("app_subtitle")}</div>'
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;'
        f'padding:4px 0 2px 0;">{_svg}<div>{_wordmark}</div></div>',
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

    nav_items = [t("nav_normbeeld"), t("nav_compare")]
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

            st.markdown("---")
            st.markdown("**Ruwe data bekijken / bewerken**")
            _render_data_editor(ds)


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


def _render_exports(result, normbeelds, ds: dict):
    """PDF-briefing + Excel-export (gebruikt op de normbeeld-pagina)."""
    c1, c2 = st.columns(2)
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
    # Regio's alfabetisch (voorspelbare volgorde voor de analist)
    locs_sorted = sorted(normbeelds.keys(), key=lambda s: s.lower())

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

    # ----- Export (briefing + Excel) -----
    st.divider()
    st.markdown(f"<div class='section-label'>Export</div>",
                unsafe_allow_html=True)
    _render_exports(result, normbeelds, ds)


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

    hist_series = nb_view.historical.set_index("date")["actual"]
    markers = _event_markers()

    st.caption(t("nb_band_explained"))
    render_normbeeld_chart(
        nb_view, theme=st.session_state.ui_theme, height=520,
        markers=markers,
    )

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


# ---------------------------------------------------------------------------
# Vergelijken: twee reeksen overlay + lag-detectie
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _cmp_load(dataset_id: int, data_hash: str):
    return storage.load_observations(dataset_id)


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
    agg_options = ["daily", "weekly", "monthly"]
    agg = st.selectbox(
        t("agg_label"), agg_options,
        format_func=lambda k: {"daily": t("agg_daily"), "weekly": t("agg_weekly"),
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
        if abs(lag.best_corr) < 0.2:
            verdict = (
                f"**Geen sterk verband** gevonden tussen {label_a} en "
                f"{label_b} (max. correlatie {lag.best_corr:.2f})."
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


APP_VERSION = "0.8.0-alpha"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
if st.session_state.show_settings:
    page_settings()
elif st.session_state.active_page == t("nav_compare"):
    page_compare()
else:
    page_normbeeld()


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
