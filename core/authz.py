"""Identiteit en rechten (RBAC), gevoed door de SSO-reverse-proxy.

Ontwerpkeuze: de app doet **geen** eigen gebruikersbeheer. Een
identity provider (Keycloak/Authelia) staat ervoor en zet headers:

    X-Forwarded-User    de gebruikersnaam
    X-Forwarded-Groups  komma-gescheiden groepen

Waarom zo: eigen wachtwoordbeheer bouwen is een bekende bron van
lekken, en in een organisatie hoort identiteit centraal geregeld te
zijn. De app vertrouwt op wat de proxy meestuurt.

**Veiligheidsvoorwaarde**: die headers mogen alleen van de proxy komen.
Staat de app rechtstreeks aan het internet, dan kan iedereen ze
verzinnen. Vandaar `TRUST_PROXY_HEADERS` (default aan zodra SSO is
geconfigureerd) en de expliciete waarschuwing in de UI zolang er geen
identity provider staat.

Rollen (oplopend):
- **viewer**   — alles lezen, niets wijzigen
- **analyst**  — bevindingen beoordelen, data importeren, weergaves opslaan
- **admin**    — datasets verwijderen, bronnen beheren, audit inzien
"""
from __future__ import annotations

import contextlib
import os
from contextvars import ContextVar
from dataclasses import dataclass, field

VIEWER, ANALYST, ADMIN = "viewer", "analyst", "admin"
ROLES = (VIEWER, ANALYST, ADMIN)
_RANK = {VIEWER: 0, ANALYST: 1, ADMIN: 2}

ROLE_LABELS = {
    VIEWER: "Meekijker (alleen lezen)",
    ANALYST: "Analist (beoordelen en importeren)",
    ADMIN: "Beheerder (volledige rechten)",
}

#: Rechten per rol. Expliciet, zodat een audit één plek hoeft te lezen.
PERMISSIONS = {
    "view":            VIEWER,
    "annotate":        ANALYST,
    "save_view":       ANALYST,
    "import_data":     ANALYST,
    "edit_metadata":   ANALYST,
    "run_connector":   ANALYST,
    "delete_dataset":  ADMIN,
    "delete_data":     ADMIN,
    "manage_sources":  ADMIN,
    "view_audit":      ADMIN,
}

#: Groepsnaam (uit de IdP) -> rol. Aanpasbaar via env:
#:   SENTINEL_GROUP_MAP="sentinel-admins:admin,sentinel-analysts:analyst"
_DEFAULT_GROUP_MAP = {
    "sentinel-admins": ADMIN,
    "sentinel-analysts": ANALYST,
    "sentinel-viewers": VIEWER,
}


@dataclass
class Identity:
    """Wie is dit, en wat mag die persoon."""

    username: str
    role: str
    groups: list[str] = field(default_factory=list)
    source: str = "default"      # 'sso' / 'env' / 'default'

    def can(self, action: str) -> bool:
        required = PERMISSIONS.get(action)
        if required is None:
            return False
        return _RANK.get(self.role, -1) >= _RANK[required]

    @property
    def is_authenticated(self) -> bool:
        return self.source == "sso"


def _group_map() -> dict[str, str]:
    raw = os.environ.get("SENTINEL_GROUP_MAP", "").strip()
    if not raw:
        return dict(_DEFAULT_GROUP_MAP)
    out = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        group, role = pair.split(":", 1)
        role = role.strip().lower()
        if role in ROLES:
            out[group.strip().lower()] = role
    return out or dict(_DEFAULT_GROUP_MAP)


def _default_role() -> str:
    """Rol wanneer geen SSO actief is.

    Default 'admin' houdt de lokale/enkelvoudige opstelling werkbaar (wie
    het wachtwoord heeft, kan alles — zoals het altijd al was). Zet
    SENTINEL_DEFAULT_ROLE=viewer om een gedeelde omgeving dicht te
    zetten zolang de IdP er nog niet is.
    """
    role = os.environ.get("SENTINEL_DEFAULT_ROLE", ADMIN).strip().lower()
    return role if role in ROLES else ADMIN


def role_from_groups(groups: list[str]) -> str | None:
    """Hoogste rol die uit de groepen volgt, of None."""
    mapping = _group_map()
    found = [mapping[g.strip().lower()] for g in groups
             if g.strip().lower() in mapping]
    if not found:
        return None
    return max(found, key=lambda r: _RANK[r])


def identity_from_headers(headers) -> Identity:
    """Bouw een Identity uit proxy-headers (dict-achtig, case-insensitive
    lookup wordt hier zelf afgehandeld)."""
    def get(name: str) -> str:
        if headers is None:
            return ""
        for key in (name, name.lower(), name.upper(), name.title()):
            try:
                value = headers.get(key)
            except Exception:
                value = None
            if value:
                return str(value)
        return ""

    user = get("X-Forwarded-User").strip()
    raw_groups = get("X-Forwarded-Groups")
    groups = [g for g in (x.strip() for x in raw_groups.split(",")) if g]

    if user:
        role = role_from_groups(groups) or os.environ.get(
            "SENTINEL_SSO_FALLBACK_ROLE", VIEWER).strip().lower()
        if role not in ROLES:
            role = VIEWER
        return Identity(username=user, role=role, groups=groups, source="sso")

    # Geen SSO: worker/CLI-context of enkelvoudige opstelling.
    env_user = os.environ.get("SENTINEL_USER")
    if env_user:
        env_role = os.environ.get("SENTINEL_ROLE", ADMIN).strip().lower()
        return Identity(
            username=env_user,
            role=env_role if env_role in ROLES else ADMIN,
            source="env",
        )
    return Identity(username="onbekend", role=_default_role(),
                    source="default")


#: Identiteit van het huidige verzoek. Nodig omdat de app twee frontends
#: heeft: Streamlit (identiteit uit st.context) en de FastAPI-service
#: (identiteit uit het request). Lagen als core/storage.py mogen geen van
#: beide kennen — die vragen simpelweg `current_identity()`.
_current: ContextVar[Identity | None] = ContextVar(
    "sentinel_identity", default=None)


def set_identity(identity: Identity):
    """Zet de identiteit voor de duur van dit verzoek. Returnt een token
    voor `reset_identity`."""
    return _current.set(identity)


def reset_identity(token) -> None:
    with contextlib.suppress(ValueError):
        _current.reset(token)


def current_identity() -> Identity:
    """Identiteit van de huidige gebruiker.

    Volgorde: expliciet gezet voor dit verzoek (API-middleware) →
    Streamlit-context → env/default.
    """
    explicit = _current.get()
    if explicit is not None:
        return explicit
    headers = None
    try:
        import streamlit as st
        headers = st.context.headers
    except Exception:
        headers = None
    return identity_from_headers(headers)


def sso_active() -> bool:
    return current_identity().source == "sso"


class PermissionDenied(PermissionError):
    """Actie geweigerd omdat de rol te laag is."""


def require(action: str, identity: Identity | None = None) -> Identity:
    """Gooi PermissionDenied als de gebruiker `action` niet mag."""
    ident = identity or current_identity()
    if not ident.can(action):
        raise PermissionDenied(
            f"'{action}' vereist minimaal de rol "
            f"'{PERMISSIONS.get(action, '?')}'; jij hebt '{ident.role}'."
        )
    return ident
