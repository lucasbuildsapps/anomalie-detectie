# sentinel/regions — incomplete, pending restoration

These four files were written immediately before the session container was
rebuilt, which destroyed six commits of `sentinel/` work (the architecture
document, `AsOfView` and the `ingested_at` migration, the evaluation harness,
the shared contracts, the dual baseline, and the confidence/detect layers).

**They do not import.** Every one of them depends on
`sentinel.core.contracts`, which no longer exists in this tree. They are
committed only so they are not lost a second time.

## To make this tree work again

The six lost commits were delivered as `git am`-applyable patches in
`sentinel-v2.zip` earlier in the session. Restore them first:

```bash
git checkout -b <branch> origin/main
git am patches/000*.patch      # the six lost commits
```

Then these region files become meaningful, and the remaining work is:

- `sentinel/regions/__init__.py` and a registry over the five modules
- `sentinel/regions/evaluate.py` — run a region's indicators, return
  `RegionStatus`
- `sentinel/core/detect/power.py` — a detection-power catalogue, so power is
  measured once per configuration rather than passed in by hand
- tests for all of the above

## What is here

| File | Contents |
|---|---|
| `base.py` | `RegionModule`, `GeoScope`. A region is configuration, not an application. Rejects an unmonitored region that does not declare its activation requirements. |
| `euro_atlantic.py` | Fully declared: three indicators (sustained tempo, single-period surge, reporting silence), two sources, a declared 2015–2021 reference period. |
| `nld_eez.py` | `DATA_ONLY`. Four indicators declared, three of them `entity_behaviour` and therefore not evaluable until the entity engine exists. Marking this region MONITORED would let a tab that cannot see the interesting cases present itself as watching for them. |
| `pending.py` | MENA, Indo-Pacific and Caribbean as `NOT_MONITORED` shells, each carrying the specific conditions that would activate it. |
