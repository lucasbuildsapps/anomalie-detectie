#!/usr/bin/env python
"""Run the entity primitives over stored positions and persist the events.

Run: python scripts/derive_events.py --dataset <id> [--region nld_eez]

This is the step between a position feed and an indicator verdict. It reads
positions through an `AsOfView`, runs `extract_events` per entity, and writes
the result to `entity_events`.

Why `ingested_at` on a derived event is the run time, not the behaviour time
---------------------------------------------------------------------------
A loiter that happened on the 3rd but was only computed on the 9th was not
knowable on the 5th. `extract_events` stamps the arrival accordingly, and a
replay dated before the run correctly sees nothing. Backdating it to the
behaviour would credit the system with foresight it did not have — and would
do it invisibly, which is worse.

Safe to re-run. Events deduplicate on (region, type, entity, time), so
re-processing a period adds nothing and leaves the original arrival stamps
alone.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import storage  # noqa: E402
from sentinel.core.time import PointInTimeStore  # noqa: E402
from sentinel.entity import extract_events  # noqa: E402
from sentinel.regions import get_region  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, required=True)
    parser.add_argument("--region", default="nld_eez")
    parser.add_argument("--as-of", default=None,
                        help="derive as this date (YYYY-MM-DD); defaults to now")
    parser.add_argument("--since", default=None,
                        help="only positions from this date onwards")
    args = parser.parse_args()

    region = get_region(args.region)
    as_of = (datetime.fromisoformat(args.as_of) if args.as_of
             else datetime.utcnow())
    since = datetime.fromisoformat(args.since) if args.since else None

    view = PointInTimeStore().view(as_of)
    positions = view.positions(args.dataset, since=since,
                               bbox=region.geography.bbox)
    if positions.empty:
        print(f"No positions known at {as_of.date()} for {region.name}. "
              f"Nothing to derive — that is not the same as nothing happening.")
        return 1

    n_entities = positions["entity_key"].nunique()
    events = []
    for _key, group in positions.groupby("entity_key"):
        events.extend(extract_events(group, region_key=region.key))

    stored = storage.insert_entity_events(args.dataset, events)
    print(f"{len(positions)} positions from {n_entities} entities -> "
          f"{len(events)} events, {stored} new "
          f"({len(events) - stored} already held).")

    if events and not stored:
        print("Nothing new. Either this period was already processed, or the "
              "detector configuration did not change what it finds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
