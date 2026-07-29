"""Streamlit entry point (shell). Run: streamlit run app.py

Alle inhoud leeft in ui/ (thema, componenten, pagina's) en core/ (analyse).
Dit bestand doet alleen: logging, page-config, DB-init, auth, state, CSS,
sidebar en routing.
"""
from __future__ import annotations

import sys
import traceback

from core.logging_setup import get_logger

logger = get_logger("app")
logger.info("app.py starting", extra={"ctx": {"python": ".".join(map(str, sys.version_info[:3]))}})

from pathlib import Path

try:
    import streamlit as st
except Exception:
    logger.exception("crash in base imports")
    raise

try:
    from core import storage
    from core.auth import check_password
    from i18n.nl import t
except Exception:
    logger.exception("crash in core imports")
    raise

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=t("app_title"),
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    storage.init_db()
except Exception as e:
    logger.exception("crash in init_db")
    st.error(f"Database-fout bij opstart: {e}")
    st.stop()

# Authenticatie (alleen actief als wachtwoord is ingesteld in secrets.toml of
# ANOMALY_PASSWORD env-var). Lokaal zonder secrets = open toegang.
if not check_password():
    st.stop()

from ui.state import init_session_state

init_session_state()

from ui.theme import APP_VERSION, P, build_css

st.markdown(build_css(), unsafe_allow_html=True)

from ui.pages.compare import page_compare
from ui.pages.normbeeld import page_normbeeld
from ui.pages.overview import page_overview
from ui.pages.settings import page_settings

# ---------------------------------------------------------------------------
# Sidebar
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
    # Maak de beveiligings-stand expliciet: zonder geconfigureerd wachtwoord
    # is de app open voor iedereen met de link. Dat mag nooit stil gebeuren.
    from core.auth import is_protected
    if is_protected():
        st.caption("🔒 Wachtwoord-login actief.")
    else:
        st.warning(
            "**Open toegang** — er is geen wachtwoord ingesteld. Iedereen "
            "met de link kan alles zien en wijzigen. Instellen: secret "
            "`password` (Streamlit Cloud → Settings → Secrets) of env-var "
            "`ANOMALY_PASSWORD`.",
            icon="⚠️",
        )
    st.divider()

    nav_items = [t("nav_overview"), t("nav_normbeeld"), t("nav_compare")]
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
# Router
# ---------------------------------------------------------------------------
# Globale vangrail: een fout in één pagina mag de app niet in Streamlit's
# geredigeerde 'Oh no'-scherm laten belanden. Toon de echte traceback in een
# expander (interne tool) en log gestructureerd.


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
# Globale vangrail: een fout in één pagina mag de app niet in Streamlit's
# geredigeerde 'Oh no'-scherm laten belanden. Toon de echte traceback in een
# expander (interne tool) en log naar stderr voor de Cloud-logs.
try:
    if st.session_state.show_settings:
        page_settings()
    elif st.session_state.active_page == t("nav_overview"):
        page_overview()
    elif st.session_state.active_page == t("nav_compare"):
        page_compare()
    else:
        page_normbeeld()
except Exception:
    _tb_text = traceback.format_exc()
    logger.exception("page render failed",
                     extra={"ctx": {"page": st.session_state.get("active_page")}})
    st.error(
        "Er ging iets mis bij het renderen van deze pagina. De analyse-"
        "onderdelen die wél lukten blijven bruikbaar via de andere pagina's."
    )
    with st.expander("Technische details (voor foutmelding/beheerder)"):
        st.code(_tb_text)


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
