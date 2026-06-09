"""Tests voor kolom-rol detectie op messy real-world structuren."""
import pandas as pd

from core.auto_mapping import guess_mapping


def _base_df(**extra_cols):
    data = {
        "Datum": pd.date_range("2025-01-01", periods=30, freq="D").astype(str),
        "Aantal": range(30),
    }
    data.update(extra_cols)
    return pd.DataFrame(data)


def test_basic_time_and_value():
    m = guess_mapping(_base_df())
    assert m["time"] == "Datum"
    assert m["value"] == "Aantal"


def test_json_columns_rejected_as_location():
    """Kolommen vol '{...}' (zoals border_crossing in de missile-data)
    mogen nooit locatie of categorie worden."""
    df = _base_df(
        border_crossing=["{}"] * 30,
        target=["noord", "zuid", "oost"] * 10,
    )
    m = guess_mapping(df)
    assert m["location_name"] == "target"
    assert m["category"] != "border_crossing"


def test_count_column_not_mistaken_for_latitude():
    """Een telling die toevallig binnen [-90, 90] valt is geen latitude
    (integers zonder decimalen)."""
    df = _base_df(destroyed=[float(i % 50) for i in range(30)])
    m = guess_mapping(df)
    assert m["lat"] != "destroyed"


def test_real_coordinates_detected():
    df = _base_df(
        Latitude=[52.1 + i * 0.01 for i in range(30)],
        Longitude=[114.2 + i * 0.01 for i in range(30)],
    )
    m = guess_mapping(df)
    assert m["lat"] == "Latitude"
    assert m["lon"] == "Longitude"


def test_exact_name_match_beats_cardinality():
    """'place' met hoge cardinaliteit moet winnen van een toevallige
    lage-cardinaliteit kolom zonder locatie-naam."""
    df = _base_df(
        place=[f"Stad {i}" for i in range(30)],     # hoge cardinaliteit
        bron=["Radar", "Visueel"] * 15,             # lage cardinaliteit
    )
    m = guess_mapping(df)
    assert m["location_name"] == "place"
