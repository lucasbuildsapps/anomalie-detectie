"""Normbeeld-grafiek met groene band, forecast en per-methode lijnen."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.normbeeld import PREDICTION_METHODS, Normbeeld


LIGHT = {
    # Band = neutrale groene envelop; rand-lijntjes maken de grenzen scherp.
    "band":          "rgba(46, 139, 87, 0.10)",
    "band_edge":     "rgba(46, 139, 87, 0.45)",
    # Verwacht-lijn in een sterk contrasterende kleur (niet hetzelfde groen
    # als de band), zodat het normbeeld als duidelijke LIJN leest.
    "expected":      "#1a4d8c",
    "actual":        "#0a1929",
    "above":         "#c53030",
    "below":         "#1a4d8c",
    "now":           "#6c7886",
    "forecast_band": "rgba(120, 160, 200, 0.14)",
    "forecast_line": "#1a4d8c",
    "plot_bg":       "#ffffff",
    "grid":          "#eef0f3",
    "axis":          "#0a1929",
    "method_colors": {
        "stl":            "#ef6c00",
        "ets":            "#0277bd",
        "rolling":        "#7b1fa2",
        "seasonal_naive": "#00897b",
        "median":         "#546e7a",
    },
}
DARK = {
    "band":          "rgba(76, 218, 134, 0.14)",
    "band_edge":     "rgba(76, 218, 134, 0.50)",
    "expected":      "#79b8ff",
    "actual":        "#e6edf3",
    "above":         "#f87171",
    "below":         "#58a6ff",
    "now":           "#8b949e",
    "forecast_band": "rgba(94, 158, 255, 0.18)",
    "forecast_line": "#79b8ff",
    "plot_bg":       "#161b22",
    "grid":          "#2a3038",
    "axis":          "#e6edf3",
    "method_colors": {
        "stl":            "#ffa726",
        "ets":            "#4fc3f7",
        "rolling":        "#ba68c8",
        "seasonal_naive": "#4db6ac",
        "median":         "#90a4ae",
    },
}


def _palette(theme: str) -> dict:
    return DARK if theme == "dark" else LIGHT


def render_normbeeld_chart(
    nb: Normbeeld,
    theme: str = "light",
    height: int = 480,
    show_title: bool = False,
):
    p = _palette(theme)
    hist = nb.historical.copy()
    fc = nb.forecast.copy()

    fig = go.Figure()

    # --- Historische band (met scherpe rand-lijnen) ---
    fig.add_trace(go.Scatter(
        x=hist["date"], y=hist["upper"],
        mode="lines",
        line=dict(color=p["band_edge"], width=1, dash="dot"),
        name="Bovengrens normbeeld", showlegend=False,
        hovertemplate="bovengrens %{y:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hist["date"], y=hist["lower"],
        mode="lines",
        line=dict(color=p["band_edge"], width=1, dash="dot"),
        fill="tonexty", fillcolor=p["band"],
        name="Normbeeld (band)",
        hovertemplate="ondergrens %{y:.1f}<extra></extra>",
    ))

    # --- Werkelijke waarden (gekleurde punten, dunne lijn) ---
    marker_colors = [
        p["above"] if s == "boven"
        else (p["below"] if s == "onder" else p["actual"])
        for s in hist["status"]
    ]
    marker_sizes = [
        9 if s != "normaal" else 5 for s in hist["status"]
    ]
    fig.add_trace(go.Scatter(
        x=hist["date"], y=hist["actual"],
        mode="lines+markers",
        line=dict(color=p["actual"], width=1.0),
        opacity=0.85,
        marker=dict(color=marker_colors, size=marker_sizes,
                    line=dict(width=0)),
        name="Werkelijk",
        hovertemplate="%{x|%d %b}: %{y}<extra></extra>",
    ))

    # --- Normbeeld-lijn: het verwachte niveau, prominent en bovenop ---
    fig.add_trace(go.Scatter(
        x=hist["date"], y=hist["expected"],
        mode="lines",
        line=dict(color=p["expected"], width=3),
        name="Normbeeld (verwacht)",
        hovertemplate="%{x|%d %b}: verwacht %{y:.1f}<extra></extra>",
    ))

    # --- Forecast band (ensemble) ---
    fig.add_trace(go.Scatter(
        x=fc["date"], y=fc["upper"],
        mode="lines", line=dict(color="rgba(0,0,0,0)", width=0),
        name="_fc_upper", showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=fc["date"], y=fc["lower"],
        mode="lines", line=dict(color="rgba(0,0,0,0)", width=0),
        fill="tonexty", fillcolor=p["forecast_band"],
        name="Voorspelling (band)",
        hoverinfo="skip",
    ))

    # --- Per-methode forecast-lijnen (laat verschillen zien) ---
    method_colors = p["method_colors"]
    for m_key, m_fc in nb.per_method_forecast.items():
        color = method_colors.get(m_key, p["forecast_line"])
        label = PREDICTION_METHODS.get(m_key, m_key)
        fig.add_trace(go.Scatter(
            x=m_fc["date"], y=m_fc["expected"],
            mode="lines",
            line=dict(color=color, width=1.4, dash="dot"),
            name=f"→ {label}",
            hovertemplate=f"<b>{label}</b><br>%{{x|%d %b}}: %{{y:.1f}}<extra></extra>",
        ))

    # --- Ensemble forecast-lijn (over de individuele heen) ---
    fig.add_trace(go.Scatter(
        x=fc["date"], y=fc["expected"],
        mode="lines",
        line=dict(color=p["forecast_line"], width=2.4, dash="dash"),
        name="Voorspelling (ensemble)",
        hovertemplate="<b>Ensemble</b><br>%{x|%d %b}: %{y:.1f}<extra></extra>",
    ))

    # --- "Nu"-lijn ---
    now_date = hist["date"].iloc[-1]
    fig.add_vline(
        x=now_date,
        line=dict(color=p["now"], width=1, dash="dot"),
    )
    fig.add_annotation(
        x=now_date, y=1.02, yref="paper",
        text="nu", showarrow=False,
        font=dict(size=10, color=p["now"], family="JetBrains Mono"),
        xanchor="center",
    )

    fig.update_layout(
        title=dict(text="", font=dict(size=1)),
        height=height,
        plot_bgcolor=p["plot_bg"],
        paper_bgcolor=p["plot_bg"],
        font=dict(family="Inter, sans-serif", color=p["axis"], size=12),
        hovermode="x unified",
        margin=dict(l=40, r=20, t=20, b=40),
        hoverlabel=dict(
            bgcolor=p["plot_bg"],
            font_size=11,
            font_family="Inter",
            namelength=-1,
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.28,
            xanchor="left", x=0,
            font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            gridcolor=p["grid"], zeroline=False, title=None,
            showspikes=False,
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
