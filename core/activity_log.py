"""Tijdgestempelde log met live callbacks. Wordt zowel door auto-pilot
gevuld als door de UI live weergegeven."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


# Vaste tag-codes (6 chars max voor uitlijning).
TAG_DATA = "DATA"
TAG_PROFIL = "PROFIL"
TAG_SELECT = "SELECT"
TAG_DETECT = "DETECT"
TAG_VOTING = "VOTING"
TAG_TUNE = "TUNE"
TAG_DONE = "DONE"
TAG_ERROR = "ERROR"


@dataclass
class LogEntry:
    timestamp: str
    tag: str
    message: str


@dataclass
class ActivityLog:
    entries: list[LogEntry] = field(default_factory=list)
    callbacks: list[Callable[[LogEntry], None]] = field(default_factory=list)

    def log(self, tag: str, message: str) -> None:
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            tag=tag,
            message=message,
        )
        self.entries.append(entry)
        for cb in self.callbacks:
            try:
                cb(entry)
            except Exception:
                pass

    def render_text(self, indent_tag: int = 6) -> str:
        return "\n".join(
            f"[{e.timestamp}] [{e.tag:<{indent_tag}}] {e.message}"
            for e in self.entries
        )
