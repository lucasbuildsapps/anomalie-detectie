#!/usr/bin/env python
"""Load a curated incident chronology into a dataset.

Run: python scripts/ingest_chronology.py --dataset <id> [--path FILE]

Safe to re-run. Rows deduplicate on content, and a row already held keeps its
original arrival time — the first time we saw it is the moment we knew it, and
overwriting that on a re-import would quietly improve the past.

The exit code is 0 for a successful run *including* one that inserted nothing,
because "no new events" is a normal outcome for a curated file and failing on
it would train whoever runs this to ignore the exit code.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.ingest import ChronologyConnector, run_ingest  # noqa: E402

DEFAULT_PATH = (Path(__file__).resolve().parent.parent / "data" /
                "chronology" / "euro_atlantic.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, required=True,
                        help="dataset id to load into")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--since", default=None,
                        help="only rows known on or after this date "
                             "(YYYY-MM-DD); compares against reported_at "
                             "where present, so a late-reported old event is "
                             "still picked up")
    args = parser.parse_args()

    since = None
    if args.since:
        from datetime import datetime
        since = datetime.fromisoformat(args.since)

    result = run_ingest(ChronologyConnector(args.path), args.dataset,
                        since=since)
    print(result.describe())
    for note in result.notes:
        print(f"  note: {note}")

    if not result.ok:
        return 1
    if result.n_inserted and not result.arrival_is_faithful:
        print("\nSome rows have no reported_at. Replays that cross them "
              "describe what could have been said under instant reporting, "
              "and confidence is capped for findings resting on them. See "
              "data/chronology/README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
