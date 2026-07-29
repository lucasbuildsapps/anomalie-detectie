"""Centrale logging-configuratie.

Gebruik in elke module:

    from core.logging_setup import get_logger
    log = get_logger(__name__)

Formaat is één JSON-object per regel (machine-parseerbaar voor log-
aggregatie), naar stderr. Niveau via env-var SENTINEL_LOG_LEVEL
(default INFO). Dit is óps-logging; de gebruikersgerichte ActivityLog
in core/activity_log.py blijft daarnaast bestaan.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Extra context meegegeven via logger.info(..., extra={"ctx": {...}})
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload.update(ctx)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("sentinel")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
    level = os.environ.get("SENTINEL_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Logger onder de 'sentinel'-hiërarchie; configureert bij eerste gebruik."""
    _configure()
    return logging.getLogger(f"sentinel.{name}")
