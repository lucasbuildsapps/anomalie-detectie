"""Tests voor dataset-hernoemen, de stappenbalk en het donkere thema."""
import pytest

import core.storage as storage


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "flow.db")
    storage.init_db()
    yield


class TestRename:
    def test_rename_changes_the_name(self):
        ds = storage.create_dataset("oud", "", {})
        storage.rename_dataset(ds, "nieuw")
        assert storage.list_datasets()[0]["name"] == "nieuw"

    def test_rename_is_audited_with_the_old_name(self):
        """Zonder de oude naam zijn eerdere verwijzingen (rapporten,
        meldingen) achteraf niet meer te plaatsen."""
        ds = storage.create_dataset("oud", "", {})
        storage.rename_dataset(ds, "nieuw")
        entry = next(a for a in storage.list_audit(10)
                     if a["action"] == "dataset_hernoemd")
        assert "oud" in entry["detail"] and "nieuw" in entry["detail"]

    def test_duplicate_name_gives_a_clear_error(self):
        storage.create_dataset("bestaat", "", {})
        ds = storage.create_dataset("andere", "", {})
        with pytest.raises(ValueError, match="bestaat al"):
            storage.rename_dataset(ds, "bestaat")

    def test_empty_name_is_refused(self):
        ds = storage.create_dataset("oud", "", {})
        with pytest.raises(ValueError, match="niet leeg"):
            storage.rename_dataset(ds, "   ")

    def test_unchanged_name_is_a_no_op(self):
        ds = storage.create_dataset("zelfde", "", {})
        storage.rename_dataset(ds, "zelfde")
        assert not [a for a in storage.list_audit(10)
                    if a["action"] == "dataset_hernoemd"]

    def test_unknown_dataset_is_refused(self):
        with pytest.raises(ValueError, match="bestaat niet"):
            storage.rename_dataset(9999, "x")

    def test_whitespace_is_trimmed(self):
        ds = storage.create_dataset("oud", "", {})
        storage.rename_dataset(ds, "  netjes  ")
        assert storage.list_datasets()[0]["name"] == "netjes"


class TestFlowStepper:
    def test_steps_are_defined_in_order(self):
        from ui.components import NORMBEELD_STEPS
        namen = [n for n, _ in NORMBEELD_STEPS]
        assert namen == ["Dataset", "Tijdschaal", "Regio", "Beeld"]

    def test_every_step_has_a_hint(self):
        from ui.components import NORMBEELD_STEPS
        assert all(hint for _, hint in NORMBEELD_STEPS)


class TestDarkThemeConsistency:
    """Streamlit's eigen widgets gebruiken de kleuren uit config.toml, niet
    onze CSS. Stond die op 'light' terwijl de pagina donker was, dan kreeg
    je witte invoervelden op een donkere achtergrond."""

    def _config(self) -> str:
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        return (root / ".streamlit" / "config.toml").read_text(encoding="utf-8")

    def test_config_matches_the_default_theme(self):
        import pathlib

        from ui.state import init_session_state  # noqa: F401
        root = pathlib.Path(__file__).resolve().parent.parent
        state_src = (root / "ui" / "state.py").read_text(encoding="utf-8")
        assert '"ui_theme": "dark"' in state_src, "standaardthema is niet dark"
        assert 'base = "dark"' in self._config(), (
            "config.toml staat niet op dark; widgets renderen dan licht")

    def test_config_colours_match_the_palette(self):
        from ui.theme import PALETTES
        cfg = self._config()
        dark = PALETTES["dark"]
        assert dark["bg"] in cfg
        assert dark["surface"] in cfg
        assert dark["text"] in cfg
        assert dark["accent"] in cfg


def test_band_traces_share_a_legend_group():
    """Boven- en ondergrens moeten samen aan/uit: anders bleef bij het
    uitklikken van de band de bovengrens als losse lijn staan."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "visualizations" / "normbeeld_chart.py").read_text(
        encoding="utf-8")
    assert src.count('legendgroup="nb_band"') == 2
    assert src.count('legendgroup="fc_band"') == 2
