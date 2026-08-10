"""Storage-side point-in-time correctness: arrival policy and as-of reads.

`tests/test_as_of.py` proves `AsOfView` behaves correctly given a loader.
This file proves the real loader — backed by the database — is one of those
correct loaders, and that arrival times are recorded honestly on the way in.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import storage
from sentinel.core.time import PointInTimeStore, assert_causal

BASE = dt.datetime(2024, 1, 1)


@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    # storage caches engines per URL, so a fresh DATABASE_URL is enough to
    # get an isolated database — no cache reset needed.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'obs.db'}")
    storage.init_db()
    return storage.create_dataset("pit", "", {})


def _events(start: str, n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="D"),
        "value": [float(i) for i in range(n)],
    })


# --- arrival policy -------------------------------------------------------
def test_event_time_policy_marks_arrival_as_estimated(dataset):
    """Bulk history: we assume instant reporting and say so."""
    storage.insert_observations(dataset, _events("2024-01-01", 3),
                                arrival="event_time")
    df = storage.load_observations_as_of(dataset, dt.datetime(2030, 1, 1))
    assert len(df) == 3
    assert df["ingest_estimated"].all()
    assert (df["ingested_at"] == df["timestamp"]).all()


def test_now_policy_records_observed_arrival(dataset):
    """Connector ingest: arrival is observed, so it is not flagged."""
    storage.insert_observations(dataset, _events("2024-01-01", 3),
                                arrival="now")
    df = storage.load_observations_as_of(dataset, dt.datetime(2030, 1, 1))
    assert not df["ingest_estimated"].any()
    assert (df["ingested_at"] > df["timestamp"]).all()


def test_explicit_ingested_at_column_wins(dataset):
    """A source that reports its own receipt time is the best truth available."""
    df = _events("2024-01-01", 2)
    df["ingested_at"] = [BASE + dt.timedelta(days=10)] * 2
    storage.insert_observations(dataset, df, arrival="event_time")
    out = storage.load_observations_as_of(dataset, dt.datetime(2030, 1, 1))
    assert not out["ingest_estimated"].any()
    assert (out["ingested_at"] == pd.Timestamp(BASE + dt.timedelta(days=10))).all()


def test_unknown_arrival_policy_is_rejected(dataset):
    with pytest.raises(ValueError, match="arrival"):
        storage.insert_observations(dataset, _events("2024-01-01", 1),
                                    arrival="whenever")


def test_reimport_keeps_the_original_arrival_time(dataset):
    """Dedupe semantics: the first sighting is when we learned it.

    If a re-import refreshed `ingested_at`, replaying history would show rows
    appearing later than they really did, and every lead-time measurement
    would be wrong.
    """
    df = _events("2024-01-01", 3)
    df["ingested_at"] = [BASE + dt.timedelta(days=1)] * 3
    storage.insert_observations(dataset, df, arrival="event_time")

    df2 = df.copy()
    df2["ingested_at"] = [BASE + dt.timedelta(days=99)] * 3
    added = storage.insert_observations(dataset, df2, arrival="event_time")

    assert added == 0, "identical rows should dedupe"
    out = storage.load_observations_as_of(dataset, dt.datetime(2030, 1, 1))
    assert (out["ingested_at"] == pd.Timestamp(BASE + dt.timedelta(days=1))).all()


# --- as-of reads ----------------------------------------------------------
def test_as_of_excludes_future_events(dataset):
    storage.insert_observations(dataset, _events("2024-01-01", 10),
                                arrival="event_time")
    df = storage.load_observations_as_of(dataset, BASE + dt.timedelta(days=4))
    assert len(df) == 5
    assert_causal(df, BASE + dt.timedelta(days=4))


def test_as_of_excludes_late_arrivals(dataset):
    """Old event, late arrival — invisible until it actually arrived."""
    df = _events("2024-01-01", 1)
    df["ingested_at"] = [BASE + dt.timedelta(days=30)]
    storage.insert_observations(dataset, df, arrival="event_time")

    assert storage.load_observations_as_of(
        dataset, BASE + dt.timedelta(days=10)).empty
    assert len(storage.load_observations_as_of(
        dataset, BASE + dt.timedelta(days=31))) == 1


def test_load_observations_as_of_never_leaks(dataset):
    """Sweep the whole range and assert the invariant at every step."""
    hist = _events("2024-01-01", 20)
    hist["ingested_at"] = [
        BASE + dt.timedelta(days=i + (5 if i % 3 == 0 else 0))
        for i in range(20)
    ]
    storage.insert_observations(dataset, hist, arrival="event_time")

    store = PointInTimeStore()
    seen = 0
    for view in store.replay(BASE, BASE + dt.timedelta(days=30)):
        out = view.observations(dataset)
        assert_causal(out, view.as_of)
        assert len(out) >= seen, "knowledge must not shrink over time"
        seen = len(out)
    assert seen == 20


def test_legacy_rows_without_ingested_at_are_readable_and_flagged(dataset):
    """Rows predating the migration must not vanish from as-of reads.

    `load_observations_as_of` coalesces a NULL arrival to the event time so a
    database that was never backfilled still replays, with the assumption
    surfaced through the estimated flag rather than hidden.
    """
    storage.insert_observations(dataset, _events("2024-01-01", 3),
                                arrival="event_time")
    with storage._engine().begin() as con:
        con.execute(storage.observations.update().values(
            ingested_at=None, ingest_estimated=None))

    df = storage.load_observations_as_of(dataset, dt.datetime(2030, 1, 1))
    assert len(df) == 3
    assert df["ingest_estimated"].all(), "unknown provenance counts as estimated"

    store = PointInTimeStore()
    prov = store.view(dt.datetime(2030, 1, 1)).provenance(dataset)
    assert not prov.is_faithful


def test_plain_load_observations_still_works(dataset):
    """v1 read path must survive the schema change untouched."""
    storage.insert_observations(dataset, _events("2024-01-01", 4),
                                arrival="event_time")
    df = storage.load_observations(dataset)
    assert len(df) == 4
    assert "timestamp" in df.columns and "value" in df.columns
