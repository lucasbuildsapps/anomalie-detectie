"""Tests voor de gazetteer (regionaam -> coördinaten zonder externe dienst)."""
import pandas as pd
import pytest

from core import gazetteer


class TestNormalisation:
    @pytest.mark.parametrize("raw", [
        "Kharkiv", "kharkiv", "Kharkiv Oblast", "KHARKIV OBLAST ",
        "Kharkiv region", "Kharkiv, Ukraine",
    ])
    def test_variants_resolve_to_same_point(self, raw):
        assert gazetteer.lookup(raw) == pytest.approx((49.99, 36.23))

    def test_unknown_returns_none(self):
        assert gazetteer.lookup("Atlantis") is None
        assert gazetteer.lookup("") is None
        assert gazetteer.lookup(None) is None


class TestAnnotate:
    def test_fills_missing_coordinates(self):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=3),
            "value": [1.0, 2.0, 3.0],
            "location_name": ["Kharkiv", "Odesa", "Atlantis"],
        })
        out = gazetteer.annotate(df)
        assert out.loc[0, "lat"] == pytest.approx(49.99)
        assert out.loc[1, "lon"] == pytest.approx(30.73)
        # Onbekend blijft leeg: liever van de kaart dan een verzonnen plek
        assert pd.isna(out.loc[2, "lat"])

    def test_real_measurements_win(self):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=1),
            "value": [1.0],
            "location_name": ["Kharkiv"],
            "lat": [50.5], "lon": [36.9],
        })
        out = gazetteer.annotate(df)
        assert out.loc[0, "lat"] == pytest.approx(50.5)

    def test_partial_coordinates_are_completed(self):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=2),
            "value": [1.0, 2.0],
            "location_name": ["Kharkiv", "Lviv"],
            "lat": [50.5, None], "lon": [36.9, None],
        })
        out = gazetteer.annotate(df)
        assert out.loc[1, "lat"] == pytest.approx(49.84)

    def test_empty_and_missing_column_are_safe(self):
        assert gazetteer.annotate(pd.DataFrame()).empty
        df = pd.DataFrame({"value": [1.0]})
        assert "lat" not in gazetteer.annotate(df).columns


def test_coverage_reports_honestly():
    hits, total = gazetteer.coverage(["Kharkiv", "Lviv", "Atlantis"])
    assert (hits, total) == (2, 3)


def test_demo_dataset_is_largely_mappable():
    """De demo-dataset heeft geen lat/lon-kolommen; zonder gazetteer bleef
    de kaart leeg. Dit bewaakt dat de dekking niet stilletjes wegzakt."""
    from pathlib import Path

    from core.import_data import apply_mapping

    csv = Path(__file__).resolve().parent.parent / "data" / "missile_attacks_demo.csv"
    if not csv.exists():
        pytest.skip("demo-CSV niet aanwezig")
    raw = pd.read_csv(csv)
    df, _ = apply_mapping(raw, {
        "time": "time_start", "value": "launched", "location_name": "target",
        "category": "model", "lat": None, "lon": None, "extras": [],
    })
    out = gazetteer.annotate(df)
    assert out["lat"].notna().mean() > 0.95
