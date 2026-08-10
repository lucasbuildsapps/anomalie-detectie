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

## Network connectors, and what "tested" means for them

None of the declared sources are reachable from the environment this was
written in — the proxy refuses `CONNECT` to `web.ais.dk`, `api.acleddata.com`
and `aisstream.io` alike. That constraint shapes the design rather than
excusing its absence:

**Transport is a seam, not a layer.** `transport.py` is a dozen lines of
`urlopen` behind an injectable protocol. Everything that decides what the data
*means* — column mapping, timestamp format, sentinel values, filtering,
arrival times — lives in the connector and is tested against fixtures. A
connector written as one function that fetches, parses and stores can only be
tested by talking to the source, which in practice means it is never tested.

**The DMA parser has never seen a live file.** Its column mapping comes from
the published format description. That is a real risk, handled rather than
hidden: matching is by normalised column name so cosmetic header changes do
not break it, and a missing required column raises `SchemaError` naming what
was wanted and what arrived. There is deliberately no positional fallback — a
shifted column order would then be read as valid data. The first real run is
a legible failure if the format has moved, not silent garbage.

## What is here

| File | Contents |
|---|---|
| `base.py` | `Connector` protocol, `IngestResult`, `normalise`, `run_ingest`, `run_position_ingest`. |
| `transport.py` | `Fetcher` protocol and `UrllibFetcher`. The only code here that cannot be tested offline. |
| `chronology.py` | `ChronologyConnector` — curated incident chronology from CSV or JSON. |
| `dma_ais.py` | `DmaAisConnector` — Danish Maritime Authority daily AIS archives into `positions`. |

### Why DMA first

Of the declared sources it is the only one that needs no API key and permits
redistribution, and it is the one that unblocks something: the entity engine,
its peer baselines, the population denominator and a measured loiter floor are
all built and running on a synthetic fleet. What they lack is real tracks.

The arrival time is the archive's publication date, not the transmission time.
The file for the 3rd appears on the 4th, so the 4th is when the tool could have
known it; recording transmission time would claim instant access and flatter
every replay that crosses it.

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

## Not built, and why

**aisstream.io.** A live WebSocket feed needing an API key, with no historical
archive. It would add a `websockets` dependency and a long-running process to
supervise, and none of it could be exercised here — a key-gated live socket
cannot be fixture-tested the way a daily archive can. It is also the wrong
order: DMA history is what validates the methodology, and aisstream only ever
records forward. Worth building once there is a key and DMA has proved the
pipeline on real tracks.

**ACLED.** Needs registration, and its licence forbids redistribution — which
is a decision about the deployment, not a coding task. Euro-Atlantic currently
runs on the demo dataset; swapping in ACLED changes what may be stored and
shared, so it should be a deliberate choice rather than a connector that
quietly appears.

**Kystverket (Norwegian AIS).** Same shape as DMA. Worth adding once the DMA
mapping has been confirmed against a real file, because the two will likely
share most of the parsing and confirming one first avoids writing the same
unverified assumption twice.

**An `ingest_run` table.** Results are returned and printed, not persisted. For
a single maintainer running a script this is enough; it stops being enough as
soon as runs are scheduled and nobody watches them.
