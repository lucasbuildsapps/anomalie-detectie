"""Base class for visualizations. Add a new visualization by creating a file
in this folder that defines a subclass of Visualization."""
from abc import ABC, abstractmethod

import pandas as pd


class Visualization(ABC):
    name: str = ""
    short_description: str = ""
    requires: list[str] = []  # e.g. ["time", "value"] or ["time", "value", "lat", "lon"]

    @abstractmethod
    def render(
        self,
        df: pd.DataFrame,
        time_col: str,
        value_col: str,
        **kwargs,
    ) -> None:
        """Render into the current Streamlit page (st.plotly_chart, etc.)."""
        ...
