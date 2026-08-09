"""Run a region's indicators and report where it stands.

One entry point for every region, monitored or not. That is the whole claim
of the regional architecture: five tabs, one engine, and a region that needs
its own evaluation path has broken the abstraction.

Data comes in through a provider callable rather than being fetched here, so
regions never touch storage and stay testable without a database. It also
keeps the point-in-time discipline where it belongs: the caller holds the
`AsOfView` and decides what the region is allowed to see.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pandas as pd

from sentinel.core.baseline import (
    BaselineConfig,
    fit_adaptive,
    fit_reference,
)
from sentinel.core.confidence import assess_confidence
from sentinel.core.contracts import (
    ConfidenceInputs,
    Indicator,
    RegionStatus,
    Signal,
    Verdict,
)
from sentinel.core.detect import IndicatorContext, evaluate_indicator
from sentinel.core.detect.power import DetectionPowerCatalog
from sentinel.regions.base import RegionModule

__all__ = ["SeriesProvider", "evaluate_region"]

#: ``(indicator, as_of) -> series``, or None when the indicator has no data.
#: Returning None is not a failure — it produces an INSUFFICIENT_DATA verdict,
#: which is a legitimate and informative outcome.
SeriesProvider = Callable[[Indicator, datetime], "pd.Series | None"]


def _quality_inputs(series: pd.Series, as_of: datetime,
                    base: ConfidenceInputs) -> ConfidenceInputs:
    """Fill in the data-quality inputs that can be read off the series.

    Only coverage and staleness: the rest — calibration, measured
    performance, corroboration — come from the harness and the evidence
    layer, and inventing them here would reintroduce exactly the
    self-assessment this design removed.
    """
    observed = series.notna()
    coverage = float(observed.mean()) if len(series) else None

    staleness = base.staleness_days
    if staleness is None and observed.any():
        last = pd.Timestamp(series.index[observed][-1])
        staleness = max(0, (pd.Timestamp(as_of) - last).days)

    return ConfidenceInputs(
        data_coverage=coverage if base.data_coverage is None
        else base.data_coverage,
        staleness_days=staleness,
        calibration_gap=base.calibration_gap,
        historical_precision=base.historical_precision,
        historical_recall=base.historical_recall,
        effective_corroboration=base.effective_corroboration,
        source_reliability=base.source_reliability,
        reconstruction_faithful=base.reconstruction_faithful,
        regime_stable=base.regime_stable,
    )


def _signal_for(indicator: Indicator, region: RegionModule,
                provider: SeriesProvider, as_of: datetime,
                catalog: DetectionPowerCatalog,
                baseline_config: BaselineConfig,
                base_inputs: ConfidenceInputs) -> Signal:
    series = provider(indicator, as_of)
    if series is None or series.empty:
        return Signal(
            indicator_key=indicator.key,
            as_of=as_of,
            verdict=Verdict.INSUFFICIENT_DATA,
            confidence=assess_confidence(ConfidenceInputs()),
            insufficient_reason="no data available for this indicator",
        )

    adaptive = fit_adaptive(series, baseline_config)

    reference = None
    period = indicator.reference_period or region.reference_period
    if period is not None:
        try:
            reference = fit_reference(series, period, baseline_config)
        except ValueError:
            # Too little data inside the declared window. Left as None so the
            # sustained-divergence test reports insufficient data rather than
            # silently falling back to a baseline nobody declared.
            reference = None

    context = IndicatorContext(
        indicator=indicator,
        series=series,
        as_of=as_of,
        adaptive=adaptive,
        reference=reference,
        power=catalog.power_for(indicator),
        inputs=_quality_inputs(series, as_of, base_inputs),
    )
    return evaluate_indicator(context)


def evaluate_region(region: RegionModule, provider: SeriesProvider,
                    as_of: datetime,
                    catalog: DetectionPowerCatalog | None = None,
                    baseline_config: BaselineConfig | None = None,
                    inputs: ConfidenceInputs | None = None) -> RegionStatus:
    """Evaluate every active indicator in a region.

    An unmonitored region is returned unevaluated, carrying its activation
    requirements. That is not a shortcut: running indicators for a region
    nobody has set up would produce verdicts that look like monitoring, and
    `RegionStatus` refuses to hold signals for an unmonitored region for
    exactly that reason.
    """
    catalog = catalog or DetectionPowerCatalog()
    baseline_config = baseline_config or BaselineConfig()
    base_inputs = inputs or ConfidenceInputs()

    if not region.is_watched:
        return RegionStatus(
            region_key=region.key,
            name=region.name,
            monitoring=region.status,
            activation_requirements=region.activation_requirements,
        )

    signals = tuple(
        _signal_for(indicator, region, provider, as_of, catalog,
                    baseline_config, base_inputs)
        for indicator in region.active_indicators
    )
    return RegionStatus(
        region_key=region.key,
        name=region.name,
        monitoring=region.status,
        signals=signals,
        activation_requirements=region.activation_requirements,
    )
