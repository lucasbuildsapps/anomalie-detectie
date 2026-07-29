"""Tests voor identiteit en rollen (RBAC).

Kernpunt: autorisatie zit in de API, niet alleen in de UI. Een verborgen
knop is geen beveiliging — een script dat de API aanroept moet dezelfde
weigering krijgen.
"""
import pytest
from fastapi.testclient import TestClient

import core.storage as storage
from api.main import app
from core.authz import (
    ADMIN,
    ANALYST,
    VIEWER,
    Identity,
    PermissionDenied,
    identity_from_headers,
    require,
    role_from_groups,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("SENTINEL_GROUP_MAP", "SENTINEL_DEFAULT_ROLE", "SENTINEL_USER",
                "SENTINEL_ROLE", "SENTINEL_SSO_FALLBACK_ROLE",
                "SENTINEL_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "authz.db")
    storage.init_db()
    yield


class TestRoleResolution:
    def test_sso_headers_give_identity_and_role(self):
        ident = identity_from_headers({
            "X-Forwarded-User": "j.jansen",
            "X-Forwarded-Groups": "sentinel-analysts,other",
        })
        assert ident.username == "j.jansen"
        assert ident.role == ANALYST
        assert ident.source == "sso"
        assert ident.is_authenticated

    def test_highest_group_wins(self):
        assert role_from_groups(["sentinel-viewers", "sentinel-admins"]) == ADMIN

    def test_unknown_groups_fall_back_to_viewer(self):
        ident = identity_from_headers({
            "X-Forwarded-User": "x", "X-Forwarded-Groups": "willekeurig",
        })
        assert ident.role == VIEWER

    def test_group_map_is_configurable(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_GROUP_MAP", "mijn-team:admin")
        ident = identity_from_headers({
            "X-Forwarded-User": "x", "X-Forwarded-Groups": "mijn-team",
        })
        assert ident.role == ADMIN

    def test_no_headers_means_no_sso(self):
        ident = identity_from_headers({})
        assert ident.source == "default"
        assert not ident.is_authenticated

    def test_default_role_can_lock_down_shared_deployment(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_DEFAULT_ROLE", "viewer")
        assert identity_from_headers({}).role == VIEWER

    def test_worker_identity_from_env(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_USER", "ingest-worker")
        monkeypatch.setenv("SENTINEL_ROLE", "analyst")
        ident = identity_from_headers({})
        assert ident.username == "ingest-worker"
        assert ident.role == ANALYST
        assert ident.source == "env"

    def test_header_lookup_is_case_insensitive(self):
        ident = identity_from_headers({"x-forwarded-user": "kleine-letters"})
        assert ident.username == "kleine-letters"


class TestPermissions:
    @pytest.mark.parametrize("role,action,allowed", [
        (VIEWER, "view", True),
        (VIEWER, "annotate", False),
        (VIEWER, "delete_dataset", False),
        (ANALYST, "annotate", True),
        (ANALYST, "import_data", True),
        (ANALYST, "delete_dataset", False),
        (ANALYST, "view_audit", False),
        (ADMIN, "delete_dataset", True),
        (ADMIN, "view_audit", True),
        (ADMIN, "manage_sources", True),
    ])
    def test_permission_matrix(self, role, action, allowed):
        assert Identity("u", role).can(action) is allowed

    def test_unknown_action_is_denied(self):
        assert not Identity("u", ADMIN).can("iets_verzonnens")

    def test_require_raises_for_insufficient_role(self):
        with pytest.raises(PermissionDenied, match="delete_dataset"):
            require("delete_dataset", Identity("u", ANALYST))

    def test_require_returns_identity_when_allowed(self):
        ident = require("annotate", Identity("u", ANALYST))
        assert ident.role == ANALYST


class TestApiEnforcement:
    @pytest.fixture
    def client(self):
        with TestClient(app) as c:
            yield c

    def _headers(self, groups: str) -> dict:
        return {"X-Forwarded-User": "tester", "X-Forwarded-Groups": groups}

    def test_viewer_may_read_datasets(self, client):
        r = client.get("/datasets", headers=self._headers("sentinel-viewers"))
        assert r.status_code == 200

    def test_viewer_may_not_read_audit(self, client):
        r = client.get("/audit", headers=self._headers("sentinel-viewers"))
        assert r.status_code == 403
        assert "view_audit" in r.json()["detail"]

    def test_analyst_may_not_read_audit(self, client):
        r = client.get("/audit", headers=self._headers("sentinel-analysts"))
        assert r.status_code == 403

    def test_admin_may_read_audit(self, client):
        r = client.get("/audit", headers=self._headers("sentinel-admins"))
        assert r.status_code == 200

    def test_whoami_reports_role_and_permissions(self, client):
        r = client.get("/whoami", headers=self._headers("sentinel-analysts"))
        body = r.json()
        assert body["username"] == "tester"
        assert body["role"] == ANALYST
        assert body["identity_source"] == "sso"
        assert "annotate" in body["permissions"]
        assert "delete_dataset" not in body["permissions"]

    def test_health_stays_open_for_loadbalancers(self, client):
        assert client.get("/health").status_code == 200

    def test_locked_down_default_blocks_anonymous_reads(self, client,
                                                        monkeypatch):
        """Zonder SSO én met SENTINEL_DEFAULT_ROLE=viewer mag een anonieme
        aanroeper lezen maar niet de audit inzien."""
        monkeypatch.setenv("SENTINEL_DEFAULT_ROLE", "viewer")
        assert client.get("/datasets").status_code == 200
        assert client.get("/audit").status_code == 403


def test_audit_records_role_and_identity_source(monkeypatch):
    """De audit-trail moet niet alleen wie, maar ook met welke rechten
    vastleggen — en of die identiteit van de proxy kwam."""
    monkeypatch.setenv("SENTINEL_USER", "j.jansen")
    monkeypatch.setenv("SENTINEL_ROLE", "analyst")
    ds = storage.create_dataset("rbac-test", "", {})
    assert ds
    rows = storage.list_audit(10)
    entry = next(r for r in rows if r["action"] == "dataset_aangemaakt")
    assert entry["username"] == "j.jansen"
    assert '"_role": "analyst"' in entry["detail"]
    assert '"_identity_source": "env"' in entry["detail"]


class TestDeploymentHardening:
    """De headers zijn alleen te vertrouwen als de proxy ze zet. Deze
    tests bewaken de configuratie die dat afdwingt."""

    def _compose(self):
        import pathlib

        import yaml
        root = pathlib.Path(__file__).resolve().parent.parent
        return yaml.safe_load(
            (root / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    def _caddyfile(self) -> str:
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        return (root / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    def test_app_is_not_directly_exposed(self):
        """Wie de app rechtstreeks bereikt, kan identiteits-headers
        verzinnen. De app hoort alleen achter de proxy te staan."""
        services = self._compose()["services"]
        assert "ports" not in services["app"], (
            "app mag geen poort naar buiten publiceren")
        assert "ports" not in services["authelia"]
        assert "ports" in services["caddy"]

    def test_proxy_strips_client_supplied_identity_headers(self):
        caddy = self._caddyfile()
        assert "request_header -X-Forwarded-User" in caddy
        assert "request_header -X-Forwarded-Groups" in caddy

    def test_proxy_forwards_identity_from_authelia(self):
        caddy = self._caddyfile()
        assert "forward_auth authelia:9091" in caddy
        assert "Remote-User>X-Forwarded-User" in caddy
        assert "Remote-Groups>X-Forwarded-Groups" in caddy

    def test_app_defaults_to_viewer_without_sso(self):
        """Een verkeerd geconfigureerde proxy mag niet stilzwijgend
        beheerdersrechten opleveren."""
        env = self._compose()["services"]["app"]["environment"]
        assert env.get("SENTINEL_DEFAULT_ROLE") == "viewer"

    def test_worker_runs_as_analyst_not_admin(self):
        env = self._compose()["services"]["worker"]["environment"]
        assert env.get("SENTINEL_ROLE") == "analyst"

    def test_user_secrets_are_gitignored(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        ignored = (root / ".gitignore").read_text(encoding="utf-8")
        assert "deploy/authelia/users.yml" in ignored


class TestCompartmentalisation:
    """Need-to-know per dataset (roadmap 17). Een dataset met een
    compartiment-groep is alleen zichtbaar voor leden van die groep."""

    def _make(self, name, group=None):
        ds = storage.create_dataset(name, "", {})
        if group:
            storage.set_dataset_group(ds, group)
        return ds

    def test_open_dataset_is_visible_to_everyone(self, monkeypatch):
        self._make("open-set")
        monkeypatch.setenv("SENTINEL_DEFAULT_ROLE", "viewer")
        assert "open-set" in {d["name"] for d in storage.list_datasets()}

    def test_compartment_hides_dataset_from_outsiders(self):
        self._make("geheim", group="ops-alpha")
        ident = Identity("buitenstaander", VIEWER, groups=["andere-groep"])
        rows = storage.list_datasets(include_hidden=True)
        row = next(r for r in rows if r["name"] == "geheim")
        assert not storage.can_see_dataset(row, ident)

    def test_group_member_sees_it(self):
        self._make("geheim", group="ops-alpha")
        ident = Identity("lid", VIEWER, groups=["ops-alpha"])
        row = next(r for r in storage.list_datasets(include_hidden=True)
                   if r["name"] == "geheim")
        assert storage.can_see_dataset(row, ident)

    def test_group_match_is_case_insensitive(self):
        self._make("geheim", group="Ops-Alpha")
        ident = Identity("lid", VIEWER, groups=["ops-alpha"])
        row = next(r for r in storage.list_datasets(include_hidden=True)
                   if r["name"] == "geheim")
        assert storage.can_see_dataset(row, ident)

    def test_admin_always_sees_everything(self):
        self._make("geheim", group="ops-alpha")
        ident = Identity("beheerder", ADMIN, groups=[])
        row = next(r for r in storage.list_datasets(include_hidden=True)
                   if r["name"] == "geheim")
        assert storage.can_see_dataset(row, ident)

    def test_clearing_the_group_reopens_the_dataset(self):
        ds = self._make("geheim", group="ops-alpha")
        storage.set_dataset_group(ds, None)
        ident = Identity("iemand", VIEWER, groups=[])
        row = next(r for r in storage.list_datasets(include_hidden=True)
                   if r["name"] == "geheim")
        assert storage.can_see_dataset(row, ident)

    def test_compartment_change_is_audited(self):
        ds = self._make("geheim")
        storage.set_dataset_group(ds, "ops-alpha")
        actions = [a["action"] for a in storage.list_audit(20)]
        assert "dataset_compartiment_gewijzigd" in actions

    def test_api_hides_compartmented_dataset(self, monkeypatch):
        """Bewust 404 en geen 403: een 403 verklapt dat de dataset bestaat."""
        ds = self._make("geheim", group="ops-alpha")
        with TestClient(app) as c:
            outsider = {"X-Forwarded-User": "x",
                        "X-Forwarded-Groups": "sentinel-viewers"}
            assert ds not in [d["id"] for d in
                              c.get("/datasets", headers=outsider).json()]
            r = c.get(f"/datasets/{ds}/observations", headers=outsider)
            assert r.status_code == 404

    def test_api_shows_it_to_group_member(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_GROUP_MAP",
                           "ops-alpha:analyst,sentinel-viewers:viewer")
        ds = self._make("geheim", group="ops-alpha")
        with TestClient(app) as c:
            member = {"X-Forwarded-User": "lid",
                      "X-Forwarded-Groups": "ops-alpha"}
            ids = [d["id"] for d in c.get("/datasets", headers=member).json()]
            assert ds in ids
