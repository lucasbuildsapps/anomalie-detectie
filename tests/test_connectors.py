"""Tests voor de live-connectors (GDELT/ACLED).

Alle netwerk-aanroepen zijn gemockt: CI mag nooit afhangen van een externe
API. Wat hier getest wordt is het contract — parsing, foutafhandeling,
retry-gedrag — niet of GDELT vandaag online is.
"""
import urllib.error
from datetime import datetime

import pytest

import connectors.acled as acled_mod
import connectors.base as base_mod
import connectors.gdelt as gdelt_mod
from connectors.acled import AcledEventsConnector
from connectors.base import ConnectorError, http_json
from connectors.gdelt import GdeltNewsVolumeConnector

GDELT_OK = {
    "timeline": [{
        "series": "Volume",
        "data": [
            {"date": "20260701T000000Z", "value": 12},
            {"date": "20260702T000000Z", "value": 30},
        ],
    }]
}

ACLED_OK = {
    "success": True,
    "data": [
        {"event_date": "2026-07-01", "country": "Ukraine",
         "admin1": "Kharkiv", "event_type": "Explosions",
         "latitude": "49.99", "longitude": "36.23", "fatalities": "3"},
        {"event_date": "2026-07-02", "country": "Ukraine",
         "admin1": "Kherson", "event_type": "Battles",
         "latitude": "46.63", "longitude": "32.61", "fatalities": "0"},
    ],
}


class TestHttpJson:
    def test_retries_on_429_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def fake(url, timeout=60):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
            return _FakeResp(b'{"ok": true}')

        monkeypatch.setattr(base_mod.urllib.request, "urlopen",
                            lambda req, timeout=60: fake(req.full_url, timeout))
        monkeypatch.setattr(base_mod.time, "sleep", lambda *_: None)
        assert http_json("https://x.test", backoff=0.01) == {"ok": True}
        assert calls["n"] == 3

    def test_gives_up_after_retries(self, monkeypatch):
        def always_429(req, timeout=60):
            raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)

        monkeypatch.setattr(base_mod.urllib.request, "urlopen", always_429)
        monkeypatch.setattr(base_mod.time, "sleep", lambda *_: None)
        with pytest.raises(ConnectorError, match="onbereikbaar"):
            http_json("https://x.test", retries=2, backoff=0.01)

    def test_client_error_is_not_retried(self, monkeypatch):
        calls = {"n": 0}

        def bad_key(req, timeout=60):
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

        monkeypatch.setattr(base_mod.urllib.request, "urlopen", bad_key)
        with pytest.raises(ConnectorError, match="HTTP 403"):
            http_json("https://x.test")
        assert calls["n"] == 1  # geen zinloze retries op een foute key


class _FakeResp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestGdelt:
    def test_parses_timeline_per_region(self, monkeypatch):
        monkeypatch.setattr(gdelt_mod, "http_json", lambda url, **k: GDELT_OK)
        monkeypatch.setattr(gdelt_mod, "WATCHLIST",
                            {"Oekraïne": "q1", "Sahel": "q2"})
        df = GdeltNewsVolumeConnector().fetch(None)
        assert set(df["location_name"]) == {"Oekraïne", "Sahel"}
        assert len(df) == 4
        assert df["value"].tolist() == [12.0, 30.0, 12.0, 30.0]
        assert str(df["timestamp"].dtype).startswith("datetime64")

    def test_partial_failure_still_returns_data(self, monkeypatch):
        def flaky(url, **k):
            if "boom" in url:
                raise ConnectorError("bron plat")
            return GDELT_OK

        monkeypatch.setattr(gdelt_mod, "http_json", flaky)
        monkeypatch.setattr(gdelt_mod, "WATCHLIST",
                            {"Goed": "ok", "Stuk": "boom"})
        df = GdeltNewsVolumeConnector().fetch(None)
        assert set(df["location_name"]) == {"Goed"}

    def test_total_failure_raises(self, monkeypatch):
        def dead(url, **k):
            raise ConnectorError("plat")

        monkeypatch.setattr(gdelt_mod, "http_json", dead)
        with pytest.raises(ConnectorError):
            GdeltNewsVolumeConnector().fetch(None)

    def test_needs_no_api_key(self):
        assert GdeltNewsVolumeConnector().missing_config() == []


class TestAcled:
    def test_missing_key_is_reported_not_crashed(self, monkeypatch):
        monkeypatch.delenv("ACLED_API_KEY", raising=False)
        monkeypatch.delenv("ACLED_EMAIL", raising=False)
        c = AcledEventsConnector()
        assert set(c.missing_config()) == {"ACLED_API_KEY", "ACLED_EMAIL"}
        ok, msg = c.self_test()
        assert not ok
        assert "ACLED_API_KEY" in msg

    def test_maps_events_to_internal_schema(self, monkeypatch):
        monkeypatch.setenv("ACLED_API_KEY", "k")
        monkeypatch.setenv("ACLED_EMAIL", "a@b.c")
        monkeypatch.setattr(acled_mod, "http_json", lambda url, **k: ACLED_OK)
        df = AcledEventsConnector().fetch(datetime(2026, 6, 1))
        assert list(df["location_name"]) == ["Kharkiv", "Kherson"]
        assert df["value"].tolist() == [1.0, 1.0]  # 1 rij = 1 gebeurtenis
        assert df["lat"].iloc[0] == pytest.approx(49.99)
        assert "fatalities" in df.columns

    def test_api_refusal_raises_connector_error(self, monkeypatch):
        monkeypatch.setenv("ACLED_API_KEY", "k")
        monkeypatch.setenv("ACLED_EMAIL", "a@b.c")
        monkeypatch.setattr(acled_mod, "http_json",
                            lambda url, **k: {"success": False, "error": "bad key"})
        with pytest.raises(ConnectorError, match="weigerde"):
            AcledEventsConnector().fetch(None)

    def test_empty_result_is_not_an_error(self, monkeypatch):
        monkeypatch.setenv("ACLED_API_KEY", "k")
        monkeypatch.setenv("ACLED_EMAIL", "a@b.c")
        monkeypatch.setattr(acled_mod, "http_json",
                            lambda url, **k: {"success": True, "data": []})
        assert AcledEventsConnector().fetch(None).empty


def test_all_connectors_disabled_by_default():
    """Niets haalt ongevraagd data op; de gebruiker zet bronnen zelf aan."""
    from connectors.base import get_connectors
    for name, c in get_connectors().items():
        assert c.enabled is False, f"{name} staat standaard aan"


def test_fetched_frames_pass_import_validation(monkeypatch):
    """Wat een connector oplevert moet door dezelfde validatie komen als
    een handmatige upload — anders faalt de ingest pas in productie."""
    from core.validation import validate_mapped
    monkeypatch.setattr(gdelt_mod, "http_json", lambda url, **k: GDELT_OK)
    monkeypatch.setattr(gdelt_mod, "WATCHLIST", {"Test": "q"})
    df = GdeltNewsVolumeConnector().fetch(None)
    assert validate_mapped(df).ok

    monkeypatch.setenv("ACLED_API_KEY", "k")
    monkeypatch.setenv("ACLED_EMAIL", "a@b.c")
    monkeypatch.setattr(acled_mod, "http_json", lambda url, **k: ACLED_OK)
    assert validate_mapped(AcledEventsConnector().fetch(None)).ok


def test_dataframes_are_ingestible(tmp_path, monkeypatch):
    """End-to-end: connector-output gaat door de echte ingest-pipeline."""
    import core.storage as storage
    from core.ingest import run_connector

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "conn.db")
    storage.init_db()
    monkeypatch.setattr(gdelt_mod, "http_json", lambda url, **k: GDELT_OK)
    monkeypatch.setattr(gdelt_mod, "WATCHLIST", {"Test": "q"})

    c = GdeltNewsVolumeConnector()
    summary = run_connector(c)
    assert summary["status"] == "ok"
    assert summary["rows_added"] == 2
    # Tweede run: dedupe pakt dezelfde rijen af
    assert run_connector(c)["rows_added"] == 0


def test_self_test_reports_success(monkeypatch):
    monkeypatch.setattr(gdelt_mod, "http_json", lambda url, **k: GDELT_OK)
    monkeypatch.setattr(gdelt_mod, "WATCHLIST", {"Test": "q"})
    ok, msg = GdeltNewsVolumeConnector().self_test()
    assert ok
    assert "2 rijen" in msg


def test_self_test_reports_failure_without_raising(monkeypatch):
    def dead(url, **k):
        raise ConnectorError("bron plat")

    monkeypatch.setattr(gdelt_mod, "http_json", dead)
    ok, msg = GdeltNewsVolumeConnector().self_test()
    assert not ok
    assert "plat" in msg
