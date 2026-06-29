"""Grafieken voor de vergelijkingspagina: twee reeksen overlay (dubbele
y-as) en de cross-correlatie-curve (correlatie per vertraging)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.comparison import LagResult

LIGHT = {
    "a": "#1a4d8c", "b": "#c05621", "grid": "#eef0f3",
    "bg": "#ffffff", "axis": "#0a1929", "marker": "#2e8b57",
    "bar": "#1a4d8c", "bar_best": "#c53030", "now": "#6c7886",
}
DARK = {
    "a": "#58a6ff", "b": "#fb923c", "grid": "#2a3038",
    "bg": "#161b22", "axis": "#e6edf3", "marker": "#4cda86",
    "bar": "#58a6ff", "bar_best": "#f87171", "now": "#8b949e",
}


def _pal(theme):
    return DARK if theme == "dark" else LIGHT


def render_overlay(
    series_a: pd.Series, series_b: pd.Series,
    label_a: str, label_b: str,
    theme: str = "light",
    change_points: list[dict] | None = None,
    markers: list[dict] | None = None,
    shift_b_by: int = 0,
):
    """Twee reeksen op één tijd-as met dubbele y-as. Optioneel reeks B
    verschoven met de gevonden lag, zodat je het verband visueel ziet."""
    p = _pal(theme)
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=series_a.index, y=series_a.values,
        mode="lines", name=label_a,
        line=dict(color=p["a"], width=2),
        yaxis="y1",
        hovertemplate="%{x|%d %b %Y}: %{y:.0f}<extra>" + label_a + "</extra>",
    ))

    b_index = series_b.index
    name_b = label_b
    if shift_b_by:
        # Verschuif B terug zodat hij over A heen valt (visuele uitlijning)
        freq = pd.infer_freq(series_b.index) or "D"
        try:
            b_index = series_b.index - pd.tseries.frequencies.to_offset(freq) * shift_b_by
        except Exception:
            b_index = series_b.index
        name_b = f"{label_b} (−{shift_b_by} verschoven)"

    fig.add_trace(go.Scatter(
        x=b_index, y=series_b.values,
        mode="lines", name=name_b,
        line=dict(color=p["b"], width=2, dash="dot"),
        yaxis="y2",
        hovertemplate="%{x|%d %b %Y}: %{y:.0f}<extra>" + label_b + "</extra>",
    ))

    if change_points:
        for cp in change_points:
            fig.add_vline(
                x=cp["date"],
                line=dict(color=p["now"], width=1, dash="dot"),
            )
            fig.add_annotation(
                x=cp["date"], y=1.04, yref="paper",
                text=("▲" if cp["direction"] == "stijging" else "▼"),
                showarrow=False,
                font=dict(size=11, color=p["now"]),
            )

    if markers:
        for mk in markers:
            fig.add_vline(
                x=pd.Timestamp(mk["date"]),
                line=dict(color=p["axis"], width=1.5, dash="solid"),
            )
            fig.add_annotation(
                x=pd.Timestamp(mk["date"]), y=1.0, yref="paper",
                text=mk.get("label", ""), showarrow=False,
                font=dict(size=10, color=p["axis"]),
                bgcolor=p["bg"], xanchor="left", yanchor="bottom",
            )

    fig.update_layout(
        height=440,
        plot_bgcolor=p["bg"], paper_bgcolor=p["bg"],
        font=dict(family="Inter, sans-serif", color=p["axis"], size=12),
        hovermode="x unified",
        margin=dict(l=50, r=50, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22,
                    xanchor="left", x=0, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(gridcolor=p["grid"], zeroline=False, title=None),
        yaxis=dict(gridcolor=p["grid"], zeroline=False,
                   title=dict(text=label_a, font=dict(color=p["a"])),
                   tickfont=dict(color=p["a"]), rangemode="nonnegative"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    title=dict(text=label_b, font=dict(color=p["b"])),
                    tickfont=dict(color=p["b"]), rangemode="nonnegative"),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})


def render_lag_curve(lag: LagResult, label_a: str, label_b: str,
                     theme: str = "light"):
    """Correlatie per vertraging. De hoogste balk is de meest waarschijnlijke
    lag tussen de twee reeksen."""
    p = _pal(theme)
    colors = [
        p["bar_best"] if l == lag.best_lag else p["bar"] for l in lag.lags
    ]
    fig = go.Figure(go.Bar(
        x=lag.lags, y=lag.corrs, marker_color=colors,
        hovertemplate="vertraging %{x} " + lag.unit
                      + "<br>correlatie %{y:.2f}<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color=p["now"], width=1, dash="dot"))
    fig.update_layout(
        height=300,
        plot_bgcolor=p["bg"], paper_bgcolor=p["bg"],
        font=dict(family="Inter, sans-serif", color=p["axis"], size=12),
        margin=dict(l=50, r=20, t=20, b=44),
        xaxis=dict(
            gridcolor=p["grid"], zeroline=False,
            title=dict(text=f"Vertraging van {label_b} t.o.v. {label_a} "
                            f"(in {lag.unit}en)"),
        ),
        yaxis=dict(gridcolor=p["grid"], zeroline=True,
                   zerolinecolor=p["grid"], title="correlatie"),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})
