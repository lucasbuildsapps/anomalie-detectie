"""Tests voor kleine UI-helpers (formattering die eerder verwarrende
teksten opleverde: 'extremer dan 100%' en 'verwacht 0-17' bij band 0.4-17)."""
from ui.components import _fmt_num, _pctl_label


def test_pctl_never_shows_100_percent():
    row = {"resid_pctl": 0.9999, "status": "boven"}
    assert "100%" not in _pctl_label(row)
    assert "99%" in _pctl_label(row)


def test_pctl_below_band_uses_complement():
    row = {"resid_pctl": 0.01, "status": "onder"}
    assert "99%" in _pctl_label(row)


def test_pctl_midrange():
    row = {"resid_pctl": 0.80, "status": "boven"}
    assert "80%" in _pctl_label(row)


def test_fmt_num_small_fraction_keeps_decimal():
    # Ondergrens 0.4 mag niet als '0' tonen — dan spreekt de afwijkings-
    # tekst zichzelf tegen ('0 per dag onder band, verwacht 0-17').
    assert _fmt_num(0.4) == "0.4"


def test_fmt_num_whole_numbers_stay_compact():
    assert _fmt_num(17.02) == "17"
    assert _fmt_num(5.0) == "5"
    assert _fmt_num(123.6) == "124"
