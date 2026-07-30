"""Tests voor het waarschuwingskanaal.

Zwaartepunt ligt bij *niet* versturen. Een kanaal dat elke dag afgaat
wordt genegeerd, en dan is het erger dan geen kanaal — je denkt dat je
gewaarschuwd wordt. Ontdubbeling, filters en het dagmaximum zijn dus de
belangrijkste eigenschappen om te bewaken.
"""
import pytest

import core.notify as notify
import core.storage as storage
from core.notify import (
    build_message,
    is_configured,
    notify_new_alerts,
    select_new_alerts,
    send_test,
)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for var in ("SENTINEL_WEBHOOK_URL", "SENTINEL_SMTP_HOST",
                "SENTINEL_MAIL_TO", "SENTINEL_ALERT_MIN",
                "SENTINEL_ALERT_MAX_PER_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "notify.db")
    storage.init_db()
    yield


@pytest.fixture
def dataset():
    return storage.create_dataset("meldset", "", {})


def _alerts(n=3, richting="boven"):
    return [{"datum": f"2026-07-{i + 1:02d}", "locatie": f"Regio{i}",
             "waarde": 10 + i, "richting": richting} for i in range(n)]


@pytest.fixture
def captured(monkeypatch):
    """Vang verstuurde webhooks op in plaats van ze echt te versturen."""
    sent = []
    monkeypatch.setenv("SENTINEL_WEBHOOK_URL", "https://hook.test/x")
    monkeypatch.setattr(notify, "_send_webhook",
                        lambda subject, body, alerts: sent.append(
                            (subject, body, list(alerts))))
    return sent


def _baseline(dataset_id):
    """Leg de nulmeting vast (eerste run meldt bewust niets), zodat de
    test daarna het gedrag bij écht nieuwe afwijkingen kan toetsen."""
    notify_new_alerts(dataset_id, "meldset", [])


class TestConfiguration:
    def test_does_nothing_without_configuration(self, dataset):
        assert not is_configured()
        result = notify_new_alerts(dataset, "x", _alerts())
        assert not result.sent
        assert result.channels == []

    def test_webhook_alone_is_enough(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_WEBHOOK_URL", "https://hook.test/x")
        assert is_configured()

    def test_smtp_needs_a_recipient(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_SMTP_HOST", "mail.test")
        assert not is_configured(), "zonder MAIL_TO is e-mail onbruikbaar"
        monkeypatch.setenv("SENTINEL_MAIL_TO", "a@b.c")
        assert is_configured()

    def test_send_test_reports_missing_configuration(self):
        result = send_test()
        assert not result.sent
        assert "Geen kanaal" in result.error


class TestSuppression:
    def test_same_alert_is_never_sent_twice(self, dataset, captured):
        _baseline(dataset)
        alerts = _alerts(3)
        first = notify_new_alerts(dataset, "meldset", alerts)
        assert first.sent and first.n_new == 3

        second = notify_new_alerts(dataset, "meldset", alerts)
        assert not second.sent
        assert second.n_new == 0
        assert second.n_suppressed == 3
        assert len(captured) == 1, "tweede run mag niets versturen"

    def test_only_the_new_ones_go_out(self, dataset, captured):
        _baseline(dataset)
        notify_new_alerts(dataset, "meldset", _alerts(2))
        result = notify_new_alerts(dataset, "meldset", _alerts(4))
        assert result.n_new == 2
        assert result.n_suppressed == 2

    def test_direction_filter(self, dataset, monkeypatch):
        monkeypatch.setenv("SENTINEL_ALERT_MIN", "boven")
        mixed = _alerts(2, "boven") + _alerts(2, "onder")
        fresh, suppressed = select_new_alerts(dataset, mixed)
        assert all(a["richting"] == "boven" for a in fresh)
        assert suppressed == 2

    def test_daily_cap_prevents_a_flood(self, dataset):
        fresh, suppressed = select_new_alerts(dataset, _alerts(50),
                                              max_per_run=10)
        assert len(fresh) == 10
        assert suppressed == 40

    def test_failed_delivery_does_not_mark_as_sent(self, dataset, monkeypatch):
        """Anders verdwijnt een waarschuwing voorgoed door een tijdelijke
        storing — het gevaarlijkste faalgedrag dat dit kanaal kan hebben."""
        monkeypatch.setenv("SENTINEL_WEBHOOK_URL", "https://hook.test/x")
        _baseline(dataset)
        before = storage.notified_keys(dataset)

        def boom(*a, **k):
            raise RuntimeError("webhook plat")

        monkeypatch.setattr(notify, "_send_webhook", boom)
        result = notify_new_alerts(dataset, "meldset", _alerts(2))
        assert not result.sent
        assert result.error
        assert storage.notified_keys(dataset) == before, (
            "mislukte melding mag niets als gemeld markeren")

        # Zodra het kanaal weer werkt, gaat de melding alsnog de deur uit.
        sent = []
        monkeypatch.setattr(notify, "_send_webhook",
                            lambda s, b, a: sent.append(s))
        again = notify_new_alerts(dataset, "meldset", _alerts(2))
        assert again.sent and again.n_new == 2

    def test_notifications_are_isolated_per_dataset(self, captured):
        a = storage.create_dataset("set-a", "", {})
        b = storage.create_dataset("set-b", "", {})
        _baseline(a)
        _baseline(b)
        notify_new_alerts(a, "set-a", _alerts(2))
        result = notify_new_alerts(b, "set-b", _alerts(2))
        assert result.n_new == 2, "andere dataset staat los"


class TestMessage:
    def test_subject_states_the_count_and_dataset(self):
        subject, _ = build_message("Oekraïne", _alerts(3))
        assert "3" in subject and "Oekraïne" in subject

    def test_body_lists_alerts_and_caps_at_ten(self):
        _, body = build_message("x", _alerts(15))
        assert "en 5 meer" in body
        assert body.count("boven de verwachte band") == 10

    def test_body_carries_the_triage_caveat(self):
        """Een melding mag niet als conclusie gelezen worden."""
        _, body = build_message("x", _alerts(1))
        assert "geen conclusie" in body

    def test_suppressed_count_is_disclosed(self):
        _, body = build_message("x", _alerts(1), n_suppressed=7)
        assert "7 niet gemeld" in body


class TestAuditTrail:
    def test_successful_send_is_audited(self, dataset, captured):
        _baseline(dataset)
        notify_new_alerts(dataset, "meldset", _alerts(2))
        actions = [a["action"] for a in storage.list_audit(20)]
        assert "melding_verstuurd" in actions

    def test_failed_send_is_audited_too(self, dataset, monkeypatch):
        """Een waarschuwing die niet aankomt moet zichtbaar zijn; stille
        stilte is het ergste faalgedrag."""
        monkeypatch.setenv("SENTINEL_WEBHOOK_URL", "https://hook.test/x")
        _baseline(dataset)
        monkeypatch.setattr(notify, "_send_webhook",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("plat")))
        notify_new_alerts(dataset, "meldset", _alerts(1))
        actions = [a["action"] for a in storage.list_audit(20)]
        assert "melding_mislukt" in actions


def test_ingest_triggers_notification(tmp_path, monkeypatch, captured):
    """End-to-end: eerste inwinning is stil (nulmeting), een latere
    inwinning met een nieuwe afwijking stuurt wél een melding."""
    import numpy as np
    import pandas as pd

    from connectors.base import Connector
    from core.ingest import run_connector

    class Growing(Connector):
        """Levert een rustige reeks; pas bij de tweede run komt er een
        piek aan het einde bij."""

        name = "meld-bron"
        dataset_name = "Meld bron"
        enabled = True

        def __init__(self):
            # < 120 dagen houdt de tijdschaal op dagen; bij weekaggregatie
            # valt een piek op de laatste dag in een onvolledige week, en
            # die wordt bewust weggelaten.
            self.days = 100
            self.with_spike = False

        def fetch(self, since):
            rng = np.random.default_rng(3)
            vals = np.clip(10 + rng.normal(0, 1.5, self.days), 0, None).round()
            if self.with_spike:
                vals[-1] = 400        # onmiskenbaar, en nieuw
            return pd.DataFrame({
                "timestamp": pd.date_range("2026-01-01", periods=self.days,
                                           freq="D"),
                "value": vals,
                "location_name": ["X"] * self.days,
            })

    connector = Growing()
    assert run_connector(connector)["status"] == "ok"
    assert captured == [], "eerste inwinning legt alleen de nulmeting vast"

    # Een dag erbij, met een piek die er eerder niet was.
    connector.days = 101
    connector.with_spike = True
    assert run_connector(connector)["status"] == "ok"

    assert captured, "een nieuwe afwijking hoort gemeld te worden"
    subject, _body, alerts = captured[0]
    assert "nieuwe afwijking" in subject
    assert alerts, "de melding hoort de afwijkingen mee te sturen"


def test_ingest_without_channel_is_silent(tmp_path, monkeypatch):
    """Zonder configuratie doet de inwinning niets extra's — en crasht niet."""
    import numpy as np
    import pandas as pd

    from connectors.base import Connector
    from core.ingest import run_connector

    class Plain(Connector):
        name = "stille-bron"
        dataset_name = "Stille bron"
        enabled = True

        def fetch(self, since):
            rng = np.random.default_rng(4)
            return pd.DataFrame({
                "timestamp": pd.date_range("2026-03-01", periods=90, freq="D"),
                "value": np.clip(8 + rng.normal(0, 2, 90), 0, None).round(),
                "location_name": ["Y"] * 90,
            })

    assert run_connector(Plain())["status"] == "ok"


class TestFirstRunBaseline:
    """Het aanzetten van een bron mag geen stortvloed over oude
    gebeurtenissen geven — dat is de snelste manier om een meldkanaal
    genegeerd te krijgen."""

    def test_first_run_is_silent(self, dataset, captured):
        result = notify_new_alerts(dataset, "meldset", _alerts(20))
        assert not result.sent
        assert result.n_new == 0
        assert result.n_suppressed == 20
        assert captured == [], "eerste run mag niets versturen"

    def test_baseline_is_recorded(self, dataset, captured):
        notify_new_alerts(dataset, "meldset", _alerts(5))
        keys = storage.notified_keys(dataset)
        assert notify.BASELINE_KEY in keys
        assert len(keys) == 6          # 5 afwijkingen + de sentinel

    def test_quiet_first_run_still_establishes_the_baseline(self, dataset,
                                                            captured):
        """Zonder sentinel zou een stille eerste run betekenen dat de
        volgende run ook als 'eerste' geldt — en dan wordt de allereerste
        echte waarschuwing stilzwijgend opgeslokt."""
        notify_new_alerts(dataset, "meldset", [])
        assert notify.BASELINE_KEY in storage.notified_keys(dataset)

        result = notify_new_alerts(dataset, "meldset", _alerts(2))
        assert result.sent, "eerste echte afwijking moet gemeld worden"
        assert result.n_new == 2
        assert len(captured) == 1

    def test_second_run_alerts_only_on_genuinely_new(self, dataset, captured):
        notify_new_alerts(dataset, "meldset", _alerts(3))   # nulmeting
        result = notify_new_alerts(dataset, "meldset", _alerts(5))
        assert result.sent
        assert result.n_new == 2, "alleen de twee erbij gekomen afwijkingen"
        assert len(captured) == 1

    def test_baseline_is_audited(self, dataset, captured):
        notify_new_alerts(dataset, "meldset", _alerts(4))
        actions = [a["action"] for a in storage.list_audit(20)]
        assert "melding_nulmeting" in actions


class TestScheduledJobWiring:
    """De geplande inwinning draait in een aparte omgeving. Elke env-var
    die core/notify.py leest moet daar doorgegeven worden, anders
    waarschuwt de nachtelijke run niemand — en dat merk je niet.
    """

    def _workflow(self) -> str:
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        return (root / ".github" / "workflows" / "ingest.yml").read_text(
            encoding="utf-8")

    def test_every_notify_variable_is_passed_through(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        source = (root / "core" / "notify.py").read_text(encoding="utf-8")
        used = set(re.findall(r'environ(?:\.get)?\(?\["?(SENTINEL_[A-Z_]+)"?',
                              source))
        used |= set(re.findall(r'os\.environ\.get\("(SENTINEL_[A-Z_]+)"',
                               source))
        workflow = self._workflow()
        missing = [v for v in sorted(used) if v not in workflow]
        assert not missing, (
            f"niet doorgegeven aan de geplande run: {missing}")

    def test_connector_keys_are_passed_through(self):
        workflow = self._workflow()
        for var in ("ACLED_API_KEY", "ACLED_EMAIL", "FIRMS_MAP_KEY"):
            assert var in workflow, f"{var} ontbreekt in de workflow"

    def test_job_runs_as_analyst_not_admin(self):
        assert "SENTINEL_ROLE: analyst" in self._workflow()
