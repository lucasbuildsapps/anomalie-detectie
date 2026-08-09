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

- The entity engine, which is what `nld_eez` is waiting on.
- Precomputed detection-power catalogues per region, so null results carry a
  measured floor in production rather than needing one passed in.
- A UI surface over `RegionStatus`.
