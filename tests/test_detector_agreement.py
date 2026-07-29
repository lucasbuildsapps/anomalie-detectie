"""Tests voor de detector-correlatie-meting (METHODS.md §8).

Kernvraag die dit beantwoordt: als 4 van de 5 algoritmes iets markeren,
zijn dat dan 4 getuigen of 4x dezelfde getuige?
"""
import numpy as np
import pytest

from core.auto_pilot import detector_agreement


def _flags(pattern: str) -> np.ndarray:
    """'..X..X' -> booleans; leesbare manier om vlaggen op te schrijven."""
    return np.array([c == "X" for c in pattern], dtype=bool)


def test_returns_none_below_two_detectors():
    assert detector_agreement({}) is None
    assert detector_agreement({"a": _flags("..X..")}) is None


def test_returns_none_on_length_mismatch():
    out = detector_agreement({"a": _flags("..X.."), "b": _flags("..X")})
    assert out is None


def test_identical_detectors_collapse_to_one():
    same = _flags("X..X..X...")
    out = detector_agreement({"a": same, "b": same.copy(), "c": same.copy()})
    assert out["n_detectors"] == 3
    assert out["n_effective"] == pytest.approx(1.0, abs=0.05)
    assert out["max_phi"] == pytest.approx(1.0, abs=1e-6)
    assert all(p["jaccard"] == pytest.approx(1.0) for p in out["pairs"])


def test_disjoint_detectors_count_separately():
    a = _flags("XX........")
    b = _flags("..XX......")
    c = _flags("....XX....")
    out = detector_agreement({"a": a, "b": b, "c": c})
    # Geen overlap -> elk algoritme draagt eigen informatie
    assert all(p["jaccard"] == 0.0 for p in out["pairs"])
    assert out["n_effective"] > 2.0


def test_partial_overlap_lands_in_between():
    a = _flags("XXXX......")
    b = _flags("..XXXX....")
    out = detector_agreement({"a": a, "b": b})
    pair = out["pairs"][0]
    assert 0.0 < pair["jaccard"] < 1.0
    assert pair["n_both"] == 2
    assert 1.0 < out["n_effective"] <= 2.0


def test_constant_flags_do_not_crash():
    # Een detector die niets (of alles) markeert heeft geen variantie;
    # phi is dan niet gedefinieerd maar mag de meting niet slopen.
    out = detector_agreement({
        "stil": np.zeros(10, dtype=bool),
        "actief": _flags("X..X..X..."),
    })
    assert out is not None
    assert not np.isfinite(out["pairs"][0]["phi"])
    assert out["pairs"][0]["jaccard"] == 0.0


def test_effective_count_never_exceeds_detector_count():
    rng = np.random.default_rng(4)
    outs = {f"d{i}": rng.random(200) > 0.9 for i in range(5)}
    out = detector_agreement(outs)
    assert 1.0 <= out["n_effective"] <= 5.0


def test_pairs_cover_all_combinations():
    rng = np.random.default_rng(7)
    outs = {f"d{i}": rng.random(50) > 0.8 for i in range(4)}
    out = detector_agreement(outs)
    assert len(out["pairs"]) == 6  # 4 kies 2
