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
| `nld_eez.py` | `DATA_ONLY`. Four indicators, all four with a measured and resolved floor, all four running end to end on a synthetic fleet. It stays `DATA_ONLY` because there is no AIS feed — marking it `MONITORED` would let a tab with no data present itself as watching. |
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
- ~~An observed-entity population.~~ **Built.** The `positions` table supplies
  it: `population_from_view` returns every entity that emitted a position,
  including the silent majority that did nothing. Rarity is testable again, so
  an entity indicator can now reach ACTIVE or a genuine null instead of
  reporting magnitude-only insufficiency.

  The two providers are handed out **together** by `entity_providers`, and
  that is deliberate. Participation is *entities that did the thing / entities
  observed*. Scope the numerator to 30 days and leave the denominator at
  all-time and every behaviour looks rare — a vessel seen three years ago and
  never since still counts as observed. That drags participation under the
  rarity threshold and produces false positives in precisely the direction
  peer baselines exist to prevent. One `window_days` applies to both or to
  neither, so the mismatch is not expressible.
- ~~Identity resolution.~~ **Built** as a *kinematic* check
  (`sentinel/entity/identity.py`): one identifier reported where no single
  hull could have travelled. Floor measured at **40 km** for a ten-minute
  cadence, zero false alarms.

  Deliberately not built: the static-field version — same identifier, changed
  name, IMO or callsign. It is easy and mostly *legitimate*, since reflagging
  and renaming happen constantly, so a detector on it would spend its life
  reporting paperwork. The kinematic version cannot be explained that way:
  either the position is wrong or the identity is.
- **A live AIS feed.** This is why the region is `DATA_ONLY` rather than
  `MONITORED`: the machinery now exists, the data does not.
- **Per-vessel expected routes.** `detect_route_deviation` takes one route
  for a whole call, so applying a transit lane to a vessel that was never on
  it produces meaningless deviations. Real use needs an expected route per
  vessel, or none.
- ~~Position storage.~~ **Built**, and deliberately *not* as the roadmap
  described it. No PostGIS and no partitioning:

  - Nothing in this codebase performs a real spatial query. `entity/geo.py`
    computes haversine and cross-track offset without a geometry stack, and
    the population denominator is a `SELECT DISTINCT`. A hard PostGIS
    dependency would break the SQLite path the whole suite runs on, in
    exchange for nothing used today.
  - Partitioning answers a volume problem, and there is no volume — there is
    no live feed. Partitioning an empty table is a guess about a load nobody
    has measured.

  Where PostGIS *will* earn its place: when an indicator asks "within this
  corridor" rather than "within this box". Cable corridors are lines with a
  buffer; `bbox` filtering cannot express them, and
  `loiter_near_infrastructure` is exactly that question.

  `scripts/derive_events.py` closes the loop — positions in, typed events out,
  written back through the same point-in-time discipline.
