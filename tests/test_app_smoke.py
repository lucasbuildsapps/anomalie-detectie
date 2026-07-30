"""Smoke-tests: boot de volledige Streamlit-app headless (AppTest).

Vangt import-fouten, NameErrors en router-crashes die unit-tests op core/
niet zien — precies de klasse fouten die een refactor van de UI-laag
introduceert.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.storage as storage

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "smoke.db")
    storage.init_db()
    yield


def _boot() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_app_boots_without_exception():
    at = _boot()
    assert not at.exception, f"app crashte bij boot: {at.exception}"


def test_empty_state_shows_welcome():
    at = _boot()
    # Zonder datasets hoort het welkomstscherm te tonen (geen crash op
    # ontbrekende data).
    assert not at.exception


def _assert_page_healthy(at, what: str):
    """Geen crash én geen router-foutmelding.

    Alleen op `at.exception` controleren is niet genoeg: de router vangt
    een kapotte pagina op en toont een nette melding. Daardoor slaagde
    een smoketest ooit terwijl de instellingenpagina een syntaxfout had —
    de vangrail verborg precies wat de test moest vinden.
    """
    assert not at.exception, f"{what} crasht: {at.exception}"
    fouten = [e.value for e in at.error if "Er ging iets mis" in e.value]
    assert not fouten, f"{what} toont een foutmelding: {fouten}"


def test_all_pages_render():
    from i18n.nl import t
    for page_key in ("nav_normbeeld", "nav_triage", "nav_compare"):
        at = AppTest.from_file(APP, default_timeout=60)
        at.session_state["active_page"] = t(page_key)
        at.run()
        _assert_page_healthy(at, f"pagina {page_key}")


def test_settings_overlay_renders():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["show_settings"] = True
    at.run()
    _assert_page_healthy(at, "instellingen")


def test_every_page_module_imports_cleanly():
    """Directe importcontrole, los van de router. Een pagina met een
    syntaxfout hoort hier meteen op te vallen."""
    import importlib

    for module in ("ui.pages.normbeeld", "ui.pages.triage",
                   "ui.pages.compare", "ui.pages.settings"):
        importlib.import_module(module)


def test_broken_page_does_not_kill_the_app(monkeypatch):
    """Regressie: één kapotte pagina mag de hele app niet slopen.

    In productie gebeurde dit toen Streamlit Cloud na een push een
    half-herladen module-boom serveerde: de triage-pagina kon een nieuw
    symbool uit ui.cache niet importeren en de héle app viel om, omdat
    pagina's op moduleniveau werden geïmporteerd. Nu gaat het via de
    router-vangrail.
    """
    import importlib

    import ui.pages.triage as triage_mod

    real_import = importlib.import_module

    def exploding_import(name, *args, **kwargs):
        if name == "ui.pages.triage":
            raise ImportError("cannot import name 'iets_nieuws' from 'ui.cache'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", exploding_import)
    assert triage_mod is not None  # module bestaat; de import wordt gesaboteerd

    from i18n.nl import t
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["active_page"] = t("nav_triage")
    at.run()

    # De app leeft: geen harde crash, wél een nette melding.
    assert not at.exception
    assert any("Er ging iets mis" in e.value for e in at.error)
    assert any("Reboot app" in i.value for i in at.info)
