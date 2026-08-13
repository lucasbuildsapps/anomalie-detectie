"""Tests voor de Fase 0-verkeersprobe.

Alles gemockt: CI mag nooit een betaalde API aanroepen, en al helemaal
niet afhangen van of Google vandaag verkeer teruggeeft. Wat hier getest
wordt is het *contract* — parsing per provider, het dekkingsoordeel, het
kostenplafond, hervatbaarheid — niet de dekking zelf.

Het scherpste geval staat in `test_divergentie_zonder_beweging_is_niet_live`:
een constante afwijking van 3% tussen `duration` en `staticDuration` mag
géén 'live' opleveren. Dat is precies de fout die een hele keten op een
dood signaal zou bouwen.
"""
from __future__ import annotations

import json

import pytest

from traffic import coverage
from traffic.config import ProbeConfig
from traffic.cost import BudgetExceeded, CostLedger
from traffic.probe import ProbeRunner
from traffic.providers.base import Sample
from traffic.providers.google_routes import GoogleRoutesProvider, parse_duration
from traffic.providers.here import HereFlowProvider, verify_speed_unit
from traffic.providers.tomtom import TomTomFlowProvider
from traffic.providers.waze import WazeLivemapProvider
from traffic.segments import SEGMENTS, actieve_segmenten, by_id
from traffic.store import ProbeStore

KYIV = by_id("kyiv_metro_bridge")
WARSAW = by_id("warsaw_vistula")

GOOGLE_LIVE = {
    "routes": [{
        "distanceMeters": 6120,
        "duration": "742s",
        "staticDuration": "600s",
        "polyline": {"encodedPolyline": "abc123"},
    }]
}
GOOGLE_STATISCH = {
    "routes": [{
        "distanceMeters": 6120,
        "duration": "600s",
        "staticDuration": "600s",
        "polyline": {"encodedPolyline": "abc123"},
    }]
}
GOOGLE_403 = {
    "error": {"code": 403, "message": "Routes API has not been used in project",
              "status": "PERMISSION_DENIED"}
}
TOMTOM_OK = {
    "flowSegmentData": {
        "frc": "FRC3", "currentSpeed": 38, "freeFlowSpeed": 47,
        "currentTravelTime": 42, "freeFlowTravelTime": 34,
        "confidence": 0.95, "roadClosure": False,
    }
}
HERE_OK = {
    "results": [
        {"location": {"length": 120, "traversability": "open"},
         "currentFlow": {"speed": 8.0, "freeFlow": 13.0, "jamFactor": 4.1,
                         "confidence": 0.92}},
        {"location": {"length": 900, "traversability": "open"},
         "currentFlow": {"speed": 6.0, "freeFlow": 12.0, "jamFactor": 6.5,
                         "confidence": 0.88}},
    ]
}


# ------------------------------------------------------------------ segmenten

def test_segmenten_zijn_uniek_en_compleet():
    ids = [s.id for s in SEGMENTS]
    assert len(ids) == len(set(ids)), "dubbele segment-id"
    steden = {s.city for s in SEGMENTS if not s.is_external_control}
    assert steden == {"Kyiv", "Lviv", "Odesa", "Kharkiv", "Dnipro"}
    # Elke Oekraïense stad moet minstens drie segmenten hebben, anders is
    # een uitspraak per stad niet te onderbouwen.
    for stad in steden:
        assert sum(1 for s in SEGMENTS if s.city == stad) >= 3, stad


def test_elk_segment_heeft_een_faalpunt_en_oblast():
    for s in SEGMENTS:
        assert s.crossing.strip(), f"{s.id} zonder faalpunt-motivatie"
        assert s.oblast.strip(), f"{s.id} zonder oblast"


def test_externe_controle_ligt_buiten_oekraine():
    for s in SEGMENTS:
        if s.is_external_control:
            assert s.country != "UA", f"{s.id} is geen externe controle"


def test_optionele_controles_staan_standaard_uit():
    actief = {s.id for s in actieve_segmenten()}
    assert "chisinau_center_airport" not in actief
    assert "chisinau_center_airport" in {s.id for s in actieve_segmenten(True)}


# ------------------------------------------------------------------ Google

@pytest.mark.parametrize("waarde,verwacht", [
    ("742s", 742.0), ("12.5s", 12.5), (60, 60.0), (None, None), ("kapot", None),
])
def test_parse_duration(waarde, verwacht):
    assert parse_duration(waarde) == verwacht


def test_google_parse_live():
    s = GoogleRoutesProvider().parse(KYIV, 200, json.dumps(GOOGLE_LIVE))
    assert s.ok
    assert s.duration_s == 742.0
    assert s.static_duration_s == 600.0
    assert s.distance_m == 6120
    assert s.polyline == "abc123"
    assert s.ratio == pytest.approx(742 / 600)


def test_google_parse_statisch_geeft_ratio_een():
    s = GoogleRoutesProvider().parse(KYIV, 200, json.dumps(GOOGLE_STATISCH))
    assert s.ok and s.ratio == pytest.approx(1.0)


def test_google_403_is_geen_geslaagde_sample_en_bewaart_ruw():
    s = GoogleRoutesProvider().parse(KYIV, 403, json.dumps(GOOGLE_403))
    assert not s.ok
    assert s.error_kind == "http"
    assert "PERMISSION_DENIED" in json.dumps(s.raw)
    assert s.ratio is None      # nooit een getal verzinnen bij een fout


def test_google_zonder_routes_is_leeg_niet_kapot():
    s = GoogleRoutesProvider().parse(KYIV, 200, json.dumps({"routes": []}))
    assert not s.ok and s.error_kind == "empty" and s.n_features == 0


def test_google_niet_json():
    s = GoogleRoutesProvider().parse(KYIV, 200, "<html>gateway</html>")
    assert not s.ok and s.error_kind == "parse" and s.raw.startswith("<html>")


def test_google_body_heeft_geen_departure_time():
    """Een departureTime in het verleden weigert de API voor DRIVE, en een
    toekomstige levert een historisch profiel. Weglaten = nu."""
    body = GoogleRoutesProvider().body(KYIV)
    assert "departureTime" not in body
    assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert body["travelMode"] == "DRIVE"
    assert body["origin"]["location"]["latLng"]["latitude"] == KYIV.origin[0]


def test_google_ontbrekende_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    s = GoogleRoutesProvider().sample(KYIV)
    assert not s.ok and s.error_kind == "no_key" and s.cost_usd == 0.0


# ------------------------------------------------------------------ TomTom / HERE / Waze

def test_tomtom_parse():
    s = TomTomFlowProvider().parse(KYIV, 200, json.dumps(TOMTOM_OK))
    assert s.ok
    assert s.duration_s == 42 and s.static_duration_s == 34
    assert s.ratio == pytest.approx(42 / 34)
    assert s.confidence == 0.95 and s.road_closure is False
    assert s.polyline is None, "TomTom levert geen route, dus geen omweg-signaal"


def test_tomtom_url_bevat_key_en_punt(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "geheim")
    url = TomTomFlowProvider().url(KYIV)
    assert "point=50.4522%2C30.599" in url and "key=geheim" in url


def test_here_kiest_langste_wegvak_en_rekent_om():
    s = HereFlowProvider().parse(KYIV, 200, json.dumps(HERE_OK))
    assert s.ok
    assert s.jam_factor == 6.5, "moet het wegvak van 900 m nemen, niet dat van 120 m"
    assert s.speed_kmh == pytest.approx(6.0 * 3.6)
    assert s.ratio == pytest.approx(12.0 / 6.0)


def test_here_eenheidscontrole_betrapt_verkeerde_aanname():
    assert "aanname klopt" in verify_speed_unit(13.0)
    assert "AANNAME FOUT" in verify_speed_unit(47.0)


def test_waze_leeg_is_het_verwachte_geval():
    s = WazeLivemapProvider().parse(KYIV, 200, json.dumps({"jams": []}))
    assert not s.ok and s.error_kind == "empty"


def test_waze_is_eenmalig_en_gratis():
    p = WazeLivemapProvider()
    assert p.alleen_eenmalig is True and p.unit_cost_usd == 0.0


# ------------------------------------------------------------------ dekkingsoordeel

def _rij(provider: str, segment_id: str, ratio: float | None, ok: bool = True, **extra):
    rij = {"provider": provider, "segment_id": segment_id, "ok": ok, "ratio": ratio}
    rij.update(extra)
    return rij


def test_verdict_live_vereist_beweging():
    rijen = [_rij("google_routes", "kyiv_metro_bridge", r)
             for r in (1.02, 1.24, 1.41, 1.09)]
    o = coverage.beoordeel(rijen, "Kyiv", "google_routes")
    assert o.verdict == coverage.LIVE
    assert o.bruikbaar


def test_divergentie_zonder_beweging_is_niet_live():
    """Vaste opslag van 3% op elke sample: dat is een modelverschil, geen verkeer."""
    rijen = [_rij("google_routes", "kyiv_metro_bridge", 1.03) for _ in range(8)]
    o = coverage.beoordeel(rijen, "Kyiv", "google_routes")
    assert o.verdict == coverage.DIVERGENT_STIL
    assert not o.bruikbaar


def test_verdict_alleen_statisch():
    rijen = [_rij("google_routes", "kyiv_metro_bridge", 1.0) for _ in range(6)]
    assert coverage.beoordeel(rijen, "Kyiv", "google_routes").verdict == coverage.ALLEEN_STATISCH


def test_verdict_geen_key_en_onbereikbaar():
    geen_key = [_rij("tomtom", "kyiv_metro_bridge", None, ok=False, error_kind="no_key")]
    assert coverage.beoordeel(geen_key, "Kyiv", "tomtom").verdict == coverage.GEEN_KEY
    blok = [_rij("tomtom", "kyiv_metro_bridge", None, ok=False, error_kind="egress",
                 error="uitgaande verbinding geblokkeerd")]
    assert coverage.beoordeel(blok, "Kyiv", "tomtom").verdict == coverage.ONBEREIKBAAR


def test_verdict_te_weinig_samples():
    rijen = [_rij("google_routes", "kyiv_metro_bridge", 1.4)]
    assert coverage.beoordeel(rijen, "Kyiv", "google_routes").verdict == coverage.TE_WEINIG


def test_geometrie_en_afstandsspreiding_worden_gemeten():
    rijen = [
        _rij("google_routes", "kyiv_metro_bridge", 1.1, polyline="a", distance_m=6000),
        _rij("google_routes", "kyiv_metro_bridge", 1.3, polyline="b", distance_m=7500),
        _rij("google_routes", "kyiv_metro_bridge", 1.2, polyline="b", distance_m=7500),
    ]
    o = coverage.beoordeel(rijen, "Kyiv", "google_routes")
    assert o.n_unieke_polyline == 2
    assert o.afstand_spreiding_pct == pytest.approx(25.0)


def test_poort_negeert_externe_controle():
    """Warschau op 'live' is geen reden om Fase 1 te beginnen."""
    oordelen = [
        coverage.Oordeel("Warsaw", "google_routes", coverage.LIVE),
        coverage.Oordeel("Kyiv", "google_routes", coverage.ALLEEN_STATISCH),
    ]
    open_, boodschap = coverage.poort(oordelen)
    assert not open_ and "dood signaal" in boodschap


def test_poort_open_bij_oekraiense_stad():
    oordelen = [coverage.Oordeel("Kyiv", "google_routes", coverage.LIVE)]
    open_, boodschap = coverage.poort(oordelen)
    assert open_ and "Kyiv" in boodschap


def test_poort_meldt_ontbrekende_keys_apart():
    oordelen = [coverage.Oordeel("Kyiv", "google_routes", coverage.GEEN_KEY)]
    open_, boodschap = coverage.poort(oordelen)
    assert not open_ and "geen enkele key" in boodschap


def test_dekkingstabel_rendert_alle_combinaties():
    oordelen = [
        coverage.Oordeel("Kyiv", "google_routes", coverage.LIVE),
        coverage.Oordeel("Kyiv", "tomtom", coverage.GEEN_KEY),
        coverage.Oordeel("Warsaw", "google_routes", coverage.LIVE),
    ]
    tabel = coverage.render_dekkingstabel(oordelen)
    assert "Kyiv" in tabel and "Warsaw" in tabel and "geen-key" in tabel
    assert "—" in tabel, "ontbrekende combinatie moet zichtbaar leeg zijn"


# ------------------------------------------------------------------ kosten

def test_kostenplafond_blokkeert_voor_de_aanroep():
    ledger = CostLedger(budget_usd=0.02)
    ledger.boek("google_routes", "Pro", 0.01)
    ledger.boek("google_routes", "Pro", 0.01)
    with pytest.raises(BudgetExceeded):
        ledger.check(0.01)
    assert ledger.resterend_usd == pytest.approx(0.0)


def test_kostenlogboek_is_hervatbaar(tmp_path):
    pad = tmp_path / "cost.ndjson"
    eerste = CostLedger(budget_usd=1.0, pad=pad)
    eerste.boek("google_routes", "Pro", 0.30)
    tweede = CostLedger(budget_usd=1.0, pad=pad)
    assert tweede.besteed_usd == pytest.approx(0.30)
    assert tweede.n_calls == 1


# ------------------------------------------------------------------ probe-runner

class NepProvider:
    """Provider die vaste samples teruggeeft, zonder netwerk."""

    name = "nep"
    label = "nep"
    requires_env = ()
    sku = "nep"
    unit_cost_usd = 0.01
    heeft_geometrie = True

    def __init__(self, ratio: float = 1.2):
        self.ratio = ratio
        self.n = 0

    def missing_config(self):
        return []

    def sample(self, segment):
        self.n += 1
        return Sample(provider=self.name, segment_id=segment.id, ts_utc="2026-08-13T00:00:00+00:00",
                      ok=True, duration_s=600 * self.ratio, static_duration_s=600,
                      distance_m=6000, polyline="p", raw={"nep": self.n})


def _runner(tmp_path, **kwargs) -> ProbeRunner:
    cfg = ProbeConfig(label="test", **kwargs)
    return ProbeRunner(cfg, ProbeStore("test", wortel=tmp_path))


def test_ruwe_respons_wordt_bewaard_voor_de_geparste_rij(tmp_path):
    runner = _runner(tmp_path, budget_usd=1.0)
    runner.ronde([NepProvider()], [KYIV, WARSAW], 1)
    ruw = runner.store.ruw_pad.read_text(encoding="utf-8").strip().splitlines()
    assert len(ruw) == 2
    assert json.loads(ruw[0])["raw"] == {"nep": 1}
    rijen = runner.store.lees_samples()
    assert len(rijen) == 2 and all(r["ronde"] == 1 for r in rijen)
    assert "raw" not in rijen[0], "ruwe payload hoort niet in de platte rij"


def test_run_stopt_op_budget_en_meldt_het(tmp_path):
    # 2 segmenten × $0,01 = $0,02 per ronde; plafond $0,03 laat één ronde toe.
    runner = _runner(tmp_path, budget_usd=0.03, duration_min=30, interval_min=10,
                     providers=("nep",))
    import traffic.probe as probe_mod
    monkey = probe_mod.prov_mod.maak
    probe_mod.prov_mod.maak = lambda namen: [NepProvider()]
    try:
        uitkomst = runner.run(log=lambda *_: None, sleep=lambda *_: None)
    finally:
        probe_mod.prov_mod.maak = monkey
    assert uitkomst["gestopt_om"] and "plafond" in uitkomst["gestopt_om"]
    assert uitkomst["besteed_usd"] <= 0.03
    assert runner.store.lees_staat()["afgerond"] is False


def test_run_is_hervatbaar(tmp_path):
    import traffic.probe as probe_mod
    origineel = probe_mod.prov_mod.maak
    probe_mod.prov_mod.maak = lambda namen: [NepProvider()]
    try:
        eerste = _runner(tmp_path, budget_usd=5.0, duration_min=10, interval_min=10,
                         providers=("nep",))
        eerste.run(log=lambda *_: None, sleep=lambda *_: None)
        n_na_eerste = len(eerste.store.lees_samples())

        tweede = _runner(tmp_path, budget_usd=5.0, duration_min=30, interval_min=10,
                         providers=("nep",))
        assert tweede.store.voltooide_rondes() == 1
        tweede.run(log=lambda *_: None, sleep=lambda *_: None)
        rijen = tweede.store.lees_samples()
        assert len(rijen) > n_na_eerste
        assert sorted({r["ronde"] for r in rijen}) == [1, 2, 3]
    finally:
        probe_mod.prov_mod.maak = origineel


def test_preflight_werkt_zonder_keys_en_noemt_wat_ontbreekt(tmp_path, monkeypatch):
    for naam in ("GOOGLE_MAPS_API_KEY", "TOMTOM_API_KEY", "HERE_API_KEY"):
        monkeypatch.delenv(naam, raising=False)
    monkeypatch.setattr("traffic.probe.bereikbaar", lambda url, timeout=10: (False, "egress"))
    tekst = _runner(tmp_path, budget_usd=10.0).preflight()
    assert "ONTBREEKT" in tekst and "GEBLOKKEERD" in tekst
    assert "GOOGLE_MAPS_API_KEY" in tekst
