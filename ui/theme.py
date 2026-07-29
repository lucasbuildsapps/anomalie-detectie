"""Thema-laag: kleurpaletten + CSS-injectie.

`P` is een lazy proxy: elke sleutel-lookup leest het actieve thema uit
st.session_state, zodat alle modules automatisch het juiste palet zien.
"""
from __future__ import annotations

import streamlit as st

APP_VERSION = "0.9.0"

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
    # Operations-console: donkerblauw-zwart, cyaan accent, hoge dichtheid.
    # Bewust koeler en contrastrijker dan een gewoon 'dark mode' — dit is de
    # weergave voor langdurig meekijken op een groot scherm.
    "dark": {
        "bg":            "#080c14",
        "surface":       "#0f1620",
        "surface_alt":   "#16202c",
        "border":        "#22303f",
        "border_soft":   "#161f2a",
        "text":          "#dce6f0",
        "text_muted":    "#7d8fa3",
        "accent":        "#3dd6d0",
        "accent_text":   "#04121a",
        "accent_dim":    "#7fe6e2",
        "high":          "#ff5c5c",
        "mid":           "#ffa23e",
        "low":           "#ffd23e",
        "ok":            "#39d98a",
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

/* Dichtere, bredere werkruimte: meer beeld per scherm, minder scrollen.
   Bewust ruimer dan Streamlit's standaard 730px-kolom. */
.main .block-container {{
    padding-top: 0.6rem; padding-bottom: 2rem; max-width: 1600px;
}}
[data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}
[data-testid="stHorizontalBlock"] {{ gap: 0.7rem; }}

/* Classificatiebalk: in operationele tools staat altijd bovenaan waar je
   naar kijkt. Hier: de herkomst-status van wat je ziet. */
.classification-bar {{
    position: sticky; top: 0; z-index: 999;
    background: {p['surface_alt']};
    border-bottom: 1px solid {p['border']};
    border-top: 2px solid {p['accent']};
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.66rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: {p['text_muted']};
    padding: 5px 14px; margin: -0.6rem -1rem 0.9rem -1rem;
    display: flex; justify-content: space-between; align-items: center;
}}
.classification-bar .status {{ color: {p['accent']}; font-weight: 600; }}
.classification-bar .warn {{ color: {p['mid']}; font-weight: 600; }}

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

/* Metrics — strak, monospace, met een subtiele accentgloed in het donkere
   thema zodat kerncijfers direct opvallen op een groot scherm. */
[data-testid="stMetric"] {{
    background: {p['surface']} !important;
    border: 1px solid {p['border']} !important;
    border-left: 3px solid {p['accent']} !important;
    padding: 10px 14px;
    border-radius: 0;
}}
[data-testid="stMetric"]:hover {{ border-color: {p['accent_dim']} !important; }}

/* Tabellen strakker en compacter */
[data-testid="stDataFrame"] {{ border: 1px solid {p['border']}; }}
[data-testid="stDataFrame"] * {{ font-size: 0.84rem; }}

/* Expanders als panelen, niet als losse knoppen */
[data-testid="stExpander"] {{
    border: 1px solid {p['border']} !important;
    border-radius: 0 !important;
    background: {p['surface']} !important;
}}
[data-testid="stExpander"] summary {{
    font-size: 0.86rem; font-weight: 500;
}}

/* Tabs: onderstreept in accentkleur i.p.v. Streamlit-rood */
[data-baseweb="tab-highlight"] {{ background: {p['accent']} !important; }}
[data-baseweb="tab"] {{ font-size: 0.88rem; }}
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


def build_css() -> str:
    return _build_css(st.session_state.get("ui_theme", "light"))


class _PaletteProxy:
    """Dict-achtige toegang tot het palet van het actieve thema."""

    def __getitem__(self, key: str) -> str:
        theme = st.session_state.get("ui_theme", "light")
        return PALETTES[theme][key]

    def get(self, key: str, default: str | None = None):
        try:
            return self[key]
        except KeyError:
            return default


P = _PaletteProxy()
