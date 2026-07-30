"""Waarschuwen: laat de tool zelf melden, in plaats van wachten tot
iemand kijkt.

Zonder dit is de dagelijkse inwinning een boom die omvalt in een leeg
bos: er wordt keurig geanalyseerd, maar een piek op zaterdagavond blijft
onopgemerkt tot maandag. Voor een indicatie-en-waarschuwingsgereedschap
is dat het ontbrekende stuk.

**Het moeilijke deel is niet versturen, maar níét versturen.** Een kanaal
dat elke dag afgaat wordt binnen twee weken genegeerd, en dan is het
erger dan geen kanaal — je denkt dat je gewaarschuwd wordt. Daarom:

- alleen **nieuwe** bevindingen (elke melding wordt vastgelegd; dezelfde
  afwijking gaat nooit twee keer de deur uit);
- alleen boven een instelbare drempel;
- een samenvatting per run, geen regel per afwijking;
- een dagelijks maximum, zodat een kapotte bron geen stortvloed geeft.

Kanalen worden met env-vars aangezet; zonder configuratie doet deze
module niets (en dat is de standaard).

    SENTINEL_WEBHOOK_URL   generieke JSON-POST (Teams, Slack, Mattermost)
    SENTINEL_SMTP_HOST     e-mail; verder SMTP_PORT/USER/PASSWORD/FROM/TO
    SENTINEL_ALERT_MIN     'onder' | 'boven' | 'beide'   (default: beide)
    SENTINEL_ALERT_MAX_PER_RUN   default 25
"""
from __future__ import annotations

import contextlib
import json
import os
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from core import storage
from core.logging_setup import get_logger

_logger = get_logger("notify")

DEFAULT_MAX_PER_RUN = 25

#: Sentinel die vastlegt dat de nulmeting voor een dataset is gedaan,
#: ook als er toen geen afwijkingen waren.
BASELINE_KEY = "__nulmeting__"


@dataclass
class NotifyResult:
    """Wat er is verstuurd, en waarheen. Ook bruikbaar als testrapport."""

    sent: bool
    channels: list[str]
    n_new: int
    n_suppressed: int
    error: str | None = None


def _configured_channels() -> list[str]:
    out = []
    if os.environ.get("SENTINEL_WEBHOOK_URL"):
        out.append("webhook")
    if os.environ.get("SENTINEL_SMTP_HOST") and os.environ.get("SENTINEL_MAIL_TO"):
        out.append("email")
    return out


def is_configured() -> bool:
    return bool(_configured_channels())


def _alert_key(dataset_id: int, alert: dict) -> str:
    """Stabiele sleutel per afwijking, voor ontdubbeling over runs heen."""
    from core.annotations import finding_key
    return finding_key(str(alert.get("datum")),
                       str(alert.get("locatie") or ""), None)


def select_new_alerts(dataset_id: int, alerts: list[dict],
                      direction: str | None = None,
                      max_per_run: int | None = None) -> tuple[list, int]:
    """Filter tot wat écht nieuw en meldenswaardig is.

    Returnt (te_melden, aantal_onderdrukt). Onderdrukt = al eerder
    gemeld, of buiten de richting-filter, of boven het dagmaximum.
    """
    direction = (direction
                 or os.environ.get("SENTINEL_ALERT_MIN", "beide")).lower()
    limit = max_per_run or int(
        os.environ.get("SENTINEL_ALERT_MAX_PER_RUN", DEFAULT_MAX_PER_RUN))

    already = storage.notified_keys(dataset_id)
    fresh, suppressed = [], 0
    for a in alerts:
        if direction in ("boven", "onder") and a.get("richting") != direction:
            suppressed += 1
            continue
        if _alert_key(dataset_id, a) in already:
            suppressed += 1
            continue
        fresh.append(a)

    if len(fresh) > limit:
        suppressed += len(fresh) - limit
        fresh = fresh[:limit]
    return fresh, suppressed


def build_message(dataset_name: str, alerts: list[dict],
                  n_suppressed: int = 0) -> tuple[str, str]:
    """(onderwerp, tekst). Bewust kort: een melding moet op een telefoon
    leesbaar zijn en zeggen of je nú moet kijken."""
    n = len(alerts)
    subject = f"SENTINEL: {n} nieuwe afwijking(en) — {dataset_name}"

    lines = [f"{n} nieuwe afwijking(en) in '{dataset_name}':", ""]
    for a in alerts[:10]:
        richting = "boven" if a.get("richting") == "boven" else "onder"
        lines.append(
            f"  {a.get('datum')}  {a.get('locatie')}  "
            f"{a.get('waarde')} ({richting} de verwachte band)"
        )
    if n > 10:
        lines.append(f"  ... en {n - 10} meer")
    if n_suppressed:
        lines.append("")
        lines.append(f"({n_suppressed} niet gemeld: al eerder gemeld, "
                     f"buiten filter, of boven het dagmaximum.)")
    lines += [
        "",
        "Dit is een triage-signaal, geen conclusie: de tool markeert wat "
        "afwijkt van het normbeeld, niet wat waar is.",
        "Beoordeel de bevindingen in de tool onder Triage.",
    ]
    return subject, "\n".join(lines)


def _send_webhook(subject: str, body: str, alerts: list[dict]) -> None:
    url = os.environ["SENTINEL_WEBHOOK_URL"]
    payload = {
        # 'text' werkt out of the box bij Slack, Mattermost en Teams.
        "text": f"*{subject}*\n```\n{body}\n```",
        "subject": subject,
        "alerts": alerts[:25],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "SENTINEL/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"webhook gaf HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"webhook gaf HTTP {e.code}") from e


def _send_email(subject: str, body: str) -> None:
    host = os.environ["SENTINEL_SMTP_HOST"]
    port = int(os.environ.get("SENTINEL_SMTP_PORT", 587))
    user = os.environ.get("SENTINEL_SMTP_USER")
    password = os.environ.get("SENTINEL_SMTP_PASSWORD")
    sender = os.environ.get("SENTINEL_MAIL_FROM", user or "sentinel@localhost")
    recipients = [r.strip() for r in
                  os.environ["SENTINEL_MAIL_TO"].split(",") if r.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        if port != 25:
            with contextlib.suppress(Exception):
                smtp.starttls()
                smtp.ehlo()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def notify_new_alerts(dataset_id: int, dataset_name: str,
                      alerts: list[dict]) -> NotifyResult:
    """Meld nieuwe afwijkingen via de geconfigureerde kanalen.

    Faalt nooit hard: een onbereikbare mailserver mag de inwinning niet
    laten mislukken. Wat wél gebeurt, is loggen én in de audit-trail
    vastleggen dat de melding niet aankwam — een stille waarschuwing die
    niet verstuurd wordt is het gevaarlijkst.
    """
    channels = _configured_channels()
    if not channels:
        return NotifyResult(False, [], 0, 0)

    # Eerste keer voor deze dataset: niets melden, alleen de nulmeting
    # vastleggen. Anders levert het aanzetten van een bron meteen een
    # stortvloed over gebeurtenissen van maanden geleden — precies de
    # manier om een kanaal binnen een week gedempt te krijgen. Vanaf de
    # tweede run is 'nieuw' ook echt nieuw.
    #
    # De sentinel is nodig omdat een eerste run zónder afwijkingen anders
    # niets vastlegt; de volgende run zou dan óók als 'eerste' gelden en
    # de allereerste echte waarschuwing stilzwijgend opslokken.
    if BASELINE_KEY not in storage.notified_keys(dataset_id):
        keys = [BASELINE_KEY] + [_alert_key(dataset_id, a) for a in alerts]
        storage.mark_notified(dataset_id, keys)
        storage.record_audit(
            "melding_nulmeting", "dataset", dataset_id,
            {"n_bestaand": len(keys) - 1,
             "reden": "eerste run; historie niet gemeld"},
        )
        _logger.info("nulmeting vastgelegd, geen melding verstuurd",
                     extra={"ctx": {"dataset_id": dataset_id,
                                    "n_bestaand": len(keys)}})
        return NotifyResult(False, channels, 0, len(keys) - 1)

    fresh, suppressed = select_new_alerts(dataset_id, alerts)
    if not fresh:
        return NotifyResult(False, channels, 0, suppressed)

    subject, body = build_message(dataset_name, fresh, suppressed)
    delivered, errors = [], []
    for channel in channels:
        try:
            if channel == "webhook":
                _send_webhook(subject, body, fresh)
            elif channel == "email":
                _send_email(subject, body)
            delivered.append(channel)
        except Exception as e:
            errors.append(f"{channel}: {e}")
            _logger.exception("melding versturen mislukt",
                              extra={"ctx": {"channel": channel,
                                             "dataset_id": dataset_id}})

    # Alleen als er iets is aangekomen markeren we ze als gemeld; anders
    # zou een tijdelijke storing de melding voorgoed laten verdwijnen.
    if delivered:
        storage.mark_notified(
            dataset_id, [_alert_key(dataset_id, a) for a in fresh])

    storage.record_audit(
        "melding_verstuurd" if delivered else "melding_mislukt",
        "dataset", dataset_id,
        {"kanalen": delivered, "n_nieuw": len(fresh),
         "n_onderdrukt": suppressed,
         "fouten": errors or None},
    )
    return NotifyResult(bool(delivered), delivered, len(fresh), suppressed,
                        "; ".join(errors) or None)


def send_test(dataset_name: str = "testdataset") -> NotifyResult:
    """Stuur één testbericht om de configuratie te controleren, zonder
    iets als gemeld te markeren."""
    channels = _configured_channels()
    if not channels:
        return NotifyResult(False, [], 0, 0,
                            "Geen kanaal geconfigureerd (webhook of SMTP).")
    subject = "SENTINEL: testbericht"
    body = ("Dit is een testbericht van SENTINEL. Als je dit ziet, werkt "
            "het meldkanaal.\n\nEr is geen afwijking gemeld.")
    delivered, errors = [], []
    for channel in channels:
        try:
            if channel == "webhook":
                _send_webhook(subject, body, [])
            elif channel == "email":
                _send_email(subject, body)
            delivered.append(channel)
        except Exception as e:
            errors.append(f"{channel}: {e}")
    return NotifyResult(bool(delivered), delivered, 0, 0,
                        "; ".join(errors) or None)
