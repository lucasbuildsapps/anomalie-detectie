"""Tests voor de analyse-vingerafdruk in cache-sleutels.

Aanleiding: na een correctie aan de tolerantieband bleef de app de oude,
veel te brede band tonen. De code klopte; Streamlit serveerde simpelweg
een gecachet resultaat. `@st.cache_data` verwerkt een wijziging in de
gecachte functie zelf, maar niet in de functies die dié aanroept — en het
echte rekenwerk zit een module verderop.

Voor een analist is dat het gevaarlijkste soort fout: de grafiek hoort
niet meer bij de methode, en niets wijst erop.
"""
import pathlib

from core.fingerprint import _ANALYSIS_MODULES, analysis_fingerprint


def test_fingerprint_is_stable_within_a_run():
    assert analysis_fingerprint() == analysis_fingerprint()


def test_fingerprint_is_short_and_hexadecimal():
    fp = analysis_fingerprint()
    assert len(fp) == 12
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_changes_when_analysis_code_changes(tmp_path,
                                                        monkeypatch):
    """Kern van de bescherming: andere rekencode, andere sleutel."""
    import core.fingerprint as fp_mod

    fake = tmp_path / "core"
    fake.mkdir()
    (fake / "normbeeld.py").write_text("A = 1", encoding="utf-8")
    (tmp_path / "detectors").mkdir()

    monkeypatch.setattr(fp_mod, "__file__", str(fake / "fingerprint.py"))
    fp_mod.analysis_fingerprint.cache_clear()
    first = fp_mod.analysis_fingerprint()

    (fake / "normbeeld.py").write_text("A = 2", encoding="utf-8")
    fp_mod.analysis_fingerprint.cache_clear()
    assert fp_mod.analysis_fingerprint() != first


def test_all_listed_modules_exist():
    """Een hernoemde module zou stilzwijgend uit de vingerafdruk vallen,
    waarna wijzigingen daar de cache niet meer verversen."""
    core_dir = pathlib.Path(__file__).resolve().parent.parent / "core"
    missing = [m for m in _ANALYSIS_MODULES if not (core_dir / m).exists()]
    assert not missing, f"niet gevonden in core/: {missing}"


def test_cache_wrappers_take_a_code_version_argument():
    """Bewaakt dat nieuwe cache-wrappers de sleutel niet vergeten."""
    import inspect

    import ui.cache as cache_mod

    wrappers = [n for n in dir(cache_mod) if n.startswith("cached_")]
    assert wrappers, "geen cache-wrappers gevonden"
    for name in wrappers:
        fn = getattr(cache_mod, name)
        inner = getattr(fn, "__wrapped__", fn)
        params = inspect.signature(inner).parameters
        assert "code_version" in params, (
            f"{name} mist code_version; een wijziging in de rekenkern "
            f"vervalt dan niet de cache")
