"""Detection power for entity behaviour, and the cliff it exposes.

The prevalence measurement is the reason this module exists. Entity
behaviour fails along a dimension the series harness cannot express, and it
fails *silently* — a clean null result while the thing the capability was
built to catch becomes common.
"""
from __future__ import annotations

import numpy as np
import pytest

from sentinel.core.contracts import DetectionPower
from sentinel.eval.entity_power import (
    EntityPowerResult,
    measure,
    measure_duration_floor,
    measure_prevalence_cliff,
)


# =========================================================================
# the two floors
# =========================================================================
def test_duration_floor_is_the_primitive_minimum():
    """Below the primitive's own minimum no event is emitted at all, so
    nothing downstream can judge anything. The floor is that threshold."""
    floor, recall = measure_duration_floor(n_repeats=2)
    assert floor == pytest.approx(1.0)
    assert recall >= 0.8, "the floor must be the duration that actually met it"


def test_short_loiters_are_not_detected():
    from sentinel.eval.entity_power import _recall

    recall, _ = _recall(target_loiter_hours=0.5)
    assert recall == 0.0


def test_long_loiters_are_detected_without_flagging_trawlers():
    from sentinel.eval.entity_power import _recall

    recall, fishing = _recall(target_loiter_hours=4.0)
    assert recall == 1.0
    assert fishing == 0


def test_prevalence_cliff_is_measured_and_conservative():
    """Reported as the last prevalence that worked, not the first that
    failed — a floor an analyst relies on has to be the safe end."""
    cliff = measure_prevalence_cliff(n_repeats=1)
    assert 0.0 < cliff < 0.20


def test_detection_collapses_once_the_behaviour_stops_being_rare():
    """The silent failure this measurement exists to surface.

    Rarity is the signal. When enough of a class does the thing, it is no
    longer rare *by definition*, and the baseline flags nobody — including
    the vessels that are genuinely worth flagging. There is no graceful
    degradation and no reduced-confidence warning; the output is a clean
    null.
    """
    from sentinel.eval.entity_power import _recall

    rare, _ = _recall(n_contaminating=0)
    common, _ = _recall(n_contaminating=20)
    assert rare == 1.0
    assert common == 0.0, (
        "if this starts passing, the peer baseline has gained a defence "
        "against normalisation and the caveat can be relaxed")


# =========================================================================
# reporting
# =========================================================================
def test_result_carries_both_floors_and_the_false_alarm_check():
    result = measure(n_repeats=1)
    assert np.isfinite(result.duration_floor_hours)
    assert result.prevalence_cliff > 0
    assert result.fishing_false_positives == 0
    assert result.is_quotable


def test_a_configuration_that_flags_trawlers_is_not_quotable():
    """Recall alone would be a misleading number at fleet scale."""
    result = EntityPowerResult(duration_floor_hours=1.0, prevalence_cliff=0.09,
                               fishing_false_positives=7, threshold=0.8,
                               n_repeats=3)
    assert not result.is_quotable
    assert result.to_detection_power().confounded


def test_detection_power_reports_hours_not_a_multiplier():
    """Rendering a duration as '1x' would be quietly wrong."""
    power = measure(n_repeats=1).to_detection_power()
    assert power.unit == "hours"
    assert "1 hours" in power.describe()
    assert "1x" not in power.describe()


def test_detection_power_carries_the_prevalence_caveat():
    """An unqualified floor would overstate what the null result covers."""
    text = measure(n_repeats=1).to_detection_power().describe()
    assert "rarer than" in text
    assert "nothing is flagged" in text


def test_multiplier_units_still_render_as_before():
    """The contract change must not disturb the series measurements."""
    assert "1.5x or larger" in DetectionPower("Sustained increase", 1.5).describe()


def test_describe_reads_as_a_sentence():
    text = measure(n_repeats=1).describe()
    assert text[0].isupper()
    assert text.rstrip().endswith(".")


# =========================================================================
# the floor has to know whether it was resolved
# =========================================================================
def test_a_perfect_score_from_few_runs_is_not_a_resolved_floor():
    """The usual standard error collapses to zero at p=1, which would declare
    a floor resolved on three lucky draws. The rule of three prevents that."""
    lucky = EntityPowerResult(duration_floor_hours=1.0, prevalence_cliff=0.09,
                              fishing_false_positives=0, threshold=0.8,
                              n_repeats=3, floor_recall=1.0)
    assert not lucky.floor_is_resolved
    assert not lucky.to_detection_power().resolved


def test_a_perfect_score_from_enough_runs_is_resolved():
    solid = EntityPowerResult(duration_floor_hours=1.0, prevalence_cliff=0.09,
                              fishing_false_positives=0, threshold=0.8,
                              n_repeats=48, floor_recall=1.0)
    assert solid.floor_is_resolved
    assert solid.to_detection_power().resolved


def test_a_recall_on_the_decision_boundary_is_not_resolved():
    borderline = EntityPowerResult(
        duration_floor_hours=1.0, prevalence_cliff=0.09,
        fishing_false_positives=0, threshold=0.8, n_repeats=8,
        floor_recall=0.82)
    assert not borderline.floor_is_resolved
    assert "treat it as approximate" in \
        borderline.to_detection_power().describe()
