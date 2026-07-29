"""Tests voor de triage-workflow (behandelde vs. onbehandelde afwijkingen)."""
import pytest

import core.storage as storage
from core import annotations as anno


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "triage.db")
    storage.init_db()
    yield


def _alerts():
    return [
        {"datum": "2025-06-01", "locatie": "Alpha", "waarde": 12},
        {"datum": "2025-06-02", "locatie": "Alpha", "waarde": 15},
        {"datum": "2025-06-02", "locatie": "Bravo", "waarde": 9},
    ]


def test_all_unhandled_without_annotations():
    ds = storage.create_dataset("t", "", {})
    c = anno.triage_counts(ds, _alerts())
    assert c == {"total": 3, "handled": 0, "unhandled": 3, "escalated": 0}


def test_handled_statuses_reduce_unhandled():
    ds = storage.create_dataset("t", "", {})
    key = anno.finding_key("2025-06-01", "Alpha", None)
    anno.save_annotation(ds, key, "beoordeeld", "vals_alarm")
    c = anno.triage_counts(ds, _alerts())
    assert c["handled"] == 1
    assert c["unhandled"] == 2


def test_open_status_is_not_handled():
    ds = storage.create_dataset("t", "", {})
    key = anno.finding_key("2025-06-01", "Alpha", None)
    anno.save_annotation(ds, key, "nog bezig", "open")
    c = anno.triage_counts(ds, _alerts())
    assert c["unhandled"] == 3


def test_escalated_counted_separately():
    ds = storage.create_dataset("t", "", {})
    key = anno.finding_key("2025-06-02", "Bravo", None)
    anno.save_annotation(ds, key, "doorgezet naar S2", "geescaleerd")
    c = anno.triage_counts(ds, _alerts())
    assert c["escalated"] == 1
    assert c["handled"] == 1


def test_invalid_status_falls_back_to_open():
    ds = storage.create_dataset("t", "", {})
    key = anno.finding_key("2025-06-01", "Alpha", None)
    anno.save_annotation(ds, key, "x", "onzin-status")
    assert anno.get_annotation(ds, key)["status"] == "open"
