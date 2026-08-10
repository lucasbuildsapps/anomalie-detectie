# sentinel/regions

A region is **configuration, not an application**. Five tabs must not become
five codebases, so a region declares what it watches, where, against which
reference, and from which sources — and `evaluate_region` runs all of them
through the same engine. If a region cannot be expressed without changing
core, that is a design conversation rather than a patch.

## The honest empty tab

`MonitoringStatus` is carried on the module itself and is not optional.

A region panel with no alerts reads as "quiet". For an unmonitored region the
truth is "nobody is looking" — that is the null-result problem promoted from
the alert level to the region level, and it is the most misleading thing this
product could do. So an unmonitored region must declare what activating it
would require, and `RegionStatus.headline()` refuses to conflate the states:

```
Euro-Atlantic: 1 of 3 indicators active — strike_tempo_sustained.
Netherlands EEZ: data collected, no indicators defined. Nothing is being
  tested, so nothing can be reported.
Indo-Pacific: not monitored. Requires a parser for the Taiwan MND daily
  activity releases; an agreed reference period for pre-escalation ADIZ
  activity.
```

The same rule applies one level further in: a monitored region whose every
indicator returned insufficient data is **not** quiet either, and says so.

## What is here

| File | Contents |
|---|---|
| `base.py` | `RegionModule`, `GeoScope`. Rejects an unmonitored region that does not declare its activation requirements, and a monitored one that defines no indicators. |
| `evaluate.py` | `evaluate_region` — one entry point for every region. Data arrives through a provider callable, so regions never touch storage and the caller keeps the point-in-time discipline. |
| `euro_atlantic.py` | Fully declared and `MONITORED`: sustained tempo, single-period surge, reporting silence, against a declared 2015–2021 reference. |
| `nld_eez.py` | `DATA_ONLY`. Four indicators declared, three of them `entity_behaviour` and so not evaluable until the entity engine exists. Marking it `MONITORED` would let a tab that cannot see the interesting cases present itself as watching for them. |
| `pending.py` | MENA, Indo-Pacific and Caribbean as `NOT_MONITORED` shells, each carrying the conditions that would activate it. |

## Still to build

The entity engine's *primitives* now exist (`sentinel/entity/`): they turn
positions into `loiter`, `ais_gap` and `route_deviation` events. What is
missing between those and an activated NLD EEZ:

- ~~A peer baseline.~~ **Built** (`sentinel/entity/peers.py`) and wired into
  the `entity_behaviour` test. On a synthetic fleet of 102 vessels — 40
  trawlers that loiter by trade, 60 cargo that do not, 2 cargo that stopped
  on a cable corridor — it flags 2, both targets, no trawlers.
- ~~A measured detection floor for entity behaviour.~~ **Measured** for
  loitering (`sentinel/eval/entity_power.py`) and committed to
  `config/detection_power.json`: a loiter of **1 hour or longer** is detected
  in 80% of runs, with no trawlers flagged. It carries a condition that
  matters more than the number — detection depends on the behaviour staying
  rare within its class, and **collapses to zero at ~12% prevalence**. Not a
  slope, a cliff, and a silent one: the output is a clean null result while
  the thing the capability was built to catch becomes common. The remedy is
  the same one the series layer already uses — a *declared* reference for
  what participation historically was, so a rise in participation is itself
  the signal — and it is **not built**.
  The floor covers loitering only. The catalogue declines to quote a number
  for the AIS-gap and identity indicators rather than lending them one from a
  behaviour nobody measured.
- ~~A production path for entity indicators.~~ **Built.** `entity_events` is a
  real table with the same point-in-time columns as `observations`;
  `AsOfView.events()` reads it under the same causal guarantee; and
  `evaluate_region(..., event_provider=...)` assembles events and a peer
  baseline into the *same* `evaluate_indicator` call every count indicator
  uses. Positions → typed events → storage → verdict is covered end to end in
  `tests/test_entity_pipeline.py`.
- **An observed-entity population.** This is the live gap, and it is the one
  that keeps the NLD EEZ indicators honest rather than useful. Rarity needs a
  denominator of *every vessel observed*, including the silent majority that
  did nothing. Entity events only record vessels that did something, so
  without a population participation is `1.0` by construction, only magnitude
  can ever flag, and rarity — the primary signal — is never tested.
  `evaluate_region` takes a `population_provider` for exactly this; nothing
  supplies one yet, because that requires position storage. Until then the
  entity test returns **insufficient data** rather than calling a
  magnitude-only pass quiet. It fails closed, and it says so.
- **Identity resolution.** The `identity_swap` scenario currently produces no
  events; nothing yet compares broadcast identifiers across a track.
- **A live AIS feed.** This is why the region is `DATA_ONLY` rather than
  `MONITORED`: the machinery now exists, the data does not.
- **Per-vessel expected routes.** `detect_route_deviation` takes one route
  for a whole call, so applying a transit lane to a vessel that was never on
  it produces meaningless deviations. Real use needs an expected route per
  vessel, or none.
- **Position storage.** PostGIS plus a partitioned position table. The
  primitives run on frames and need no database, but a live AIS feed does —
  and it is also where the observed-entity population above has to come from,
  which makes it the highest-value item left on this list.
