"""Causal baselines and the divergence between them.

Two baselines, deliberately: the adaptive one follows the recent regime, the
declared reference does not move. Their disagreement is the sustained
escalation signal. See `causal` for the recursion and `divergence` for the
joint test.
"""
from sentinel.core.baseline.causal import (
    BaselineConfig,
    BaselineFit,
    fit_adaptive,
    fit_reference,
    phases_for,
)
from sentinel.core.baseline.divergence import (
    DivergenceConfig,
    DivergenceResult,
    DivergenceState,
    assess_divergence,
    cusum,
    divergence_detector,
)

__all__ = [
    "BaselineConfig",
    "BaselineFit",
    "DivergenceConfig",
    "DivergenceResult",
    "DivergenceState",
    "assess_divergence",
    "cusum",
    "divergence_detector",
    "fit_adaptive",
    "fit_reference",
    "phases_for",
]
