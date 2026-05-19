import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .base import Visualization


class HeatmapVisualization(Visualization):
    name = "Heatmap locatie × tijd"
    short_description = (
        "Locaties op de Y-as, tijd (per week) op de X-as. Kleur = totaal aantal. "
        "Afwijkingen worden met een kruis gemarkeerd."
    )
    requires = ["time", "value", "location_name"]

    def render(self, df, time_col, value_col, **_):
        loc_col = None
        for candidate in ("location_name", "category"):
            if candidate in df.columns and df[candidate].notna().any():
                loc_col = candidate
                break
        if loc_col is None:
            st.warning(
                "Heatmap vereist een 'location_name' of 'category' kolom."
            )
            return

        work = df.copy()
        work[time_col] = pd.to_datetime(work[time_col])
        work["bucket"] = work[time_col].dt.to_period("W").dt.start_time

        pivot = work.pivot_table(
            index=loc_col,
            columns="bucket",
            values=value_col,
            aggfunc="sum",
            fill_value=0,
        )

        fig = go.Figure(
            data=go.Heatmap(
                z=pivot.values,
                x=pivot.columns,
                y=pivot.index,
                colorscale="Reds",
                colorbar=dict(title=value_col),
                hovertemplate=(
                    "Locatie: %{y}<br>Week: %{x|%Y-%m-%d}<br>"
                    "Totaal: %{z}<extra></extra>"
                ),
            )
        )

        if "is_anomaly" in work.columns and work["is_anomaly"].any():
            anom = work[work["is_anomaly"]].copy()
            anom_bucket = anom.groupby([loc_col, "bucket"]).size().reset_index(
                name="n"
            )
            fig.add_trace(
                go.Scatter(
                    x=anom_bucket["bucket"],
                    y=anom_bucket[loc_col],
                    mode="markers",
                    marker=dict(symbol="x", color="black", size=12,
                                line=dict(width=2)),
                    name="Afwijking",
                    hovertemplate=(
                        "Locatie: %{y}<br>Week: %{x|%Y-%m-%d}<br>"
                        "Afwijkingen: %{text}<extra></extra>"
                    ),
                    text=anom_bucket["n"],
                )
            )

        fig.update_layout(
            xaxis_title="Week",
            yaxis_title=loc_col,
            height=max(350, 60 * len(pivot.index)),
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig, use_container_width=True)
