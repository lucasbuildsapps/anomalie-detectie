"""Ingestion: getting outside data in, with its arrival time intact.

The load-bearing question in this layer is not *what* arrived but *when we
learned it*. Everything the point-in-time machinery does rests on
`ingested_at`, and a replay built on guessed arrivals answers "what could we
have said if reporting were instant" rather than "what would we have said".
Phase 1 built the column; this layer is where it gets a truthful value.

Three arrival cases, and only one of them is faithful
-----------------------------------------------------
**The source states when it published.** Use that. This is the only case that
produces a faithful reconstruction, and it is why the chronology format below
asks for a `reported_at` column rather than treating one as a nicety.

**Bulk history with no publication dates.** Fall back to event time and mark
every row estimated. Useful — it is how any backfill has to work — but it
flatters the system, so it is recorded rather than assumed.

**Live tailing.** Arrival is now, and now is the truth.

What this layer refuses to do is pick silently. `IngestResult` carries how
many rows landed with observed arrivals, so a dataset that is 100% estimated
cannot present itself as a faithful replay later on — `Provenance` already
knows how to say that, and this is what feeds it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import pandas as pd

from sentinel.core.contracts import Source

__all__ = [
    "ArrivalPolicy",
    "Connector",
    "IngestResult",
    "normalise",
    "run_ingest",
]

#: How `ingested_at` is filled when the source does not state it.
#: Deliberately the same vocabulary as `core.storage.ARRIVAL_POLICIES`.
ArrivalPolicy = str

#: Columns the shared observation contract understands. Anything else a
#: connector emits is kept as an extra attribute rather than dropped — a
#: source's own identifiers are what make a finding traceable back.
_CORE_COLUMNS = ("timestamp", "value", "category", "location_name",
                 "lat", "lon")


class Connector(Protocol):
    """Anything that can produce observations for one source.

    Kept to a single method on purpose. A connector that also decides where
    rows are stored, or when to run, becomes untestable without the thing it
    is talking to — and the reason v1's data path was hard to reason about is
    that fetching, parsing and persisting were the same function.
    """

    source: Source

    def fetch(self, since: datetime | None = None) -> pd.DataFrame:
        """Return raw rows for this source. Network or disk lives here."""
        ...


@dataclass(frozen=True)
class IngestResult:
    """What one run of one connector actually did."""

    source_key: str
    started_at: datetime
    finished_at: datetime
    n_fetched: int = 0
    n_inserted: int = 0
    n_with_observed_arrival: int = 0
    error: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def n_duplicate(self) -> int:
        """Rows already held. Not a failure — re-running is how backfills work.

        Worth surfacing rather than hiding: a run that fetches thousands and
        inserts nothing is either correctly idempotent or silently broken, and
        the two look identical without this number.
        """
        return max(self.n_fetched - self.n_inserted, 0)

    @property
    def arrival_is_faithful(self) -> bool:
        return (self.n_inserted > 0
                and self.n_with_observed_arrival == self.n_inserted)

    def describe(self) -> str:
        if not self.ok:
            return f"{self.source_key}: failed — {self.error}"
        if self.n_fetched == 0:
            return f"{self.source_key}: nothing to fetch."
        text = (f"{self.source_key}: {self.n_fetched} fetched, "
                f"{self.n_inserted} new, {self.n_duplicate} already held.")
        if self.n_inserted and not self.arrival_is_faithful:
            estimated = self.n_inserted - self.n_with_observed_arrival
            text += (f" {estimated} of the new rows have an assumed arrival "
                     f"time, so replays before today are optimistic for them.")
        return text


def normalise(df: pd.DataFrame, source: Source,
              arrival: ArrivalPolicy = "event_time") -> pd.DataFrame:
    """Raw connector output -> the shared observation shape.

    Does not invent an arrival time. If the frame carries `reported_at`, that
    becomes `ingested_at` and the row counts as observed; otherwise the column
    is left off entirely and the storage layer's declared policy applies, which
    is what marks the row estimated.
    """
    if df.empty:
        return df

    out = df.copy()
    if "timestamp" not in out.columns:
        raise ValueError(
            f"connector for {source.key!r} produced no 'timestamp' column; "
            f"an observation without an event time cannot be placed in a "
            f"series or replayed")

    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce",
                                      utc=False, format="mixed")
    dropped = int(out["timestamp"].isna().sum())
    out = out[out["timestamp"].notna()]

    if "reported_at" in out.columns:
        reported = pd.to_datetime(out["reported_at"], errors="coerce",
                                  utc=False, format="mixed")
        # A publication date before the event it reports is a data error, not
        # a scoop. Letting it through would place the row in the past of its
        # own occurrence and quietly corrupt every replay that crosses it.
        invalid = reported.notna() & (reported < out["timestamp"])
        reported = reported.mask(invalid)
        out = out.drop(columns=["reported_at"])
        if reported.notna().any():
            out["ingested_at"] = reported

    if "value" not in out.columns:
        out["value"] = 1.0

    if dropped:
        out.attrs["dropped_unparseable_timestamps"] = dropped
    return out


def run_ingest(connector: Connector, dataset_id: int, *,
               since: datetime | None = None,
               arrival: ArrivalPolicy = "event_time",
               insert=None) -> IngestResult:
    """Fetch, normalise and store one source. Never raises on source failure.

    A connector that throws must not take the run down with it: in a
    multi-source region the other feeds are still worth collecting, and a
    failed source is a finding of its own — an indicator that goes quiet
    because nothing arrived should be able to point at the reason.
    """
    started = datetime.now(UTC).replace(tzinfo=None)

    def _finish(**kwargs) -> IngestResult:
        return IngestResult(
            source_key=connector.source.key, started_at=started,
            finished_at=datetime.now(UTC).replace(tzinfo=None), **kwargs)

    try:
        raw = connector.fetch(since)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return _finish(error=f"{type(exc).__name__}: {exc}")

    if raw is None or raw.empty:
        return _finish(n_fetched=0)

    try:
        frame = normalise(raw, connector.source, arrival=arrival)
    except Exception as exc:  # noqa: BLE001
        return _finish(n_fetched=len(raw), error=f"{type(exc).__name__}: {exc}")

    notes = []
    dropped = frame.attrs.get("dropped_unparseable_timestamps", 0)
    if dropped:
        notes.append(f"{dropped} row(s) had an unreadable timestamp and were "
                     f"skipped")

    observed = (int(frame["ingested_at"].notna().sum())
                if "ingested_at" in frame.columns else 0)

    if insert is None:
        from core.storage import insert_observations as insert
    n_inserted = int(insert(dataset_id, frame, arrival=arrival))

    # Observed arrivals are counted against what was fetched, so the ratio
    # cannot exceed what actually landed.
    return _finish(
        n_fetched=len(frame), n_inserted=n_inserted,
        n_with_observed_arrival=min(observed, n_inserted),
        notes=tuple(notes),
    )
