"""Wat is er veranderd sinds de vorige beoordeling?

Twee redenen voor dit bestand.

De praktische: een analist die na een week terugkomt wil niet het hele
beeld opnieuw lezen, maar weten wát er anders is. Zonder dat vergelijkt
hij op geheugen, en dat is precies waar dingen wegvallen.

De formele: ICD 203 vraagt om het expliciet benoemen van wijzigingen ten
opzichte van eerdere oordelen. Een beeld dat stilletjes verschuift is
lastiger te verantwoorden dan een beeld dat zegt dat het is verschoven.

De momentopnames (`analysis_snapshots`) bevatten al de stand per moment;
dit vergelijkt er twee en zegt in gewone taal wat het verschil is.

Bewust géén drempel-loze diff: elk normbeeld schuift een beetje bij nieuwe
data. Alleen verschuivingen die er analytisch toe doen worden gemeld,
zodat de lijst leesbaar blijft.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Relatieve verschuiving van het verwachte niveau die de moeite waard is.
LEVEL_SHIFT_PCT = 0.20
#: Absolute ondergrens, zodat 0.2 -> 0.3 niet als '50% stijging' telt.
LEVEL_SHIFT_MIN = 1.0


@dataclass
class Change:
    """Eén verschil tussen twee momentopnames."""

    kind: str          # 'nieuw' / 'verdwenen' / 'niveau' / 'vertrouwen' / 'model'
    subject: str       # regio of dataset
    description: str
    important: bool = False


def _normbeelds(snapshot: dict) -> dict:
    payload = (snapshot or {}).get("payload") or {}
    return payload.get("normbeelds") or {}


def _alerts(snapshot: dict) -> list:
    payload = (snapshot or {}).get("payload") or {}
    return payload.get("alerts") or []


def _alert_keys(snapshot: dict) -> set:
    out = set()
    for a in _alerts(snapshot):
        out.add((str(a.get("datum")), str(a.get("locatie") or "")))
    return out


def compare(previous: dict, current: dict) -> list[Change]:
    """Vergelijk twee momentopnames en beschrijf de verschillen.

    `previous` en `current` zijn snapshots zoals `storage.get_snapshot`
    ze teruggeeft (met een uitgepakte `payload`).
    """
    changes: list[Change] = []
    if not previous or not current:
        return changes

    # --- Afwijkingen erbij / eraf ---
    old_keys, new_keys = _alert_keys(previous), _alert_keys(current)
    added = new_keys - old_keys
    gone = old_keys - new_keys

    for datum, locatie in sorted(added)[:10]:
        changes.append(Change(
            "nieuw", locatie or "onbekend",
            f"nieuwe afwijking op {datum}", important=True,
        ))
    if len(added) > 10:
        changes.append(Change("nieuw", "—",
                              f"en nog {len(added) - 10} nieuwe afwijkingen",
                              important=True))
    if gone:
        changes.append(Change(
            "verdwenen", "—",
            f"{len(gone)} eerdere afwijking(en) staan niet meer in het beeld "
            f"— meestal omdat ze buiten het 'recente' venster zijn gevallen",
        ))

    # --- Verschuiving van het verwachte niveau per regio ---
    old_nb, new_nb = _normbeelds(previous), _normbeelds(current)
    for loc, new in sorted(new_nb.items()):
        old = old_nb.get(loc)
        if not old:
            changes.append(Change("nieuw", loc,
                                  "regio is nieuw in het beeld"))
            continue

        old_exp = float(old.get("expected") or 0.0)
        new_exp = float(new.get("expected") or 0.0)
        delta = new_exp - old_exp
        base = max(abs(old_exp), LEVEL_SHIFT_MIN)
        if abs(delta) / base >= LEVEL_SHIFT_PCT and abs(delta) >= LEVEL_SHIFT_MIN:
            richting = "hoger" if delta > 0 else "lager"
            changes.append(Change(
                "niveau", loc,
                f"verwacht niveau {abs(delta) / base * 100:.0f}% {richting} "
                f"({old_exp:.1f} → {new_exp:.1f})",
                important=abs(delta) / base >= 0.5,
            ))

        # --- Vertrouwen: een daling hoort opgemerkt te worden ---
        rank = {"laag": 0, "gemiddeld": 1, "midden": 1, "hoog": 2}
        old_c = str(old.get("confidence") or "")
        new_c = str(new.get("confidence") or "")
        if old_c and new_c and old_c != new_c:
            gedaald = rank.get(new_c, 1) < rank.get(old_c, 1)
            changes.append(Change(
                "vertrouwen", loc,
                f"vertrouwen ging van {old_c} naar {new_c}",
                important=gedaald,
            ))

        # --- Bandmodel: een wissel betekent dat de reeks van aard veranderde
        old_m, new_m = old.get("band_model"), new.get("band_model")
        if old_m and new_m and old_m != new_m:
            changes.append(Change(
                "model", loc,
                f"bandmodel gewisseld van {old_m} naar {new_m}",
            ))

    # Verdwenen regio's
    for loc in sorted(set(old_nb) - set(new_nb)):
        changes.append(Change("verdwenen", loc,
                              "regio komt niet meer voor in de data"))

    changes.sort(key=lambda c: (not c.important, c.kind, c.subject))
    return changes


def summarise(changes: list[Change], previous: dict | None = None) -> str:
    """Eén regel over wat er sinds de vorige beoordeling is veranderd."""
    since = ""
    if previous and previous.get("created_at"):
        since = f" sinds {previous['created_at']}"
    if not changes:
        return (f"Geen noemenswaardige verschillen{since}. Dat is zelf ook "
                f"informatie: het beeld is stabiel.")
    belangrijk = [c for c in changes if c.important]
    if belangrijk:
        return (f"{len(changes)} verschillen{since}, waarvan "
                f"{len(belangrijk)} de aandacht verdienen.")
    return f"{len(changes)} kleine verschillen{since}; niets urgents."


def since_last(dataset_id: int) -> tuple[list[Change], dict | None]:
    """Vergelijk de nieuwste momentopname met de voorgaande.

    Returnt (verschillen, vorige_snapshot). Lege lijst als er minder dan
    twee momentopnames zijn — dan valt er nog niets te vergelijken.
    """
    from core import storage

    rows = storage.list_snapshots(dataset_id, limit=2)
    if len(rows) < 2:
        return [], None
    current = storage.get_snapshot(rows[0]["id"])
    previous = storage.get_snapshot(rows[1]["id"])
    return compare(previous, current), previous
