"""The DMA AIS connector, tested everywhere it can honestly be tested.

This parser has never seen a live DMA file — the sandbox it was written in has
no outbound access, and the proxy refuses CONNECT to `web.ais.dk`. That is the
reason for the shape of these tests rather than an excuse for their absence:
transport is a dozen lines behind an injectable seam, and everything that
decides what the data *means* is exercised here against fixtures.

The fixture headers are taken from the published format description. If the
real files differ, `test_a_moved_schema_fails_legibly` is the test that
describes what will happen — a named failure, not silent garbage.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile

import pandas as pd
import pytest

from core import storage
from sentinel.core.time import PointInTimeStore
from sentinel.ingest import (
    DmaAisConnector,
    FetchError,
    SchemaError,
    UrllibFetcher,
    parse_dma_csv,
    run_position_ingest,
)

_HEADER = ("# Timestamp,Type of mobile,MMSI,Latitude,Longitude,"
           "Navigational status,ROT,SOG,COG,Heading,IMO,Callsign,Name,"
           "Ship type,Cargo type,Width,Length,Draught,Destination")


def _row(ts="01/06/2024 00:00:00", mmsi="244123456", lat=54.5, lon=4.2,
         sog=12.3, ship="Cargo") -> str:
    return (f"{ts},Class A,{mmsi},{lat},{lon},Under way using engine,0,"
            f"{sog},180.0,182,9123456,PBAA,TESTER,{ship},,20,150,7.5,ROT")


def _csv(*rows: str) -> str:
    return "\n".join([_HEADER, *rows]) + "\n"


class _FakeFetcher:
    """Serves canned bytes per URL, and records what was asked for."""

    def __init__(self, payloads: dict, fail: set = frozenset()):
        self.payloads = payloads
        self.fail = fail
        self.requested: list[str] = []

    def get(self, url: str, timeout: float = 60.0) -> bytes:
        self.requested.append(url)
        for marker in self.fail:
            if marker in url:
                raise FetchError(f"canned failure for {url}")
        for marker, payload in self.payloads.items():
            if marker in url:
                return payload
        raise FetchError(f"no canned payload for {url}")


def _zipped(csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("aisdk-2024-06-01.csv", csv_text)
    return buffer.getvalue()


# =========================================================================
# parsing
# =========================================================================
def test_the_published_header_maps_onto_our_fields():
    frame = parse_dma_csv(_csv(_row()))
    assert list(frame.columns) == [
        "entity_key", "entity_kind", "timestamp", "lat", "lon",
        "sog", "cog", "heading", "vessel_class", "source_key"]
    assert frame["entity_key"].iloc[0] == "mmsi:244123456"


def test_header_variants_still_match():
    """Case, spaces and the leading '#' are cosmetic; a parser that broke on
    them would fail on the first day the publisher tidied their export."""
    header = ("Timestamp,MMSI,latitude,LONGITUDE,S O G,Ship Type")
    text = f"{header}\n01/06/2024 00:00:00,244123456,54.5,4.2,12.3,Cargo\n"
    frame = parse_dma_csv(text)
    assert len(frame) == 1
    assert frame["lat"].iloc[0] == 54.5
    assert frame["vessel_class"].iloc[0] == "cargo"


def test_timestamps_are_read_day_first():
    """DMA writes dd/mm/yyyy. Letting pandas infer would read 03/04 as
    4 March in one file and 3 April in the next, and nothing would complain."""
    frame = parse_dma_csv(_csv(_row(ts="03/04/2024 10:00:00")))
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2024-04-03 10:00:00")


def test_ais_position_sentinels_are_dropped():
    """AIS sends 91/181 for 'position unavailable' rather than a null."""
    frame = parse_dma_csv(_csv(_row(), _row(lat=91.0, lon=181.0)))
    assert len(frame) == 1
    assert frame["lat"].iloc[0] == 54.5


def test_rows_without_a_usable_fix_are_dropped():
    frame = parse_dma_csv(_csv(_row(), _row(lat="", lon=""),
                               _row(ts="not a date")))
    assert len(frame) == 1


def test_a_bounding_box_filters_before_storage():
    """A national daily archive is millions of rows and the Dutch EEZ is a
    small corner of it; filtering after storage would be the wrong end."""
    frame = parse_dma_csv(
        _csv(_row(lat=54.5, lon=4.2), _row(mmsi="9", lat=57.0, lon=11.0)),
        bbox=(51.0, 56.0, 2.0, 7.5))
    assert len(frame) == 1
    assert frame["entity_key"].iloc[0] == "mmsi:244123456"


def test_an_empty_result_after_filtering_is_not_an_error():
    frame = parse_dma_csv(_csv(_row()), bbox=(10.0, 20.0, 100.0, 110.0))
    assert frame.empty


def test_mmsi_is_namespaced_so_identifiers_cannot_collide():
    """A bare integer would collide with any other numbering scheme the
    entity layer later ingests."""
    frame = parse_dma_csv(_csv(_row()))
    assert frame["entity_key"].iloc[0].startswith("mmsi:")


def test_a_moved_schema_fails_legibly():
    """The first real run is also the first test of this mapping, so the
    failure has to say what was wanted and what arrived."""
    with pytest.raises(SchemaError) as caught:
        parse_dma_csv("vessel,when,where\n1,2,3\n")
    message = str(caught.value)
    assert "timestamp" in message and "entity_key" in message
    assert "vessel, when, where" in message
    assert "has not been checked against a live file" in message


# =========================================================================
# fetching a day
# =========================================================================
def test_a_zipped_archive_is_unpacked():
    connector = DmaAisConnector(
        fetcher=_FakeFetcher({"2024-06-01": _zipped(_csv(_row()))}))
    frame = connector.fetch_day(dt.datetime(2024, 6, 1))
    assert len(frame) == 1


def test_a_plain_csv_response_also_works():
    connector = DmaAisConnector(
        fetcher=_FakeFetcher({"2024-06-01": _csv(_row()).encode()}))
    assert len(connector.fetch_day(dt.datetime(2024, 6, 1))) == 1


def test_arrival_is_when_the_archive_existed_not_when_vessels_transmitted():
    """The file for the 3rd appears on the 4th. Recording transmission time
    would claim we had the data instantly and flatter every replay."""
    connector = DmaAisConnector(
        fetcher=_FakeFetcher({"2024-06-01": _zipped(_csv(_row()))}),
        archive_lag_days=1)
    frame = connector.fetch_day(dt.datetime(2024, 6, 1))
    assert frame["ingested_at"].iloc[0] == pd.Timestamp("2024-06-02")
    assert frame["ingested_at"].iloc[0] > frame["timestamp"].iloc[0]


def test_the_url_follows_the_published_naming():
    connector = DmaAisConnector(fetcher=_FakeFetcher({}))
    assert connector.url_for(dt.datetime(2024, 6, 1)).endswith(
        "aisdk-2024-06-01.zip")


# =========================================================================
# a range, and holes in it
# =========================================================================
def test_a_failed_day_does_not_lose_the_others():
    """An AIS history with a hole in it is still worth having."""
    fetcher = _FakeFetcher(
        {"2024-06-01": _zipped(_csv(_row())),
         "2024-06-03": _zipped(_csv(_row(ts="03/06/2024 00:00:00")))},
        fail={"2024-06-02"})
    frame = DmaAisConnector(fetcher=fetcher).fetch(
        since=dt.datetime(2024, 6, 1), until=dt.datetime(2024, 6, 3))
    assert len(frame) == 2
    assert len(frame.attrs["failed_days"]) == 1
    assert "2024-06-02" in frame.attrs["failed_days"][0]


def test_fetching_without_a_start_date_is_refused():
    """'Everything' is several terabytes of national AIS."""
    with pytest.raises(ValueError, match="start date is required"):
        DmaAisConnector(fetcher=_FakeFetcher({})).fetch()


def test_a_reversed_range_is_refused():
    with pytest.raises(ValueError, match="until is before since"):
        DmaAisConnector(fetcher=_FakeFetcher({})).fetch(
            since=dt.datetime(2024, 6, 3), until=dt.datetime(2024, 6, 1))


# =========================================================================
# transport
# =========================================================================
def test_a_client_error_is_not_retried():
    """A 404 will not fix itself by waiting, and retrying it three times
    just delays the report."""
    import urllib.error

    calls = []

    def _raise(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    fetcher = UrllibFetcher(retries=3, backoff_seconds=0)
    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = _raise
    try:
        with pytest.raises(FetchError, match="404"):
            fetcher.get("https://example.invalid/x")
    finally:
        urllib.request.urlopen = original
    assert len(calls) == 1


def test_a_server_error_is_retried_then_reported():
    import urllib.error
    import urllib.request

    calls = []

    def _raise(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("u", 503, "Unavailable", {}, None)

    original = urllib.request.urlopen
    urllib.request.urlopen = _raise
    try:
        with pytest.raises(FetchError, match="after 3 attempts"):
            UrllibFetcher(retries=3, backoff_seconds=0).get("https://x.invalid")
    finally:
        urllib.request.urlopen = original
    assert len(calls) == 3


# =========================================================================
# the whole chain
# =========================================================================
@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ais.db'}")
    storage.init_db()
    return storage.create_dataset("ais", "", {})


def test_positions_reach_storage_and_the_point_in_time_view(dataset):
    """Fetch through to a causal read, with only the transport faked."""
    fetcher = _FakeFetcher({"2024-06-01": _zipped(_csv(
        _row(), _row(mmsi="265999999", lat=55.1, lon=3.9, ship="Fishing")))})
    connector = DmaAisConnector(fetcher=fetcher,
                                bbox=(51.0, 56.0, 2.0, 7.5))

    result = run_position_ingest(connector, dataset,
                                 since=dt.datetime(2024, 6, 1))
    assert result.ok, result.error
    assert result.n_inserted == 2
    assert result.arrival_is_faithful, (
        "the archive date is an observed arrival, not an assumed one")

    view = PointInTimeStore().view(dt.datetime(2024, 6, 5))
    stored = view.positions(dataset)
    assert len(stored) == 2
    assert set(stored["vessel_class"]) == {"cargo", "fishing"}


def test_the_data_is_invisible_before_the_archive_was_published(dataset):
    fetcher = _FakeFetcher({"2024-06-01": _zipped(_csv(_row()))})
    run_position_ingest(DmaAisConnector(fetcher=fetcher), dataset,
                        since=dt.datetime(2024, 6, 1))

    store = PointInTimeStore()
    assert store.view(dt.datetime(2024, 6, 1, 12)).positions(dataset).empty
    assert len(store.view(dt.datetime(2024, 6, 2)).positions(dataset)) == 1


def test_re_running_a_day_adds_nothing(dataset):
    fetcher = _FakeFetcher({"2024-06-01": _zipped(_csv(_row()))})
    connector = DmaAisConnector(fetcher=fetcher)
    run_position_ingest(connector, dataset, since=dt.datetime(2024, 6, 1))
    again = run_position_ingest(connector, dataset,
                                since=dt.datetime(2024, 6, 1))
    assert again.n_inserted == 0
    assert again.n_duplicate == 1


def test_a_run_where_every_day_failed_is_an_error_not_a_quiet_success(dataset):
    """Otherwise a month of outages looks identical to a month of no traffic."""
    connector = DmaAisConnector(fetcher=_FakeFetcher({}, fail={"2024"}))
    result = run_position_ingest(connector, dataset,
                                 since=dt.datetime(2024, 6, 1),
                                 until=dt.datetime(2024, 6, 2))
    assert not result.ok
    assert "every requested archive failed" in result.error
    assert len(result.notes) == 2


def test_a_partial_run_succeeds_and_names_the_missing_days(dataset):
    fetcher = _FakeFetcher({"2024-06-01": _zipped(_csv(_row()))},
                           fail={"2024-06-02"})
    result = run_position_ingest(
        DmaAisConnector(fetcher=fetcher), dataset,
        since=dt.datetime(2024, 6, 1), until=dt.datetime(2024, 6, 2))
    assert result.ok
    assert result.n_inserted == 1
    assert any("2024-06-02" in note for note in result.notes)
