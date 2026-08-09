"""Detection power as a property of a configuration, measured once.

A null result is only worth reading if it comes with "and here is the
smallest thing we would have caught". That number comes from the evaluation
harness, and measuring it costs roughly eighty baseline fits — far too much
to repeat on every evaluation.

So it is cached. The cache key is the *configuration*, not the indicator:
two indicators with the same test type and thresholds have the same detection
power, and pretending otherwise would multiply the cost for no information.

Which scenario measures which test
----------------------------------
Each test type is measured against the scenario that represents its actual
job:

- `level_deviation` is asked about isolated excursions, so it is measured on
  `spike`.
- `sustained_divergence` exists for the case a purely adaptive system loses,
  so it is measured on `adaptation_failure` — a long escalation scored only
  at its far end. Measuring it on `sustained_increase` would flatter it, by
  crediting detection of the onset that any detector manages.
- `condition` is deterministic. It either holds or it does not; there is no
  effect size below which it would be missed, so there is nothing to measure
  and saying so is not the same as claiming a measurement.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from sentinel.core.contracts import DetectionPower, Indicator, IndicatorTest

__all__ = ["DetectionPowerCatalog", "scenario_for"]

#: Which scenario honestly measures each test type.
_SCENARIO_FOR_TEST = {
    IndicatorTest.LEVEL_DEVIATION: "spike",
    IndicatorTest.SUSTAINED_DIVERGENCE: "adaptation_failure",
}


def scenario_for(test_type: IndicatorTest) -> str | None:
    """The scenario a test type should be measured against, if any."""
    return _SCENARIO_FOR_TEST.get(test_type)


def _config_key(indicator: Indicator) -> str:
    """Stable key over the things that actually change detection power.

    Deliberately excludes the indicator's identity, region and wording: two
    indicators configured identically detect identically, and giving them
    separate cache entries would multiply the measurement cost while adding
    nothing.
    """
    relevant = {
        k: v for k, v in sorted(indicator.test_config.items())
        if k in ("threshold", "aggregation", "reference_periods",
                 "cusum_threshold", "min_sustained_periods")
    }
    return json.dumps(
        {"test": indicator.test_type.value, "config": relevant},
        sort_keys=True,
    )


#: A detector factory: given an indicator, return `series -> bool series`.
DetectorFactory = Callable[[Indicator], Callable[[pd.Series], pd.Series]]


@dataclass
class DetectionPowerCatalog:
    """Measured detection power, keyed by configuration.

    `measure_missing=False` is the production default. Measuring on demand
    inside a live evaluation would make an analyst wait minutes for a number
    that should have been computed in advance — and, worse, would make the
    first run of a quiet region slower than a busy one. Precompute with
    `warm()` and persist.
    """

    entries: dict[str, DetectionPower] = field(default_factory=dict)
    measure_missing: bool = False
    n_repeats: int = 8
    threshold: float = 0.8

    # -- lookup ----------------------------------------------------------
    def power_for(self, indicator: Indicator,
                  factory: DetectorFactory | None = None,
                  ) -> DetectionPower | None:
        """Power for this indicator's configuration, or None if unmeasured.

        Returning None is meaningful: the detect layer turns it into an
        INSUFFICIENT_DATA verdict rather than an unbacked claim of quiet.
        """
        if indicator.test_type is IndicatorTest.CONDITION:
            # Deterministic: no effect size below which it would be missed.
            return DetectionPower(
                scenario_kind=f"condition:{indicator.test_config.get('rule', '')}",
                floor_magnitude=0.0, threshold=1.0, n_repeats=0,
            )

        key = _config_key(indicator)
        if key in self.entries:
            return self.entries[key]
        if self.measure_missing and factory is not None:
            return self.measure(indicator, factory)
        return None

    # -- measurement -----------------------------------------------------
    def measure(self, indicator: Indicator,
                factory: DetectorFactory) -> DetectionPower | None:
        """Run the harness for this configuration and cache the result."""
        scenario = scenario_for(indicator.test_type)
        if scenario is None:
            return None

        # Imported here so the catalogue stays importable without the
        # evaluation stack, and so core never depends on eval at module load.
        from sentinel.eval import power_curve

        curve = power_curve(factory(indicator), kind=scenario,
                            n_repeats=self.n_repeats, threshold=self.threshold)
        power = DetectionPower(
            scenario_kind=scenario,
            floor_magnitude=float("nan") if curve.is_confounded else curve.floor,
            threshold=self.threshold,
            n_repeats=self.n_repeats,
            confounded=curve.is_confounded,
        )
        self.entries[_config_key(indicator)] = power
        return power

    def warm(self, indicators, factory: DetectorFactory) -> int:
        """Measure every configuration not already cached. Returns how many."""
        measured = 0
        for indicator in indicators:
            if indicator.test_type is IndicatorTest.CONDITION:
                continue
            key = _config_key(indicator)
            if key in self.entries:
                continue
            if self.measure(indicator, factory) is not None:
                measured += 1
        return measured

    # -- persistence -----------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {
                key: {
                    "scenario_kind": power.scenario_kind,
                    "floor_magnitude": power.floor_magnitude,
                    "threshold": power.threshold,
                    "n_repeats": power.n_repeats,
                    "confounded": power.confounded,
                }
                for key, power in sorted(self.entries.items())
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str, **kwargs) -> DetectionPowerCatalog:
        raw = json.loads(text or "{}")
        entries = {
            key: DetectionPower(
                scenario_kind=value["scenario_kind"],
                floor_magnitude=float(value["floor_magnitude"]),
                threshold=float(value.get("threshold", 0.8)),
                n_repeats=int(value.get("n_repeats", 0)),
                confounded=bool(value.get("confounded", False)),
            )
            for key, value in raw.items()
        }
        return cls(entries=entries, **kwargs)

    def save(self, path: Path | str) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def load(cls, path: Path | str, **kwargs) -> DetectionPowerCatalog:
        path = Path(path)
        if not path.exists():
            return cls(**kwargs)
        return cls.from_json(path.read_text(), **kwargs)
