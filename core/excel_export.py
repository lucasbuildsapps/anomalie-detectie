"""XLSX-export van een complete analyse: samenvatting, bevindingen,
normbeeld-tabel en ruwe afwijkingen."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pandas as pd

from core.auto_pilot import AutoPilotResult, build_findings
from core.normbeeld import Normbeeld


def build_excel_export(
    result: AutoPilotResult,
    normbeelds: dict[str, Normbeeld],
    dataset_name: str,
    description: str | None = None,
) -> bytes:
    res = result.results
    findings = build_findings(result, top_n=50)
    buf = BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        # ----- Samenvatting -----
        n_hoog = int((res["severity"] == "hoog").sum())
        n_mid = int((res["severity"] == "midden").sum())
        n_laag = int((res["severity"] == "laag").sum())

        summary_rows = [
            ["Dataset", dataset_name],
            ["Beschrijving", description or ""],
            ["Gegenereerd", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["Observaties", len(res)],
            ["Methodes gebruikt", ", ".join(result.methods_used)],
            ["Gevoeligheid", result.sensitivity_used],
            ["Iteraties", result.iterations],
            ["Doorlooptijd (s)", f"{result.duration_seconds:.2f}"],
            ["", ""],
            ["Afwijkingen — HOOG", n_hoog],
            ["Afwijkingen — MIDDEN", n_mid],
            ["Afwijkingen — LAAG", n_laag],
            ["Afwijkingen — totaal", n_hoog + n_mid + n_laag],
        ]
        pd.DataFrame(summary_rows, columns=["Veld", "Waarde"]).to_excel(
            xw, sheet_name="Samenvatting", index=False
        )

        # ----- Normbeeld per locatie -----
        nb_rows = []
        for loc, nb in normbeelds.items():
            nb_rows.append({
                "Locatie": nb.location,
                "Categorie": nb.category or "",
                "Verwachte waarde (per dag)": round(nb.expected_value, 2),
                "Ondergrens band": round(nb.lower_band, 2),
                "Bovengrens band": round(nb.upper_band, 2),
                "Vertrouwen": nb.confidence,
                "Methode": nb.method_used,
                "Historische dagen": nb.n_history_days,
                "Recente afwijkingen (14d)": nb.n_recent_deviations,
                "Patroon": nb.pattern_description,
            })
        if nb_rows:
            pd.DataFrame(nb_rows).to_excel(
                xw, sheet_name="Normbeeld", index=False
            )

        # ----- Bevindingen -----
        find_rows = []
        for f in findings:
            exp = f["explanation"]
            find_rows.append({
                "Datum": f["datum"],
                "Locatie": f["locatie"],
                "Waarde": f["waarde"],
                "Severity": f["severity"],
                "Stemmen": f"{f['stemmen']}/{f['totaal_methodes']}",
                "Observatie": exp.get("observation", ""),
                "Baseline": exp.get("baseline", ""),
                "Factor": exp.get("factor", ""),
                "Weekdag-context": exp.get("weekday_context", ""),
                "Methodes (aan)": ", ".join(f["methodes_aan"]),
                "Methodes (uit)": ", ".join(f["methodes_uit"]),
            })
        if find_rows:
            pd.DataFrame(find_rows).to_excel(
                xw, sheet_name="Bevindingen", index=False
            )

        # ----- Forecast per locatie -----
        fc_rows = []
        for loc, nb in normbeelds.items():
            for _, r in nb.forecast.iterrows():
                fc_rows.append({
                    "Locatie": nb.location,
                    "Datum": pd.Timestamp(r["date"]).date().isoformat(),
                    "Verwacht": round(float(r["expected"]), 2),
                    "Ondergrens": round(float(r["lower"]), 2),
                    "Bovengrens": round(float(r["upper"]), 2),
                })
        if fc_rows:
            pd.DataFrame(fc_rows).to_excel(
                xw, sheet_name="Forecast", index=False
            )

        # ----- Ruwe afwijkingen -----
        anom = res[res["is_anomaly"]].copy()
        if not anom.empty:
            anom = anom.sort_values(
                ["severity", "anomaly_score"], ascending=[True, False]
            )
            cols = [c for c in [
                "timestamp", "location_name", "category", "value",
                "severity", "votes", "anomaly_score",
            ] if c in anom.columns]
            anom[cols].to_excel(xw, sheet_name="Ruwe afwijkingen", index=False)

    return buf.getvalue()


def excel_filename(dataset_name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in dataset_name)[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"analyse_{safe}_{stamp}.xlsx"
