"""Base class for detection methods. Add a new method by creating a file in
this folder that defines a subclass of Detector."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class ParameterSpec:
    label: str
    type: str  # "float" | "int" | "bool" | "select"
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list | None = None
    help: str | None = None


class Detector(ABC):
    name: str = ""
    short_description: str = ""
    long_description: str = ""        # Technisch, voor experts.
    plain_explanation: str = ""       # Voor niet-data-scientists.
    parameters: dict[str, ParameterSpec] = {}

    @abstractmethod
    def detect(
        self,
        df: pd.DataFrame,
        time_col: str,
        value_col: str,
        **params,
    ) -> pd.DataFrame:
        """Return df sorted on time with two extra columns:
        - anomaly_score: float
        - is_anomaly: bool
        """
        ...
