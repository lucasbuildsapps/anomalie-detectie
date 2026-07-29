"""Sessie-state-initialisatie (defaults voor alle pagina's)."""
from __future__ import annotations

import streamlit as st

from i18n.nl import t


def init_session_state() -> None:
    _DEFAULTS = {
        "ui_theme": "dark",
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

