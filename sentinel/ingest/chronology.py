"""A curated incident chronology, read from a file.

This is the first real connector because it is the one the ground truth
depends on. A hand-curated chronology of major escalation events, drawn from
public reporting, is what lets retrospective validation say anything — and,
unlike an API client, it can be exercised end to end in a test.

Why a file and not a database table
-----------------------------------
The chronology is an *analytical artefact*: someone decided that an event
belongs in it, from which source, and on what date it became known. That
belongs in version control, next to the reference-period declaration it will
be argued about alongside, not in a mutable table where a silent edit changes
the meaning of every past assessment.

The column that matters most
----------------------------
`reported_at` — when the event became publicly known, which is usually not
when it happened. Without it the tool can only replay under an assumption of
instant reporting, which flatters every warning-time claim it will ever make.
It is optional because a curator will not always find a defensible date, but
its absence is recorded rather than papered over.

Expected columns
----------------
| column | required | meaning |
|---|---|---|
| `timestamp` | yes | when the event occurred |
| `reported_at` | no | when it became publicly known |
| `category` | no | event type, free text |
| `location_name` | no | place label |
| `lat`, `lon` | no | coordinates |
| `value` | no | magnitude; defaults to 1 (one event) |
| `summary` | no | one line of description, kept as an attribute |
| `source_url` | no | where the curator got it |
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from sentinel.core.contracts import Credibility, Reliability, Source

__all__ = ["CHRONOLOGY_SOURCE", "ChronologyConnector", "REQUIRED_COLUMNS"]

REQUIRED_COLUMNS = ("timestamp",)

#: Graded honestly: a curated secondary compilation, not a primary feed. The
#: grading is the curator's reliability, not that of whatever they read.
CHRONOLOGY_SOURCE = Source(
    key="curated_chronology",
    name="Curated incident chronology (public sources)",
    kind="chronology",
    reliability=Reliability.C,
    credibility=Credibility.C2,
    licence="compiled from public reporting; check per-entry source_url",
    redistribution_allowed=False,
)


@dataclass
class ChronologyConnector:
    """Reads a curated chronology from CSV or JSON.

    `since` is honoured against `reported_at` where present, falling back to
    event time. Filtering on event time alone would silently drop a
    late-reported event that arrived after the last run — exactly the case
    the arrival-time machinery exists to handle.
    """

    path: Path | str
    source: Source = CHRONOLOGY_SOURCE

    def fetch(self, since: datetime | None = None) -> pd.DataFrame:
        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(
                f"no chronology at {path}. This is a curated file, so an "
                f"absent one means nobody has written it yet — not that "
                f"nothing happened.")

        if path.suffix.lower() == ".json":
            records = json.loads(path.read_text() or "[]")
            frame = pd.DataFrame(records)
        else:
            frame = pd.read_csv(path)

        if frame.empty:
            return frame

        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(
                f"chronology {path} is missing required column(s): "
                f"{', '.join(missing)}")

        if since is not None:
            effective = pd.to_datetime(frame["timestamp"], errors="coerce",
                                       format="mixed")
            if "reported_at" in frame.columns:
                known = pd.to_datetime(frame["reported_at"], errors="coerce",
                                       format="mixed")
                effective = known.fillna(effective)
            frame = frame[effective >= pd.Timestamp(since)]

        return frame

    def describe(self) -> str:
        return f"{self.source.name} <- {self.path}"
