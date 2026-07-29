#!/usr/bin/env python
"""Controleer of alle databronnen echt werken — zonder iets op te slaan.

De unit-tests mocken het netwerk (CI mag niet afhangen van een externe
API). Dit script doet het tegenovergestelde: het praat wél met de echte
bronnen, zodat je weet of een key klopt, een endpoint is veranderd of een
bron simpelweg plat ligt.

Draai vanuit de projectmap, met dezelfde omgeving als de app:

    python scripts/check_connectors.py             # alle bronnen
    python scripts/check_connectors.py nasa-firms  # één bron

Exit-code 1 als een geconfigureerde bron faalt — bruikbaar in een cron
of monitoring-check. Bronnen zonder key worden overgeslagen (niet als
fout gerekend).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.base import get_connectors  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")


def main(argv: list[str]) -> int:
    wanted = set(argv[1:])
    connectors = get_connectors()
    if wanted:
        unknown = wanted - set(connectors)
        if unknown:
            print(f"Onbekende bron(nen): {', '.join(sorted(unknown))}")
            print(f"Beschikbaar: {', '.join(sorted(connectors))}")
            return 2
        connectors = {k: v for k, v in connectors.items() if k in wanted}

    print(f"\n{len(connectors)} bron(nen) controleren — dit praat met de "
          f"echte API's.\n")
    failures, skipped = [], []

    for name, c in sorted(connectors.items()):
        missing = c.missing_config()
        if missing:
            print(f"  {YELLOW}OVERGESLAGEN{RESET}  {name}")
            print(f"                {DIM}ontbreekt: {', '.join(missing)}{RESET}")
            skipped.append(name)
            continue

        print(f"  {DIM}bezig...{RESET}     {name}", end="\r", flush=True)
        started = time.time()
        ok, msg = c.self_test()
        secs = time.time() - started

        status = f"{GREEN}OK{RESET}          " if ok else f"{RED}FOUT{RESET}        "
        print(f"  {status}{name}  {DIM}({secs:.1f}s){RESET}")
        print(f"                {DIM}{msg}{RESET}")
        if not ok:
            failures.append(name)

    print()
    parts = [f"{len(connectors) - len(failures) - len(skipped)} ok"]
    if failures:
        parts.append(f"{len(failures)} fout: {', '.join(failures)}")
    if skipped:
        parts.append(f"{len(skipped)} overgeslagen (geen key)")
    print("  " + " · ".join(parts) + "\n")

    if skipped and not failures:
        print(f"  {DIM}Tip: bronnen zonder key doen niets. Zet de genoemde "
              f"env-vars om ze te activeren.{RESET}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
