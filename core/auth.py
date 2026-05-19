"""Eenvoudige wachtwoord-authenticatie voor publieke deployment.

Gebruik:
- Lokaal (geen auth): laat .streamlit/secrets.toml leeg of niet aanwezig.
- Online deployment: zet password in .streamlit/secrets.toml of via env-var
  ANOMALY_PASSWORD.

Deze auth biedt basisbescherming tegen casual access. Niet geschikt voor
classified data — voor echt operationeel gebruik moet IT een echte
SSO/Authentication-laag voor de app zetten.
"""
from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st


def _get_configured_password() -> str | None:
    """Haal wachtwoord op uit secrets.toml of env-var."""
    try:
        if hasattr(st, "secrets") and "password" in st.secrets:
            return str(st.secrets["password"])
    except Exception:
        pass
    return os.environ.get("ANOMALY_PASSWORD") or None


def _safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(),
        hashlib.sha256(b.encode()).digest(),
    )


def check_password() -> bool:
    """Toon login-formulier indien een wachtwoord is geconfigureerd.

    Returns True als (a) geen wachtwoord ingesteld, of (b) gebruiker
    geauthenticeerd. False als login nog niet voltooid.
    """
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False

    configured = _get_configured_password()
    if not configured:
        return True  # Geen auth ingesteld = open toegang (lokaal dev)

    if st.session_state.auth_ok:
        return True

    # Toon login-scherm
    st.markdown(
        """
        <div style="max-width: 400px; margin: 80px auto; padding: 32px;
                    border-radius: 4px; background: #ffffff;
                    border: 1px solid #dde1e6;">
            <h2 style="margin: 0 0 16px 0;">Anomalie-detectie</h2>
            <p style="color: #56616e; margin-bottom: 20px;">
                Toegang vereist een wachtwoord. Neem contact op met de beheerder
                als je dat niet hebt.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login_form"):
            pwd = st.text_input("Wachtwoord", type="password",
                                 label_visibility="collapsed",
                                 placeholder="Wachtwoord")
            submit = st.form_submit_button("Inloggen", type="primary",
                                            use_container_width=True)
        if submit:
            if _safe_compare(pwd, configured):
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("Onjuist wachtwoord.")
    return False
