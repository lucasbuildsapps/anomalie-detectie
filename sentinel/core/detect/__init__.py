"""Indicator tests: one indicator, one test, one verdict.

See `tests` for the four test types and for why a null result cannot be
produced without measured detection power.
"""
from sentinel.core.detect.tests import IndicatorContext, evaluate_indicator

__all__ = ["IndicatorContext", "evaluate_indicator"]
