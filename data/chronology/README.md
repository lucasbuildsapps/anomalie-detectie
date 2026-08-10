# Curated incident chronology

`euro_atlantic.csv` ships **empty except for its header**, deliberately. It is
an analytical artefact: someone decides that an event belongs in it, from which
source, and on what date it became known. Nobody but the analyst maintaining
the region can make those calls, and a file pre-filled with plausible-looking
entries would be indistinguishable from one that had been curated — which is
the worst of both worlds.

It lives in version control rather than in a table so that a change to it shows
up in a diff, next to the reference-period declaration it will be argued about
alongside. A silent edit in a mutable table would change the meaning of every
past assessment without leaving a trace.

## Columns

| column | required | meaning |
|---|---|---|
| `timestamp` | **yes** | when the event occurred |
| `reported_at` | no, but see below | when it became publicly known |
| `category` | no | event type, free text |
| `location_name` | no | place label |
| `lat`, `lon` | no | coordinates |
| `value` | no | magnitude; defaults to 1, meaning one event |
| `summary` | no | one line of description |
| `source_url` | no | where you got it |

Any other column you add is kept as an attribute rather than dropped, so a
finding stays traceable back to what you read.

## `reported_at` is the one that matters

It is when the event became *publicly known*, which is usually not when it
happened. Without it the tool can only replay the past under an assumption of
instant reporting — which flatters every warning-time claim it will ever make,
by exactly the lag you did not record.

With it, a replay dated two days after an event correctly shows nothing if the
event was not reported until the fourth day. That is the difference between
"what could we have said" and "what would we have said", and it is the whole
reason the point-in-time machinery exists.

Leave it blank when you cannot find a defensible date. The row is still
ingested, still counted, and marked as having an assumed arrival — the tool
reports the proportion rather than hiding it, and confidence is capped for
findings that rest on it.

## Loading it

```bash
python scripts/ingest_chronology.py --dataset <id> --path data/chronology/euro_atlantic.csv
```

Re-running is safe. Rows are deduplicated on content, and a row already held
keeps its original arrival time — the first time you saw it *is* the moment you
knew it.

## What to put in it

Major escalation events and significant developments, not a complete event
database. The chronology exists to give retrospective validation something
independent to check against, so breadth matters less than each entry being
defensible and dated from a public source you can point at.
