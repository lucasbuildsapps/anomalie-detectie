"""Episode-level scoring: the unit an analyst actually reviews."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.episodes import (
    Episode,
    match_episodes,
    to_episodes,
)

IDX = pd.date_range("2024-01-01", periods=20, freq="D")


def _flags(*positions: int) -> pd.Series:
    s = pd.Series(False, index=IDX)
    for p in positions:
        s.iloc[p] = True
    return s


# --- building episodes ----------------------------------------------------
def test_consecutive_flags_form_one_episode():
    eps = to_episodes(_flags(3, 4, 5))
    assert len(eps) == 1
    assert eps[0].start == IDX[3] and eps[0].end == IDX[5]


def test_separated_flags_form_separate_episodes():
    eps = to_episodes(_flags(3, 10), max_gap=0)
    assert len(eps) == 2


def test_max_gap_bridges_short_interruptions():
    """Intermittent flagging during one developing event is one event."""
    assert len(to_episodes(_flags(3, 5), max_gap=0)) == 2
    assert len(to_episodes(_flags(3, 5), max_gap=1)) == 1
    assert len(to_episodes(_flags(3, 7), max_gap=1)) == 2


def test_no_flags_gives_no_episodes():
    assert to_episodes(pd.Series(False, index=IDX)) == []


def test_empty_input_is_safe():
    assert to_episodes(pd.Series(dtype=bool)) == []
    assert to_episodes(None) == []


def test_nan_flags_count_as_not_flagged():
    """A detector may return object-dtype flags with gaps; NaN is not True.

    Built as object dtype because that is what a detector mixing NaN and
    booleans actually produces — pandas 3 refuses NaN in a bool column and
    refuses True in a float one.
    """
    values = [np.nan] * 20
    values[4] = True
    eps = to_episodes(pd.Series(values, index=IDX, dtype=object))
    assert len(eps) == 1 and eps[0].start == IDX[4]


def test_episode_rejects_reversed_bounds():
    with pytest.raises(ValueError, match="before it starts"):
        Episode(IDX[5], IDX[2])


# --- matching -------------------------------------------------------------
def test_overlapping_prediction_is_a_hit():
    truth = [Episode(IDX[5], IDX[8], "sustained")]
    pred = [Episode(IDX[6], IDX[7], "predicted")]
    r = match_episodes(truth, pred)
    assert r.hits == 1 and r.misses == 0 and r.false_alarms == 0
    assert r.recall == 1.0


def test_disjoint_prediction_is_a_false_alarm_and_a_miss():
    truth = [Episode(IDX[2], IDX[3])]
    pred = [Episode(IDX[15], IDX[16])]
    r = match_episodes(truth, pred)
    assert r.hits == 0 and r.misses == 1 and r.false_alarms == 1


def test_tolerance_forgives_a_reporting_offset():
    """A report dated one day late is a reporting difference, not a miss."""
    truth = [Episode(IDX[5], IDX[5])]
    pred = [Episode(IDX[6], IDX[6])]
    assert match_episodes(truth, pred, tolerance_periods=0).hits == 0
    assert match_episodes(truth, pred, tolerance_periods=1).hits == 1


def test_multiple_predictions_for_one_event_count_once_plus_duplicates():
    """Three alerts for one event is one detection, not three."""
    truth = [Episode(IDX[5], IDX[12])]
    pred = [Episode(IDX[5], IDX[6]), Episode(IDX[8], IDX[9]),
            Episode(IDX[11], IDX[12])]
    r = match_episodes(truth, pred)
    assert r.hits == 1
    assert r.duplicates == 2
    assert r.false_alarms == 0, "redundant alerts are not false alarms"
    assert r.recall == 1.0


def test_lead_time_measures_delay_to_first_detection():
    truth = [Episode(IDX[5], IDX[12])]
    pred = [Episode(IDX[8], IDX[9])]
    r = match_episodes(truth, pred)
    assert r.median_lead_time == 3.0


def test_negative_lead_time_is_representable():
    """Detection before onset must be visible, since it signals leakage."""
    truth = [Episode(IDX[10], IDX[12])]
    pred = [Episode(IDX[7], IDX[11])]
    r = match_episodes(truth, pred)
    assert r.median_lead_time == -3.0


def test_missed_episodes_are_retained_for_inspection():
    truth = [Episode(IDX[2], IDX[3], "spike"), Episode(IDX[15], IDX[16], "drop")]
    pred = [Episode(IDX[2], IDX[3])]
    r = match_episodes(truth, pred)
    assert [e.kind for e in r.missed_episodes] == ["drop"]


# --- the quiet case -------------------------------------------------------
def test_silence_on_a_quiet_dataset_is_perfect_precision():
    """No events, no alerts: correct behaviour, not a precision failure."""
    r = match_episodes([], [])
    assert r.false_alarms == 0
    assert r.precision == 1.0
    assert np.isnan(r.recall)


def test_alerts_on_a_quiet_dataset_are_all_false_alarms():
    r = match_episodes([], [Episode(IDX[1], IDX[2]), Episode(IDX[8], IDX[9])])
    assert r.false_alarms == 2
    assert r.precision == 0.0


def test_silence_on_an_eventful_dataset_is_undefined_precision_zero_recall():
    """Precision is undefined without predictions; recall is plainly zero.

    Reporting precision 0.0 here would conflate 'said nothing' with 'said
    something wrong', which are different failures.
    """
    r = match_episodes([Episode(IDX[3], IDX[4])], [])
    assert r.recall == 0.0
    assert np.isnan(r.precision)
    assert np.isnan(r.f1)


def test_summary_reads_sensibly_in_both_regimes():
    assert "No events to find" in match_episodes([], []).summary()
    truth = [Episode(IDX[5], IDX[6])]
    assert "1/1 found" in match_episodes(truth, truth).summary()
