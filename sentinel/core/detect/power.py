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
- `entity_behaviour` is not scored on a series at all — it is judged per
  entity against peers — so it is measured by `sentinel.eval.entity_power` on
  a synthetic fleet, and only for the behaviour that harness actually injects
  (`loiter`). An indicator watching for AIS gaps or identity conflicts gets
  **no** floor from a loiter measurement, and quoting one at it would be the
  precise dishonesty the null-result rule exists to prevent. Those return
  None, which the detect layer reads as insufficient data.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from sentinel.core.contracts import DetectionPower, Indicator, IndicatorTest

__all__ = ["DetectionPowerCatalog", "production_detector", "scenario_for"]

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
                 "cusum_threshold", "min_sustained_periods",
                 # entity behaviour: what is watched and how strictly
                 "event_types", "peer_baseline", "min_duration_minutes",
                 "min_gap_minutes")
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
    #: Bumped from 8 after measuring that the spike floor flipped between
    #: 5x and 3x depending on sample size. See PowerCurve.floor_is_uncertain.
    n_repeats: int = 24
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
        if self.measure_missing:
            return self.measure(indicator, factory)
        return None

    # -- measurement -----------------------------------------------------
    def measure(self, indicator: Indicator,
                factory: DetectorFactory | None = None,
                ) -> DetectionPower | None:
        """Run the harness for this configuration and cache the result."""
        if indicator.test_type is IndicatorTest.ENTITY_BEHAVIOUR:
            return self._measure_entity(indicator)

        scenario = scenario_for(indicator.test_type)
        if scenario is None or factory is None:
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
            resolved=not curve.floor_is_uncertain,
        )
        self.entries[_config_key(indicator)] = power
        return power

    def _measure_entity(self, indicator: Indicator) -> DetectionPower | None:
        """Measure an entity indicator on the synthetic fleet, or decline.

        The fleet harness injects loitering. An indicator that watches for AIS
        gaps or identity conflicts is therefore unmeasured, and declining is
        the honest answer — a loiter floor attached to a gap indicator would
        let a null result claim coverage nobody measured.
        """
        watched = set(indicator.test_config.get("event_types", ()))
        if "loiter" not in watched:
            return None

        from sentinel.eval.entity_power import measure as measure_entity

        result = measure_entity(threshold=self.threshold,
                                n_repeats=max(self.n_repeats // 4, 1))
        power = result.to_detection_power()
        self.entries[_config_key(indicator)] = power
        return power

    def warm(self, indicators, factory: DetectorFactory | None = None) -> int:
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
                    "unit": power.unit,
                    "resolved": power.resolved,
                    "caveat": power.caveat,
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
                unit=str(value.get("unit", "x")),
                resolved=bool(value.get("resolved", True)),
                caveat=value.get("caveat"),
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


def production_detector(indicator: Indicator,
                        baseline_config=None,
                        reference_periods: int = 180,
                        ) -> Callable[[pd.Series], pd.Series]:
    """A `series -> bool series` detector matching how this indicator ships.

    This function is the whole reason the measured floor means anything. v1's
    evaluation scored five raw detectors at default parameters while the
    product ran a tuned ensemble over a different aggregation — so the numbers
    described a system nobody used. Here the detector is built from the same
    baseline fitting, the same thresholds, and the same divergence logic that
    `evaluate_indicator` runs.

    One documented approximation: production takes its reference window from
    the indicator's declared calendar dates, while synthetic scenarios have no
    real calendar. The window is therefore taken positionally, as the opening
    `reference_periods` of the series. The *shape* of the test is identical;
    only the window's provenance differs, and taking it from the start
    preserves the property that matters — the reference must not contain
    whatever is currently happening.
    """
    from sentinel.core.baseline import (
        BaselineConfig,
        divergence_detector,
        fit_adaptive,
    )

    config = baseline_config or BaselineConfig()

    if indicator.test_type is IndicatorTest.SUSTAINED_DIVERGENCE:
        return divergence_detector(reference_periods=reference_periods,
                                   baseline_config=config)

    if indicator.test_type is IndicatorTest.LEVEL_DEVIATION:
        threshold = float(indicator.test_config.get("threshold", 3.5))

        def detect(series: pd.Series) -> pd.Series:
            fit = fit_adaptive(series, config)
            deviation = fit.deviation(series).abs()
            flags = (deviation > threshold).fillna(False)
            # Warm-up periods are untested, not quiet: excluding them keeps
            # the measurement consistent with what production would report.
            return (flags & fit.is_usable).astype(bool)

        return detect

    raise ValueError(
        f"no production detector for {indicator.test_type.value}; "
        f"detection power for it cannot be measured"
    )
