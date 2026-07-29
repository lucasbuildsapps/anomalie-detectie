"""Tests voor de ingest-pipeline (connector → validatie → opslag → run-log)."""
from datetime import datetime

import pandas as pd
import pytest

import core.storage as storage
from connectors.base import Connector, get_connectors
from core.ingest import run_connector


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "ingest.db")
    storage.init_db()
    yield


class FakeConnector(Connector):
    name = "fake-bron"
    dataset_name = "Fake bron (test)"
    schedule_minutes = 5
    enabled = True

    def __init__(self, n=20):
        self.n = n
        self.calls: list[datetime | None] = []

    def fetch(self, since):
        self.calls.append(since)
        return pd.DataFrame({
            "timestamp": pd.date_range("2025-03-01", periods=self.n, freq="D"),
            "value": [float(i % 4) for i in range(self.n)],
            "location_name": ["X"] * self.n,
        })


class BrokenConnector(Connector):
    name = "kapotte-bron"
    dataset_name = "Kapot (test)"
    enabled = True

    def fetch(self, since):
        raise ConnectionError("bron onbereikbaar")


def test_first_run_creates_dataset_and_inserts():
    c = FakeConnector()
    summary = run_connector(c)
    assert summary["status"] == "ok"
    assert summary["rows_added"] == 20
    assert c.calls == [None]  # lege dataset: volledige pull
    names = [d["name"] for d in storage.list_datasets()]
    assert "Fake bron (test)" in names


def test_second_run_dedupes_and_is_incremental():
    c = FakeConnector()
    run_connector(c)
    summary = run_connector(c)
    assert summary["status"] == "ok"
    assert summary["rows_added"] == 0  # zelfde rijen → dedupe
    assert c.calls[1] is not None      # incrementele 'since' meegegeven


def test_failure_is_recorded_not_raised():
    summary = run_connector(BrokenConnector())
    assert summary["status"] == "error"
    assert "ConnectionError" in summary["error"]
    runs = storage.list_ingest_runs("kapotte-bron")
    assert runs[0]["status"] == "error"


def test_run_log_and_health():
    run_connector(FakeConnector())
    health = storage.source_health()
    assert any(h["source"] == "fake-bron" and h["last_status"] == "ok"
               for h in health)


def test_connector_discovery_finds_demo():
    names = get_connectors()
    assert "demo-csv" in names
    assert names["demo-csv"].enabled is False  # sjabloon staat uit
