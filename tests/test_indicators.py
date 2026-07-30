"""Tests voor het watchboard (vooraf gedefinieerde I&W-indicatoren).

De waarde van een watchboard zit in de discipline: je legt vóóraf vast
wat ertoe doet. Deze tests bewaken dat de toetsing dat eerlijk uitvoert —
inclusief het geval dat er niets aan de hand is, want een indicator die
niet afgaat is óók informatie.
"""
import numpy as np
import pandas as pd
import pytest

import core.storage as storage
from core.indicators import (
    Indicator,
    evaluate,
    evaluate_all,
    summarise,
)
from core.normbeeld import compute_normbeeld


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "ind.db")
    storage.init_db()
    yield


def _normbeeld(values, location="A"):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(values), freq="D"),
        "value": np.asarray(values, dtype=float),
        "location_name": location,
    })
    return compute_normbeeld(df, location=location, horizon_days=7,
                             aggregation="daily")


@pytest.fixture
def calm_then_spike():
    rng = np.random.default_rng(21)
    vals = np.clip(10 + rng.normal(0, 1.5, 120), 0, None).round()
    vals[-3:] = 200          # drie dagen fors boven de band
    return _normbeeld(vals)


@pytest.fixture
def calm():
    rng = np.random.default_rng(22)
    return _normbeeld(np.clip(10 + rng.normal(0, 1.5, 120), 0, None).round())


class TestConditions:
    def test_above_band_activates(self, calm_then_spike):
        ind = Indicator("Piek", 1, "boven_band", location="A")
        state = evaluate(ind, calm_then_spike)
        assert state.active
        assert state.streak >= 3
        assert state.since is not None

    def test_quiet_series_does_not_activate(self, calm):
        state = evaluate(Indicator("Piek", 1, "boven_band", location="A"), calm)
        assert not state.active
        assert state.streak == 0

    def test_absolute_threshold(self, calm_then_spike):
        assert evaluate(Indicator("Boven 150", 1, "drempel_boven",
                                  threshold=150), calm_then_spike).active
        assert not evaluate(Indicator("Boven 500", 1, "drempel_boven",
                                      threshold=500), calm_then_spike).active

    def test_silence_is_a_signal(self):
        """Afwezigheid van activiteit is in waarschuwingswerk een
        klassiek signaal — en precies wat een piek-detector mist."""
        vals = np.concatenate([np.full(110, 12.0), np.zeros(6)])
        state = evaluate(Indicator("Stilte", 1, "stilte", periods=5),
                         _normbeeld(vals))
        assert state.active
        assert state.streak >= 5

    def test_silence_does_not_fire_on_normal_activity(self, calm):
        assert not evaluate(Indicator("Stilte", 1, "stilte", periods=3),
                            calm).active

    def test_relative_increase(self):
        vals = np.concatenate([np.full(110, 10.0), np.full(6, 40.0)])
        state = evaluate(Indicator("Verdubbeling", 1, "stijging_pct",
                                   threshold=100, periods=3), _normbeeld(vals))
        assert state.active

    def test_sustained_increase_keeps_burning(self):
        """Kern van het watchboard-ontwerp: een indicator mag niet uitgaan
        omdát de situatie aanhoudt. Gemeten tegen de verwachting zou dat
        wel gebeuren — die past zich aan het nieuwe niveau aan."""
        vals = np.concatenate([np.full(200, 10.0), np.full(60, 40.0)])
        state = evaluate(Indicator("Aanhoudend hoog", 1, "stijging_pct",
                                   threshold=100, periods=10),
                         _normbeeld(vals))
        assert state.active, (
            "verhoogd niveau hoort te blijven branden zolang het duurt")
        assert state.streak >= 10


class TestConsecutivePeriods:
    def test_requires_full_streak(self, calm_then_spike):
        """Drie dagen piek voldoet aan 3, niet aan 5."""
        assert evaluate(Indicator("3 dagen", 1, "boven_band", periods=3),
                        calm_then_spike).active
        assert not evaluate(Indicator("5 dagen", 1, "boven_band", periods=5),
                            calm_then_spike).active

    def test_partial_streak_is_reported(self, calm_then_spike):
        state = evaluate(Indicator("5 dagen", 1, "boven_band", periods=5),
                         calm_then_spike)
        assert not state.active
        assert "nog niet" in state.evidence


class TestEdgeCases:
    def test_missing_normbeeld_is_reported(self):
        state = evaluate(Indicator("X", 1, "boven_band"), None)
        assert not state.active
        assert "geen data" in state.evidence

    def test_series_shorter_than_required_periods(self):
        nb = _normbeeld(np.full(40, 5.0))
        state = evaluate(Indicator("X", 1, "boven_band", periods=200), nb)
        assert not state.active
        assert "te weinig" in state.evidence

    def test_unknown_condition_never_activates(self, calm_then_spike):
        assert not evaluate(Indicator("?", 1, "verzonnen"),
                            calm_then_spike).active

    def test_describe_is_human_readable(self):
        ind = Indicator("Test", 1, "drempel_boven", location="Kyiv",
                        threshold=50, periods=3)
        text = ind.describe()
        assert "Kyiv" in text and "50" in text and "3 perioden" in text


class TestWatchboard:
    def test_disabled_indicators_are_skipped(self, calm_then_spike):
        inds = [Indicator("Uit", 1, "boven_band", location="A", enabled=False)]
        assert evaluate_all(inds, {"A": calm_then_spike}) == []

    def test_active_indicators_come_first(self, calm_then_spike, calm):
        inds = [
            Indicator("Rustig", 1, "boven_band", location="B"),
            Indicator("Actief", 1, "boven_band", location="A"),
        ]
        states = evaluate_all(inds, {"A": calm_then_spike, "B": calm})
        assert states[0].indicator.name == "Actief"
        assert states[0].active

    def test_region_agnostic_indicator_finds_the_hot_region(
            self, calm_then_spike, calm):
        """Zonder regio hoeft een analist niet per regio een kopie te
        maken; de indicator wijst zelf de regio aan."""
        states = evaluate_all([Indicator("Ergens", 1, "boven_band")],
                              {"rustig": calm, "heet": calm_then_spike})
        assert states[0].active
        assert "heet" in states[0].evidence

    def test_unknown_region_is_reported(self, calm):
        states = evaluate_all([Indicator("X", 1, "boven_band",
                                         location="Bestaat niet")],
                              {"A": calm})
        assert not states[0].active
        assert "niet in data" in states[0].evidence


class TestSummary:
    def test_empty_watchboard_explains_its_purpose(self):
        assert "vooraf" in summarise([])

    def test_nothing_active_is_itself_information(self, calm):
        states = evaluate_all([Indicator("X", 1, "boven_band", location="A")],
                              {"A": calm})
        text = summarise(states)
        assert "zelf ook informatie" in text

    def test_active_indicators_are_named(self, calm_then_spike):
        states = evaluate_all(
            [Indicator("Zware beschieting", 1, "boven_band", location="A")],
            {"A": calm_then_spike})
        assert "Zware beschieting" in summarise(states)


class TestPersistence:
    def test_roundtrip(self):
        ds = storage.create_dataset("wb", "", {})
        ind_id = storage.add_indicator(
            ds, "Stilte Kyiv", "stilte", location="Kyiv", periods=5,
            meaning="Mogelijk hergroepering of gewijzigde rapportage",
        )
        rows = storage.list_indicators(ds)
        assert len(rows) == 1
        assert rows[0]["name"] == "Stilte Kyiv"
        assert rows[0]["periods"] == 5
        assert rows[0]["meaning"].startswith("Mogelijk")
        assert rows[0]["id"] == ind_id

    def test_creation_is_audited(self):
        """'We hadden dit vooraf opgeschreven' is alleen navolgbaar als
        er een datum en een naam bij staan."""
        ds = storage.create_dataset("wb", "", {})
        storage.add_indicator(ds, "X", "boven_band")
        rows = storage.list_audit(20)
        entry = next(r for r in rows if r["action"] == "indicator_toegevoegd")
        assert entry["username"]
        assert entry["ts"] is not None

    def test_enable_disable_and_delete(self):
        ds = storage.create_dataset("wb", "", {})
        ind_id = storage.add_indicator(ds, "X", "boven_band")
        storage.set_indicator_enabled(ind_id, False)
        assert storage.list_indicators(ds)[0]["enabled"] == 0
        storage.delete_indicator(ind_id)
        assert storage.list_indicators(ds) == []
