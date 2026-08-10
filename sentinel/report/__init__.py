"""Turning verdicts into written product.

The detect layer decides *what is true*. This layer decides *how to say it*,
and nothing here may change a verdict — a composer that could talk a signal
up or down would be a second truth model wearing a prose costume.
"""
from sentinel.report.assess import compose, compose_region

__all__ = ["compose", "compose_region"]
