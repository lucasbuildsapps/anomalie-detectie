#!/usr/bin/env python
"""Fetch Danish Maritime Authority AIS archives into a dataset.

Run: python scripts/ingest_ais.py --dataset <id> --since 2024-06-01 [--until ...]

One archive per day, filtered to the region's bounding box before storage — a
national daily file is millions of rows and the Dutch EEZ is a small corner of
it. Safe to re-run: positions deduplicate on (entity, time, position), which
also absorbs the double reception that is normal in AIS.

Read this before trusting the first run
---------------------------------------
The column mapping in `sentinel/ingest/dma_ais.py` was written from the
published format description and has **never been checked against a live
file** — the environment it was written in has no outbound network access. It
is built to fail loudly if the schema has moved: you will get a `SchemaError`
naming the columns it wanted and the columns it found, not silently empty
results. If that happens, the mapping table is what needs updating.

After this, `scripts/derive_events.py` turns the stored positions into typed
events, and the entity indicators can then be evaluated against them.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.ingest import DmaAisConnector, run_position_ingest  # noqa: E402
from sentinel.regions import get_region  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, required=True)
    parser.add_argument("--since", required=True, help="YYYY-MM-DD")
    parser.add_argument("--until", default=None,
                        help="YYYY-MM-DD; defaults to --since")
    parser.add_argument("--region", default="nld_eez",
                        help="supplies the bounding box; pass 'none' for the "
                             "whole archive")
    parser.add_argument("--archive-lag-days", type=int, default=1,
                        help="days between a day's traffic and its archive "
                             "being downloadable; this becomes the arrival "
                             "time, so getting it wrong flatters every replay")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since)
    until = datetime.fromisoformat(args.until) if args.until else since

    bbox = None
    if args.region.lower() != "none":
        bbox = get_region(args.region).geography.bbox
        if bbox is None:
            print(f"{args.region} declares no bounding box; fetching the "
                  f"whole archive.")

    days = (until - since).days + 1
    print(f"Fetching {days} archive(s) from {since.date()} to {until.date()}"
          f"{' within ' + str(bbox) if bbox else ''}...", flush=True)

    result = run_position_ingest(
        DmaAisConnector(bbox=bbox, archive_lag_days=args.archive_lag_days),
        args.dataset, since=since, until=until)

    print(result.describe())
    for note in result.notes:
        print(f"  note: {note}")

    if result.ok and result.n_inserted:
        print("\nNext: python scripts/derive_events.py --dataset "
              f"{args.dataset} --region {args.region}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
