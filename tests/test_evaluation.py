"""Tests voor het evaluatie-harnas (roadmap 18).

Doel van die code: de bewering "5 detectie-algoritmes" omzetten in een
cijfer per dataset. Deze tests bewaken dat de cijfers kloppen — inclusief
het eerlijke antwoord wanneer er niets te meten valt.
"""
import numpy as np
import pandas as pd
import pytest

import core.storage as storage
from core.evaluation import (
    Incident,
    evaluate_detectors,
    incidents_from_annotations,
    incidents_from_frame,
    summarize,
    to_frame,
)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "eval.db")
    storage.init_db()
    yield


@pytest.fixture
def spiky_series():
    """Rustige reeks met drie duidelijke pieken op bekende dagen."""
    rng = np.random.default_rng(12)
    n = 180
    vals = np.clip(20 + rng.normal(0, 2, n), 0, None)
    spike_idx = [40, 90, 140]
    for i in spike_idx:
        vals[i] += 60
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame({"timestamp": dates, "value": vals,
                       "location_name": "Alpha"})
    incidents = [Incident(start=dates[i], location="Alpha") for i in spike_idx]
    return df, incidents


class TestIncidentParsing:
    def test_from_frame_reads_ranges_and_locations(self):
        df = pd.DataFrame({
            "start": ["2025-03-01", "2025-04-01"],
            "end": [None, "2025-04-03"],
            "location": ["Alpha", None],
            "label": ["aanval", "golf"],
        })
        incidents = incidents_from_frame(df)
        assert len(incidents) == 2
        assert incidents[0].location == "Alpha"
        assert incidents[0].end is None
        assert incidents[1].end == pd.Timestamp("2025-04-03")

    def test_from_frame_skips_unparseable_dates(self):
        df = pd.DataFrame({"start": ["geen datum", "2025-05-05"]})
        assert len(incidents_from_frame(df)) == 1

    def test_incident_covers_respects_tolerance(self):
        inc = Incident(start=pd.Timestamp("2025-06-10"))
        tol = pd.Timedelta(days=1)
        assert inc.covers(pd.Timestamp("2025-06-11"), tol)
        assert not inc.covers(pd.Timestamp("2025-06-12"), tol)

    def test_range_incident_covers_whole_period(self):
        inc = Incident(start=pd.Timestamp("2025-06-10"),
                       end=pd.Timestamp("2025-06-14"))
        tol = pd.Timedelta(0)
        assert inc.covers(pd.Timestamp("2025-06-12"), tol)


class TestScoring:
    def test_detectors_find_obvious_spikes(self, spiky_series):
        df, incidents = spiky_series
        scores = evaluate_detectors(df, incidents, tolerance_periods=1)
        assert scores
        assert scores[0].recall > 0.6, "duidelijke pieken horen gevonden te worden"

    def test_sorted_by_f1_descending(self, spiky_series):
        df, incidents = spiky_series
        scores = evaluate_detectors(df, incidents)
        f1s = [s.f1 for s in scores]
        assert f1s == sorted(f1s, reverse=True)

    def test_wrong_labels_yield_low_recall(self, spiky_series):
        """Labels op rustige dagen horen niet gevonden te worden."""
        df, _ = spiky_series
        bogus = [Incident(start=pd.Timestamp("2025-01-05"), location="Alpha"),
                 Incident(start=pd.Timestamp("2025-01-08"), location="Alpha")]
        scores = evaluate_detectors(df, bogus, tolerance_periods=0)
        assert all(s.recall < 0.6 for s in scores)

    def test_counts_are_internally_consistent(self, spiky_series):
        df, incidents = spiky_series
        for s in evaluate_detectors(df, incidents):
            assert s.hits + s.misses == len(incidents)
            assert s.false_alarms <= s.n_flagged
            assert 0.0 <= s.recall <= 1.0
            assert 0.0 <= s.precision <= 1.0

    def test_location_mismatch_is_not_a_hit(self, spiky_series):
        df, _ = spiky_series
        elsewhere = [Incident(start=df["timestamp"].iloc[40],
                              location="Bravo")]
        scores = evaluate_detectors(df, elsewhere, tolerance_periods=0)
        assert all(s.hits == 0 for s in scores)

    def test_no_incidents_returns_nothing(self, spiky_series):
        df, _ = spiky_series
        assert evaluate_detectors(df, []) == []

    def test_empty_data_is_safe(self):
        assert evaluate_detectors(pd.DataFrame(), [Incident(
            start=pd.Timestamp("2025-01-01"))]) == []

    def test_restrict_to_named_detectors(self, spiky_series):
        df, incidents = spiky_series
        scores = evaluate_detectors(df, incidents,
                                    detectors=["Z-score (MAD)"])
        assert [s.detector for s in scores] == ["Z-score (MAD)"]


class TestReporting:
    def test_summary_names_the_best_detector(self, spiky_series):
        df, incidents = spiky_series
        text = summarize(evaluate_detectors(df, incidents))
        assert "presteert hier het best" in text
        assert "recall" in text

    def test_summary_is_honest_without_labels(self):
        text = summarize([])
        assert "Geen evaluatie mogelijk" in text
        assert "bevestigd" in text

    def test_summary_warns_on_poor_recall(self, spiky_series):
        df, _ = spiky_series
        bogus = [Incident(start=pd.Timestamp("2025-01-05"))] * 4
        text = summarize(evaluate_detectors(df, bogus, tolerance_periods=0))
        assert "gemist" in text or "Geen enkele detector" in text

    def test_frame_has_readable_columns(self, spiky_series):
        df, incidents = spiky_series
        table = to_frame(evaluate_detectors(df, incidents))
        assert {"Detector", "Gevonden", "Gemist", "Recall", "F1"} <= set(
            table.columns)


class TestAnnotationLabels:
    def test_confirmed_annotations_become_incidents(self):
        """Labels ontstaan als bijproduct van normaal triage-werk."""
        from core import annotations as anno

        dates = pd.date_range("2025-02-01", periods=30, freq="D")
        df = pd.DataFrame({"timestamp": dates, "value": 5.0,
                           "location_name": "Alpha"})
        ds = storage.create_dataset("labels", "", {})
        storage.insert_observations(ds, df)

        key = anno.finding_key(dates[5].date().isoformat(), "Alpha", None)
        anno.save_annotation(ds, key, "raak", "bevestigd")

        incidents = incidents_from_annotations(ds)
        assert len(incidents) == 1
        assert incidents[0].location == "Alpha"

    def test_false_alarms_are_not_labels(self):
        from core import annotations as anno

        dates = pd.date_range("2025-02-01", periods=10, freq="D")
        df = pd.DataFrame({"timestamp": dates, "value": 5.0,
                           "location_name": "Alpha"})
        ds = storage.create_dataset("labels2", "", {})
        storage.insert_observations(ds, df)
        key = anno.finding_key(dates[2].date().isoformat(), "Alpha", None)
        anno.save_annotation(ds, key, "niets aan de hand", "vals_alarm")
        assert incidents_from_annotations(ds) == []

    def test_no_annotations_gives_no_labels(self):
        ds = storage.create_dataset("leeg", "", {})
        assert incidents_from_annotations(ds) == []
