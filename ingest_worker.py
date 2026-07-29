"""Ingest-worker: draait connectors op hun eigen schema (APScheduler).

Start:
    python ingest_worker.py

Draait als aparte service naast de app (zie docker-compose.prod.yml) —
nooit ín het Streamlit-proces: dat herstart per interactie en is geen
plek voor achtergrondwerk.
"""
from __future__ import annotations

from core.logging_setup import get_logger

logger = get_logger("worker")


def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    from connectors.base import get_connectors
    from core import storage
    from core.ingest import run_connector

    storage.init_db()
    scheduler = BlockingScheduler(timezone="UTC")

    active = [c for c in get_connectors().values() if c.enabled]
    if not active:
        logger.warning(
            "geen enabled connectors gevonden — worker stopt "
            "(zet enabled = True in een connectors/*.py om te starten)"
        )
        return

    for connector in active:
        scheduler.add_job(
            run_connector,
            "interval",
            minutes=connector.schedule_minutes,
            args=[connector],
            id=connector.name,
            next_run_time=None,  # eerste run: direct hieronder
        )
        # Direct één keer draaien bij opstart, dan volgens schema.
        run_connector(connector)
        logger.info("connector gepland",
                    extra={"ctx": {"source": connector.name,
                                   "interval_min": connector.schedule_minutes}})

    scheduler.start()


if __name__ == "__main__":
    main()
