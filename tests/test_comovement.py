"""Tests voor peer-groep-analyse: wie loopt uit de pas met zijn groep?

Dit beantwoordt een andere vraag dan het normbeeld. Normbeeld: 'ongewoon
t.o.v. eigen verleden'. Hier: 'ongewoon t.o.v. de regio's die normaal
hetzelfde doen'. Een landelijke stijging is dan géén signaal; één regio
die stijgt terwijl zijn peers vlak blijven wél.
"""
import numpy as np
import pandas as pd
import pytest

from core.comparison import region_comovement


def _panel(series_by_region: dict, start="2025-01-01") -> pd.DataFrame:
    frames = []
    for region, values in series_by_region.items():
        frames.append(pd.DataFrame({
            "timestamp": pd.date_range(start, periods=len(values), freq="D"),
            "value": np.asarray(values, dtype=float),
            "location_name": region,
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def correlated_panel():
    """Vier regio's die een gedeeld patroon volgen, met eigen ruis."""
    rng = np.random.default_rng(4)
    n = 160
    common = 40 + 12 * np.sin(2 * np.pi * np.arange(n) / 30)
    return {
        r: np.clip(common + rng.normal(0, 2.5, n), 0, None)
        for r in ("Alpha", "Bravo", "Charlie", "Delta")
    }


class TestGrouping:
    def test_correlated_regions_form_a_group(self, correlated_panel):
        corr, _ = region_comovement(_panel(correlated_panel))
        assert corr is not None
        assert corr.loc["Alpha", "Bravo"] > 0.5

    def test_too_few_regions_returns_nothing(self):
        corr, devs = region_comovement(_panel({"A": np.arange(60.0),
                                               "B": np.arange(60.0)}))
        assert corr is None and devs == []

    def test_constant_regions_are_ignored(self, correlated_panel):
        panel = dict(correlated_panel)
        panel["Dood"] = np.zeros(160)
        corr, _ = region_comovement(_panel(panel))
        assert "Dood" not in corr.columns

    def test_empty_input_is_safe(self):
        assert region_comovement(pd.DataFrame()) == (None, [])


class TestDeviationDetection:
    def test_lone_riser_is_flagged(self, correlated_panel):
        """Eén regio stijgt terwijl zijn peers gelijk blijven."""
        panel = {k: v.copy() for k, v in correlated_panel.items()}
        panel["Delta"][-3:] += 45
        _, devs = region_comovement(_panel(panel))
        flagged = {d.region for d in devs}
        assert "Delta" in flagged
        delta = next(d for d in devs if d.region == "Delta")
        assert delta.direction == "boven"
        assert delta.recent_z > 2

    def test_lone_faller_is_flagged(self, correlated_panel):
        panel = {k: v.copy() for k, v in correlated_panel.items()}
        panel["Charlie"][-3:] = 0
        _, devs = region_comovement(_panel(panel))
        charlie = next((d for d in devs if d.region == "Charlie"), None)
        assert charlie is not None
        assert charlie.direction == "onder"

    def test_shared_rise_is_not_flagged(self, correlated_panel):
        """Kern van het idee: stijgen ze allemaal, dan is er geen peer-
        afwijking — dat is een landelijke ontwikkeling, geen lokaal signaal."""
        panel = {k: v.copy() for k, v in correlated_panel.items()}
        for k in panel:
            panel[k][-3:] += 45
        _, devs = region_comovement(_panel(panel))
        assert devs == []

    def test_quiet_period_flags_nothing(self, correlated_panel):
        _, devs = region_comovement(_panel(correlated_panel))
        assert devs == []

    def test_deviation_carries_its_evidence(self, correlated_panel):
        panel = {k: v.copy() for k, v in correlated_panel.items()}
        panel["Bravo"][-3:] += 50
        _, devs = region_comovement(_panel(panel))
        d = next(x for x in devs if x.region == "Bravo")
        assert len(d.peers) >= 2
        assert d.peer_correlation > 0.4
        assert d.recent_value > d.peer_expected

    def test_uncorrelated_region_gets_no_peers(self):
        """Zonder groep valt er niets te vergelijken — geen valse melding."""
        rng = np.random.default_rng(6)
        n = 160
        common = 30 + 10 * np.sin(2 * np.pi * np.arange(n) / 30)
        panel = {
            "Alpha": common + rng.normal(0, 2, n),
            "Bravo": common + rng.normal(0, 2, n),
            "Charlie": common + rng.normal(0, 2, n),
            "Eenling": rng.random(n) * 50,
        }
        panel["Eenling"][-3:] += 80
        _, devs = region_comovement(_panel(panel))
        assert "Eenling" not in {d.region for d in devs}
