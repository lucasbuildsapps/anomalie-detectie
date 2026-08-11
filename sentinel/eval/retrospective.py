"""Retrospective validation: warning time against events we did not inject.

The synthetic harness measures what the system *can* detect. This measures
whether it *did* — replaying the region as it would have looked on each past
date and asking, for each event in a curated chronology, whether an indicator
was already active before it happened, and by how long.

Why the number this produces is weaker than it looks
----------------------------------------------------
Everything here has to be read against four limits, and the report states all
four rather than leaving them to a footnote nobody reads:

**Sample size.** A curated chronology of major escalations holds tens of
events, not thousands. A detection rate over twelve events has a confidence
interval wide enough to cover most claims anyone would want to make, so the
report refuses to quote a rate below `min_events` and says why.

**Selection bias.** The chronology is written by the same person tuning the
system. Not fraud — an honest curator still picks events they consider
significant, and significance is correlated with visibility in the data. The
synthetic harness has no such loop, which is why it stays primary.

**Arrival fidelity.** If the replay rests on assumed arrival times, every
warning time here is optimistic by the unrecorded reporting lag. The report
carries that flag rather than averaging over it.

**Warning time is not lead time on intent.** An indicator going active before
an event means the *measured activity* shifted first. It does not establish
that the shift was preparation for the event, and a chronology cannot settle
that question.

**Indicator coverage.** `evaluate_region` now selects indicators by whether
they were watching at the replayed instant, not by today's status — otherwise
an indicator written last month would be credited with warnings from two years
ago. Indicators with no declared activation date cannot be placed that way and
are assumed to have been watching throughout; the report says how many.

None of this makes the measurement worthless. It makes it a check on the
synthetic floors rather than a replacement for them: if the system detects
1.5x sustained increases in simulation but never fires before a real
escalation, one of the two is wrong, and that disagreement is the finding.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sentinel.core.contracts import Verdict

__all__ = [
    "RetrospectiveReport",
    "TruthEvent",
    "WarningResult",
    "events_from_chronology",
    "replay",
]


@dataclass(frozen=True)
class TruthEvent:
    """Something that happened, from an independent chronology."""

    key: str
    occurred_at: datetime
    label: str = ""
    known_at: datetime | None = None
    category: str | None = None

    def __post_init__(self) -> None:
        if self.known_at is not None and self.known_at < self.occurred_at:
            raise ValueError(
                f"event {self.key!r} claims to have been known before it "
                f"happened; that is a curation error, not a scoop")


@dataclass(frozen=True)
class WarningResult:
    """Whether anything was active before one event, and how far before."""

    event: TruthEvent
    first_alert_at: datetime | None = None
    indicator_key: str | None = None

    @property
    def detected(self) -> bool:
        return self.first_alert_at is not None

    @property
    def warning(self) -> timedelta | None:
        if self.first_alert_at is None:
            return None
        return self.event.occurred_at - self.first_alert_at

    @property
    def warning_days(self) -> float | None:
        warning = self.warning
        return None if warning is None else warning.total_seconds() / 86400.0

    def describe(self) -> str:
        if not self.detected:
            return f"{self.event.key}: no indicator was active beforehand."
        return (f"{self.event.key}: {self.indicator_key} was active "
                f"{self.warning_days:.0f} days before.")


@dataclass(frozen=True)
class RetrospectiveReport:
    """Warning performance over a chronology, with its own limits attached."""

    results: tuple[WarningResult, ...]
    n_alert_dates: int = 0
    n_unattributed_alerts: int = 0
    min_events: int = 8
    arrival_faithful: bool | None = None
    #: Active indicators with no declared activation date. Their coverage in
    #: this replay is assumed rather than known.
    undated_indicators: tuple[str, ...] = field(default_factory=tuple)
    lead_window_days: int = 90
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_events(self) -> int:
        return len(self.results)

    @property
    def detected(self) -> tuple[WarningResult, ...]:
        return tuple(r for r in self.results if r.detected)

    @property
    def is_quotable(self) -> bool:
        """Whether a detection rate may be stated at all.

        Below `min_events` the interval around any rate is wide enough to
        cover almost any claim, and a fraction printed without one invites
        exactly the over-reading this system exists to prevent.
        """
        return self.n_events >= self.min_events

    @property
    def detection_rate(self) -> float | None:
        if not self.is_quotable or not self.n_events:
            return None
        return len(self.detected) / self.n_events

    @property
    def median_warning_days(self) -> float | None:
        days = [r.warning_days for r in self.detected]
        return statistics.median(days) if days else None

    def describe(self) -> str:
        if not self.n_events:
            return ("No chronology events fell in the replay window, so "
                    "nothing was validated. This is not a passing result.")

        lines = []
        if self.is_quotable:
            lines.append(
                f"{len(self.detected)} of {self.n_events} chronology events "
                f"had an indicator active in the {self.lead_window_days} days "
                f"before them ({self.detection_rate:.0%}).")
        else:
            lines.append(
                f"{len(self.detected)} of {self.n_events} chronology events "
                f"had an indicator active beforehand. Too few events to quote "
                f"a rate — at least {self.min_events} are needed before the "
                f"fraction means anything.")

        median = self.median_warning_days
        if median is not None:
            lines.append(f"Median warning time, where there was one: "
                         f"{median:.0f} days.")

        if self.n_alert_dates:
            lines.append(
                f"{self.n_unattributed_alerts} of {self.n_alert_dates} alert "
                f"dates were not within the window before any chronology "
                f"event. A curated chronology is not a complete record of "
                f"what happened, so these are unattributed rather than wrong.")

        if self.undated_indicators:
            lines.append(
                f"{len(self.undated_indicators)} indicator(s) have no declared "
                f"activation date, so this replay assumes they were watching "
                f"throughout. Where they were in fact written later, the "
                f"warning times they contribute are credit the system did not "
                f"earn.")

        if self.arrival_faithful is False:
            lines.append(
                "The replay rests on assumed arrival times, so every warning "
                "time above is optimistic by the unrecorded reporting lag.")

        lines.append(
            "The chronology is curated by the same person tuning the system, "
            "so these numbers check the synthetic floors rather than replace "
            "them.")
        lines.extend(self.notes)
        return " ".join(lines)


def events_from_chronology(path, category: str | None = None,
                           ) -> tuple[TruthEvent, ...]:
    """Load truth events from the curated chronology file.

    `known_at` comes from `reported_at` where the curator recorded it. It is
    carried but not used to score: an event is scored against when it
    *happened*, because that is what a warning has to precede.
    """
    import pandas as pd

    from sentinel.ingest import ChronologyConnector

    frame = ChronologyConnector(path).fetch()
    if frame.empty:
        return ()

    if category is not None and "category" in frame.columns:
        frame = frame[frame["category"] == category]

    events = []
    for i, row in enumerate(frame.to_dict("records")):
        occurred = pd.to_datetime(row["timestamp"], errors="coerce")
        if pd.isna(occurred):
            continue
        known = pd.to_datetime(row.get("reported_at"), errors="coerce")
        events.append(TruthEvent(
            key=str(row.get("summary") or f"event_{i}")[:60],
            occurred_at=occurred.to_pydatetime(),
            label=str(row.get("summary") or ""),
            known_at=None if pd.isna(known) else known.to_pydatetime(),
            category=row.get("category"),
        ))
    return tuple(events)


def replay(evaluate_at, events, start: datetime, end: datetime, *,
           step: timedelta | None = None,
           lead_window: timedelta | None = None,
           min_events: int = 8,
           arrival_faithful: bool | None = None,
           undated_indicators: tuple[str, ...] = ()) -> RetrospectiveReport:
    """Walk the past date by date and record when indicators were active.

    `evaluate_at` is a callable ``as_of -> iterable of Signal``. Injected
    rather than assembled here so this runs without a database, and so it
    cannot accidentally read data the point-in-time view would have hidden —
    the caller keeps that discipline, as everywhere else in this codebase.

    Only alerts *inside* `lead_window` before an event count as warnings. An
    indicator that fired eight months earlier and stayed quiet since did not
    warn about this event; counting it would let a system that alerts
    constantly claim credit for everything that followed.
    """
    step = timedelta(days=7) if step is None else step
    lead_window = timedelta(days=90) if lead_window is None else lead_window
    if step <= timedelta(0):
        raise ValueError("step must be positive, or the replay never advances")

    events = tuple(events)

    # as_of -> the indicators that were active then.
    active_by_date: list[tuple[datetime, str]] = []
    as_of = start
    n_dates = 0
    while as_of <= end:
        n_dates += 1
        for signal in evaluate_at(as_of):
            if signal.verdict is Verdict.ACTIVE:
                active_by_date.append((as_of, signal.indicator_key))
        as_of += step

    results = []
    attributed: set[datetime] = set()
    for event in events:
        window_start = event.occurred_at - lead_window
        in_window = [
            (when, key) for when, key in active_by_date
            if window_start <= when < event.occurred_at
        ]
        if in_window:
            first_at, indicator_key = min(in_window, key=lambda pair: pair[0])
            attributed.update(when for when, _ in in_window)
            results.append(WarningResult(event, first_at, indicator_key))
        else:
            results.append(WarningResult(event))

    alert_dates = {when for when, _ in active_by_date}
    return RetrospectiveReport(
        results=tuple(results),
        n_alert_dates=len(alert_dates),
        n_unattributed_alerts=len(alert_dates - attributed),
        min_events=min_events,
        arrival_faithful=arrival_faithful,
        undated_indicators=tuple(undated_indicators),
        lead_window_days=int(lead_window.total_seconds() // 86400),
    )
