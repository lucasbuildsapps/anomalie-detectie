# Declared infrastructure corridors

`nld_eez.json` ships as `[]` — empty, on purpose, exactly like the chronology.
Where a cable actually runs is published information, but coordinates this tool
invented would be indistinguishable from coordinates someone verified, and an
analyst would have no way to tell which they were relying on.

A corridor is an **analytical declaration**: someone decided this line, with
this buffer, from this source. That belongs in version control next to the
reference-period declaration, reviewable in a diff, rather than in a mutable
table where a silent edit changes the meaning of every past assessment.

## Format

```json
[
  {
    "key": "cable_example",
    "kind": "cable",
    "vertices": [[54.10, 3.20], [54.05, 4.40], [53.90, 5.10]],
    "buffer_m": 2000,
    "source_url": "https://example.org/where-you-got-this",
    "attrs": {"operator": "…", "note": "…"}
  }
]
```

| field | required | meaning |
|---|---|---|
| `key` | **yes** | stable identifier; appears on every event as `area_key` |
| `kind` | no | `cable`, `pipeline`, `wind_farm`, … free text |
| `vertices` | **yes** | `[lat, lon]` in order, at least two |
| `buffer_m` | no | how close counts as near; defaults to 2000 |
| `source_url` | no | where the route came from — the thing that makes it checkable |
| `attrs` | no | anything else; carried onto the event |

## Why `buffer_m` is per corridor

A wind-farm boundary and a deep-water cable do not warrant the same margin. A
single global buffer would be too tight for one and too loose for the other,
and the resulting events would be wrong in opposite directions with nothing to
show which.

## What a proximity event does and does not mean

It means a vessel came within the declared buffer of a declared corridor.
Nothing else. Passing over a cable is what the North Sea is *for* — the
shipping lanes and the cable routes cross constantly — which is why
`loiter_near_infrastructure` pairs proximity with *loitering* rather than
alerting on proximity alone.

Adding corridors here therefore sharpens that indicator rather than creating a
new source of alerts: it changes "a cargo vessel stopped somewhere in the EEZ"
into "a cargo vessel stopped on a cable", which is a much narrower and much
more interesting question.

## Effect on the measured floor

The loiter floor in `config/detection_power.json` is measured **without**
corridors, since the synthetic fleet has none. Declaring real corridors makes
the indicator strictly more specific, so the floor stays a conservative bound —
but if you add corridors and want the number to describe what you are actually
running, re-run `scripts/precompute_power.py`.
