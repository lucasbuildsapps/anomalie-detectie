"""The wiring from stored observations to the regional watchboard.

Two things are checked: that the provider reads through `AsOfView` rather
than around it, and that the page's empty states never render as calm.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from core import storage
from sentinel.core.contracts import Verdict
from sentinel.core.detect.power import DetectionPowerCatalog
from sentinel.core.time import PointInTimeStore
from sentinel.regions import REGIONS, evaluate_region, get_region
from sentinel.regions.providers import series_from_view, storage_provider

AS_OF = dt.datetime(2023, 12, 5)


@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    storage.init_db()
    ds = storage.create_dataset("regions", "", {})

    rng = np.random.default_rng(7)
    index = pd.date_range("2019-01-01", periods=1500, freq="D")
    level = np.where(index < pd.Timestamp("2022-03-01"), 20.0, 60.0)
    seasonal = 1.0 + 0.25 * np.sin(2 * np.pi * np.arange(len(index)) / 7)
    rows = [
        {"timestamp": ts, "value": float(rng.poisson(level[i] * seasonal[i] / 3)),
         "location_name": area}
        for i, ts in enumerate(index)
        for area in ("north", "east", "south")
    ]
    storage.insert_observations(ds, pd.DataFrame(rows), arrival="event_time")
    return ds


def _catalog() -> DetectionPowerCatalog:
    return DetectionPowerCatalog.load("config/detection_power.json")


# =========================================================================
# the provider reads through the point-in-time view
# =========================================================================
def test_provider_respects_as_of(dataset):
    store = PointInTimeStore()
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, store.view)
    indicator = region.active_indicators[0]

    early = provider(indicator, dt.datetime(2020, 1, 1))
    late = provider(indicator, AS_OF)
    assert len(early) < len(late)
    assert early.index.max() <= pd.Timestamp("2020-01-01")


def test_provider_returns_none_before_any_data(dataset):
    """None, not an empty series: the detect layer turns it into an explicit
    'no data available' verdict rather than a quiet one."""
    store = PointInTimeStore()
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, store.view)
    assert provider(region.active_indicators[0], dt.datetime(2015, 1, 1)) is None


def test_series_aggregates_to_the_requested_grain(dataset):
    view = PointInTimeStore().view(AS_OF)
    daily = series_from_view(view, dataset, aggregation="daily")
    weekly = series_from_view(view, dataset, aggregation="weekly")
    assert len(weekly) < len(daily)
    assert weekly.sum() == pytest.approx(daily.sum(), rel=0.01)


def test_unmatched_area_names_fall_back_rather_than_returning_nothing(dataset):
    """A region whose area names do not match the dataset should look
    unconfigured, not silently empty."""
    view = PointInTimeStore().view(AS_OF)
    matched = series_from_view(view, dataset, areas=("north", "east", "south"))
    unmatched = series_from_view(view, dataset, areas=("atlantis",))
    assert len(unmatched) > 0
    assert unmatched.sum() >= matched.sum()


# =========================================================================
# end to end through storage
# =========================================================================
def test_escalation_reaches_the_board(dataset):
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, PointInTimeStore().view)
    status = evaluate_region(region, provider, AS_OF, _catalog())
    assert "strike_tempo_sustained" in {s.indicator_key for s in status.active}


def test_bulk_imported_history_is_flagged_as_assumed(dataset):
    """A replay over bulk-imported data is optimistic, and must say so."""
    provenance = PointInTimeStore().view(AS_OF).provenance(dataset)
    assert provenance.n_rows > 0
    assert not provenance.is_faithful
    assert "optimistic" in provenance.describe()


def test_every_region_renders_without_error(dataset):
    """Whatever the state, each of the five must produce a headline."""
    provider_for = lambda r: storage_provider(  # noqa: E731
        dataset, r, PointInTimeStore().view)
    for region in REGIONS:
        status = evaluate_region(region, provider_for(region), AS_OF,
                                 _catalog())
        assert status.headline()


# =========================================================================
# empty states must never read as calm
# =========================================================================
def test_no_dataset_is_not_a_quiet_result(dataset):
    status = evaluate_region(get_region("euro_atlantic"),
                             lambda i, a: None, AS_OF, _catalog())
    assert not status.is_quiet
    assert "not a quiet result" in status.headline()


def test_missing_power_catalogue_blocks_quiet_claims(dataset):
    """Without measured power, nothing statistical may be reported as quiet.

    Active verdicts are unaffected — an effect that is present does not need
    a floor to be asserted. It is the *null* result that requires knowing
    what would have been caught, so the block applies only there.
    """
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, PointInTimeStore().view)
    status = evaluate_region(region, provider, AS_OF,
                             DetectionPowerCatalog())
    statistical = [s for s in status.signals
                   if s.indicator_key != "reporting_silence"]
    assert statistical
    assert not any(s.verdict is Verdict.NOT_ACTIVE for s in statistical)
    for signal in statistical:
        if signal.verdict is Verdict.INSUFFICIENT_DATA:
            assert "detection power" in signal.insufficient_reason


def test_active_verdicts_do_not_need_a_measured_floor(dataset):
    """A present effect can be asserted without knowing the detection floor."""
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, PointInTimeStore().view)
    status = evaluate_region(region, provider, AS_OF,
                             DetectionPowerCatalog())
    assert "strike_tempo_sustained" in {s.indicator_key for s in status.active}


# =========================================================================
# page wiring
# =========================================================================
def test_page_module_imports_and_exposes_its_entry_point():
    from ui.pages import regions

    assert callable(regions.page_regions)


def test_page_is_reachable_from_the_router():
    """A page nobody can navigate to is not shipped."""
    app_source = __import__("pathlib").Path("app.py").read_text()
    assert 'nav_regions' in app_source
    assert 'ui.pages.regions' in app_source


def test_every_verdict_has_a_display_style():
    """A verdict without a style would render blank — the worst failure mode
    for a screen whose whole job is to distinguish three states."""
    from sentinel.core.contracts import Verdict
    from ui.pages.regions import _VERDICT_STYLE

    assert set(_VERDICT_STYLE) == set(Verdict)


def test_unwatched_statuses_all_carry_an_explanatory_note():
    from sentinel.core.contracts import MonitoringStatus
    from ui.pages.regions import _STATUS_NOTE

    for status in MonitoringStatus:
        if not status.can_produce_verdicts:
            assert status in _STATUS_NOTE, (
                f"{status} would render with no explanation of why it is empty")


# =========================================================================
# the written product, from the same signals the board renders
# =========================================================================
def test_region_report_is_generated_from_real_signals(dataset):
    """End to end: stored observations -> verdicts -> written assessment.

    The report and the panel read from one `RegionStatus`, so the prose
    cannot drift from the verdicts it describes.
    """
    from sentinel.report import compose_region

    store = PointInTimeStore()
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, store.view)
    status = evaluate_region(region, provider, AS_OF, _catalog())

    report = compose_region(
        status, {i.key: i for i in region.indicators})

    assert report.splitlines()[0] == status.headline()
    for signal in status.signals:
        heading = {
            Verdict.ACTIVE: "## Active",
            Verdict.NOT_ACTIVE: "## Tested and quiet",
            Verdict.INSUFFICIENT_DATA: "## Could not be tested",
        }[signal.verdict]
        assert heading in report


def test_the_report_never_reports_a_bare_null(dataset):
    """Every quiet finding in the product carries its floor."""
    from sentinel.report import compose

    store = PointInTimeStore()
    region = get_region("euro_atlantic")
    provider = storage_provider(dataset, region, store.view)
    status = evaluate_region(region, provider, AS_OF, _catalog())
    by_key = {i.key: i for i in region.indicators}

    for signal in status.tested:
        text = compose(signal, by_key[signal.indicator_key]).format()
        assert "would have been detected" in text or "fixed rule" in text


def test_the_watchboard_supplies_an_event_provider():
    """Without one, every entity indicator on the page reports insufficient
    data regardless of what was derived — the gap this wiring closed."""
    source = __import__("pathlib").Path("ui/pages/regions.py").read_text()
    assert "storage_event_provider" in source
    assert "event_provider=" in source
