"""Eén interface voor alle verkeersproviders.

De keten moet een kanaal kunnen laten vallen zodra de dekking faalt, dus
weet niets buiten dit bestand welke provider het antwoord gaf. Iedere
provider levert `Sample`-objecten; een mislukte aanroep levert óók een
Sample, met `ok=False` en een reden. Een ontbrekende waarneming blijft
ontbreken — er wordt nooit geïnterpoleerd of teruggevallen op een
model-schatting alsof het een meting was.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

USER_AGENT = "SENTINEL-traffic/0.1 (onderzoek: verkeersimpact luchtaanvallen)"

#: Onderscheid dat je in een zandbak nodig hebt: "de provider weigert" is
#: iets anders dan "onze uitgaande verbinding is geblokkeerd".
EGRESS_MARKERS = ("tunnel connection failed", "connect tunnel failed",
                  "proxy", "name or service not known")


class ProviderError(RuntimeError):
    """Harde fout: verkeerde configuratie, of een antwoord dat niet klopt."""


@dataclass
class Sample:
    """Eén waarneming van één provider voor één segment op één moment.

    Twee families velden, want de providers meten iets anders:

    - **route-niveau** (Google Routes): `duration_s` versus
      `static_duration_s`, plus afstand en geometrie. Hier zit de omweg
      in `distance_m` en `polyline`.
    - **wegvak-niveau** (TomTom, HERE): actuele snelheid versus
      free-flow-snelheid op één punt. Geen geometrie, dus geen omweg.

    `ratio` normaliseert beide naar dezelfde grootheid: waargenomen
    reistijd gedeeld door reistijd zonder verkeer. 1.0 = niets aan de
    hand, 1.4 = 40% langer onderweg.
    """

    provider: str
    segment_id: str
    ts_utc: str
    ok: bool
    # route-niveau
    duration_s: float | None = None
    static_duration_s: float | None = None
    distance_m: int | None = None
    polyline: str | None = None
    # wegvak-niveau
    speed_kmh: float | None = None
    free_flow_kmh: float | None = None
    jam_factor: float | None = None
    confidence: float | None = None
    road_closure: bool | None = None
    # boekhouding
    n_features: int | None = None
    http_status: int | None = None
    error: str | None = None
    error_kind: str | None = None      # 'no_key' | 'egress' | 'http' | 'parse' | 'empty'
    cost_usd: float = 0.0
    raw: Any = field(default=None, repr=False)

    @property
    def ratio(self) -> float | None:
        """Waargenomen reistijd / reistijd zonder verkeer, of None."""
        if self.duration_s and self.static_duration_s:
            return self.duration_s / self.static_duration_s
        if self.speed_kmh and self.free_flow_kmh and self.speed_kmh > 0:
            return self.free_flow_kmh / self.speed_kmh
        return None

    def to_row(self) -> dict[str, Any]:
        """Platte rij voor NDJSON. `raw` gaat naar de ruwe opslag, niet hierin."""
        rij = {k: v for k, v in self.__dict__.items() if k != "raw"}
        rij["ratio"] = self.ratio
        return rij


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify_error(exc: Exception) -> tuple[str, str]:
    """(error_kind, boodschap) — scheidt zandbak-blokkade van providerfout."""
    tekst = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return "http", f"HTTP {exc.code}: {tekst}"
    laag = tekst.lower()
    if any(m in laag for m in EGRESS_MARKERS):
        return "egress", f"uitgaande verbinding geblokkeerd: {tekst}"
    return "http", tekst


def http_call(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
              body: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    """Eén HTTP-aanroep, zonder retry.

    Bewust géén retry-lus hier: de probe bepaalt zelf of hij het opnieuw
    probeert, want elke poging kost geld en moet in het kostenlogboek
    landen. Een 4xx/5xx komt terug als (status, tekst) in plaats van als
    exceptie, zodat het foutantwoord ruw bewaard kan worden.
    """
    kop = {"User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        kop.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=kop, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


class TrafficProvider:
    """Basisklasse. Eén provider = één kanaal in de dekkingstabel.

    Verplicht: `name`, `sku`, `unit_cost_usd` (prijs per aanroep, zie
    `traffic/pricing.py`) en `sample()`.
    """

    name: str = "?"
    label: str = "?"
    #: Env-vars die de provider nodig heeft. Leeg = geen key.
    requires_env: tuple[str, ...] = ()
    #: Naam van de SKU waarop dit geboekt wordt (voor het kostenlogboek).
    sku: str = "?"
    #: Prijs per aanroep in USD, buiten de gratis staffel.
    unit_cost_usd: float = 0.0
    #: Levert deze provider routegeometrie (en dus een omweg-signaal)?
    heeft_geometrie: bool = False

    def missing_config(self) -> list[str]:
        import os
        return [k for k in self.requires_env if not os.environ.get(k, "").strip()]

    def sample(self, segment) -> Sample:  # noqa: ANN001 — Segment, cyclische import
        raise NotImplementedError

    # --- hulpstukken voor de subklassen ---

    def _no_key(self, segment) -> Sample:  # noqa: ANN001
        return Sample(
            provider=self.name, segment_id=segment.id, ts_utc=now_iso(), ok=False,
            error=f"ontbrekende key: {', '.join(self.missing_config())}",
            error_kind="no_key",
        )

    def _fout(self, segment, exc: Exception) -> Sample:  # noqa: ANN001
        soort, boodschap = classify_error(exc)
        return Sample(provider=self.name, segment_id=segment.id, ts_utc=now_iso(),
                      ok=False, error=boodschap, error_kind=soort)
