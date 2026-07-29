"""Tests voor de import-validatie."""
import pandas as pd

from core.validation import validate_mapped


def _df(**overrides):
    base = {
        "timestamp": pd.date_range("2025-01-01", periods=30, freq="D"),
        "value": [float(i % 5) for i in range(30)],
        "location_name": ["A"] * 30,
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_clean_data_passes():
    rep = validate_mapped(_df())
    assert rep.ok
    assert rep.warnings == []


def test_empty_frame_blocks():
    rep = validate_mapped(pd.DataFrame({"timestamp": [], "value": []}))
    assert not rep.ok


def test_all_bad_timestamps_blocks():
    df = _df(timestamp=["geen datum"] * 30)
    rep = validate_mapped(df)
    assert not rep.ok


def test_future_dates_warn():
    df = _df(timestamp=pd.date_range("2035-01-01", periods=30, freq="D"))
    rep = validate_mapped(df)
    assert rep.ok
    assert any("toekomst" in w for w in rep.warnings)


def test_constant_values_warn():
    df = _df(value=[7.0] * 30)
    rep = validate_mapped(df)
    assert rep.ok
    assert any("identiek" in w for w in rep.warnings)


def test_out_of_range_coords_warn():
    df = _df()
    df["lat"] = 123.0
    rep = validate_mapped(df)
    assert any("lat" in w for w in rep.warnings)


def test_negative_values_warn_but_pass():
    df = _df(value=[-1.0] * 15 + [1.0] * 15)
    rep = validate_mapped(df)
    assert rep.ok
    assert any("negatieve" in w for w in rep.warnings)


def test_heavy_drop_stats_warn():
    rep = validate_mapped(_df(), import_stats={
        "input_rows": 100, "dropped_total": 40,
    })
    assert any("gedropt" in w for w in rep.warnings)


def test_many_duplicates_warn():
    df = _df(timestamp=[pd.Timestamp("2025-01-01")] * 30,
             value=[1.0] * 30)
    rep = validate_mapped(df)
    assert any("duplicaten" in w for w in rep.warnings)
