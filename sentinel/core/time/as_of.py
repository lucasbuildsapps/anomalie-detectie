"""Point-in-time views: the only way production code reads data.

Why this module exists
----------------------
SENTINEL v1 treated causal correctness as a matter of discipline. Discipline
failed: three leakage paths survived in code whose own docstrings reason about
leakage (centred smoothing of the expectation, change-points detected over the
full series and then applied backwards, and calibration measured on the data
used to tune it).

So v2 does not rely on discipline. Every computation that feeds a production
output reads through an `AsOfView`, which physically cannot return data that
was unknown at its `as_of` instant. Backtest and production then run the same
code with a different timestamp, which is the only arrangement under which an
evaluation number describes production behaviour.

The two filters
---------------
An `AsOfView(t)` excludes a row unless **both** hold:

- ``ingested_at <= t`` — we could not use what had not arrived. This is what
  keeps late-arriving reporting out of a replay.
- ``timestamp <= t`` — a warning system at time *t* should not know about
  events dated after *t*, even if a source announced them in advance.

Honest reconstruction
---------------------
Historical bulk imports have no true arrival time. Those rows are stored with
``ingested_at = timestamp`` and ``ingest_estimated = True``. A replay over them
assumes zero reporting lag, which is optimistic. `AsOfView.provenance()`
reports exactly how much of the view rests on that assumption, so the
optimism is visible instead of implied. See ARCHITECTURE_V2.md §2.1.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

__all__ = [
    "AsOfView",
    "LeakageError",
    "PointInTimeStore",
    "Provenance",
    "assert_causal",
    "to_naive_utc",
]


class LeakageError(AssertionError):
    """Raised when a frame contains data that postdates its `as_of` instant.

    An assertion rather than a plain error: this signals a defect in the
    reading code, not a condition callers are expected to handle.
    """


def to_naive_utc(ts) -> datetime:
    """Any timestamp-like value to a naive-UTC ``datetime``.

    The storage layer keeps naive UTC throughout. Mixing aware and naive
    values is the classic way a comparison silently becomes wrong, so every
    boundary in this module normalises first.
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.to_pydatetime()


@dataclass(frozen=True)
class Provenance:
    """How faithful is this reconstruction of the past?

    `n_estimated` counts rows whose arrival time was assumed rather than
    observed. A view built entirely from estimated rows is still useful — it
    is how any replay over bulk-imported history has to work — but it answers
    "what could we have said if reporting were instant", not "what would we
    have said".
    """

    n_rows: int
    n_estimated: int

    @property
    def estimated_fraction(self) -> float:
        return (self.n_estimated / self.n_rows) if self.n_rows else 0.0

    @property
    def is_faithful(self) -> bool:
        """True when every row's arrival time was actually observed."""
        return self.n_rows > 0 and self.n_estimated == 0

    def describe(self) -> str:
        if self.n_rows == 0:
            return "No data known at this point in time."
        if self.is_faithful:
            return (f"{self.n_rows} rows, all with observed arrival times — "
                    f"a faithful reconstruction.")
        pct = self.estimated_fraction * 100
        return (
            f"{self.n_rows} rows, of which {self.n_estimated} ({pct:.0f}%) "
            f"have an assumed arrival time. Results for those rows describe "
            f"what could have been said under instant reporting, which is "
            f"optimistic by an unknown margin."
        )


#: Loader contract: ``(dataset_id, as_of) -> DataFrame`` already filtered to
#: what was known at ``as_of``. Injectable so that tests, and later the entity
#: layer, do not need a live database.
Loader = Callable[[int, datetime], pd.DataFrame]


def _default_loader(dataset_id: int, as_of: datetime) -> pd.DataFrame:
    # Imported lazily: this module must stay importable without a database,
    # and the import-boundary test asserts that core modules do not reach for
    # storage at module level.
    from core.storage import load_observations_as_of

    return load_observations_as_of(dataset_id, as_of)


#: Event loader contract: ``(dataset_id, as_of, region_key) -> DataFrame``.
EventLoader = Callable[[int, datetime, "str | None"], pd.DataFrame]


def _default_event_loader(dataset_id: int, as_of: datetime,
                          region_key: str | None = None) -> pd.DataFrame:
    from core.storage import load_entity_events_as_of

    return load_entity_events_as_of(dataset_id, as_of, region_key)


#: Position loader contract: ``(dataset_id, as_of, since, bbox) -> DataFrame``.
PositionLoader = Callable[..., pd.DataFrame]


def _default_position_loader(dataset_id: int, as_of: datetime,
                             since: datetime | None = None,
                             bbox: tuple | None = None) -> pd.DataFrame:
    from core.storage import load_positions_as_of

    return load_positions_as_of(dataset_id, as_of, since=since, bbox=bbox)


@dataclass(frozen=True)
class AsOfView:
    """A read-only window on the world as it was known at `as_of`.

    Immutable on purpose. A view that could be widened after construction
    would let a caller quietly acquire future data mid-computation, which is
    the failure this class exists to prevent.
    """

    as_of: datetime
    loader: Loader = _default_loader
    event_loader: EventLoader = _default_event_loader
    position_loader: PositionLoader = _default_position_loader

    def __post_init__(self) -> None:
        # frozen dataclass: assign through object.__setattr__
        object.__setattr__(self, "as_of", to_naive_utc(self.as_of))

    # -- reading ---------------------------------------------------------
    def observations(self, dataset_id: int) -> pd.DataFrame:
        """Observations known at `as_of`, verified causal before returning.

        The verification is deliberately redundant with the loader's own
        filtering. A loader is easy to get subtly wrong — an inclusive bound,
        a timezone mismatch — and this class is the guarantee the rest of the
        system relies on, so it checks rather than trusts.
        """
        df = self.loader(dataset_id, self.as_of)
        assert_causal(df, self.as_of)
        return df

    def events(self, dataset_id: int,
               region_key: str | None = None) -> pd.DataFrame:
        """Entity events known at `as_of`, verified causal before returning.

        The same guarantee as `observations`, and it matters more here: a
        derived event carries the time the *detector ran*, so a loiter that
        occurred on the 3rd but was only computed on the 9th must stay
        invisible to a replay dated the 5th. Otherwise the system is credited
        with foresight it did not have.
        """
        df = self.event_loader(dataset_id, self.as_of, region_key)
        assert_causal(df, self.as_of, time_col="event_time")
        return df

    def positions(self, dataset_id: int, since: datetime | None = None,
                  bbox: tuple | None = None) -> pd.DataFrame:
        """Position reports known at `as_of`, verified causal.

        The raw stream the entity primitives run over, and the source of the
        observed population that makes rarity testable at all.
        """
        df = self.position_loader(dataset_id, self.as_of, since, bbox)
        assert_causal(df, self.as_of)
        return df

    def provenance(self, dataset_id: int) -> Provenance:
        """How much of this view rests on assumed arrival times."""
        df = self.observations(dataset_id)
        if df.empty:
            return Provenance(n_rows=0, n_estimated=0)
        if "ingest_estimated" not in df.columns:
            # No provenance column at all: assume nothing is verified.
            return Provenance(n_rows=len(df), n_estimated=len(df))
        return Provenance(
            n_rows=len(df),
            n_estimated=int(df["ingest_estimated"].fillna(True).sum()),
        )

    # -- navigation ------------------------------------------------------
    def at(self, as_of) -> AsOfView:
        """A view on the same source at a different instant."""
        return AsOfView(as_of=to_naive_utc(as_of), loader=self.loader,
                        event_loader=self.event_loader,
                        position_loader=self.position_loader)

    def rewind(self, **delta) -> AsOfView:
        """A view further back in time, e.g. ``view.rewind(days=7)``."""
        return self.at(self.as_of - timedelta(**delta))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"AsOfView(as_of={self.as_of.isoformat()})"


def assert_causal(df: pd.DataFrame, as_of: datetime,
                  time_col: str = "timestamp",
                  arrival_col: str = "ingested_at") -> None:
    """Raise `LeakageError` if `df` contains anything unknown at `as_of`.

    Usable directly in tests on any frame, which is the point: the guarantee
    should be checkable from outside the class that provides it.
    """
    if df is None or df.empty:
        return
    boundary = to_naive_utc(as_of)
    for col in (time_col, arrival_col):
        if col not in df.columns:
            continue
        values = pd.to_datetime(df[col], errors="coerce")
        future = values > pd.Timestamp(boundary)
        n_future = int(future.sum())
        if n_future:
            worst = values[future].max()
            raise LeakageError(
                f"{n_future} row(s) have {col} after as_of "
                f"{boundary.isoformat()} (latest {worst.isoformat()}). "
                f"The reader returned data that was not available then."
            )


class PointInTimeStore:
    """Factory for `AsOfView`, and the entry point for replay.

    `replay()` is what makes evaluation meaningful: a backtest is not a
    separate code path with its own bugs, it is the production pipeline
    driven at a series of past instants.
    """

    def __init__(self, loader: Loader | None = None,
                 event_loader: EventLoader | None = None,
                 position_loader: PositionLoader | None = None) -> None:
        self.loader: Loader = loader or _default_loader
        self.event_loader: EventLoader = event_loader or _default_event_loader
        self.position_loader: PositionLoader = (
            position_loader or _default_position_loader)

    def view(self, as_of) -> AsOfView:
        return AsOfView(as_of=to_naive_utc(as_of), loader=self.loader,
                        event_loader=self.event_loader,
                        position_loader=self.position_loader)

    def now(self) -> AsOfView:
        return self.view(datetime.now(UTC).replace(tzinfo=None))

    def replay(self, start, end, step: timedelta | None = None,
               ) -> Iterator[AsOfView]:
        """Yield a view for each step from `start` to `end`, inclusive.

        Steps forward in time so that a caller accumulating results sees them
        in the order a live system would have produced them.
        """
        # `step or default` would be wrong: timedelta(0) is falsy, so a
        # zero step would silently become one day and loop forever-ish.
        step = timedelta(days=1) if step is None else step
        if step <= timedelta(0):
            raise ValueError("step must be positive; replay moves forward")
        current, last = to_naive_utc(start), to_naive_utc(end)
        if current > last:
            raise ValueError(
                f"start {current.isoformat()} is after end {last.isoformat()}"
            )
        while current <= last:
            yield self.view(current)
            current = current + step
