"""The alert budget, and the line between a budget and a quota.

v1 died of a quota: `run_auto_pilot()` loosened sensitivity until it had
something to show, which guaranteed findings and guaranteed them loudest when
the data was quietest. The budget replaces it, and the tests that matter here
are the ones that keep it from becoming the same thing wearing a new name.
"""
from __future__ import annotations

import math

import pytest

from sentinel.core.contracts import (
    Indicator,
    IndicatorStatus,
    IndicatorTest,
)
from sentinel.eval.budget import (
    AlertBudget,
    BudgetCalibration,
    ThresholdOption,
)


def _option(threshold=3.0, quiet=1.0, floor=3.0, confounded=False) -> ThresholdOption:
    return ThresholdOption(threshold=threshold, quiet_per_week=quiet,
                           floor=floor, confounded=confounded,
                           scenario_kind="spike")


def _calibration(*options, budget=None) -> BudgetCalibration:
    return BudgetCalibration(indicator_key="k", options=tuple(options),
                             budget=budget or AlertBudget())


# =========================================================================
# the budget is not a quota
# =========================================================================
def test_only_the_upper_bound_binds():
    """Producing under the minimum on quiet data is the desired outcome.

    The obvious way to 'reach' 5/week is to loosen until noise supplies the
    difference — which is the quota rebuilt with extra steps.
    """
    budget = AlertBudget(min_per_week=5.0, max_per_week=15.0)
    assert budget.admits(0.0)
    assert budget.admits(2.0)
    assert budget.admits(15.0)
    assert not budget.admits(15.1)


def test_a_quiet_configuration_is_not_reported_as_a_shortfall():
    text = _calibration(_option(quiet=0.2)).describe()
    assert "desired outcome" in text
    assert "not a shortfall" in text


def test_the_recommendation_maximises_sensitivity_not_alert_count():
    """Picking the option nearest the middle of the range would be optimising
    for a number of alerts, which is how a budget turns back into a quota."""
    loose = _option(threshold=2.0, quiet=8.0, floor=2.0)
    tight = _option(threshold=5.0, quiet=0.0, floor=8.0)
    assert _calibration(loose, tight).recommended is loose


def test_an_option_over_budget_is_not_recommended():
    over = _option(threshold=1.0, quiet=40.0, floor=1.5)
    fine = _option(threshold=3.0, quiet=1.0, floor=3.0)
    calibration = _calibration(over, fine)
    assert calibration.recommended is fine
    assert over not in calibration.affordable


# =========================================================================
# the cost of fitting the budget
# =========================================================================
def test_a_threshold_without_a_usable_floor_is_never_recommended():
    """A budget met by going blind is the same failure as a negative control
    passed by blindness."""
    blind = _option(threshold=9.0, quiet=0.0, floor=float("nan"))
    assert _calibration(blind).recommended is None
    assert not blind.floor_is_quotable


def test_a_confounded_option_is_not_affordable():
    noisy = _option(threshold=0.5, quiet=1.0, floor=float("nan"),
                    confounded=True)
    assert _calibration(noisy).affordable == ()


def test_when_nothing_fits_the_report_says_retuning_will_not_help():
    text = _calibration(_option(quiet=99.0, floor=float("nan"))).describe()
    assert "needs rethinking, not retuning" in text


def test_every_option_reports_volume_and_floor_together():
    """Either number alone is misleading: quiet-and-blind and sensitive-and-
    deafening both look good on one axis."""
    text = _option(threshold=3.0, quiet=1.4, floor=3.0).describe()
    assert "1.4 alerts/week" in text
    assert "from 3x" in text


def test_a_blind_option_says_so_rather_than_quoting_a_floor():
    text = _option(floor=float("nan")).describe()
    assert "no usable detection floor" in text
    assert "nan" not in text.lower()


# =========================================================================
# inputs
# =========================================================================
def test_an_inverted_budget_is_refused():
    with pytest.raises(ValueError, match="minimum above its maximum"):
        AlertBudget(min_per_week=20.0, max_per_week=5.0)


def test_a_negative_budget_is_refused():
    with pytest.raises(ValueError, match="not a budget"):
        AlertBudget(min_per_week=-1.0)


def test_a_deterministic_condition_has_no_threshold_to_sweep():
    """Offering one would invite tuning away a finding nobody wanted to see."""
    from sentinel.eval.budget import calibrate_indicator

    condition = Indicator(
        key="silence", region_key="euro_atlantic", name="S", question="q?",
        meaning="m", test_type=IndicatorTest.CONDITION,
        test_config={"rule": "silence"}, status=IndicatorStatus.ACTIVE)
    assert calibrate_indicator(condition) is None


# =========================================================================
# the floor stability defect this work uncovered
# =========================================================================
def test_a_floor_on_the_decision_boundary_is_flagged_unresolved():
    """Measured, not reasoned: the shipped spike configuration read 5x at 8
    repeats and 3x at 24, because net recall at 3x sat on the 0.8 boundary."""
    from sentinel.eval.harness import PowerCurve

    curve = PowerCurve(kind="spike", magnitudes=[1.5, 2.0, 3.0, 5.0],
                       recalls=[0.0, 0.25, 0.75, 1.0],
                       chance_rates=[0.0, 0.0, 0.0, 0.0],
                       n_repeats=8, threshold=0.8)
    assert curve.floor == 5.0
    assert curve.floor_is_uncertain
    assert "not resolved" in curve.describe()


def test_a_floor_clear_of_the_boundary_is_resolved():
    from sentinel.eval.harness import PowerCurve

    curve = PowerCurve(kind="spike", magnitudes=[1.5, 2.0, 3.0],
                       recalls=[0.0, 0.05, 1.0],
                       chance_rates=[0.0, 0.0, 0.0],
                       n_repeats=24, threshold=0.8)
    assert curve.floor == 3.0
    assert not curve.floor_is_uncertain
    assert "not resolved" not in curve.describe()


def test_an_unmeasurable_floor_is_not_also_called_unresolved():
    """Two different failures; conflating them would hide the worse one."""
    from sentinel.eval.harness import PowerCurve

    curve = PowerCurve(kind="spike", magnitudes=[1.5, 3.0],
                       recalls=[0.0, 0.0], chance_rates=[0.0, 0.0],
                       n_repeats=24, threshold=0.8)
    assert math.isnan(curve.floor)
    assert not curve.floor_is_uncertain


def test_the_committed_catalogue_carries_resolution(tmp_path):
    from sentinel.core.contracts import DetectionPower
    from sentinel.core.detect.power import DetectionPowerCatalog

    catalog = DetectionPowerCatalog(entries={
        "k": DetectionPower("spike", 5.0, n_repeats=8, resolved=False)})
    path = tmp_path / "power.json"
    catalog.save(path)
    restored = DetectionPowerCatalog.load(path).entries["k"]
    assert restored.resolved is False
    assert "treat it as approximate" in restored.describe()


def test_the_shipped_catalogue_has_no_unresolved_floors():
    """Guards the regeneration: a floor nobody resolved should not be the one
    backing a null result on the watchboard."""
    from sentinel.core.detect.power import DetectionPowerCatalog

    catalog = DetectionPowerCatalog.load("config/detection_power.json")
    unresolved = [k for k, p in catalog.entries.items() if not p.resolved]
    assert not unresolved, (
        f"{len(unresolved)} configuration(s) quote an unresolved floor; "
        f"re-run scripts/precompute_power.py --force with more repeats")


def test_a_recommendation_resting_on_an_unresolved_floor_says_so():
    """The failure this module made on its own first run: at four repeats it
    recommended changing a shipped threshold, and at twenty-four the two
    options were indistinguishable."""
    shaky = ThresholdOption(threshold=3.0, quiet_per_week=0.0, floor=3.0,
                            scenario_kind="spike", floor_resolved=False,
                            n_repeats=4)
    steady = ThresholdOption(threshold=3.5, quiet_per_week=0.0, floor=5.0,
                             scenario_kind="spike", n_repeats=4)
    text = _calibration(shaky, steady).describe()
    assert "unresolved" in text
    assert "before changing a shipped threshold" in text


def test_a_fully_resolved_calibration_carries_no_such_warning():
    steady = ThresholdOption(threshold=3.0, quiet_per_week=0.0, floor=3.0,
                             scenario_kind="spike", n_repeats=24)
    assert "unresolved" not in _calibration(steady).describe()
