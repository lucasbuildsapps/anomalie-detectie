"""Tests voor severity-classificatie: de expliciete stem-tabel."""
import numpy as np

from core.auto_pilot import classify_severity


def _sev(votes, n):
    return list(classify_severity(np.array(votes), n))


def test_single_vote_is_never_anomaly_with_multiple_methods():
    """Eén methode die roept = ruis, geen afwijking (bij 2+ methodes)."""
    for n in (2, 3, 4, 5):
        assert _sev([1], n) == [None], f"1 stem bij n={n} moet None zijn"


def test_three_methods():
    assert _sev([0, 1, 2, 3], 3) == [None, None, "midden", "hoog"]


def test_four_methods():
    assert _sev([1, 2, 3, 4], 4) == [None, "laag", "midden", "hoog"]


def test_five_methods():
    assert _sev([1, 2, 3, 4, 5], 5) == [None, "laag", "midden", "hoog", "hoog"]


def test_two_methods_agreement_is_midden():
    assert _sev([2], 2) == ["midden"]


def test_one_method_cannot_exceed_laag():
    assert _sev([1], 1) == ["laag"]


def test_zero_methods_safe():
    assert _sev([0, 1], 0) == [None, None]
