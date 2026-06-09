"""Tests voor robuust importeren — de plek waar echte data het vaakst breekt."""
import pandas as pd

from core.import_data import apply_mapping, parse_datetime_robust


def test_mixed_datetime_formats_no_loss(mixed_format_dates):
    """Pandas 3.0 NaT't date-only rijen in een mixed kolom; onze parser niet."""
    parsed = parse_datetime_robust(mixed_format_dates)
    assert parsed.notna().all(), "geen enkele rij mag verloren gaan"


def test_parse_keeps_time_component(mixed_format_dates):
    parsed = parse_datetime_robust(mixed_format_dates)
    assert parsed.iloc[1].hour == 18


def test_apply_mapping_returns_stats():
    df = pd.DataFrame({
        "d": ["2025-01-01", "2025-01-02", "NIET-EEN-DATUM"],
        "n": [1, 2, 3],
    })
    normalized, stats = apply_mapping(df, {"time": "d", "value": "n"})
    assert stats["input_rows"] == 3
    assert stats["output_rows"] == 2
    assert stats["dropped_bad_time"] == 1


def test_apply_mapping_requires_time_and_value():
    df = pd.DataFrame({"a": [1]})
    try:
        apply_mapping(df, {"time": None, "value": "a"})
        assert False, "had ValueError moeten geven"
    except ValueError:
        pass


def test_apply_mapping_coerces_nonnumeric_values():
    df = pd.DataFrame({
        "d": ["2025-01-01", "2025-01-02"],
        "n": ["5", "geen-getal"],
    })
    normalized, _ = apply_mapping(df, {"time": "d", "value": "n"})
    assert normalized["value"].iloc[0] == 5.0
    assert pd.isna(normalized["value"].iloc[1])
