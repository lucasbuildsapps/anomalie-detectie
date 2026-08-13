"""Provider-register. Eén plek waar kanalen aan- en uitgezet worden."""
from __future__ import annotations

from traffic.providers.base import ProviderError, Sample, TrafficProvider
from traffic.providers.google_routes import GoogleRoutesProvider
from traffic.providers.here import HereFlowProvider
from traffic.providers.tomtom import TomTomFlowProvider
from traffic.providers.waze import WazeLivemapProvider

#: Naam → klasse. De probe kiest hieruit; de rest van de keten kent
#: alleen namen, zodat een kanaal met falende dekking uit één lijst valt.
REGISTER: dict[str, type[TrafficProvider]] = {
    "google_routes": GoogleRoutesProvider,
    "tomtom": TomTomFlowProvider,
    "here": HereFlowProvider,
    "waze": WazeLivemapProvider,
}


def maak(namen: list[str] | tuple[str, ...]) -> list[TrafficProvider]:
    onbekend = [n for n in namen if n not in REGISTER]
    if onbekend:
        raise ProviderError(
            f"onbekende provider(s): {', '.join(onbekend)}. "
            f"Bekend: {', '.join(REGISTER)}"
        )
    return [REGISTER[n]() for n in namen]


__all__ = ["REGISTER", "ProviderError", "Sample", "TrafficProvider", "maak"]
