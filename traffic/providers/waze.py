"""Waze livemap — eenmalige dekkingscontrole, daarna laten vallen.

Verwachting: leeg. Google zette live verkeer op Waze in vergelijkbare
conflictsituaties tegelijk met Maps uit en voegde de twee productteams in
2022 samen. Deze module bestaat om die verwachting te *controleren* in
plaats van hem aan te nemen, want dat is het verschil tussen een
vastgestelde en een aangenomen dekking.

**Uitdrukkelijk voorbehoud.** Het livemap-endpoint is geen
gedocumenteerde API. De keten mag niet op ongedocumenteerde kanalen
leunen — dat is een van de non-goals — dus deze provider staat standaard
uit (`--include-waze` om hem aan te zetten), doet één ronde, en het
resultaat gaat alleen de dekkingstabel in. Als de uitkomst 'live' zou
zijn, is de juiste vervolgstap een gedocumenteerd kanaal zoeken, niet
hier gaan verzamelen.
"""
from __future__ import annotations

import json
import urllib.parse

from traffic.providers.base import Sample, TrafficProvider, now_iso

ENDPOINT = "https://www.waze.com/live-map/api/georss"

#: Halve zijde van de bounding box rond het segment, in graden (~5 km).
MARGE_GRADEN = 0.05


class WazeLivemapProvider(TrafficProvider):
    name = "waze"
    label = "Waze livemap (ongedocumenteerd — alleen dekkingscontrole)"
    requires_env = ()
    sku = "geen (ongedocumenteerd endpoint)"
    unit_cost_usd = 0.0
    heeft_geometrie = False
    #: Deze provider hoort niet in een verzamel-loop; één ronde en klaar.
    alleen_eenmalig = True

    def url(self, segment) -> str:  # noqa: ANN001
        lats = [segment.origin[0], segment.destination[0]]
        lons = [segment.origin[1], segment.destination[1]]
        params = urllib.parse.urlencode({
            "top": max(lats) + MARGE_GRADEN,
            "bottom": min(lats) - MARGE_GRADEN,
            "left": min(lons) - MARGE_GRADEN,
            "right": max(lons) + MARGE_GRADEN,
            "env": "row",
            "types": "traffic,alerts",
        })
        return f"{ENDPOINT}?{params}"

    def sample(self, segment) -> Sample:  # noqa: ANN001
        from traffic.providers.base import http_call
        try:
            status, tekst = http_call(self.url(segment), timeout=30)
        except Exception as e:
            return self._fout(segment, e)
        return self.parse(segment, status, tekst)

    def parse(self, segment, status: int, tekst: str) -> Sample:  # noqa: ANN001
        basis = dict(provider=self.name, segment_id=segment.id, ts_utc=now_iso(),
                     http_status=status, cost_usd=0.0)
        try:
            payload = json.loads(tekst)
        except json.JSONDecodeError:
            return Sample(**basis, ok=False, error_kind="parse",
                          error=f"geen geldige JSON: {tekst[:160]}", raw=tekst)
        if status != 200:
            return Sample(**basis, ok=False, error_kind="http",
                          error=f"HTTP {status}: {tekst[:160]}", raw=payload)

        jams = payload.get("jams") or []
        if not jams:
            return Sample(**basis, ok=False, error_kind="empty",
                          error="geen jams in de bounding box", n_features=0, raw=payload)

        # Zwaarste file als representant; 'delay' is in seconden, 'level' 0-5.
        zwaarste = max(jams, key=lambda j: float(j.get("delay") or 0))
        vertraging = float(zwaarste.get("delay") or 0)
        return Sample(
            **basis, ok=True,
            duration_s=vertraging if vertraging > 0 else None,
            jam_factor=float(zwaarste.get("level") or 0) * 2.0,   # 0-5 → 0-10, als HERE
            speed_kmh=zwaarste.get("speedKMH"),
            n_features=len(jams), raw=payload,
        )
