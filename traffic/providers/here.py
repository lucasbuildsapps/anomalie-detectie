"""HERE Traffic API v7 — flow.

Derde kanaal voor de dekkingstabel. Ook wegvak-niveau, dus ook geen
omweg-signaal. HERE levert per wegvak een `jamFactor` (0 = vrij, 10 =
stilstand) en een `confidence` die aangeeft of de waarde uit realtime
metingen komt of uit een historisch/voorspeld profiel. Die confidence is
hier het belangrijkste veld: een hoge jamFactor met lage confidence is
een model, geen meting.

**Eenheid nog niet empirisch bevestigd**: de v7-documentatie is vanuit
deze omgeving niet op te vragen (uitgaande verbinding geblokkeerd, zie
PHASE0.md). De snelheden komen naar verwachting in meter per seconde;
`SPEED_NAAR_KMH` zet ze om. Bij de eerste echte aanroep moet dit tegen de
ruwe respons gecontroleerd worden — een stadsarterie met free-flow van
"13" is m/s, "47" is km/h. `verify_speed_unit()` doet die controle en
schreeuwt als de aanname niet klopt.
"""
from __future__ import annotations

import json
import os
import urllib.parse

from traffic.providers.base import Sample, TrafficProvider, now_iso

ENDPOINT = "https://data.traffic.hereapi.com/v7/flow"

#: Aangenomen eenheid m/s → km/h. Zie de module-docstring: te verifiëren.
SPEED_NAAR_KMH = 3.6

#: Zoekradius rond het meetpunt, in meters.
RADIUS_M = 500


def verify_speed_unit(free_flow_ruw: float | None) -> str:
    """Plausibiliteitscontrole op de eenheid van de snelheidsvelden.

    Geen exacte wetenschap, maar wel genoeg om een factor 3,6 te betrappen
    voordat die als 'geen verkeer' door de analyse walst.
    """
    if free_flow_ruw is None:
        return "onbekend"
    if 3 <= free_flow_ruw <= 45:
        return "m/s (aanname klopt)"
    if 45 < free_flow_ruw <= 160:
        return "km/h — AANNAME FOUT, omrekening uitzetten"
    return f"onverwachte grootte: {free_flow_ruw}"


class HereFlowProvider(TrafficProvider):
    name = "here"
    label = "HERE Traffic v7 (flow)"
    requires_env = ("HERE_API_KEY",)
    sku = "Traffic Flow transaction"
    #: 250k gratis transacties/maand; daarboven ~$1,00/1.000.
    unit_cost_usd = 0.001
    heeft_geometrie = False

    def url(self, segment) -> str:  # noqa: ANN001
        lat, lon = segment.origin
        params = urllib.parse.urlencode({
            "in": f"circle:{lat},{lon};r={RADIUS_M}",
            "locationReferencing": "shape",
            "apiKey": os.environ["HERE_API_KEY"],
        })
        return f"{ENDPOINT}?{params}"

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

        resultaten = payload.get("results") or []
        if not resultaten:
            return Sample(**basis, ok=False, error_kind="empty",
                          error="antwoord zonder results", n_features=0, raw=payload)

        # Het langste wegvak in de cirkel is representatiever dan het eerste:
        # korte stukjes zijn vaak op- en afritten met eigen ruis.
        def lengte(res: dict) -> float:
            return float((res.get("location") or {}).get("length") or 0)

        beste = max(resultaten, key=lengte)
        stroom = beste.get("currentFlow") or {}
        snelheid = stroom.get("speed")
        vrij = stroom.get("freeFlow")
        return Sample(
            **basis, ok=True,
            speed_kmh=snelheid * SPEED_NAAR_KMH if snelheid is not None else None,
            free_flow_kmh=vrij * SPEED_NAAR_KMH if vrij is not None else None,
            jam_factor=stroom.get("jamFactor"),
            confidence=stroom.get("confidence"),
            road_closure=(beste.get("location") or {}).get("traversability") == "closed",
            n_features=len(resultaten), raw=payload,
        )
