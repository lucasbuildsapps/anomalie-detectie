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
    run_position_ingest,
)
from sentinel.ingest.chronology import CHRONOLOGY_SOURCE, ChronologyConnector
from sentinel.ingest.dma_ais import (
    DMA_SOURCE,
    DmaAisConnector,
    SchemaError,
    parse_dma_csv,
)
from sentinel.ingest.transport import Fetcher, FetchError, UrllibFetcher

__all__ = [
    "CHRONOLOGY_SOURCE",
    "DMA_SOURCE",
    "DmaAisConnector",
    "FetchError",
    "Fetcher",
    "SchemaError",
    "UrllibFetcher",
    "parse_dma_csv",
    "run_position_ingest",
    "ChronologyConnector",
    "Connector",
    "IngestResult",
    "normalise",
    "run_ingest",
]
