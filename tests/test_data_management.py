"""Tests Fase 2: gap-policy, datakwaliteit, uur-aggregatie, metadata."""
import numpy as np
import pandas as pd
import pytest

import core.storage as storage
from core.normbeeld import (
    _aggregate,
    _detect_period,
    compute_normbeeld,
    data_quality,
)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "t.db")
    storage._engines.clear()
    storage.init_db()
    yield


def _gappy_df():
    """30 dagen data, dan 10 dagen gat, dan 20 dagen data."""
    idx1 = pd.date_range("2025-01-01", periods=30, freq="D")
    idx2 = pd.date_range("2025-02-10", periods=20, freq="D")
    idx = idx1.append(idx2)
    return pd.DataFrame({
        "timestamp": idx, "value": 5.0, "location_name": "X",
    })


# ---------------------------------------------------------------------------
# Gap-policy
# ---------------------------------------------------------------------------
def test_gap_policy_zero_fills_zero():
    s, observed = _aggregate(_gappy_df(), "D", "zero")
    gap = s.loc["2025-02-01":"2025-02-05"]
    assert (gap == 0).all()
    assert not observed.loc["2025-02-01":"2025-02-05"].any()


def test_gap_policy_interpolate_fills_between():
    s, _ = _aggregate(_gappy_df(), "D", "interpolate")
    gap = s.loc["2025-02-01":"2025-02-05"]
    assert (gap > 0).all(), "interpolatie moet tussen 5 en 5 blijven, niet 0"


def test_gap_policy_mask_marks_geen_data():
    nb = compute_normbeeld(_gappy_df(), location="X", horizon_days=7,
                           aggregation="daily", gap_policy="mask")
    assert nb is not None
    statuses = set(nb.historical["status"].unique())
    assert "geen data" in statuses
    # gemaskeerde punten zijn nooit een afwijking
    masked = nb.historical[nb.historical["status"] == "geen data"]
    assert masked["actual"].isna().all()


def test_gap_policy_zero_flags_gap_as_onder():
    """Met zero-policy is een gat gewoon '0 events' — bij een baseline van 5
    moet dat als 'onder' geflagd (kunnen) worden. Contrast met mask."""
    nb_zero = compute_normbeeld(_gappy_df(), location="X", horizon_days=7,
                                aggregation="daily", gap_policy="zero")
    gap_dates = pd.date_range("2025-02-01", periods=5, freq="D")
    zero_statuses = nb_zero.historical[
        nb_zero.historical["date"].isin(gap_dates)
    ]["status"]
    assert (zero_statuses != "geen data").all()


# ---------------------------------------------------------------------------
# Datakwaliteit
# ---------------------------------------------------------------------------
def test_data_quality_coverage_below_one_for_gaps():
    q = data_quality(_gappy_df(), "daily")
    assert q["coverage"] is not None
    assert 0.5 < q["coverage"] < 1.0
    assert q["staleness_days"] >= 0
    assert q["n_rows"] == 50


def test_data_quality_empty_df():
    q = data_quality(pd.DataFrame(), "daily")
    assert q["coverage"] is None


# ---------------------------------------------------------------------------
# Uur-aggregatie
# ---------------------------------------------------------------------------
def test_hourly_aggregation_and_period_detection():
    """5 dagen uur-data met dag-ritme => periode 24 gedetecteerd."""
    idx = pd.date_range("2025-01-01", periods=24 * 7, freq="h")
    vals = 10 + 8 * np.sin(2 * np.pi * idx.hour / 24)
    df = pd.DataFrame({"timestamp": idx, "value": vals,
                       "location_name": "X"})
    s, _ = _aggregate(df, "h", "zero")
    assert len(s) == 24 * 7
    assert _detect_period(s, "hourly") == 24
    nb = compute_normbeeld(df, location="X", horizon_days=24,
                           aggregation="hourly")
    assert nb is not None
    assert len(nb.forecast) == 24


# ---------------------------------------------------------------------------
# Metadata-opslag
# ---------------------------------------------------------------------------
def test_update_dataset_mapping_roundtrip():
    ds = storage.create_dataset("test", "", {"time": "t", "value": "v"})
    meta = {"time": "t", "value": "v", "gap_policy": "mask",
            "source_reliability": "B", "info_credibility": "2"}
    storage.update_dataset_mapping(ds, meta)
    loaded = [d for d in storage.list_datasets() if d["id"] == ds][0]
    assert loaded["column_mapping"]["gap_policy"] == "mask"
    assert loaded["column_mapping"]["source_reliability"] == "B"
