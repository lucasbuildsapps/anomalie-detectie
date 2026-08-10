"""Getting outside data in, with its arrival time intact.

The point of this layer is `ingested_at`. Everything the point-in-time
machinery claims rests on when we *learned* something, not when it happened,
and a replay built on guessed arrivals answers a flattering question.
"""
from sentinel.ingest.base import (
    Connector,
    IngestResult,
    normalise,
    run_ingest,
)
from sentinel.ingest.chronology import CHRONOLOGY_SOURCE, ChronologyConnector

__all__ = [
    "CHRONOLOGY_SOURCE",
    "ChronologyConnector",
    "Connector",
    "IngestResult",
    "normalise",
    "run_ingest",
]
