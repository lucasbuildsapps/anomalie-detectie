"""PDF-briefing: rapport met bevindingen, profiel en method-attributie.
Pure-Python via fpdf2 — geen externe binaries nodig."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pandas as pd
from fpdf import FPDF

from core.auto_pilot import AutoPilotResult, build_findings
from core.explanations import explanation_to_markdown


PRIMARY_RGB = (26, 77, 140)        # #1a4d8c
SEV_HIGH_RGB = (197, 48, 48)       # #c53030
SEV_MID_RGB = (192, 86, 33)        # #c05621
SEV_LOW_RGB = (151, 90, 22)        # #975a16
MUTED_RGB = (100, 110, 125)


class BriefingPDF(FPDF):
    def __init__(self, header_left: str = "ANOMALIE-DETECTIE  //  INTERN"):
        super().__init__()
        self._header_left = header_left

    def header(self):
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*MUTED_RGB)
        self.cell(95, 5, self._header_left, align="L")
        self.cell(95, 5, datetime.now().strftime("%Y-%m-%d %H:%M"), align="R")
        self.ln(2)
        self.set_draw_color(*PRIMARY_RGB)
        self.set_line_width(0.4)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        self.set_text_color(0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED_RGB)
        self.cell(0, 5, f"Pagina {self.page_no()}", align="C")


def _section_title(pdf: FPDF, text: str):
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*PRIMARY_RGB)
    pdf.cell(0, 7, _safe(text.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*PRIMARY_RGB)
    pdf.set_line_width(0.2)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 35, pdf.get_y())
    pdf.ln(3)
    pdf.set_text_color(0)


def _key_value_table(pdf: FPDF, pairs: list[tuple[str, str]], col_widths=(55, 130)):
    pdf.set_font("Helvetica", "", 10)
    for label, value in pairs:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(col_widths[0], 6, _safe(label))
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(col_widths[1], 6, _safe(value))


def _severity_color(severity: str) -> tuple:
    return {
        "hoog": SEV_HIGH_RGB,
        "midden": SEV_MID_RGB,
        "laag": SEV_LOW_RGB,
    }.get(severity, MUTED_RGB)


_UNICODE_REPLACEMENTS = {
    "—": "-",     # em dash
    "–": "-",     # en dash
    "−": "-",     # minus sign
    "‘": "'",     # left single quote
    "’": "'",     # right single quote
    "“": '"',     # left double quote
    "”": '"',     # right double quote
    "…": "...",   # ellipsis
    "•": "*",     # bullet
    "·": "*",     # middle dot
    "→": "->",    # right arrow
    "←": "<-",    # left arrow
    "↑": "^",     # up arrow
    "↓": "v",     # down arrow
    "×": "x",     # multiplication sign
    "°": " deg",  # degree sign (niet in pure Latin-1)
    " ": " ",     # non-breaking space
}


def _safe(text) -> str:
    """Encode-veilig maken voor Helvetica/Latin-1.
    Vervangt veelvoorkomende Unicode-tekens met ASCII-equivalenten."""
    s = "" if text is None else str(text)
    for k, v in _UNICODE_REPLACEMENTS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def build_briefing_pdf(
    result: AutoPilotResult,
    dataset_name: str,
    description: str | None = None,
    normbeelds: dict | None = None,
) -> bytes:
    res = result.results
    profile = result.profile
    findings = build_findings(result, top_n=15)

    pdf = BriefingPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # === Titel ===
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Intelligence Briefing",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED_RGB)
    pdf.cell(0, 5, _safe("Geautomatiseerde anomaliedetectie — interne rapportage"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(2)

    # === Header-info ===
    ts = pd.to_datetime(res["timestamp"]) if "timestamp" in res.columns else None
    period = (
        f"{ts.min().date()} t/m {ts.max().date()}"
        if ts is not None and not ts.empty else "—"
    )
    n_obs = len(res)
    n_loc = (
        int(res["location_name"].nunique())
        if "location_name" in res.columns
        and res["location_name"].notna().any() else 0
    )
    _key_value_table(pdf, [
        ("Dataset", _safe(dataset_name)),
        ("Beschrijving", _safe(description or "—")),
        ("Periode", period),
        ("Observaties", f"{n_obs:,}".replace(",", ".")),
        ("Locaties", str(n_loc) if n_loc else "—"),
        ("Gegenereerd", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ])

    # === 1. Executive summary ===
    _section_title(pdf, "1. Samenvatting")
    n_high = int((res["severity"] == "hoog").sum())
    n_mid = int((res["severity"] == "midden").sum())
    n_low = int((res["severity"] == "laag").sum())
    n_anom = n_high + n_mid + n_low

    summary_parts = []
    if n_anom == 0:
        summary_parts.append(
            "Het systeem heeft geen significante afwijkingen gedetecteerd "
            "in deze dataset. De waarnemingen vallen binnen het verwachte "
            "patroon."
        )
    else:
        summary_parts.append(
            f"De analyse heeft {n_anom} afwijking(en) geïdentificeerd "
            f"over {n_obs} observaties. Daarvan zijn {n_high} als HOOG "
            f"geclassificeerd, {n_mid} als MIDDEN en {n_low} als LAAG, "
            "op basis van het aantal detectiemethoden dat het eens is."
        )
        if findings:
            top = findings[0]
            summary_parts.append(
                f"Primaire bevinding: {top['locatie']} op {top['datum']} — "
                f"{top['waarde']} waarnemingen, bevestigd door "
                f"{top['stemmen']}/{top['totaal_methodes']} methodes."
            )

    pdf.set_font("Helvetica", "", 10)
    for p in summary_parts:
        pdf.multi_cell(0, 5, _safe(p))
        pdf.ln(1)

    # === 2. Key metrics ===
    _section_title(pdf, "2. Kerncijfers")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(45, 6, "Categorie")
    pdf.cell(40, 6, "Aantal", align="R")
    pdf.cell(105, 6, "Toelichting", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for sev_name, n_sev, sev_desc in [
        ("HOOG", n_high, "(vrijwel) alle methodes eens — sterke consensus"),
        ("MIDDEN", n_mid, "duidelijke meerderheid van methodes bevestigt"),
        ("LAAG", n_low, "precies 2 methodes — mogelijk vals alarm"),
    ]:
        c = _severity_color(sev_name.lower())
        pdf.set_text_color(*c)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(45, 6, sev_name)
        pdf.set_text_color(0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(40, 6, str(n_sev), align="R")
        pdf.cell(105, 6, _safe(sev_desc), new_x="LMARGIN", new_y="NEXT")

    # === 3. Data-profiel ===
    _section_title(pdf, "3. Dataprofiel")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _safe(
        "Het volgende werd in de data gedetecteerd vóór de analyse:"
    ))
    pdf.ln(1)
    _key_value_table(pdf, [
        ("Aantal observaties", profile.n_observations),
        ("Aantal dagen", profile.n_days),
        ("Seizoenspatroon",
         f"{profile.seasonality_period} dagen" if profile.seasonality_period else "Niet gedetecteerd"),
        ("Trend",
         f"Ja (helling {profile.trend_slope:+.3%}/dag)" if profile.has_trend else "Geen significante trend"),
        ("Stationariteit",
         "Stationair" if profile.is_stationary else "Niet-stationair"),
        ("Dagelijks gemiddelde", f"{profile.daily_mean:.2f}"),
        ("Dagelijkse std. dev.", f"{profile.daily_std:.2f}"),
    ])

    # === 3b. Normbeeld per locatie ===
    if normbeelds:
        _section_title(pdf, "4. Normbeeld per locatie")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _safe(
            "Per locatie het verwachte niveau, de tolerantieband en het aantal "
            "recente afwijkingen."
        ))
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(60, 6, "Locatie")
        pdf.cell(28, 6, "Verwacht", align="R")
        pdf.cell(36, 6, "Band", align="R")
        pdf.cell(26, 6, "Recent afw.", align="R")
        pdf.cell(0, 6, "Vertrouwen", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        for loc, nb in normbeelds.items():
            if pdf.get_y() > 260:
                pdf.add_page()
            pdf.cell(60, 5, _safe(loc)[:30])
            pdf.cell(28, 5, f"{nb.expected_value:.1f}", align="R")
            pdf.cell(36, 5,
                     f"{nb.lower_band:.0f}-{nb.upper_band:.0f}", align="R")
            pdf.cell(26, 5, str(nb.n_recent_deviations), align="R")
            pdf.cell(0, 5, _safe(nb.confidence),
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # === 5. Method attribution ===
    _section_title(pdf, "5. Gebruikte detectiemethoden")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _safe(
        f"De auto-pilot heeft {len(result.methods_used)} methodes gekozen op "
        f"basis van het dataprofiel, met gevoeligheid '{result.sensitivity_used}'."
    ))
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(85, 6, "Methode")
    pdf.cell(35, 6, "Gemarkeerd", align="R")
    pdf.cell(70, 6, "% van rijen", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    n_total = max(1, len(res))
    for m, flags in result.method_outputs.items():
        n_flagged = int(flags.sum())
        pdf.cell(85, 5, _safe(m))
        pdf.cell(35, 5, str(n_flagged), align="R")
        pdf.cell(70, 5, f"{100 * n_flagged / n_total:.1f}%",
                 align="R", new_x="LMARGIN", new_y="NEXT")

    # === 6. Key findings ===
    _section_title(pdf, "6. Belangrijkste bevindingen")
    if not findings:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*MUTED_RGB)
        pdf.multi_cell(0, 5, _safe("Geen afwijkingen om te rapporteren."))
        pdf.set_text_color(0)
    else:
        for i, f in enumerate(findings, start=1):
            if pdf.get_y() > 245:
                pdf.add_page()
            c = _severity_color(f["severity"])
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*c)
            pdf.cell(15, 6, f"#{i}")
            pdf.cell(0, 6, _safe(f"[{f['severity'].upper()}]  {f['locatie']} - {f['datum']}"),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0)

            pdf.set_font("Helvetica", "", 9)
            text = explanation_to_markdown(f["explanation"])
            pdf.multi_cell(0, 4.5, _safe(text))
            pdf.ln(2)

    # === 7. Appendix ===
    if not res[res["is_anomaly"]].empty:
        pdf.add_page()
        _section_title(pdf, "7. Bijlage - alle afwijkingen")
        anom = res[res["is_anomaly"]].copy()
        anom = anom.sort_values(["severity", "anomaly_score"],
                                ascending=[True, False])
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(28, 5, "Datum")
        pdf.cell(60, 5, "Locatie")
        pdf.cell(20, 5, "Waarde", align="R")
        pdf.cell(25, 5, "Severity")
        pdf.cell(20, 5, "Stemmen", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        for _, r in anom.head(80).iterrows():
            date = pd.Timestamp(r["timestamp"]).date().isoformat()
            loc = r.get("location_name") or r.get("category") or "—"
            sev = r["severity"]
            pdf.cell(28, 4.5, date)
            pdf.cell(60, 4.5, _safe(str(loc))[:30])
            pdf.cell(20, 4.5, str(int(r["value"])), align="R")
            pdf.set_text_color(*_severity_color(sev))
            pdf.cell(25, 4.5, sev.upper())
            pdf.set_text_color(0)
            pdf.cell(20, 4.5, str(int(r["votes"])),
                     align="R", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def briefing_filename(dataset_name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in dataset_name)[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"briefing_{safe}_{stamp}.pdf"
