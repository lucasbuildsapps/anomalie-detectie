"""Google Routes API — computeRoutes.

Waarom dit het meest waarschijnlijke werkende kanaal is: Google zette in
februari 2022 op verzoek van de Oekraïense autoriteiten de live-
verkeerslaag en de drukte-informatie voor Oekraïne uit, maar verklaarde
publiek dat *navigeren naar een bestemming* nog steeds routes en
aankomsttijden geeft die met de actuele verkeerssituatie rekenen. Of dat
in de API-uitvoer meetbaar is, is precies de vraag van Fase 0.

Wat we meten:

- `duration`        — reistijd mét verkeer
- `staticDuration`  — reistijd zonder verkeer, op dezelfde route
- `distanceMeters`  — afstand (het omweg-signaal)
- `polyline`        — geometrie (verandert bij een omleiding)

`duration == staticDuration` bij elke sample betekent: geen levend
verkeerssignaal voor die stad.

Vastgestelde beperking, niet opnieuw uitzoeken: een `departureTime` in
het verleden wordt voor `DRIVE` geweigerd. Alleen `TRANSIT` accepteert
dat. Een toekomstige of weggelaten `departureTime` levert een schatting
uit historische gemiddelden (`trafficModel`) — bruikbaar als *baseline*,
nooit als waarneming van een nacht die al voorbij is. Daarom laten we
`departureTime` hier weg: dat betekent "nu".
"""
from __future__ import annotations

import json
import os

from traffic.providers.base import Sample, TrafficProvider, now_iso

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"

#: Alleen deze velden opvragen. De FieldMask is verplicht (zonder mask
#: antwoordt de API met een fout) en bepaalt mede de gefactureerde SKU.
FIELD_MASK = ",".join((
    "routes.duration",
    "routes.staticDuration",
    "routes.distanceMeters",
    "routes.polyline.encodedPolyline",
    "routes.routeLabels",
))


def parse_duration(waarde: str | int | float | None) -> float | None:
    """Google levert duur als protobuf-Duration-string: `"1234s"`.

    Kan een fractie bevatten (`"12.5s"`). Alles wat er niet op lijkt geeft
    None terug — expliciet ontbrekend, niet stilzwijgend 0.
    """
    if waarde is None:
        return None
    if isinstance(waarde, int | float):
        return float(waarde)
    tekst = str(waarde).strip()
    if tekst.endswith("s"):
        tekst = tekst[:-1]
    try:
        return float(tekst)
    except ValueError:
        return None


class GoogleRoutesProvider(TrafficProvider):
    name = "google_routes"
    label = "Google Routes (computeRoutes)"
    requires_env = ("GOOGLE_MAPS_API_KEY",)
    sku = "Compute Routes Pro"
    #: TRAFFIC_AWARE_OPTIMAL valt in de Pro-SKU. Zie traffic/pricing.py.
    unit_cost_usd = 0.010
    heeft_geometrie = True

    def __init__(self, routing_preference: str = "TRAFFIC_AWARE_OPTIMAL") -> None:
        self.routing_preference = routing_preference

    def body(self, segment) -> dict:  # noqa: ANN001
        olat, olon = segment.origin
        dlat, dlon = segment.destination
        return {
            "origin": {"location": {"latLng": {"latitude": olat, "longitude": olon}}},
            "destination": {"location": {"latLng": {"latitude": dlat, "longitude": dlon}}},
            "travelMode": "DRIVE",
            # Geen departureTime: dat betekent "nu" en levert de actuele
            # verkeersschatting in plaats van een historisch profiel.
            "routingPreference": self.routing_preference,
            "computeAlternativeRoutes": False,
            "polylineQuality": "OVERVIEW",
            "languageCode": "en-US",
            "units": "METRIC",
        }

    def sample(self, segment) -> Sample:  # noqa: ANN001
        from traffic.providers.base import http_call
        if self.missing_config():
            return self._no_key(segment)
        headers = {
            "X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"],
            "X-Goog-FieldMask": FIELD_MASK,
        }
        try:
            status, tekst = http_call(ENDPOINT, method="POST", headers=headers,
                                      body=self.body(segment), timeout=30)
        except Exception as e:
            return self._fout(segment, e)
        return self.parse(segment, status, tekst)

    def parse(self, segment, status: int, tekst: str) -> Sample:  # noqa: ANN001
        """Antwoord → Sample. Gescheiden van het netwerk zodat het te testen is."""
        basis = dict(provider=self.name, segment_id=segment.id, ts_utc=now_iso(),
                     http_status=status, cost_usd=self.unit_cost_usd)
        try:
            payload = json.loads(tekst)
        except json.JSONDecodeError:
            return Sample(**basis, ok=False, error_kind="parse",
                          error=f"geen geldige JSON: {tekst[:160]}", raw=tekst)

        if status != 200:
            boodschap = (payload.get("error", {}) or {}).get("message", tekst[:160])
            return Sample(**basis, ok=False, error_kind="http",
                          error=f"HTTP {status}: {boodschap}", raw=payload)

        routes = payload.get("routes") or []
        if not routes:
            # Geen route gevonden is een echt antwoord, geen fout: bewaren
            # als lege waarneming (bijv. weg volledig afgesloten).
            return Sample(**basis, ok=False, error_kind="empty",
                          error="antwoord zonder routes", n_features=0, raw=payload)

        route = routes[0]
        return Sample(
            **basis, ok=True,
            duration_s=parse_duration(route.get("duration")),
            static_duration_s=parse_duration(route.get("staticDuration")),
            distance_m=route.get("distanceMeters"),
            polyline=(route.get("polyline") or {}).get("encodedPolyline"),
            n_features=len(routes), raw=payload,
        )
