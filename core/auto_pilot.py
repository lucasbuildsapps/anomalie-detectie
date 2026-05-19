"""Auto-pilot: profile data, pick methods, run them, build consensus ensemble.

Output per row:
    - votes: hoeveel methodes vinden de rij afwijkend
    - severity: 'hoog' / 'midden' / 'laag' / None
    - is_anomaly: True als severity != None

Iteratie: als er bij default-gevoeligheid 0 high-severity gevallen zijn,
herhalen met losser instellingen. Als er > 3% high-severity is, strakker.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.activity_log import (
    ActivityLog, TAG_DATA, TAG_DETECT, TAG_DONE, TAG_PROFIL,
    TAG_SELECT, TAG_TUNE, TAG_VOTING,
)
from core.explanations import explain_finding
from core.profiler import DataProfile, profile_data
from core.registry import get_detectors


SEVERITY_HIGH_FRAC = 0.8
SEVERITY_MID_FRAC = 0.5
SEVERITY_LOW_FRAC = 0.3

TARGET_HIGH_MIN = 0.005
TARGET_HIGH_MAX = 0.03


@dataclass
class AutoPilotResult:
    results: pd.DataFrame
    profile: DataProfile
    methods_used: list[str]
    sensitivity_used: str
    iterations: int
    method_outputs: dict           # method_name -> per-row bool array
    log: ActivityLog
    duration_seconds: float


def _select_methods(profile: DataProfile, log: ActivityLog) -> list[str]:
    available = list(get_detectors().keys())
    chosen: list[str] = []
    reasons: list[str] = []

    def has(substr: str) -> str | None:
        for n in available:
            if substr.lower() in n.lower():
                return n
        return None

    if (n := has("Z-score")):
        chosen.append(n); reasons.append("Z-score: standaard")
    if (n := has("Isolation Forest")):
        chosen.append(n); reasons.append("Isolation Forest: multidimensionaal vangnet")
    if (n := has("Rolling")):
        chosen.append(n); reasons.append("Rolling: korte-termijn afwijkingen")
    if profile.seasonality_period and profile.n_days >= 2 * profile.seasonality_period + 1:
        if (n := has("STL")):
            chosen.append(n)
            reasons.append(
                f"STL: seizoenspatroon van {profile.seasonality_period} dagen gedetecteerd"
            )
    if (profile.has_trend or not profile.is_stationary) and profile.n_days >= 20:
        if (n := has("Change-point")):
            chosen.append(n)
            reasons.append(
                "Change-point: " +
                ("trend aanwezig" if profile.has_trend else "niet-stationaire reeks")
            )

    for r in reasons:
        log.log(TAG_SELECT, r)
    return chosen


_SENSITIVITY_PARAMS = {
    "streng": {
        "Z-score (MAD)": {"threshold": 4.5},
        "Rolling mean ± N·std": {"threshold": 4.0},
        "STL residual": {"threshold": 4.5},
        "Change-point (windowed t-test)": {"threshold": 3.5},
        "Isolation Forest": {"contamination": 0.02},
    },
    "normaal": {
        "Z-score (MAD)": {"threshold": 3.5},
        "Rolling mean ± N·std": {"threshold": 3.0},
        "STL residual": {"threshold": 3.5},
        "Change-point (windowed t-test)": {"threshold": 2.5},
        "Isolation Forest": {"contamination": 0.05},
    },
    "soepel": {
        "Z-score (MAD)": {"threshold": 2.5},
        "Rolling mean ± N·std": {"threshold": 2.5},
        "STL residual": {"threshold": 2.5},
        "Change-point (windowed t-test)": {"threshold": 2.0},
        "Isolation Forest": {"contamination": 0.10},
    },
}


def _params_for(sensitivity: str, method_name: str) -> dict:
    return _SENSITIVITY_PARAMS.get(sensitivity, {}).get(method_name, {})


def _run_methods_on_group(
    df: pd.DataFrame,
    methods: list[str],
    sensitivity: str,
    log: ActivityLog,
    group_label: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    detectors = get_detectors()
    out = df.copy().sort_values("timestamp").reset_index(drop=True)
    votes = np.zeros(len(out), dtype=int)
    per_method: dict = {}

    prefix = f"[{group_label}] " if group_label else ""
    for m in methods:
        if m not in detectors:
            continue
        params = _params_for(sensitivity, m)
        try:
            sub = detectors[m].detect(df, "timestamp", "value", **params)
        except Exception as e:
            log.log("ERROR", f"{prefix}{m} faalde: {e}")
            continue
        sub = sub.sort_values("timestamp").reset_index(drop=True)
        if len(sub) != len(out):
            continue
        flag = sub["is_anomaly"].astype(bool).to_numpy()
        n_flagged = int(flag.sum())
        log.log(TAG_DETECT, f"{prefix}{m}: {n_flagged} punten gemarkeerd")
        votes += flag.astype(int)
        per_method[m] = flag

    out["votes"] = votes
    n_methods = max(1, len(per_method))
    fracs = votes / n_methods
    severity = np.full(len(out), None, dtype=object)
    severity[fracs >= SEVERITY_HIGH_FRAC] = "hoog"
    severity[(fracs >= SEVERITY_MID_FRAC) & (fracs < SEVERITY_HIGH_FRAC)] = "midden"
    severity[(fracs >= SEVERITY_LOW_FRAC) & (fracs < SEVERITY_MID_FRAC)] = "laag"
    out["severity"] = severity
    out["is_anomaly"] = out["severity"].notna()
    out["anomaly_score"] = fracs

    return out, per_method


_MIN_GROUP_SIZE = 5  # detectors hebben niets te zeggen over groepen < 5 punten


def _run_with_grouping(
    df: pd.DataFrame,
    methods: list[str],
    sensitivity: str,
    group_col: str | None,
    log: ActivityLog,
) -> tuple[pd.DataFrame, dict]:
    if group_col and group_col in df.columns:
        parts = []
        merged_per_method: dict = {}
        all_groups = list(df.groupby(group_col, dropna=False))
        # Skip tiny groepen — geen zinvolle detectie op 1-4 punten
        groups = [(k, g) for k, g in all_groups if len(g) >= _MIN_GROUP_SIZE]
        skipped = len(all_groups) - len(groups)
        log.log(
            TAG_DETECT,
            f"Per groep draaien op '{group_col}': {len(groups)} groepen "
            f"({skipped} tiny groepen overgeslagen)",
        )
        for key, g in groups:
            sub, per = _run_methods_on_group(
                g, methods, sensitivity, log, group_label=str(key)
            )
            parts.append(sub)
            for name, flag in per.items():
                merged_per_method.setdefault(name, []).append(flag)
        if not parts:
            return _run_methods_on_group(df, methods, sensitivity, log)
        merged = pd.concat(parts, ignore_index=True).sort_values("timestamp")
        return merged, {k: np.concatenate(v) for k, v in merged_per_method.items()}
    return _run_methods_on_group(df, methods, sensitivity, log)


def run_auto_pilot(
    df: pd.DataFrame,
    group_col: str | None = None,
    log: ActivityLog | None = None,
) -> AutoPilotResult:
    if log is None:
        log = ActivityLog()
    start = time.time()

    # === Data ingest ===
    log.log(TAG_DATA, f"{len(df)} observaties ingelezen")
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
        log.log(TAG_DATA, f"Periode: {ts.min().date()} t/m {ts.max().date()} "
                          f"({(ts.max() - ts.min()).days + 1} dagen)")
    if "location_name" in df.columns and df["location_name"].notna().any():
        locs = df["location_name"].dropna().unique()
        log.log(TAG_DATA, f"{len(locs)} locaties: {', '.join(map(str, locs[:5]))}"
                          + (" ..." if len(locs) > 5 else ""))
    if group_col:
        log.log(TAG_DATA, f"Groeperen per: {group_col}")

    # === Profileren ===
    log.log(TAG_PROFIL, "Data-eigenschappen analyseren...")
    profile = profile_data(df, "timestamp", "value")
    if profile.seasonality_period:
        log.log(TAG_PROFIL,
                f"Periodiek patroon van {profile.seasonality_period} dagen gedetecteerd")
    else:
        log.log(TAG_PROFIL, "Geen duidelijk periodiek patroon")
    log.log(TAG_PROFIL,
            f"Trend: {'aanwezig' if profile.has_trend else 'afwezig'} "
            f"(helling {profile.trend_slope:+.3%}/dag)")
    log.log(TAG_PROFIL,
            f"Stationariteit: {'ja' if profile.is_stationary else 'nee'}")

    # === Methode-selectie ===
    methods = _select_methods(profile, log)
    log.log(TAG_SELECT, f"{len(methods)} methodes geselecteerd")

    # === Detectie (met iteratie) ===
    sensitivity = "normaal"
    iterations = 0
    results = None
    per_method: dict = {}
    for attempt in range(3):
        iterations += 1
        log.log(TAG_TUNE, f"Iteratie {attempt + 1}: gevoeligheid '{sensitivity}'")
        results, per_method = _run_with_grouping(
            df, methods, sensitivity, group_col, log
        )
        n_high = int((results["severity"] == "hoog").sum())
        n_total = max(1, len(results))
        rate_high = n_high / n_total
        log.log(TAG_VOTING, f"  → {n_high} hoog ({rate_high * 100:.1f}% van rijen)")

        if rate_high < TARGET_HIGH_MIN and sensitivity != "soepel":
            log.log(TAG_TUNE, "Te weinig signaal — terug naar 'soepel'")
            sensitivity = "soepel"
            continue
        if rate_high > TARGET_HIGH_MAX and sensitivity != "streng":
            log.log(TAG_TUNE, "Te veel signaal — terug naar 'streng'")
            sensitivity = "streng"
            continue
        break

    # === Stemmen tellen ===
    n_high = int((results["severity"] == "hoog").sum())
    n_mid = int((results["severity"] == "midden").sum())
    n_low = int((results["severity"] == "laag").sum())
    log.log(TAG_VOTING, f"HOOG  (≥{SEVERITY_HIGH_FRAC * 100:.0f}% methodes): {n_high}")
    log.log(TAG_VOTING, f"MID   (≥{SEVERITY_MID_FRAC * 100:.0f}%):  {n_mid}")
    log.log(TAG_VOTING, f"LAAG  (≥{SEVERITY_LOW_FRAC * 100:.0f}%):  {n_low}")

    duration = time.time() - start
    log.log(TAG_DONE, f"Analyse voltooid in {duration:.1f}s")

    return AutoPilotResult(
        results=results,
        profile=profile,
        methods_used=methods,
        sensitivity_used=sensitivity,
        iterations=iterations,
        method_outputs=per_method,
        log=log,
        duration_seconds=duration,
    )


def build_findings(
    result: AutoPilotResult,
    top_n: int = 20,
) -> list[dict]:
    """Top bevindingen, gesorteerd op severity dan score."""
    res = result.results
    anom = res[res["is_anomaly"]].copy()
    if anom.empty:
        return []

    severity_order = {"hoog": 0, "midden": 1, "laag": 2}
    anom = anom.assign(_sev_rank=anom["severity"].map(severity_order))
    anom = anom.sort_values(
        ["_sev_rank", "anomaly_score", "value"],
        ascending=[True, False, False],
    ).head(top_n)

    findings = []
    for idx, row in anom.iterrows():
        methods_flagged = [
            m for m, flags in result.method_outputs.items()
            if idx < len(flags) and flags[idx]
        ]
        methods_not_flagged = [
            m for m in result.methods_used if m not in methods_flagged
        ]
        explanation = explain_finding(
            row, res, result.profile, methods_flagged, methods_not_flagged
        )
        findings.append({
            "datum": pd.Timestamp(row["timestamp"]).date().isoformat(),
            "locatie": row.get("location_name") or row.get("category") or "—",
            "waarde": int(row["value"]) if pd.notna(row["value"]) else 0,
            "severity": row["severity"],
            "stemmen": int(row["votes"]),
            "totaal_methodes": len(result.methods_used),
            "methodes_aan": methods_flagged,
            "methodes_uit": methods_not_flagged,
            "explanation": explanation,
        })
    return findings
