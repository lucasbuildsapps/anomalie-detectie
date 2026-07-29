"""Tests voor analyse-momentopnames (herleidbaarheid van een oordeel)."""
import numpy as np
import pandas as pd
import pytest

import core.storage as storage
from core.normbeeld import compute_all_normbeelds, detect_recent_alerts


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "snap.db")
    storage.init_db()
    yield


@pytest.fixture
def seeded():
    rng = np.random.default_rng(3)
    n = 120
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
        "value": np.clip(6 + rng.normal(0, 2, n), 0, None).round(),
        "location_name": ["Alpha"] * n,
    })
    ds = storage.create_dataset("snap-test", "", {})
    storage.insert_observations(ds, df)
    return ds, df


def _make(ds, df, label=None):
    nbs = compute_all_normbeelds(df, horizon_days=14, aggregation="daily")
    alerts = detect_recent_alerts(nbs, aggregation="daily")
    return storage.save_snapshot(ds, alerts, nbs, aggregation="daily",
                                 horizon=14, n_rows=len(df), label=label)


def test_snapshot_roundtrip(seeded):
    ds, df = seeded
    sid = _make(ds, df, label="test")
    snap = storage.get_snapshot(sid)
    assert snap["dataset_id"] == ds
    assert snap["label"] == "test"
    assert "alerts" in snap["payload"]
    assert "Alpha" in snap["payload"]["normbeelds"]


def test_normbeeld_summary_has_decision_relevant_fields(seeded):
    ds, df = seeded
    snap = storage.get_snapshot(_make(ds, df))
    entry = snap["payload"]["normbeelds"]["Alpha"]
    for key in ("expected", "lower", "upper", "band_model", "confidence",
                "n_recent_deviations", "methods_used"):
        assert key in entry, f"{key} ontbreekt in snapshot"


def test_listing_is_newest_first_and_omits_payload(seeded):
    ds, df = seeded
    _make(ds, df, label="oud")
    _make(ds, df, label="nieuw")
    rows = storage.list_snapshots(ds)
    assert rows[0]["label"] == "nieuw"
    assert "payload" not in rows[0]  # lijst blijft licht


def test_snapshot_is_audited(seeded):
    ds, df = seeded
    _make(ds, df)
    actions = [a["action"] for a in storage.list_audit(50)]
    assert "save_snapshot" in actions


def test_unknown_id_returns_none():
    assert storage.get_snapshot(9999) is None


def test_payload_survives_numpy_types(seeded):
    """Alerts bevatten numpy-scalars; die moeten JSON-serialiseerbaar zijn."""
    ds, df = seeded
    alerts = [{"datum": "2025-04-01", "locatie": "Alpha",
               "waarde": np.int64(12), "richting": "boven",
               "score": np.float64(2.5)}]
    sid = storage.save_snapshot(ds, alerts, {}, aggregation="daily",
                                horizon=14, n_rows=len(df))
    got = storage.get_snapshot(sid)["payload"]["alerts"][0]
    assert got["waarde"] == 12
    assert got["score"] == pytest.approx(2.5)


def test_ingest_creates_snapshot(tmp_path, monkeypatch):
    """De dagelijkse inwinning laat vanzelf een spoor na."""
    from connectors.base import Connector
    from core.ingest import run_connector

    class Fake(Connector):
        name = "snap-bron"
        dataset_name = "Snap bron"
        enabled = True

        def fetch(self, since):
            rng = np.random.default_rng(5)
            return pd.DataFrame({
                "timestamp": pd.date_range("2025-02-01", periods=60, freq="D"),
                "value": np.clip(4 + rng.normal(0, 1.5, 60), 0, None).round(),
                "location_name": ["X"] * 60,
            })

    assert run_connector(Fake())["status"] == "ok"
    assert len(storage.list_snapshots()) == 1
