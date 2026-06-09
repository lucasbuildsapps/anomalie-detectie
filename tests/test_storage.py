"""Tests voor de SQLite-opslaglaag: dedupe, batch-insert, data-hash."""
import pandas as pd
import pytest

import core.storage as storage


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Elke test krijgt een verse database in een tempdir."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    yield


def _sample_df(n=10, offset=0):
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D")
                       + pd.Timedelta(days=offset),
        "value": [float(i) for i in range(n)],
        "location_name": ["A"] * n,
    })


def test_insert_and_load_roundtrip():
    ds = storage.create_dataset("test", "", {"time": "t", "value": "v"})
    n = storage.insert_observations(ds, _sample_df())
    assert n == 10
    df = storage.load_observations(ds)
    assert len(df) == 10
    assert df["value"].sum() == 45.0


def test_duplicate_rows_ignored():
    """Tweemaal hetzelfde bestand importeren mag geen dubbele rijen geven."""
    ds = storage.create_dataset("test", "", {})
    assert storage.insert_observations(ds, _sample_df()) == 10
    assert storage.insert_observations(ds, _sample_df()) == 0
    assert len(storage.load_observations(ds)) == 10


def test_partial_overlap_inserts_only_new():
    ds = storage.create_dataset("test", "", {})
    storage.insert_observations(ds, _sample_df(10))
    n = storage.insert_observations(ds, _sample_df(10, offset=5))
    assert n == 10  # andere timestamps → allemaal nieuw
    assert len(storage.load_observations(ds)) == 20


def test_data_hash_changes_on_insert():
    ds = storage.create_dataset("test", "", {})
    h1 = storage.dataset_data_hash(ds)
    storage.insert_observations(ds, _sample_df())
    h2 = storage.dataset_data_hash(ds)
    assert h1 != h2


def test_clear_observations_keeps_dataset():
    ds = storage.create_dataset("test", "", {})
    storage.insert_observations(ds, _sample_df())
    storage.clear_observations(ds)
    assert len(storage.load_observations(ds)) == 0
    assert any(d["id"] == ds for d in storage.list_datasets())
