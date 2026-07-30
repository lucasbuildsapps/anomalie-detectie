"""Tests voor 'wat is er veranderd sinds de vorige beoordeling'.

Praktisch: wie na een week terugkomt wil weten wát er anders is, niet het
hele beeld herlezen. Formeel: ICD 203 vraagt om het expliciet benoemen van
wijzigingen ten opzichte van eerdere oordelen.
"""
import numpy as np
import pandas as pd
import pytest

import core.storage as storage
from core.changes import compare, since_last, summarise


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "chg.db")
    storage.init_db()
    yield


def _snap(alerts=(), normbeelds=None, created_at="2026-07-01"):
    return {
        "created_at": created_at,
        "payload": {
            "alerts": [
                {"datum": d, "locatie": loc, "waarde": 1, "richting": "boven"}
                for d, loc in alerts
            ],
            "normbeelds": normbeelds or {},
        },
    }


def _nb(expected, confidence="hoog", model="negbin"):
    return {"expected": expected, "confidence": confidence,
            "band_model": model}


class TestAlertChanges:
    def test_new_alert_is_reported_as_important(self):
        prev = _snap(alerts=[("2026-07-01", "Kyiv")])
        curr = _snap(alerts=[("2026-07-01", "Kyiv"), ("2026-07-02", "Kharkiv")])
        changes = compare(prev, curr)
        nieuw = [c for c in changes if c.kind == "nieuw"]
        assert len(nieuw) == 1
        assert nieuw[0].subject == "Kharkiv"
        assert nieuw[0].important

    def test_disappeared_alerts_are_explained_not_alarming(self):
        """Een afwijking die uit beeld valt is meestal gewoon oud
        geworden; dat hoort geen waarschuwing te zijn."""
        prev = _snap(alerts=[("2026-06-01", "Kyiv")])
        curr = _snap(alerts=[])
        change = next(c for c in compare(prev, curr) if c.kind == "verdwenen")
        assert not change.important
        assert "venster" in change.description

    def test_many_new_alerts_are_summarised(self):
        prev = _snap()
        curr = _snap(alerts=[(f"2026-07-{i:02d}", f"R{i}") for i in range(1, 16)])
        changes = compare(prev, curr)
        assert any("nog 5 nieuwe" in c.description for c in changes)

    def test_identical_snapshots_give_nothing(self):
        snap = _snap(alerts=[("2026-07-01", "Kyiv")],
                     normbeelds={"Kyiv": _nb(10.0)})
        assert compare(snap, snap) == []


class TestLevelChanges:
    def test_material_shift_is_reported(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(20.0)})
        change = next(c for c in compare(prev, curr) if c.kind == "niveau")
        assert "hoger" in change.description
        assert change.important

    def test_small_drift_is_ignored(self):
        """Elk normbeeld schuift een beetje bij nieuwe data; dat is geen
        nieuws en zou de lijst onleesbaar maken."""
        prev = _snap(normbeelds={"Kyiv": _nb(100.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(105.0)})
        assert not [c for c in compare(prev, curr) if c.kind == "niveau"]

    def test_tiny_absolute_change_is_ignored(self):
        """0.2 -> 0.3 is +50% maar analytisch niets."""
        prev = _snap(normbeelds={"Kyiv": _nb(0.2)})
        curr = _snap(normbeelds={"Kyiv": _nb(0.3)})
        assert not [c for c in compare(prev, curr) if c.kind == "niveau"]

    def test_decrease_is_reported_too(self):
        prev = _snap(normbeelds={"Kyiv": _nb(50.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(20.0)})
        change = next(c for c in compare(prev, curr) if c.kind == "niveau")
        assert "lager" in change.description


class TestConfidenceAndModel:
    def test_confidence_drop_is_important(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0, confidence="hoog")})
        curr = _snap(normbeelds={"Kyiv": _nb(10.0, confidence="laag")})
        change = next(c for c in compare(prev, curr) if c.kind == "vertrouwen")
        assert change.important

    def test_confidence_rise_is_noted_but_not_urgent(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0, confidence="laag")})
        curr = _snap(normbeelds={"Kyiv": _nb(10.0, confidence="hoog")})
        change = next(c for c in compare(prev, curr) if c.kind == "vertrouwen")
        assert not change.important

    def test_band_model_switch_is_reported(self):
        """Een wissel betekent dat de reeks van aard is veranderd."""
        prev = _snap(normbeelds={"Kyiv": _nb(10.0, model="poisson")})
        curr = _snap(normbeelds={"Kyiv": _nb(10.0, model="negbin")})
        assert any(c.kind == "model" for c in compare(prev, curr))


class TestRegions:
    def test_new_region_is_reported(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(10.0), "Lviv": _nb(5.0)})
        assert any(c.subject == "Lviv" and c.kind == "nieuw"
                   for c in compare(prev, curr))

    def test_vanished_region_is_reported(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0), "Lviv": _nb(5.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(10.0)})
        assert any(c.subject == "Lviv" and c.kind == "verdwenen"
                   for c in compare(prev, curr))


class TestSummary:
    def test_stability_is_itself_information(self):
        text = summarise([], previous={"created_at": "2026-07-01"})
        assert "stabiel" in text
        assert "2026-07-01" in text

    def test_important_changes_are_counted(self):
        prev = _snap(normbeelds={"Kyiv": _nb(10.0)})
        curr = _snap(normbeelds={"Kyiv": _nb(40.0)})
        assert "aandacht" in summarise(compare(prev, curr))

    def test_missing_snapshot_is_safe(self):
        assert compare(None, _snap()) == []
        assert compare(_snap(), None) == []


class TestSinceLast:
    def _seed(self, values):
        from core.normbeeld import (
            compute_all_normbeelds,
            detect_recent_alerts,
        )
        df = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=len(values),
                                       freq="D"),
            "value": np.asarray(values, dtype=float),
            "location_name": "A",
        })
        nbs = compute_all_normbeelds(df, horizon_days=14, aggregation="daily")
        alerts = detect_recent_alerts(nbs, aggregation="daily")
        return nbs, alerts, df

    def test_needs_two_snapshots(self):
        ds = storage.create_dataset("chg", "", {})
        nbs, alerts, df = self._seed(np.full(80, 10.0))
        storage.save_snapshot(ds, alerts, nbs, aggregation="daily",
                              horizon=14, n_rows=len(df))
        changes, previous = since_last(ds)
        assert changes == [] and previous is None

    def test_compares_the_two_most_recent(self):
        ds = storage.create_dataset("chg", "", {})
        rng = np.random.default_rng(4)
        quiet = np.clip(10 + rng.normal(0, 1, 90), 0, None).round()
        nbs, alerts, df = self._seed(quiet)
        storage.save_snapshot(ds, alerts, nbs, aggregation="daily",
                              horizon=14, n_rows=len(df))

        busy = np.concatenate([quiet, np.full(30, 60.0)])
        nbs2, alerts2, df2 = self._seed(busy)
        storage.save_snapshot(ds, alerts2, nbs2, aggregation="daily",
                              horizon=14, n_rows=len(df2))

        changes, previous = since_last(ds)
        assert previous is not None
        assert changes, "een forse niveauverschuiving hoort opgemerkt te worden"
