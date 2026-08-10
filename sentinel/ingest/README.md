# sentinel/ingest

The point of this layer is one column: `ingested_at`.

Everything the point-in-time machinery claims rests on when we *learned*
something, not when it happened. Phase 1 built the column and backfilled it
with `ingest_estimated = 1`; this layer is where it gets a truthful value.

## Three arrival cases, one of them faithful

| case | what happens | faithful? |
|---|---|---|
| source states when it published | that becomes `ingested_at` | **yes** |
| bulk history, no publication dates | falls back to event time, every row marked estimated | no |
| live tailing | arrival is now, and now is the truth | yes |

The layer never picks silently. `IngestResult` carries how many rows landed
with observed arrivals, so a dataset that is entirely estimated cannot present
itself later as a faithful replay — `Provenance` already knows how to say that,
and this is what feeds it.

A replay built on guessed arrivals answers *"what could we have said if
reporting were instant"*. That is a useful question and a flattering one, and
it is not the question a warning system is judged on.

## Why the first connector reads a file

The curated chronology is what retrospective validation checks against, and
unlike an API client it can be exercised end to end in a test. Building an
untested network client would have produced code that looks finished and is
not. The network connectors are a deliberately empty slot.

## What is here

| File | Contents |
|---|---|
| `base.py` | `Connector` protocol, `IngestResult`, `normalise`, `run_ingest`. |
| `chronology.py` | `ChronologyConnector` — curated incident chronology from CSV or JSON. |

## Design notes

**A connector only fetches.** It does not decide where rows are stored or when
to run. v1's data path was hard to reason about because fetching, parsing and
persisting were one function.

**A failing source is a finding, not a crash.** `run_ingest` never raises on
source failure: in a multi-source region the other feeds are still worth
collecting, and an indicator that goes quiet because nothing arrived should be
able to point at the reason.

**A publication date before its own event is rejected.** It is a data error,
not a scoop, and letting it through would place a row in the past of its own
occurrence and corrupt every replay that crosses it.

**Duplicates are counted, not hidden.** A run that fetches thousands and
inserts nothing is either correctly idempotent or silently broken, and the two
are indistinguishable without the number.

## Not built

- Network connectors (ACLED, aisstream.io, Danish/Norwegian AIS). The protocol
  is there; the clients are not, and stubbing them would be worse than their
  absence.
- An `ingest_run` table. Results are returned and printed, not persisted. For a
  single maintainer running a script this is enough; it stops being enough as
  soon as runs are scheduled and nobody watches them.
