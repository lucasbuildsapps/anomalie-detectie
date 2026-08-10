"""Ingestion, and the one column it exists for.

Everything the point-in-time machinery claims rests on `ingested_at`. These
tests are mostly about that: that a stated publication date is used, that an
absent one is recorded rather than invented, and that a run which fetches
plenty and stores nothing cannot look like a run that worked.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import storage
from sentinel.core.time import PointInTimeStore
from sentinel.ingest import ChronologyConnector, normalise, run_ingest
from sentinel.ingest.chronology import CHRONOLOGY_SOURCE


@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ingest.db'}")
    storage.init_db()
    return storage.create_dataset("chronology", "", {})


def _write(tmp_path, rows, name="chron.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


_ROWS = [
    {"timestamp": "2024-01-05", "reported_at": "2024-01-08",
     "category": "strike", "location_name": "kyiv", "value": 3},
    {"timestamp": "2024-02-11", "reported_at": "2024-02-11",
     "category": "strike", "location_name": "odesa", "value": 1},
]


class _Boom:
    source = CHRONOLOGY_SOURCE

    def fetch(self, since=None):
        raise ConnectionError("upstream refused")


# =========================================================================
# arrival time — the reason this layer exists
# =========================================================================
def test_a_stated_publication_date_becomes_the_arrival_time(dataset, tmp_path):
    result = run_ingest(ChronologyConnector(_write(tmp_path, _ROWS)), dataset)
    assert result.ok
    assert result.n_inserted == 2
    assert result.arrival_is_faithful


def test_a_faithful_ingest_produces_a_faithful_replay(dataset, tmp_path):
    """The payoff. Without observed arrivals every replay is optimistic by
    an unknown margin, and confidence is capped for it."""
    run_ingest(ChronologyConnector(_write(tmp_path, _ROWS)), dataset)
    provenance = PointInTimeStore().view(
        dt.datetime(2024, 6, 1)).provenance(dataset)
    assert provenance.is_faithful
    assert "faithful reconstruction" in provenance.describe()


def test_an_event_is_invisible_until_it_was_reported(dataset, tmp_path):
    """Three days passed between the strike and the reporting of it. A replay
    on the second day must not see it, or warning time is measured against
    knowledge nobody had."""
    run_ingest(ChronologyConnector(_write(tmp_path, _ROWS)), dataset)
    store = PointInTimeStore()
    assert len(store.view(dt.datetime(2024, 1, 6)).observations(dataset)) == 0
    assert len(store.view(dt.datetime(2024, 1, 9)).observations(dataset)) == 1


def test_a_missing_publication_date_is_recorded_not_invented(dataset, tmp_path):
    rows = [{"timestamp": "2024-01-05", "value": 2}]
    result = run_ingest(ChronologyConnector(_write(tmp_path, rows)), dataset)
    assert result.n_inserted == 1
    assert not result.arrival_is_faithful
    assert "assumed arrival time" in result.describe()


def test_a_publication_date_before_the_event_is_rejected():
    """A source cannot report an event before it happens. Letting that
    through would place the row in the past of its own occurrence."""
    frame = normalise(pd.DataFrame([
        {"timestamp": "2024-05-10", "reported_at": "2024-05-01"},
    ]), CHRONOLOGY_SOURCE)
    assert "ingested_at" not in frame.columns or frame["ingested_at"].isna().all()


def test_partial_publication_dates_are_handled_per_row(dataset, tmp_path):
    rows = [
        {"timestamp": "2024-01-05", "reported_at": "2024-01-08", "value": 1},
        {"timestamp": "2024-01-06", "value": 1},
    ]
    result = run_ingest(ChronologyConnector(_write(tmp_path, rows)), dataset)
    assert result.n_inserted == 2
    assert result.n_with_observed_arrival == 1
    assert not result.arrival_is_faithful


# =========================================================================
# re-running
# =========================================================================
def test_re_running_inserts_nothing_and_says_so(dataset, tmp_path):
    """A run that fetches plenty and stores nothing is either correctly
    idempotent or silently broken. The two look identical without a count."""
    path = _write(tmp_path, _ROWS)
    run_ingest(ChronologyConnector(path), dataset)
    again = run_ingest(ChronologyConnector(path), dataset)
    assert again.ok
    assert again.n_fetched == 2
    assert again.n_inserted == 0
    assert again.n_duplicate == 2
    assert "already held" in again.describe()


def test_since_filters_on_when_it_became_known(tmp_path):
    """A late-reported event arrived after the last run even though it
    happened before it — filtering on event time would drop it."""
    rows = [{"timestamp": "2024-01-05", "reported_at": "2024-03-20"}]
    fetched = ChronologyConnector(_write(tmp_path, rows)).fetch(
        since=dt.datetime(2024, 3, 1))
    assert len(fetched) == 1


def test_since_still_works_without_publication_dates(tmp_path):
    rows = [{"timestamp": "2024-01-05"}, {"timestamp": "2024-04-05"}]
    fetched = ChronologyConnector(_write(tmp_path, rows)).fetch(
        since=dt.datetime(2024, 3, 1))
    assert len(fetched) == 1


# =========================================================================
# failure is a finding, not a crash
# =========================================================================
def test_a_failing_source_does_not_take_the_run_down(dataset):
    result = run_ingest(_Boom(), dataset)
    assert not result.ok
    assert "upstream refused" in result.error
    assert "failed" in result.describe()


def test_an_absent_chronology_says_nobody_wrote_it(dataset, tmp_path):
    result = run_ingest(ChronologyConnector(tmp_path / "nope.csv"), dataset)
    assert not result.ok
    assert "not that" in result.error


def test_a_chronology_without_timestamps_is_refused(dataset, tmp_path):
    path = _write(tmp_path, [{"category": "strike"}])
    result = run_ingest(ChronologyConnector(path), dataset)
    assert not result.ok
    assert "timestamp" in result.error


def test_unreadable_timestamps_are_skipped_and_counted(dataset, tmp_path):
    rows = [{"timestamp": "2024-01-05"}, {"timestamp": "not a date"}]
    result = run_ingest(ChronologyConnector(_write(tmp_path, rows)), dataset)
    assert result.ok
    assert result.n_inserted == 1
    assert any("unreadable timestamp" in note for note in result.notes)


def test_an_empty_chronology_is_not_an_error(dataset, tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("timestamp,value\n")
    result = run_ingest(ChronologyConnector(path), dataset)
    assert result.ok
    assert result.n_fetched == 0
    assert "nothing to fetch" in result.describe()


# =========================================================================
# shape
# =========================================================================
def test_curator_columns_survive_as_attributes(dataset, tmp_path):
    """A finding has to be traceable back to what the curator read."""
    rows = [{"timestamp": "2024-01-05", "reported_at": "2024-01-08",
             "summary": "strike on power grid",
             "source_url": "https://example.org/report"}]
    run_ingest(ChronologyConnector(_write(tmp_path, rows)), dataset)
    stored = storage.load_observations(dataset)
    assert "source_url" in stored.columns
    assert stored["summary"].iloc[0] == "strike on power grid"


def test_each_row_counts_as_one_event_by_default():
    frame = normalise(pd.DataFrame([{"timestamp": "2024-01-05"}]),
                      CHRONOLOGY_SOURCE)
    assert frame["value"].iloc[0] == 1.0


def test_the_source_is_graded_as_a_compilation_not_a_primary_feed():
    assert CHRONOLOGY_SOURCE.is_graded
    assert CHRONOLOGY_SOURCE.grading == "C2"


# =========================================================================
# the payoff: provenance reaches confidence, not just the caption
# =========================================================================
def test_an_estimated_replay_caps_confidence(dataset, tmp_path):
    """Measuring faithfulness and then not telling the confidence pillar
    would let a finding read as better-grounded than its provenance says."""
    from sentinel.core.confidence import assess_confidence
    from sentinel.core.contracts import ConfidenceInputs, ConfidenceLevel

    rows = [{"timestamp": f"2024-01-{d:02d}"} for d in range(1, 20)]
    run_ingest(ChronologyConnector(_write(tmp_path, rows)), dataset)
    provenance = PointInTimeStore().view(
        dt.datetime(2024, 6, 1)).provenance(dataset)
    assert not provenance.is_faithful

    strong = dict(data_coverage=1.0, staleness_days=0, calibration_gap=0.01,
                  historical_precision=0.9, historical_recall=0.9,
                  effective_corroboration=3.0)
    capped = assess_confidence(ConfidenceInputs(
        reconstruction_faithful=provenance.is_faithful, **strong))
    assert capped.level is not ConfidenceLevel.HIGH


def test_the_watchboard_passes_provenance_into_confidence():
    """Wiring test: the page must not compute faithfulness for display only."""
    source = __import__("pathlib").Path("ui/pages/regions.py").read_text()
    assert "reconstruction_faithful" in source
    assert source.index("provenance = ") < source.index("evaluate_region(")
