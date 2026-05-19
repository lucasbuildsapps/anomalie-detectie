import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from .base import Visualization


class GeoMapVisualization(Visualization):
    name = "Geografische kaart"
    short_description = (
        "Toont waarnemingen op een kaart. Locaties met afwijkingen worden "
        "rood en groter gemarkeerd."
    )
    requires = ["time", "value", "lat", "lon"]

    def render(self, df, time_col, value_col, **_):
        if not {"lat", "lon"}.issubset(df.columns):
            st.warning(
                "Voor deze visualisatie zijn de kolommen 'lat' en 'lon' vereist."
            )
            return
        gdf = df.dropna(subset=["lat", "lon"])
        if gdf.empty:
            st.warning("Geen geldige coördinaten in deze dataset.")
            return

        loc_col = "location_name" if "location_name" in gdf.columns else None

        group_cols = ["lat", "lon"] + ([loc_col] if loc_col else [])
        agg_spec = {value_col: "sum"}
        if "is_anomaly" in gdf.columns:
            agg_spec["is_anomaly"] = "sum"
        if "anomaly_score" in gdf.columns:
            agg_spec["anomaly_score"] = "max"
        agg = gdf.groupby(group_cols).agg(agg_spec).reset_index()
        agg = agg.rename(columns={value_col: "totaal"})
        if "is_anomaly" in agg.columns:
            agg = agg.rename(columns={"is_anomaly": "afwijkingen"})
        else:
            agg["afwijkingen"] = 0

        center = [gdf["lat"].mean(), gdf["lon"].mean()]
        m = folium.Map(location=center, zoom_start=7, tiles="CartoDB positron")

        for _, row in agg.iterrows():
            n_anom = int(row["afwijkingen"])
            label = str(row[loc_col]) if loc_col else "Locatie"
            color = "red" if n_anom > 0 else "#1f77b4"
            radius = 8 + min(25, n_anom * 1.5)
            popup_html = (
                f"<b>{label}</b><br>"
                f"Totaal: {int(row['totaal'])}<br>"
                f"Afwijkingen: {n_anom}"
            )
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=radius,
                color=color,
                fill=True,
                fill_opacity=0.6,
                weight=2,
                popup=folium.Popup(popup_html, max_width=260),
                tooltip=label,
            ).add_to(m)

        st_folium(m, use_container_width=True, height=500,
                  returned_objects=[])
