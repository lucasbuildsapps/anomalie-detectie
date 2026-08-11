"""Signalen bovenop het normbeeld — antwoorden op "is er iets aan de hand
dat je niet aan losse punten ziet?":

- variability_signal: is de activiteit recent grilliger/vlakker dan normaal?
- persistence_signal: zit de reeks al N periodes aan één kant van verwachting?
- change_signal: is er recent een structurele niveau-verschuiving?

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


# similar_period() is verwijderd (ARCHITECTURE_V2.md §1).
#
# Het zocht de historische periode met de hoogste correlatie met het heden en
# presenteerde die als analogie. Een ongecontroleerde max-zoektocht over
# honderden vensters vindt altijd een goede match — ook als er geen is — en de
# uitkomst is een geruststellende historische parallel op het moment dat de
# situatie juist ongekend is. Dat is de gevaarlijkste vorm die een fout in dit
# product kan aannemen: niet een gemist alarm, maar een uitgesproken
# geruststelling.
#
# Er komt niets voor in de plaats. De vraag "wanneer zag het er eerder zo uit"
# is legitiem, maar hij vereist een nulverdeling voor "hoe goed matcht een
# willekeurig venster", en die is er niet.



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
    ):
        try:
            sig = fn(hist, **kwargs)
        except Exception:
            sig = None
        if sig is not None:
            out.append(sig)
    return out
