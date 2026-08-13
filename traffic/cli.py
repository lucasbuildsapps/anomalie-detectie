"""Commandoregel voor de verkeersprobe.

    python -m traffic.cli preflight                 # wat werkt, zonder te betalen
    python -m traffic.cli pricing                   # prijzen + herkomst
    python -m traffic.cli segments                   # de meetsegmenten
    python -m traffic.cli run --label piek-2026-08-14 --duration-min 180
    python -m traffic.cli coverage --label piek-2026-08-14

`run` is hervatbaar: opnieuw starten met hetzelfde label vult aan.
"""
from __future__ import annotations

import argparse
import sys

from traffic import coverage, pricing
from traffic.config import ProbeConfig, load_dotenv
from traffic.probe import ProbeRunner
from traffic.segments import actieve_segmenten, per_stad
from traffic.store import ProbeStore

STANDAARD_PROVIDERS = ("google_routes", "tomtom", "here")


def _cfg(args: argparse.Namespace) -> ProbeConfig:
    return ProbeConfig(
        budget_usd=args.budget,
        interval_min=args.interval_min,
        duration_min=args.duration_min,
        providers=tuple(args.providers),
        include_waze=args.include_waze,
        include_optional_controls=args.include_optional_controls,
        label=args.label,
    )


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    print(ProbeRunner(cfg, ProbeStore(cfg.label)).preflight())
    return 0


def cmd_pricing(_: argparse.Namespace) -> int:
    print(pricing.tabel())
    return 0


def cmd_segments(args: argparse.Namespace) -> int:
    for stad, segmenten in per_stad(args.include_optional_controls).items():
        merk = " (externe controle)" if segmenten[0].is_external_control else ""
        print(f"\n{stad}{merk}")
        for s in segmenten:
            print(f"  {s.id:<26} {s.label}")
            print(f"  {'':<26} faalpunt: {s.crossing}")
    print(f"\n{len(actieve_segmenten(args.include_optional_controls))} segmenten actief.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    store = ProbeStore(cfg.label)
    runner = ProbeRunner(cfg, store)
    print(runner.preflight())
    print()
    uitkomst = runner.run()
    print()
    print(runner.ledger.samenvatting())
    if uitkomst["gestopt_om"]:
        print(f"\nVroegtijdig gestopt: {uitkomst['gestopt_om']}")
    return cmd_coverage(args)


def cmd_coverage(args: argparse.Namespace) -> int:
    samples = ProbeStore(args.label).lees_samples()
    if not samples:
        print(f"Geen samples voor label '{args.label}'. Eerst `run` draaien.")
        return 1
    per_kanaal = coverage.per_stad_kanaal(samples)
    print("DEKKINGSTABEL — stad × kanaal\n")
    print(coverage.render_dekkingstabel(per_kanaal))
    print("\nDETAIL — segment × kanaal\n")
    print(coverage.render_detail(coverage.per_segment_kanaal(samples)))
    open_, boodschap = coverage.poort(per_kanaal)
    print(f"\nPOORT: {boodschap}")
    fouten = {o.voorbeeldfout for o in per_kanaal if o.voorbeeldfout}
    if fouten:
        print("\nVoorbeeldfouten:")
        for f in sorted(fouten):
            print(f"  - {f[:200]}")
    return 0 if open_ else 2


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="traffic", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", default="probe", help="naam van de run (map onder data/traffic/)")
    p.add_argument("--budget", type=float, default=10.0, help="hard kostenplafond in USD")
    p.add_argument("--interval-min", type=int, default=10, help="minuten tussen rondes")
    p.add_argument("--duration-min", type=int, default=180, help="lengte van het meetvenster")
    p.add_argument("--providers", nargs="+", default=list(STANDAARD_PROVIDERS))
    p.add_argument("--include-waze", action="store_true",
                   help="eenmalige dekkingscontrole op het ongedocumenteerde livemap-endpoint")
    p.add_argument("--include-optional-controls", action="store_true",
                   help="Chisinau en Kraków meenemen als extra externe controle")

    sub = p.add_subparsers(dest="commando", required=True)
    for naam, functie, hulp in (
        ("preflight", cmd_preflight, "keys, bereikbaarheid en kostenschatting"),
        ("pricing", cmd_pricing, "prijstabel met herkomst en verificatiedatum"),
        ("segments", cmd_segments, "de meetsegmenten per stad"),
        ("run", cmd_run, "de probe draaien (hervatbaar)"),
        ("coverage", cmd_coverage, "dekkingstabel uit een eerdere run"),
    ):
        s = sub.add_parser(naam, help=hulp)
        s.set_defaults(func=functie)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
