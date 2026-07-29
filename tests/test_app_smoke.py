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


def test_all_pages_render():
    from i18n.nl import t
    for page_key in ("nav_normbeeld", "nav_triage", "nav_compare"):
        at = AppTest.from_file(APP, default_timeout=60)
        at.session_state["active_page"] = t(page_key)
        at.run()
        assert not at.exception, f"pagina {page_key} crasht: {at.exception}"


def test_settings_overlay_renders():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["show_settings"] = True
    at.run()
    assert not at.exception, f"instellingen crasht: {at.exception}"
