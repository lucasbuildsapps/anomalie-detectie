"""Tijdreeks-visualisatie met normbeeld-band en gemarkeerde afwijkingen."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .base import Visualization


LIGHT = {
    "band":     "rgba(46, 139, 87, 0.14)",
    "expected": "#2e8b57",
    "actual":   "#0a1929",
    "anomaly":  "#c53030",
    "grid":     "#eef0f3",
    "plot_bg":  "#ffffff",
    "axis":     "#0a1929",
}
DARK = {
    "band":     "rgba(76, 218, 134, 0.18)",
    "expected": "#4cda86",
    "actual":   "#e6edf3",
    "anomaly":  "#f87171",
    "grid":     "#2a3038",
    "plot_bg":  "#161b22",
    "axis":     "#e6edf3",
}


class TimeSeriesVisualization(Visualization):
    name = "Tijdreeks met afwijkingen"
    short_description = (
        "Lijn van waarden over tijd. Groene band toont het verwachte bereik; "
        "rode punten zijn afwijkingen."
    )
    requires = ["time", "value"]

    def render(self, df, time_col, value_col, category_col=None, theme="light", **_):
        p = DARK if theme == "dark" else LIGHT
        work = df.copy().sort_values(time_col).reset_index(drop=True)
        work[time_col] = pd.to_datetime(work[time_col])

        # Aggregeer per dag (over alle categorieën) voor een schone band-berekening
        daily = (
            work.set_index(time_col)[value_col].resample("D").sum().fillna(0)
        )

        # Verwachte lijn = rolling mediaan; band = ±2 * MAD
        w = min(14, max(3, len(daily) // 5))
        expected = daily.rolling(window=w, min_periods=2, center=True).median().bfill().ffill()
        resid = daily - expected
        mad = float(np.median(np.abs(resid - np.median(resid)))) or 1.0
        band = 2.5 * mad
        lower = (expected - band).clip(lower=0)
        upper = expected + band

        fig = go.Figure()

        # --- Band ---
        fig.add_trace(go.Scatter(
            x=upper.index, y=upper.values,
            mode="lines", line=dict(color="rgba(0,0,0,0)", width=0),
            showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=lower.index, y=lower.values,
            mode="lines", line=dict(color="rgba(0,0,0,0)", width=0),
            fill="tonexty", fillcolor=p["band"],
            name="Normbeeld",
            hovertemplate="<extra></extra>",
        ))

        # --- Verwachte lijn ---
        fig.add_trace(go.Scatter(
            x=expected.index, y=expected.values,
            mode="lines",
            line=dict(color=p["expected"], width=2),
            name="Verwacht",
            hovertemplate="%{x|%d %b}: verwacht %{y:.1f}<extra></extra>",
        ))

        # --- Werkelijke waarden, gekleurd ---
        if "is_anomaly" in work.columns:
            anom_per_day = work.groupby(work[time_col].dt.floor("D"))["is_anomaly"].any()
        else:
            anom_per_day = pd.Series(False, index=daily.index)

        marker_colors = [
            p["anomaly"] if anom_per_day.get(d, False) else p["actual"]
            for d in daily.index
        ]
        marker_sizes = [
            8 if anom_per_day.get(d, False) else 5
            for d in daily.index
        ]

        fig.add_trace(go.Scatter(
            x=daily.index, y=daily.values,
            mode="lines+markers",
            line=dict(color=p["actual"], width=1.5),
            marker=dict(
                color=marker_colors,
                size=marker_sizes,
                line=dict(width=0),
            ),
            name="Werkelijk",
            hovertemplate="%{x|%d %b}: %{y}<extra></extra>",
        ))

        fig.update_layout(
            height=460,
            plot_bgcolor=p["plot_bg"],
            paper_bgcolor=p["plot_bg"],
            font=dict(family="Inter, sans-serif", color=p["axis"], size=12),
            hovermode="x unified",
            margin=dict(l=40, r=20, t=20, b=40),
            legend=dict(
                orientation="h", yanchor="bottom", y=-0.18,
                xanchor="left", x=0,
                font=dict(size=10),
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(
                gridcolor=p["grid"], zeroline=False, title=None,
            ),
            yaxis=dict(
                gridcolor=p["grid"], zeroline=True,
                zerolinecolor=p["grid"], title=None,
                rangemode="nonnegative",
            ),
        )

        st.plotly_chart(
            fig, use_container_width=True,
            config={"displayModeBar": False},
        )
