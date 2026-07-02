"""Signalen bovenop het normbeeld — antwoorden op "is er iets aan de hand
dat je niet aan losse punten ziet?":

- variability_signal: is de activiteit recent grilliger/vlakker dan normaal?
- persistence_signal: zit de reeks al N periodes aan één kant van verwachting?
- change_signal: is er recent een structurele niveau-verschuiving?
- similar_period: op welke historische periode lijkt de huidige situatie?

Alle functies werken op Normbeeld.historical (date, actual, expected) en
returnen None wanneer er niets noemenswaardigs is — geen signaal is ook
informatie, maar geen alert-ruis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def variability_signal(hist: pd.DataFrame, window: int = 10) -> dict | None:
    """Vergelijk de spreiding (rolling std) van het recentste venster met de
    verdeling van diezelfde maat in de OUDERE historie (recente vensters
    overlappen elkaar en zouden de vergelijking vervuilen). Naast het
    percentiel eisen we een effectgrootte (1.5x / 0.5x) tegen ruis-alarmen."""
    s = pd.Series(hist["actual"].values, dtype=float).dropna()
    if len(s) < window * 4:
        return None
    roll = s.rolling(window, min_periods=window).std().dropna()
    baseline = roll.iloc[:-(2 * window)]  # exclusief recent-overlappende vensters
    if len(baseline) < 8:
        return None
    recent = float(roll.iloc[-1])
    pctl = float((baseline < recent).mean())
    typical = float(baseline.median())
    if pctl >= 0.95 and typical > 0 and recent > 1.5 * typical:
        return {
            "type": "variability", "richting": "grilliger",
            "pctl": pctl, "recent_std": recent, "typical_std": typical,
        }
    if pctl <= 0.05 and typical > 0 and recent < 0.5 * typical:
        return {
            "type": "variability", "richting": "vlakker",
            "pctl": pctl, "recent_std": recent, "typical_std": typical,
        }
    return None


def persistence_signal(hist: pd.DataFrame, min_run: int = 5) -> dict | None:
    """Aanhoudende afwijking: N opeenvolgende periodes aan dezelfde kant van
    de verwachting. Elke losse periode kan toeval zijn (p~0.5); een run van
    N heeft kans ~0.5^N. Vanaf min_run=5 (p<4%) melden we het."""
    d = hist.dropna(subset=["actual"])
    if len(d) < min_run + 3:
        return None
    diffs = (d["actual"].values - d["expected"].values)
    # negeer exacte nullen (precies op verwachting)
    side = np.sign(diffs)
    run = 0
    direction = 0.0
    for v in side[::-1]:
        if v == 0:
            break
        if direction == 0.0:
            direction = v
            run = 1
        elif v == direction:
            run += 1
        else:
            break
    if run >= min_run:
        return {
            "type": "persistence",
            "run": int(run),
            "richting": "boven" if direction > 0 else "onder",
            "p": float(0.5 ** run),
            "sinds": pd.Timestamp(d.iloc[-run]["date"]),
        }
    return None


def change_signal(hist: pd.DataFrame, recent_periods: int = 14) -> dict | None:
    """Recente structurele niveau-verschuiving (change-point in het laatste
    venster). Gebruikt de bestaande windowed-t-test detector."""
    from core.comparison import detect_change_points
    d = hist.dropna(subset=["actual"])
    if len(d) < 12:
        return None
    s = pd.Series(d["actual"].values,
                  index=pd.to_datetime(d["date"].values))
    cps = detect_change_points(s)
    if not cps:
        return None
    last = cps[-1]
    pos = s.index.get_indexer([last["date"]])
    if len(pos) == 0 or pos[0] < 0:
        return None
    if len(s) - int(pos[0]) <= recent_periods:
        return {"type": "change", **last}
    return None


def similar_period(hist: pd.DataFrame, window: int | None = None) -> dict | None:
    """Vind de historische periode die het meest lijkt op het recentste
    venster (vorm via correlatie, met een straf voor niveauverschil).
    Antwoord op: 'wanneer zag het er eerder zo uit?'"""
    d = hist.dropna(subset=["actual"]).reset_index(drop=True)
    vals = d["actual"].astype(float).values
    n = len(vals)
    if n < 30:
        return None
    w = window or max(10, min(30, n // 4))
    cur = vals[-w:]
    if np.std(cur) < 1e-9:
        return None

    best: tuple | None = None
    for start in range(0, n - 2 * w):  # geen overlap met het huidige venster
        seg = vals[start:start + w]
        if np.std(seg) < 1e-9:
            continue
        corr = float(np.corrcoef(cur, seg)[0, 1])
        level_pen = abs(seg.mean() - cur.mean()) / max(abs(cur.mean()), 1.0)
        score = corr - 0.3 * min(level_pen, 1.0)
        if best is None or score > best[0]:
            best = (score, start, corr)

    if best is None or best[2] < 0.5:
        return None
    _, start, corr = best
    return {
        "type": "similar",
        "start": pd.Timestamp(d.iloc[start]["date"]),
        "end": pd.Timestamp(d.iloc[start + w - 1]["date"]),
        "corr": corr,
        "window": int(w),
    }


def collect_signals(hist: pd.DataFrame, aggregation: str = "daily") -> list[dict]:
    """Draai alle signaal-detectors; returnt alleen wat daadwerkelijk speelt."""
    recent = {"hourly": 48, "daily": 14, "weekly": 8, "monthly": 6}.get(
        aggregation, 14
    )
    out = []
    for fn, kwargs in (
        (variability_signal, {}),
        (persistence_signal, {}),
        (change_signal, {"recent_periods": recent}),
        (similar_period, {}),
    ):
        try:
            sig = fn(hist, **kwargs)
        except Exception:
            sig = None
        if sig is not None:
            out.append(sig)
    return out
