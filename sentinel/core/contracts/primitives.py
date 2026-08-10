"""Small value types shared by everything else.

Deliberately dumb: no behaviour beyond validation and a readable rendering.
They exist so that "a place" and "a period" mean the same thing in the
maritime module and the strike-tempo module, which is the whole premise of a
shared data contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["DateRange", "GeoPoint"]


@dataclass(frozen=True)
class GeoPoint:
    """A position, with an honest statement of how precisely it is known.

    `precision_m` is not decoration. A strike located to the nearest province
    and a vessel located to the nearest 10 metres cannot be compared for
    proximity to infrastructure, and a model that treats them alike will
    produce confident nonsense. Carrying precision makes the difference
    available to whoever needs it instead of losing it at ingestion.
    """

    lat: float
    lon: float
    precision_m: float | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= float(self.lat) <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= float(self.lon) <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")
        if self.precision_m is not None and float(self.precision_m) < 0:
            raise ValueError(f"precision cannot be negative: {self.precision_m}")

    @property
    def is_precise(self) -> bool:
        """True when the position is known well enough for proximity work."""
        return self.precision_m is not None and self.precision_m <= 1000.0

    def __str__(self) -> str:
        base = f"{self.lat:.4f}, {self.lon:.4f}"
        return base if self.precision_m is None else f"{base} (±{self.precision_m:g} m)"


@dataclass(frozen=True)
class DateRange:
    """A closed interval. Used for reference periods and episode bounds."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"range ends ({self.end.isoformat()}) before it starts "
                f"({self.start.isoformat()})"
            )

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end

    def overlaps(self, other: DateRange) -> bool:
        return self.start <= other.end and other.start <= self.end

    def __str__(self) -> str:
        return f"{self.start.date().isoformat()} to {self.end.date().isoformat()}"
