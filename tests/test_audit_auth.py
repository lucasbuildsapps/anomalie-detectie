"""Tests voor de audit-trail en de login-rate-limiting."""
import pandas as pd
import pytest

import core.storage as storage
from core import auth


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    yield


def test_mutations_leave_audit_trail():
    ds = storage.create_dataset("audit-test", "", {})
    storage.insert_observations(ds, pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=3, freq="D"),
        "value": [1.0, 2.0, 3.0],
    }))
    storage.delete_dataset(ds)

    actions = [r["action"] for r in storage.list_audit()]
    assert "dataset_aangemaakt" in actions
    assert "observaties_geimporteerd" in actions
    assert "dataset_verwijderd" in actions


def test_audit_records_username_from_env(monkeypatch):
    monkeypatch.setenv("SENTINEL_USER", "analist-x")
    storage.record_audit("test_actie")
    rows = storage.list_audit(limit=1)
    assert rows[0]["username"] == "analist-x"
    assert rows[0]["action"] == "test_actie"


def test_audit_newest_first():
    storage.record_audit("eerste")
    storage.record_audit("tweede")
    rows = storage.list_audit(limit=2)
    assert rows[0]["action"] == "tweede"


def test_audit_failure_does_not_raise(monkeypatch):
    """Een kapotte audit-schrijf mag de hoofdoperatie nooit laten falen."""
    def boom():
        raise RuntimeError("db weg")
    monkeypatch.setattr(storage, "_engine", boom)
    storage.record_audit("actie")  # geen exception


class TestRateLimiting:
    @pytest.fixture(autouse=True)
    def reset_attempts(self):
        auth._attempts.clear()
        yield
        auth._attempts.clear()

    def test_no_lockout_below_threshold(self):
        for _ in range(auth.MAX_FAILURES - 1):
            auth._register_failure("1.2.3.4")
        assert auth._check_lockout("1.2.3.4") == 0.0

    def test_lockout_at_threshold(self):
        for _ in range(auth.MAX_FAILURES):
            auth._register_failure("1.2.3.4")
        remaining = auth._check_lockout("1.2.3.4")
        assert 0 < remaining <= auth.BASE_LOCKOUT_SECONDS

    def test_lockout_doubles_and_caps(self):
        for _ in range(auth.MAX_FAILURES + 20):
            auth._register_failure("1.2.3.4")
        remaining = auth._check_lockout("1.2.3.4")
        assert remaining <= auth.MAX_LOCKOUT_SECONDS

    def test_success_resets(self):
        for _ in range(auth.MAX_FAILURES):
            auth._register_failure("1.2.3.4")
        auth._register_success("1.2.3.4")
        assert auth._check_lockout("1.2.3.4") == 0.0

    def test_clients_are_independent(self):
        for _ in range(auth.MAX_FAILURES):
            auth._register_failure("1.2.3.4")
        assert auth._check_lockout("5.6.7.8") == 0.0
