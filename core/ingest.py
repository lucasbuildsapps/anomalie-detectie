"""Ingest-pipeline: connector → validatie → opslag → run-log.

Eén functie (`run_connector`) draait één bron end-to-end:
1. bepaal 'since' (laatste waarneming in de doel-dataset, min overlap-marge)
2. fetch bij de connector
3. valideer (core/validation.py) — fouten blokkeren, warnings loggen
4. insert met dedupe (row-hash; overlappende pulls zijn onschadelijk)
5. schrijf ingest_runs-regel + audit-regel

Wordt aangeroepen door ingest_worker.py (APScheduler) of handmatig/CLI:
    python -c "from core.ingest import run_all; run_all()"
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from connectors.base import Connector, get_connectors
from core import storage
from core.logging_setup import get_logger
from core.validation import validate_mapped

_logger = get_logger("ingest")

# Overlap-marge bij incrementele pulls: liever dubbel aangeboden (dedupe
# vangt het af) dan een gat door klokverschil of nagekomen meldingen.
OVERLAP = timedelta(days=3)


def _target_dataset_id(connector: Connector) -> int:
    for d in storage.list_datasets():
        if d["name"] == connector.dataset_name:
            return d["id"]
    return storage.create_dataset(
        connector.dataset_name,
        f"Automatisch ingewonnen via connector '{connector.name}'. "
        f"{connector.description}",
        {"source_connector": connector.name},
    )


def _since_for(dataset_id: int) -> datetime | None:
    df = storage.load_observations(dataset_id)
    if df.empty:
        return None
    return (pd.Timestamp(df["timestamp"].max()) - OVERLAP).to_pydatetime()


def run_connector(connector: Connector) -> dict:
    """Draai één connector. Returnt een run-samenvatting (ook bij falen)."""
    started = datetime.now(UTC).replace(tzinfo=None)
    summary = {"source": connector.name, "status": "error",
               "rows_offered": 0, "rows_added": 0, "error": None}
    try:
        dataset_id = _target_dataset_id(connector)
        since = _since_for(dataset_id)
        df = connector.fetch(since)
        summary["rows_offered"] = len(df)

        if not df.empty:
            report = validate_mapped(df)
            for w in report.warnings:
                _logger.warning("ingest-validatie",
                                extra={"ctx": {"source": connector.name,
                                               "warning": w}})
            if not report.ok:
                raise ValueError("; ".join(report.errors))
            summary["rows_added"] = storage.insert_observations(dataset_id, df)

        # Momentopname na binnenkomst van nieuwe data: zo ontstaat vanzelf
        # een historie van wát de tool wanneer zei — nodig om een eerder
        # oordeel achteraf te kunnen verantwoorden.
        if summary["rows_added"]:
            _snapshot_after_ingest(dataset_id, connector.name)

        summary["status"] = "ok"
        _logger.info("ingest-run klaar", extra={"ctx": summary})
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
        _logger.exception("ingest-run faalde",
                          extra={"ctx": {"source": connector.name}})
    finally:
        storage.record_ingest_run(
            connector.name, started, summary["status"],
            rows_offered=summary["rows_offered"],
            rows_added=summary["rows_added"],
            error=summary["error"],
        )
        storage.record_audit(
            "ingest_run", "connector", connector.name,
            {k: v for k, v in summary.items() if k != "source"},
        )
    return summary


def _snapshot_after_ingest(dataset_id: int, source: str) -> None:
    """Bewaar de analyse-stand na een geslaagde inwinning (best effort:
    een mislukte snapshot mag de ingest zelf nooit laten falen)."""
    try:
        from core.normbeeld import (
            _suggest_best_aggregation,
            compute_all_normbeelds,
            detect_recent_alerts,
        )
        df = storage.load_observations(dataset_id)
        if df.empty:
            return
        agg = _suggest_best_aggregation(df)
        normbeelds = compute_all_normbeelds(df, horizon_days=14, aggregation=agg)
        alerts = detect_recent_alerts(normbeelds, aggregation=agg)
        storage.save_snapshot(
            dataset_id, alerts, normbeelds, aggregation=agg, horizon=14,
            n_rows=len(df), label=f"na inwinning ({source})",
        )

        # Waarschuwen is het sluitstuk: zonder melding blijft een piek op
        # zaterdagavond liggen tot maandag. Losstaand van de snapshot in
        # een eigen try, zodat een kapot meldkanaal de vastlegging niet
        # meesleept.
        #
        # Bewust ook aanroepen bij nul afwijkingen: de eerste run legt de
        # nulmeting vast. Met een `if alerts`-guard bleef die achterwege
        # bij een rustige eerste run, en werd de eerste run mét
        # afwijkingen de nulmeting — precies die afwijkingen verdwenen dan
        # geruisloos.
        _notify_after_ingest(dataset_id, alerts)
    except Exception:
        _logger.exception("snapshot na ingest mislukt",
                          extra={"ctx": {"dataset_id": dataset_id}})


def _notify_after_ingest(dataset_id: int, alerts: list) -> None:
    try:
        from core.notify import is_configured, notify_new_alerts
        if not is_configured():
            return
        name = next((d["name"] for d in storage.list_datasets(include_hidden=True)
                     if d["id"] == dataset_id), str(dataset_id))
        result = notify_new_alerts(dataset_id, name, alerts)
        _logger.info("melding-resultaat", extra={"ctx": {
            "dataset_id": dataset_id, "verstuurd": result.sent,
            "kanalen": result.channels, "nieuw": result.n_new,
            "onderdrukt": result.n_suppressed}})
    except Exception:
        _logger.exception("melden na ingest mislukt",
                          extra={"ctx": {"dataset_id": dataset_id}})


def run_all(include_disabled: bool = False) -> list[dict]:
    """Draai alle (enabled) connectors één keer."""
    storage.init_db()
    results = []
    for c in get_connectors().values():
        if c.enabled or include_disabled:
            results.append(run_connector(c))
    return results
