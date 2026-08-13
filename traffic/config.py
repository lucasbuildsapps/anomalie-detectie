"""Configuratie en secrets voor de verkeersketen.

Keys staan uitsluitend in env-vars of in een niet-gecommitte `.env`
(zie `.env.example`). Nooit in code, nooit in de repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Wortel van de repo (dit bestand zit in traffic/).
ROOT = Path(__file__).resolve().parent.parent

#: Alle probe-uitvoer landt hier. Ruwe responses eerst, geparste rijen daarna.
DATA_DIR = ROOT / "data" / "traffic"

#: Tijdzone van de waarnemingen. Alle opslag is UTC; dit is puur voor
#: het afleiden van lokale uren (spits/off-peak, avondklok).
LOCAL_TZ = "Europe/Kyiv"


def load_dotenv(path: Path | None = None) -> list[str]:
    """Laadt `.env` in os.environ zonder bestaande vars te overschrijven.

    Bewust minimaal (geen python-dotenv-afhankelijkheid): `KEY=value`,
    `#`-commentaar, optionele aanhalingstekens. Geeft de gezette namen
    terug zodat een preflight kan melden wat er vandaan komt.
    """
    path = path or (ROOT / ".env")
    if not path.exists():
        return []
    gezet: list[str] = []
    for regel in path.read_text(encoding="utf-8").splitlines():
        regel = regel.strip()
        if not regel or regel.startswith("#") or "=" not in regel:
            continue
        naam, _, waarde = regel.partition("=")
        naam, waarde = naam.strip(), waarde.strip().strip("'\"")
        if naam and waarde and not os.environ.get(naam):
            os.environ[naam] = waarde
            gezet.append(naam)
    return gezet


@dataclass(frozen=True)
class ProbeConfig:
    """Instellingen van één probe-run.

    `budget_usd` is een harde grens: de kostenteller weigert een aanroep
    die het plafond zou doorbreken, in plaats van hem te doen en het
    daarna te melden.
    """

    budget_usd: float = 10.0
    interval_min: int = 10
    duration_min: int = 180
    providers: tuple[str, ...] = ("google_routes", "tomtom", "here")
    include_waze: bool = False
    include_optional_controls: bool = False
    label: str = "probe"

    @property
    def n_rondes(self) -> int:
        """Aantal sample-rondes; altijd minimaal 1 (ook bij duration 0)."""
        return max(1, self.duration_min // max(1, self.interval_min))


def env_or_none(naam: str) -> str | None:
    waarde = os.environ.get(naam, "").strip()
    return waarde or None
