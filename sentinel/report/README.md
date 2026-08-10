# sentinel/report

Turns a `Signal` into words. The detect layer decides **what is true**; this
layer decides **how to say it**, and it is not allowed to do anything else.

## Three rules

**It never changes a verdict.** A composer that could soften or sharpen a
finding would be a second truth model wearing a prose costume — the thing
retiring v1's voting scheme was meant to end.

**It never asserts intent.** A vessel that stopped over a cable did that; why
is not in the data. Statements describe behaviour or level, and follow-ups are
collection tasks rather than conclusions.

**It declines to quote a probability it cannot compute.** `effect_size` is not
one scale:

| Test | What `effect_size` is | Probability? |
|---|---|---|
| `level_deviation` | standardised deviation | yes |
| `sustained_divergence` | standardised deviation from the reference | yes |
| `condition:above/below` | raw distance from a threshold | no |
| `condition:silence` | a count of periods | no |
| `entity_behaviour` | a peer ranking score | no |

A normal tail over all five produces confident-looking numbers for three of
them that mean nothing. The estimative band is the most quotable thing the
product emits, and a number on the wrong scale gets quoted anyway.

## Every verdict gets written product

Including the boring ones. A null result and an untestable indicator are
findings an analyst has to be able to read and cite; if only alarming outcomes
reach the page, the system trains its readers to expect alarm.

```
Reporting silence in euro_atlantic could not be tested as of 2026-08-10:
the feed delivered no records for the last 9 days.
Baseline: no comparison was possible, so this is not a statement about
whether anything is happening.
No judgement was reached, so this carries no confidence in either direction.
(Input quality was rated high.)
```

That last line replaced a plain "Confidence is high", which under a sentence
saying nothing could be tested read as *we are confident nothing happened*.

## Alternatives are not boilerplate

The contract requires an active assessment to carry a competing explanation.
A generic "could be something else" would satisfy it while informing nobody,
turning the requirement into a rubber stamp. So the defaults are specific to
the test type, and a signal that worked out why *it* might be wrong overrides
them — the test knows more than a table keyed on test type does.

## What is here

| File | Contents |
|---|---|
| `assess.py` | `compose` (one signal) and `compose_region` (the periodic product). |
