"""Retrospective validation, and its refusal to overclaim.

Most of these tests are about the guardrails rather than the arithmetic. A
detection rate over a dozen curated events is the most quotable and least
reliable number this system can produce, so the constraints on quoting it are
the part worth protecting with tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from sentinel.core.contracts import (
    Confidence,
    ConfidenceLevel,
    DetectionPower,
    Direction,
    Signal,
    Verdict,
)
from sentinel.eval.retrospective import (
    RetrospectiveReport,
    TruthEvent,
    WarningResult,
    events_from_chronology,
    replay,
)

CONF = Confidence(level=ConfidenceLevel.MODERATE, reasons=("one source",))
START, END = datetime(2024, 1, 1), datetime(2024, 12, 31)


def _active(as_of, key="tempo"):
    return Signal(indicator_key=key, as_of=as_of, verdict=Verdict.ACTIVE,
                  confidence=CONF, effect_size=2.0, direction=Direction.ABOVE)


def _quiet(as_of, key="tempo"):
    return Signal(indicator_key=key, as_of=as_of, verdict=Verdict.NOT_ACTIVE,
                  confidence=CONF, detection_power=DetectionPower("spike", 3.0))


def _events(n, first=datetime(2024, 6, 1), spacing=30):
    return tuple(
        TruthEvent(key=f"e{i}", occurred_at=first + timedelta(days=i * spacing))
        for i in range(n)
    )


def _alerting_between(start, end, key="tempo"):
    """A system that is active only inside a stated window."""
    def evaluate_at(as_of):
        return [_active(as_of, key) if start <= as_of <= end
                else _quiet(as_of, key)]
    return evaluate_at


# =========================================================================
# warning time
# =========================================================================
def test_an_alert_before_an_event_is_a_warning():
    event = TruthEvent(key="x", occurred_at=datetime(2024, 6, 1))
    report = replay(_alerting_between(datetime(2024, 5, 1),
                                      datetime(2024, 5, 20)),
                    [event], START, END)
    assert report.results[0].detected
    assert 25 <= report.results[0].warning_days <= 35


def test_the_earliest_alert_in_the_window_is_the_warning():
    """Warning time is measured from when the system first said something,
    not from the most recent time it repeated itself."""
    event = TruthEvent(key="x", occurred_at=datetime(2024, 6, 1))
    report = replay(_alerting_between(datetime(2024, 4, 1),
                                      datetime(2024, 5, 30)),
                    [event], START, END)
    assert report.results[0].warning_days > 45


def test_an_alert_after_the_event_is_not_a_warning():
    event = TruthEvent(key="x", occurred_at=datetime(2024, 6, 1))
    report = replay(_alerting_between(datetime(2024, 6, 2),
                                      datetime(2024, 7, 1)),
                    [event], START, END)
    assert not report.results[0].detected


def test_an_alert_long_before_the_event_does_not_count():
    """An indicator that fired eight months earlier and went quiet did not
    warn about this. Counting it lets a noisy system claim everything."""
    event = TruthEvent(key="x", occurred_at=datetime(2024, 12, 1))
    report = replay(_alerting_between(datetime(2024, 1, 5),
                                      datetime(2024, 1, 20)),
                    [event], START, END, lead_window=timedelta(days=90))
    assert not report.results[0].detected


def test_a_system_that_never_fires_detects_nothing():
    report = replay(lambda as_of: [_quiet(as_of)], _events(10), START, END)
    assert not report.detected
    assert report.detection_rate == 0.0


# =========================================================================
# refusing to quote
# =========================================================================
def test_a_rate_is_not_quoted_below_the_minimum_event_count():
    """Over a handful of events the interval covers almost any claim."""
    report = replay(_alerting_between(START, END), _events(3), START, END)
    assert not report.is_quotable
    assert report.detection_rate is None
    assert "Too few events to quote a rate" in report.describe()


def test_a_rate_is_quoted_once_there_are_enough_events():
    report = replay(_alerting_between(START, END), _events(10, spacing=20),
                    START, END)
    assert report.is_quotable
    assert report.detection_rate == 1.0
    assert "100%" in report.describe()


def test_an_empty_chronology_is_not_a_pass():
    report = replay(_alerting_between(START, END), [], START, END)
    assert "nothing was validated" in report.describe()
    assert "not a passing result" in report.describe()


# =========================================================================
# the caveats are part of the output, not a footnote
# =========================================================================
def test_the_curation_loop_is_always_stated():
    report = replay(_alerting_between(START, END), _events(10, spacing=20),
                    START, END)
    assert "curated by the same person tuning the system" in report.describe()


def test_an_estimated_replay_says_its_warning_times_are_optimistic():
    report = replay(_alerting_between(START, END), _events(10, spacing=20),
                    START, END, arrival_faithful=False)
    assert "optimistic" in report.describe()


def test_a_faithful_replay_does_not_carry_the_optimism_warning():
    report = replay(_alerting_between(START, END), _events(10, spacing=20),
                    START, END, arrival_faithful=True)
    assert "optimistic" not in report.describe()


def test_alerts_matching_no_event_are_unattributed_not_wrong():
    """A curated chronology is not a complete record of what happened, so an
    alert outside it is not evidence of a false alarm."""
    event = TruthEvent(key="x", occurred_at=datetime(2024, 3, 1))
    report = replay(_alerting_between(datetime(2024, 8, 1),
                                      datetime(2024, 9, 1)),
                    [event], START, END)
    assert report.n_unattributed_alerts > 0
    assert "unattributed rather than wrong" in report.describe()


# =========================================================================
# inputs
# =========================================================================
def test_an_event_known_before_it_happened_is_a_curation_error():
    with pytest.raises(ValueError, match="known before it happened"):
        TruthEvent(key="x", occurred_at=datetime(2024, 6, 1),
                   known_at=datetime(2024, 5, 1))


def test_a_zero_step_is_refused():
    """The replay would never advance and would hang rather than fail."""
    with pytest.raises(ValueError, match="never advances"):
        replay(_alerting_between(START, END), _events(2), START, END,
               step=timedelta(0))


def test_events_load_from_the_curated_chronology(tmp_path):
    path = tmp_path / "chron.csv"
    pd.DataFrame([
        {"timestamp": "2024-03-01", "reported_at": "2024-03-04",
         "category": "strike", "summary": "grid attack"},
        {"timestamp": "2024-05-02", "category": "naval", "summary": "cable"},
    ]).to_csv(path, index=False)

    events = events_from_chronology(path)
    assert len(events) == 2
    assert events[0].known_at == datetime(2024, 3, 4)
    assert events[1].known_at is None


def test_the_chronology_can_be_filtered_by_category(tmp_path):
    path = tmp_path / "chron.csv"
    pd.DataFrame([
        {"timestamp": "2024-03-01", "category": "strike", "summary": "a"},
        {"timestamp": "2024-05-02", "category": "naval", "summary": "b"},
    ]).to_csv(path, index=False)
    assert len(events_from_chronology(path, category="naval")) == 1


def test_an_empty_chronology_file_yields_no_events(tmp_path):
    """The shipped file has a header and nothing else, and must not crash."""
    path = tmp_path / "chron.csv"
    path.write_text("timestamp,reported_at,category,summary\n")
    assert events_from_chronology(path) == ()


def test_the_shipped_chronology_is_readable_and_empty():
    """It ships empty on purpose; a pre-filled one is indistinguishable from
    a curated one. This guards the format, not the content."""
    assert events_from_chronology("data/chronology/euro_atlantic.csv") == ()


# =========================================================================
# reporting
# =========================================================================
def test_median_warning_time_ignores_the_events_that_were_missed():
    """Averaging a miss in as zero would understate the warning the system
    gave when it gave one, and overstate how often it gives any."""
    report = RetrospectiveReport(results=(
        WarningResult(TruthEvent("a", datetime(2024, 6, 1)),
                      datetime(2024, 5, 1), "tempo"),
        WarningResult(TruthEvent("b", datetime(2024, 7, 1))),
    ))
    assert report.median_warning_days == pytest.approx(31.0)


def test_a_result_describes_itself_for_a_written_report():
    result = WarningResult(TruthEvent("a", datetime(2024, 6, 1)),
                           datetime(2024, 5, 1), "tempo")
    assert "tempo was active 31 days before" in result.describe()
    assert "no indicator" in WarningResult(
        TruthEvent("b", datetime(2024, 7, 1))).describe()


# =========================================================================
# against the real region engine, not an injected callable
# =========================================================================
def test_replay_runs_against_a_real_region(tmp_path, monkeypatch):
    """End to end: stored observations -> point-in-time evaluation -> warning
    time. Proves the harness scores the engine that ships, not a stand-in."""
    import numpy as np

    from core import storage
    from sentinel.core.detect.power import DetectionPowerCatalog
    from sentinel.core.time import PointInTimeStore
    from sentinel.regions import evaluate_region, get_region
    from sentinel.regions.providers import storage_provider

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'retro.db'}")
    storage.init_db()
    ds = storage.create_dataset("retro", "", {})

    rng = np.random.default_rng(3)
    index = pd.date_range("2019-01-01", periods=1400, freq="D")
    escalates_at = pd.Timestamp("2022-06-01")
    level = np.where(index < escalates_at, 12.0, 44.0)
    rows = [
        {"timestamp": ts, "value": float(rng.poisson(level[i])),
         "location_name": "north"}
        for i, ts in enumerate(index)
    ]
    storage.insert_observations(ds, pd.DataFrame(rows), arrival="event_time")

    region = get_region("euro_atlantic")
    store = PointInTimeStore()
    provider = storage_provider(ds, region, store.view)
    catalog = DetectionPowerCatalog.load("config/detection_power.json")

    def evaluate_at(as_of):
        return evaluate_region(region, provider, as_of, catalog).signals

    event = TruthEvent(key="escalation", occurred_at=datetime(2022, 9, 1))
    report = replay(evaluate_at, [event],
                    datetime(2022, 6, 15), datetime(2022, 9, 1),
                    step=timedelta(days=14), lead_window=timedelta(days=120),
                    arrival_faithful=False)

    assert report.results[0].detected, (
        "a 3.7x sustained level shift three months before the event should "
        "have produced a warning; if this fails the divergence test is not "
        "firing on real stored data")
    assert report.results[0].warning_days > 0
    assert "optimistic" in report.describe()
