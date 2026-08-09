#!/usr/bin/env python
"""Measure detection power for every configured indicator, once.

Run: python scripts/precompute_power.py [--output config/detection_power.json]

Why this is a script and not something the app does on demand
-------------------------------------------------------------
Measuring one configuration costs roughly eighty baseline fits. Doing that
inside a live evaluation would make an analyst wait minutes for a number that
never changes between runs — and would perversely make a quiet region slower
to load than a busy one. So it is precomputed and committed.

The output is what lets a null result say something. Without an entry here,
`evaluate_indicator` returns INSUFFICIENT_DATA rather than claiming quiet:
the system will not assert that nothing is happening until it has measured
what it would have caught.

Re-run this whenever a threshold, a baseline default, or the divergence
configuration changes. The numbers describe a configuration, so a change to
any of those invalidates them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.core.detect.power import (  # noqa: E402
    DetectionPowerCatalog,
    production_detector,
)
from sentinel.regions import REGIONS  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "config" / \
    "detection_power.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repeats", type=int, default=8,
                        help="draws per magnitude; more is slower but steadier")
    parser.add_argument("--force", action="store_true",
                        help="re-measure configurations already cached")
    args = parser.parse_args()

    catalog = (DetectionPowerCatalog(n_repeats=args.repeats) if args.force
               else DetectionPowerCatalog.load(args.output,
                                               n_repeats=args.repeats))

    # Every declared indicator, including drafts: knowing the power of an
    # indicator before activating it is exactly when the number is useful.
    indicators = [i for region in REGIONS for i in region.indicators]
    measurable = [i for i in indicators
                  if i.test_type.value in ("level_deviation",
                                           "sustained_divergence")]

    print(f"{len(indicators)} indicators declared, "
          f"{len(measurable)} measurable "
          f"(conditions are deterministic and need no measurement)")

    measured = 0
    for indicator in measurable:
        from sentinel.core.detect.power import _config_key

        if not args.force and _config_key(indicator) in catalog.entries:
            print(f"  {indicator.key:32s} cached")
            continue
        print(f"  {indicator.key:32s} measuring...", flush=True)
        power = catalog.measure(indicator, production_detector)
        measured += 1
        if power is None:
            print(f"  {'':32s} -> not measurable")
        else:
            print(f"  {'':32s} -> {power.describe()}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    catalog.save(args.output)
    print(f"\n{measured} newly measured, {len(catalog.entries)} total")
    print(f"written to {args.output}")

    unquotable = [k for k, p in catalog.entries.items() if not p.is_quotable]
    if unquotable:
        print(f"\n{len(unquotable)} configuration(s) have no quotable floor. "
              f"Indicators using them cannot produce a trustworthy null "
              f"result and should be retuned before activation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
