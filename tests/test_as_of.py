"""Point-in-time correctness — the guarantee the rest of v2 rests on.

These tests are deliberately adversarial. `AsOfView` exists because v1's
leakage protection was discipline-based and failed; a test suite that only
checks the happy path would reproduce that mistake at a different level.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pandas as pd
import pytest

from sentinel.core.time import (
    AsOfView,
    LeakageError,
    PointInTimeStore,
    assert_causal,
    to_naive_utc,
)

BASE = datetime(2024, 1, 1)


def _frame(rows: list[tuple[int, int, bool]]) -> pd.DataFrame:
    """rows: (event_day_offset, arrival_day_offset, estimated)."""
    return pd.DataFrame([
        {
            "timestamp": BASE + timedelta(days=ev),
            "ingested_at": BASE + timedelta(days=arr),
            "ingest_estimated": est,
            "value": float(i),
        }
        for i, (ev, arr, est) in enumerate(rows)
    ])


def _loader_from(df: pd.DataFrame):
    """A correctly-filtering loader, matching load_observations_as_of."""
    def loader(dataset_id: int, as_of: datetime) -> pd.DataFrame:
        cut = pd.Timestamp(as_of)
        keep = (df["timestamp"] <= cut) & (df["ingested_at"] <= cut)
        return df[keep].reset_index(drop=True)
    return loader


# --- the two filters ------------------------------------------------------
def test_future_events_are_excluded():
    df = _frame([(0, 0, False), (5, 5, False), (10, 10, False)])
    view = AsOfView(BASE + timedelta(days=5), _loader_from(df))
    out = view.observations(1)
    assert len(out) == 2
    assert out["timestamp"].max() <= pd.Timestamp(BASE + timedelta(days=5))


def test_late_arriving_data_is_excluded_even_when_the_event_is_old():
    """The case a naive timestamp filter gets wrong.

    An event on day 1 that only reached us on day 9 must be invisible to a
    view at day 5. This is precisely the bias that makes an uncorrected
    backtest optimistic.
    """
    df = _frame([(1, 1, False), (1, 9, False)])
    view = AsOfView(BASE + timedelta(days=5), _loader_from(df))
    out = view.observations(1)
    assert len(out) == 1
    assert out["ingested_at"].iloc[0] == pd.Timestamp(BASE + timedelta(days=1))


def test_view_rejects_a_loader_that_leaks():
    """The redundant check earns its keep: a broken loader must not pass."""
    df = _frame([(0, 0, False), (99, 99, False)])

    def sloppy_loader(dataset_id, as_of):
        return df  # ignores as_of entirely

    view = AsOfView(BASE, sloppy_loader)
    with pytest.raises(LeakageError, match="after as_of"):
        view.observations(1)


def test_leak_message_names_the_column_and_the_worst_offender():
    df = _frame([(50, 0, False)])
    with pytest.raises(LeakageError) as exc:
        assert_causal(df, BASE)
    msg = str(exc.value)
    assert "timestamp" in msg
    assert "2024-02-20" in msg  # BASE + 50 days


def test_arrival_leak_is_caught_separately_from_event_leak():
    """Event time is fine, arrival time is not — must still raise."""
    df = _frame([(0, 40, False)])
    with pytest.raises(LeakageError, match="ingested_at"):
        assert_causal(df, BASE + timedelta(days=1))


# --- provenance -----------------------------------------------------------
def test_provenance_reports_assumed_arrival_times():
    df = _frame([(0, 0, True), (1, 1, True), (2, 2, False)])
    view = AsOfView(BASE + timedelta(days=10), _loader_from(df))
    prov = view.provenance(1)
    assert prov.n_rows == 3
    assert prov.n_estimated == 2
    assert prov.estimated_fraction == pytest.approx(2 / 3)
    assert not prov.is_faithful
    assert "optimistic" in prov.describe()


def test_provenance_faithful_when_all_arrivals_observed():
    df = _frame([(0, 0, False), (1, 1, False)])
    view = AsOfView(BASE + timedelta(days=10), _loader_from(df))
    prov = view.provenance(1)
    assert prov.is_faithful
    assert "faithful" in prov.describe()


def test_missing_provenance_column_is_treated_as_unverified():
    """Absence of evidence about provenance is not evidence of quality."""
    df = _frame([(0, 0, False)]).drop(columns=["ingest_estimated"])
    view = AsOfView(BASE + timedelta(days=10), _loader_from(df))
    prov = view.provenance(1)
    assert prov.n_estimated == prov.n_rows
    assert not prov.is_faithful


def test_empty_view_is_not_faithful_and_says_so():
    view = AsOfView(BASE - timedelta(days=1), _loader_from(_frame([(0, 0, False)])))
    prov = view.provenance(1)
    assert prov.n_rows == 0
    assert not prov.is_faithful
    assert prov.estimated_fraction == 0.0


# --- immutability ---------------------------------------------------------
def test_view_is_immutable():
    view = AsOfView(BASE, _loader_from(_frame([(0, 0, False)])))
    with pytest.raises(FrozenInstanceError):
        view.as_of = BASE + timedelta(days=100)


def test_at_and_rewind_return_new_views():
    view = AsOfView(BASE + timedelta(days=10), _loader_from(_frame([])))
    assert view.at(BASE).as_of == BASE
    assert view.rewind(days=3).as_of == BASE + timedelta(days=7)
    assert view.as_of == BASE + timedelta(days=10)  # original untouched


def test_timezone_aware_as_of_is_normalised():
    aware = pd.Timestamp("2024-01-01T12:00:00+02:00")
    view = AsOfView(aware, _loader_from(_frame([])))
    assert view.as_of.tzinfo is None
    assert view.as_of == datetime(2024, 1, 1, 10, 0)  # converted to UTC


# --- replay ---------------------------------------------------------------
def test_replay_walks_forward_inclusively():
    store = PointInTimeStore(_loader_from(_frame([])))
    views = list(store.replay(BASE, BASE + timedelta(days=3)))
    assert [v.as_of for v in views] == [
        BASE + timedelta(days=d) for d in range(4)
    ]


def test_replay_respects_step():
    store = PointInTimeStore(_loader_from(_frame([])))
    views = list(store.replay(BASE, BASE + timedelta(days=6),
                             step=timedelta(days=2)))
    assert len(views) == 4


def test_replay_rejects_non_positive_step():
    store = PointInTimeStore(_loader_from(_frame([])))
    with pytest.raises(ValueError, match="positive"):
        list(store.replay(BASE, BASE + timedelta(days=3),
                          step=timedelta(0)))


def test_replay_rejects_reversed_range():
    store = PointInTimeStore(_loader_from(_frame([])))
    with pytest.raises(ValueError, match="after end"):
        list(store.replay(BASE + timedelta(days=3), BASE))


def test_replay_reveals_growing_knowledge():
    """The property that makes replay worth having.

    Each successive view must be a superset of the previous one. If a later
    view ever loses a row, the point-in-time reconstruction is inconsistent
    and every metric derived from it is untrustworthy.
    """
    df = _frame([(0, 0, False), (1, 3, False), (2, 2, False), (9, 9, False)])
    store = PointInTimeStore(_loader_from(df))
    seen = 0
    for view in store.replay(BASE, BASE + timedelta(days=9)):
        n = len(view.observations(1))
        assert n >= seen, "a later view lost rows a earlier view had"
        seen = n
    assert seen == 4


def test_replay_is_identical_to_a_direct_view():
    """Backtest and production must be the same code path, not two."""
    df = _frame([(0, 0, False), (2, 2, False), (4, 8, False)])
    store = PointInTimeStore(_loader_from(df))
    target = BASE + timedelta(days=5)
    from_replay = [v for v in store.replay(BASE, BASE + timedelta(days=9))
                   if v.as_of == target][0]
    direct = store.view(target)
    pd.testing.assert_frame_equal(
        from_replay.observations(1), direct.observations(1)
    )


# --- helpers --------------------------------------------------------------
def test_to_naive_utc_is_idempotent():
    once = to_naive_utc("2024-03-01T05:00:00+03:00")
    assert to_naive_utc(once) == once


def test_assert_causal_tolerates_empty_and_missing_columns():
    assert_causal(pd.DataFrame(), BASE)          # empty
    assert_causal(pd.DataFrame({"x": [1]}), BASE)  # no time columns
