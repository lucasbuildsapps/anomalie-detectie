"""TomTom Traffic Flow — flowSegmentData.

Onafhankelijke tweede mening over de dekking. Meet op wegvak-niveau bij
één punt: actuele snelheid tegen free-flow-snelheid, plus de bijbehorende
reistijden. Geen route, dus **geen omweg-signaal** — dit kanaal kan een
gesloten brug niet als geometriewijziging zien.

Let op bij het lezen van de dekkingstabel: TomTom antwoordt óók als er
geen realtime-invoer is; dan is `currentSpeed` simpelweg gelijk aan
`freeFlowSpeed`. Een geldig antwoord is dus geen bewijs van een levend
signaal. Het `confidence`-veld en variatie over tijd zijn dat wel.
"""
from __future__ import annotations

import json
import os
import urllib.parse

from traffic.providers.base import Sample, TrafficProvider, now_iso

BASE = "https://api.tomtom.com/traffic/services/4/flowSegmentData"

#: 'absolute' geeft de snelheden ongeschaald terug; zoom 10 ligt op
#: stads-arterie-niveau (hoger = fijner wegvak, maar vaker geen data).
STYLE = "absolute"
ZOOM = 10


class TomTomFlowProvider(TrafficProvider):
    name = "tomtom"
    label = "TomTom Traffic Flow (flowSegmentData)"
    requires_env = ("TOMTOM_API_KEY",)
    sku = "Traffic Flow non-tile request"
    #: Gratis staffel 2.500 non-tile requests/dag; daarboven ~$0,50/1.000.
    unit_cost_usd = 0.0005
    heeft_geometrie = False

    def url(self, segment) -> str:  # noqa: ANN001
        """Meetpunt = de herkomst van het segment.

        Bewust de herkomst en niet het midden: het punt moet stabiel zijn
        over de hele meetreeks, en een herkomst is een expliciet gekozen
        knooppunt terwijl een middelpunt met de route mee kan schuiven.
        """
        lat, lon = segment.origin
        params = urllib.parse.urlencode({
            "point": f"{lat},{lon}",
            "unit": "KMPH",
            "openLr": "false",
            "key": os.environ["TOMTOM_API_KEY"],
        })
        return f"{BASE}/{STYLE}/{ZOOM}/json?{params}"

    def sample(self, segment) -> Sample:  # noqa: ANN001
        from traffic.providers.base import http_call
        if self.missing_config():
            return self._no_key(segment)
        try:
            status, tekst = http_call(self.url(segment), timeout=30)
        except Exception as e:
            return self._fout(segment, e)
        return self.parse(segment, status, tekst)

    def parse(self, segment, status: int, tekst: str) -> Sample:  # noqa: ANN001
        basis = dict(provider=self.name, segment_id=segment.id, ts_utc=now_iso(),
                     http_status=status, cost_usd=self.unit_cost_usd)
        try:
            payload = json.loads(tekst)
        except json.JSONDecodeError:
            return Sample(**basis, ok=False, error_kind="parse",
                          error=f"geen geldige JSON: {tekst[:160]}", raw=tekst)
        if status != 200:
            return Sample(**basis, ok=False, error_kind="http",
                          error=f"HTTP {status}: {tekst[:160]}", raw=payload)

        blok = payload.get("flowSegmentData") or {}
        if not blok:
            return Sample(**basis, ok=False, error_kind="empty",
                          error="antwoord zonder flowSegmentData", n_features=0, raw=payload)

        huidig = blok.get("currentTravelTime")
        vrij = blok.get("freeFlowTravelTime")
        return Sample(
            **basis, ok=True,
            duration_s=float(huidig) if huidig is not None else None,
            static_duration_s=float(vrij) if vrij is not None else None,
            speed_kmh=blok.get("currentSpeed"),
            free_flow_kmh=blok.get("freeFlowSpeed"),
            confidence=blok.get("confidence"),
            road_closure=blok.get("roadClosure"),
            n_features=1, raw=payload,
        )
