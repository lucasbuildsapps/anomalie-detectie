#!/usr/bin/env python
"""Replay a region against the curated chronology and report warning time.

Run: python scripts/retrospective.py --dataset <id> [--region euro_atlantic]

This is the secondary evaluation. The synthetic harness stays primary, because
it has no curation loop and enough samples to mean something. What this adds
is the check the synthetic harness cannot perform: whether the floors measured
in simulation correspond to anything that happened.

If the two disagree — good detection power in simulation, nothing firing before
real escalations — that disagreement is the finding, and it points at the
scenario generator rather than at the detector.

Cost note: each replay date fits every baseline in the region from scratch, so
a weekly step over five years is a few thousand fits. Widen `--step` before
widening the window.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.core.contracts import ConfidenceInputs  # noqa: E402
from sentinel.core.detect.power import DetectionPowerCatalog  # noqa: E402
from sentinel.core.time import PointInTimeStore  # noqa: E402
from sentinel.eval.retrospective import events_from_chronology, replay  # noqa: E402
from sentinel.regions import evaluate_region, get_region  # noqa: E402
from sentinel.regions.providers import storage_provider  # noqa: E402

DEFAULT_CHRONOLOGY = (Path(__file__).resolve().parent.parent / "data" /
                      "chronology" / "euro_atlantic.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, required=True)
    parser.add_argument("--region", default="euro_atlantic")
    parser.add_argument("--chronology", type=Path, default=DEFAULT_CHRONOLOGY)
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--lead-days", type=int, default=90,
                        help="how long before an event an alert still counts "
                             "as a warning about it")
    args = parser.parse_args()

    events = events_from_chronology(args.chronology)
    if not events:
        print(f"No events in {args.chronology}. Nothing can be validated — "
              f"this is not a passing result. See data/chronology/README.md "
              f"for the format.")
        return 1

    region = get_region(args.region)
    if not region.is_watched:
        print(f"{region.name} is {region.status.value}; there is nothing to "
              f"replay until it is monitored.")
        return 1

    store = PointInTimeStore()
    provider = storage_provider(args.dataset, region, store.view)
    catalog = DetectionPowerCatalog.load("config/detection_power.json")

    occurred = [e.occurred_at for e in events]
    start = (datetime.fromisoformat(args.start) if args.start
             else min(occurred) - timedelta(days=args.lead_days))
    end = datetime.fromisoformat(args.end) if args.end else max(occurred)

    faithful = store.view(end).provenance(args.dataset).is_faithful

    def evaluate_at(as_of):
        status = evaluate_region(
            region, provider, as_of, catalog,
            inputs=ConfidenceInputs(reconstruction_faithful=faithful))
        return status.signals

    print(f"Replaying {region.name} from {start.date()} to {end.date()} "
          f"in {args.step_days}-day steps against {len(events)} events...",
          flush=True)

    report = replay(evaluate_at, events, start, end,
                    step=timedelta(days=args.step_days),
                    lead_window=timedelta(days=args.lead_days),
                    arrival_faithful=faithful,
                    undated_indicators=tuple(
                        i.key for i in region.undated_indicators))

    print()
    for result in report.results:
        print(f"  {result.describe()}")
    print()
    print(report.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
