"""Tests voor de FIRMS-connector en de prestatie-terugkoppeling."""
import pytest

import connectors.firms as firms_mod
import core.storage as storage
from connectors.firms import FirmsThermalConnector
from core import annotations as anno

FIRMS_CSV = (
    "country_id,latitude,longitude,bright_ti4,acq_date,acq_time,satellite,"
    "confidence,frp,daynight\n"
    "UKR,49.99,36.23,320.1,2026-07-20,0412,N,nominal,12.4,N\n"
    "UKR,46.63,32.61,298.7,2026-07-21,1130,N,high,4.1,D\n"
    "UKR,50.10,30.50,290.0,2026-07-21,1131,N,low,1.0,D\n"
)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "fb.db")
    storage.init_db()
    yield


class TestFirms:
    def test_missing_key_is_reported(self, monkeypatch):
        monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
        c = FirmsThermalConnector()
        assert c.missing_config() == ["FIRMS_MAP_KEY"]
        ok, msg = c.self_test()
        assert not ok and "FIRMS_MAP_KEY" in msg

    def test_parses_csv_into_internal_schema(self, monkeypatch):
        monkeypatch.setenv("FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(firms_mod, "_http_text", lambda url, **k: FIRMS_CSV)
        monkeypatch.setattr(firms_mod, "AREAS", {"Oekraïne": (22, 44, 40, 52)})
        df = FirmsThermalConnector().fetch(None)
        assert set(df["location_name"]) == {"Oekraïne"}
        assert df["value"].tolist() == [1.0, 1.0]   # 'low' is eruit gefilterd
        assert df["lat"].iloc[0] == pytest.approx(49.99)
        assert "frp" in df.columns

    def test_low_confidence_rows_are_dropped(self, monkeypatch):
        monkeypatch.setenv("FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(firms_mod, "_http_text", lambda url, **k: FIRMS_CSV)
        monkeypatch.setattr(firms_mod, "AREAS", {"X": (0, 0, 1, 1)})
        assert len(FirmsThermalConnector().fetch(None)) == 2

    def test_error_page_is_detected(self, monkeypatch):
        monkeypatch.setenv("FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(firms_mod, "_http_text",
                            lambda url, **k: "Invalid MAP_KEY.")
        monkeypatch.setattr(firms_mod, "AREAS", {"X": (0, 0, 1, 1)})
        with pytest.raises(Exception, match="weigerde|Invalid"):
            FirmsThermalConnector().fetch(None)

    def test_output_has_usable_coordinates(self, monkeypatch):
        """FIRMS levert eigen coördinaten — de kaart moet zonder gazetteer
        werken."""
        monkeypatch.setenv("FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(firms_mod, "_http_text", lambda url, **k: FIRMS_CSV)
        monkeypatch.setattr(firms_mod, "AREAS", {"X": (0, 0, 1, 1)})
        df = FirmsThermalConnector().fetch(None)
        assert df["lat"].notna().all() and df["lon"].notna().all()

    def test_passes_import_validation(self, monkeypatch):
        from core.validation import validate_mapped
        monkeypatch.setenv("FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(firms_mod, "_http_text", lambda url, **k: FIRMS_CSV)
        monkeypatch.setattr(firms_mod, "AREAS", {"X": (0, 0, 1, 1)})
        assert validate_mapped(FirmsThermalConnector().fetch(None)).ok


def _annotate(ds, n, status, prefix="f"):
    for i in range(n):
        key = anno.finding_key(f"2026-07-{i + 1:02d}", f"{prefix}{i}", None)
        anno.save_annotation(ds, key, "", status)


class TestFeedbackLoop:
    def test_no_verdict_below_threshold(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 3, "bevestigd")
        perf = anno.performance(ds)
        assert not perf["reliable"]
        verdict = anno.performance_verdict(perf)
        assert "Nog geen uitspraak" in verdict
        # Geen percentage tonen op te weinig data: '100% raak' op 3 gevallen
        # is misleidender dan helemaal geen getal.
        assert "%" not in verdict

    def test_precision_counts_confirmed_over_judged(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 8, "bevestigd", "a")
        _annotate(ds, 2, "vals_alarm", "b")
        perf = anno.performance(ds)
        assert perf["reliable"]
        assert perf["precision"] == pytest.approx(0.8)
        assert "80% raak" in anno.performance_verdict(perf)

    def test_escalated_counts_as_confirmed(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 6, "geescaleerd", "a")
        _annotate(ds, 4, "vals_alarm", "b")
        perf = anno.performance(ds)
        assert perf["confirmed"] == 6
        assert perf["precision"] == pytest.approx(0.6)

    def test_open_and_investigated_do_not_affect_precision(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 5, "bevestigd", "a")
        _annotate(ds, 5, "vals_alarm", "b")
        _annotate(ds, 20, "open", "c")
        _annotate(ds, 20, "onderzocht", "d")
        perf = anno.performance(ds)
        assert perf["n_judged"] == 10
        assert perf["precision"] == pytest.approx(0.5)

    def test_bad_precision_gets_blunt_verdict(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 2, "bevestigd", "a")
        _annotate(ds, 10, "vals_alarm", "b")
        verdict = anno.performance_verdict(anno.performance(ds))
        assert "vals alarm" in verdict
        assert "meer tijd dan hij oplevert" in verdict

    def test_empty_dataset_is_safe(self):
        ds = storage.create_dataset("d", "", {})
        perf = anno.performance(ds)
        assert perf["n_judged"] == 0
        assert perf["precision"] is None
        assert not perf["reliable"]

    def test_full_history_window(self):
        ds = storage.create_dataset("d", "", {})
        _annotate(ds, 12, "bevestigd")
        assert anno.performance(ds, window_days=None)["n_judged"] == 12
