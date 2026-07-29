"""Eenvoudige wachtwoord-authenticatie voor publieke deployment.

Gebruik:
- Lokaal (geen auth): laat .streamlit/secrets.toml leeg of niet aanwezig.
- Online deployment: zet password in .streamlit/secrets.toml of via env-var
  ANOMALY_PASSWORD.

Bescherming tegen brute force: na MAX_FAILURES mislukte pogingen (per client,
op basis van X-Forwarded-For als een reverse proxy die zet, anders globaal)
volgt een lockout die per verdere poging verdubbelt. Elke poging wordt in de
audit-trail vastgelegd.

Deze auth biedt basisbescherming tegen casual access. Niet geschikt voor
classified data — voor echt operationeel gebruik hoort hier een
SSO/reverse-proxy-laag voor (X-Forwarded-User wordt dan de identiteit,
zie core/storage.py::current_user).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass

import streamlit as st

from core import storage
from core.logging_setup import get_logger

_logger = get_logger("auth")

MAX_FAILURES = 5          # pogingen vóór de eerste lockout
BASE_LOCKOUT_SECONDS = 60  # eerste lockout; verdubbelt per volgende mislukking
MAX_LOCKOUT_SECONDS = 900  # plafond: 15 minuten


@dataclass
class _AttemptState:
    failures: int = 0
    locked_until: float = 0.0


_attempts: dict[str, _AttemptState] = {}
_attempts_lock = threading.Lock()


def _get_configured_password() -> str | None:
    """Haal wachtwoord op uit secrets.toml of env-var."""
    try:
        if hasattr(st, "secrets") and "password" in st.secrets:
            return str(st.secrets["password"])
    except Exception:
        # Geen secrets-bestand aanwezig is een normaal (lokaal) scenario.
        pass
    return os.environ.get("ANOMALY_PASSWORD") or None


def _safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(),
        hashlib.sha256(b.encode()).digest(),
    )


def _client_key() -> str:
    """Sleutel voor rate limiting: client-IP als de proxy dat meegeeft,
    anders één globale bucket (beter een te strenge dan geen limiet)."""
    try:
        fwd = st.context.headers.get("X-Forwarded-For")
        if fwd:
            return str(fwd).split(",")[0].strip()
    except Exception:
        pass
    return "global"


def _check_lockout(key: str) -> float:
    """Resterende lockout-seconden voor deze client (0 = niet gelockt)."""
    with _attempts_lock:
        state = _attempts.get(key)
        if state is None:
            return 0.0
        return max(0.0, state.locked_until - time.time())


def _register_failure(key: str) -> None:
    with _attempts_lock:
        state = _attempts.setdefault(key, _AttemptState())
        state.failures += 1
        if state.failures >= MAX_FAILURES:
            over = state.failures - MAX_FAILURES
            lockout = min(BASE_LOCKOUT_SECONDS * (2 ** over),
                          MAX_LOCKOUT_SECONDS)
            state.locked_until = time.time() + lockout
            _logger.warning("login-lockout actief",
                            extra={"ctx": {"client": key,
                                           "failures": state.failures,
                                           "lockout_s": lockout}})


def _register_success(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def is_protected() -> bool:
    """True zodra een wachtwoord is geconfigureerd (login-scherm actief)."""
    return _get_configured_password() is not None


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
    key = _client_key()
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        remaining = _check_lockout(key)
        if remaining > 0:
            st.error(
                f"Te veel mislukte pogingen. Probeer het over "
                f"{int(remaining) + 1} seconden opnieuw."
            )
            return False
        with st.form("login_form"):
            pwd = st.text_input("Wachtwoord", type="password",
                                 label_visibility="collapsed",
                                 placeholder="Wachtwoord")
            submit = st.form_submit_button("Inloggen", type="primary",
                                            use_container_width=True)
        if submit:
            if _safe_compare(pwd, configured):
                _register_success(key)
                storage.record_audit("login_gelukt", detail={"client": key})
                st.session_state.auth_ok = True
                st.rerun()
            else:
                _register_failure(key)
                storage.record_audit("login_mislukt", detail={"client": key})
                st.error("Onjuist wachtwoord.")
    return False
