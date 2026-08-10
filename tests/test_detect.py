"""Indicator tests: verdict emission, and the honesty of the null result.

The centrepiece is `test_null_result_requires_measured_detection_power`. The
contract makes an uncalibrated "nothing found" impossible to construct, and
this file checks the detect layer responds to that by saying so plainly
rather than routing around it.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.core.baseline import fit_adaptive, fit_reference
from sentinel.core.contracts import (
    ConfidenceInputs,
    DateRange,
    DetectionPower,
    Direction,
    EvidenceKind,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    Reliability,
    Verdict,
)
from sentinel.core.detect import IndicatorContext, evaluate_indicator

AS_OF = datetime(2024, 12, 15)
REFERENCE = DateRange(datetime(2024, 1, 1), datetime(2024, 5, 1))
POWER = DetectionPower(scenario_kind="sustained_increase", floor_magnitude=1.5)


def _series(levels, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    values = np.asarray(levels, dtype=float)
    seasonal = 1.0 + 0.25 * np.sin(2 * np.pi * np.arange(len(values)) / 7)
    return pd.Series(
        rng.poisson(values * seasonal),
        index=pd.date_range("2024-01-01", periods=len(values), freq="D"),
        dtype=float,
    )


def _quiet_series(n: int = 350, seed: int = 0) -> pd.Series:
    return _series(np.full(n, 20.0), seed)


def _indicator(**overrides) -> Indicator:
    kwargs = dict(
        key="tempo", region_key="euro_atlantic", name="Strike tempo",
        question="Is the tempo unusual?",
        meaning="A rise suggests a change in posture.",
        test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.ACTIVE,
    )
    kwargs.update(overrides)
    return Indicator(**kwargs)


def _context(series: pd.Series, indicator: Indicator | None = None,
             with_reference: bool = False, power=POWER,
             **overrides) -> IndicatorContext:
    indicator = indicator or _indicator()
    kwargs = dict(
        indicator=indicator,
        series=series,
        as_of=AS_OF,
        adaptive=fit_adaptive(series),
        reference=fit_reference(series, REFERENCE) if with_reference else None,
        power=power,
        inputs=ConfidenceInputs(
            data_coverage=0.99, staleness_days=1, calibration_gap=0.02,
            source_reliability=Reliability.B, effective_corroboration=2.0,
            reconstruction_faithful=True),
    )
    kwargs.update(overrides)
    return IndicatorContext(**kwargs)


# =========================================================================
# the null result
# =========================================================================
def test_quiet_series_produces_a_null_result_with_a_floor():
    signal = evaluate_indicator(_context(_quiet_series()))
    assert signal.verdict is Verdict.NOT_ACTIVE
    assert signal.is_null_result
    assert signal.detection_power is POWER
    assert "1.5x or larger" in signal.null_statement()


def test_null_result_requires_measured_detection_power():
    """Without power, the system must not claim quiet.

    "Nothing found" that has never been calibrated is the most expensive
    kind of reassurance, so the honest answer is that we cannot say.
    """
    signal = evaluate_indicator(_context(_quiet_series(), power=None))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "detection power has not been measured" in signal.insufficient_reason
    assert not signal.is_null_result


def test_insufficient_data_is_distinguishable_from_quiet():
    quiet = evaluate_indicator(_context(_quiet_series()))
    unknown = evaluate_indicator(_context(_quiet_series(), power=None))
    assert quiet.verdict is not unknown.verdict


# =========================================================================
# level_deviation
# =========================================================================
def test_spike_produces_an_active_signal_above():
    series = _quiet_series(seed=3)
    series.iloc[-1] = 400.0
    signal = evaluate_indicator(_context(series))
    assert signal.verdict is Verdict.ACTIVE
    assert signal.direction is Direction.ABOVE
    assert signal.effect_size > 3.5


def test_collapse_produces_an_active_signal_below():
    series = _quiet_series(seed=5)
    series.iloc[-1] = 0.0
    signal = evaluate_indicator(_context(series))
    assert signal.verdict is Verdict.ACTIVE
    assert signal.direction is Direction.BELOW


def test_threshold_is_configurable_per_indicator():
    series = _quiet_series(seed=7)
    series.iloc[-1] = series.iloc[-1] * 2.2
    strict = _indicator(test_config={"threshold": 12.0})
    loose = _indicator(test_config={"threshold": 1.0})
    assert evaluate_indicator(
        _context(series, strict)).verdict is Verdict.NOT_ACTIVE
    assert evaluate_indicator(
        _context(series, loose)).verdict is Verdict.ACTIVE


def test_series_too_short_to_judge_reports_insufficient():
    signal = evaluate_indicator(_context(_quiet_series(n=10)))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "enough history" in signal.insufficient_reason


def test_missing_latest_observation_reports_insufficient():
    """A gap in the latest period is unknown, not quiet."""
    series = _quiet_series(seed=11)
    series.iloc[-1] = np.nan
    signal = evaluate_indicator(_context(series))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no observation" in signal.insufficient_reason


# =========================================================================
# sustained_divergence
# =========================================================================
def _sustained_indicator() -> Indicator:
    return _indicator(test_type=IndicatorTest.SUSTAINED_DIVERGENCE,
                      reference_period=REFERENCE)


def test_sustained_divergence_needs_a_reference_baseline():
    signal = evaluate_indicator(
        _context(_quiet_series(), _sustained_indicator(),
                 with_reference=False))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no reference baseline" in signal.insufficient_reason


def test_normalised_escalation_is_reported_as_active():
    """The v1 failure: a rise the adaptive baseline has absorbed."""
    levels = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    signal = evaluate_indicator(
        _context(_series(levels, seed=13), _sustained_indicator(),
                 with_reference=True))
    assert signal.verdict is Verdict.ACTIVE
    assert signal.direction is Direction.ABOVE


def test_normalised_escalation_explains_the_silence():
    levels = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    signal = evaluate_indicator(
        _context(_series(levels, seed=17), _sustained_indicator(),
                 with_reference=True))
    summaries = " ".join(e.summary for e in signal.evidence)
    assert "habituation" in summaries
    assert "not a return to normal" in summaries


def test_sustained_divergence_always_offers_the_reporting_alternative():
    """A level shift and a reporting change look identical in the numbers."""
    levels = np.concatenate([np.full(150, 20.0), np.full(200, 60.0)])
    signal = evaluate_indicator(
        _context(_series(levels, seed=19), _sustained_indicator(),
                 with_reference=True))
    alternatives = signal.evidence_of(EvidenceKind.ALTERNATIVE)
    assert alternatives
    assert "reporting" in alternatives[0].summary
    assert alternatives[0].weight <= 0


def test_quiet_series_is_not_a_sustained_divergence():
    signal = evaluate_indicator(
        _context(_quiet_series(seed=23), _sustained_indicator(),
                 with_reference=True))
    assert signal.verdict is Verdict.NOT_ACTIVE


def test_isolated_spike_is_not_reported_as_a_sustained_shift():
    """One indicator answers one question; a spike belongs to level_deviation."""
    series = _quiet_series(seed=29)
    series.iloc[300] = 500.0
    signal = evaluate_indicator(
        _context(series, _sustained_indicator(), with_reference=True))
    assert signal.verdict is Verdict.NOT_ACTIVE


# =========================================================================
# condition
# =========================================================================
def _condition(**config) -> Indicator:
    return _indicator(test_type=IndicatorTest.CONDITION, test_config=config)


def test_condition_above_threshold_fires():
    series = _quiet_series(seed=31)
    series.iloc[-3:] = 500.0
    signal = evaluate_indicator(
        _context(series, _condition(rule="above", threshold=100, periods=3)))
    assert signal.verdict is Verdict.ACTIVE
    assert signal.direction is Direction.ABOVE


def test_condition_requires_all_periods_to_hold():
    series = _quiet_series(seed=37)
    series.iloc[-3:] = 500.0
    series.iloc[-2] = 1.0  # breaks the run
    signal = evaluate_indicator(
        _context(series, _condition(rule="above", threshold=100, periods=3)))
    assert signal.verdict is Verdict.NOT_ACTIVE


def test_silence_condition_fires_on_real_zeros():
    """Absence is a signal, and a systematic blind spot of peak-finding."""
    series = _quiet_series(seed=41)
    series.iloc[-5:] = 0.0
    signal = evaluate_indicator(
        _context(series, _condition(rule="silence", periods=5)))
    assert signal.verdict is Verdict.ACTIVE
    assert signal.direction is Direction.BELOW


def test_unmet_condition_reports_power_without_pretending_to_measure():
    """A deterministic rule either holds or it does not — there is no effect
    size below which it would be missed, and saying so is not a measurement."""
    signal = evaluate_indicator(
        _context(_quiet_series(seed=43),
                 _condition(rule="above", threshold=1e6, periods=1),
                 power=None))
    assert signal.verdict is Verdict.NOT_ACTIVE
    assert signal.detection_power.floor_magnitude == 0.0
    assert signal.detection_power.is_quotable


def test_condition_without_a_threshold_is_rejected():
    signal = evaluate_indicator(
        _context(_quiet_series(seed=47), _condition(rule="above", periods=1)))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "needs a threshold" in signal.insufficient_reason


def test_unknown_condition_rule_is_rejected():
    signal = evaluate_indicator(
        _context(_quiet_series(seed=53), _condition(rule="vibes")))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "unknown condition rule" in signal.insufficient_reason


# =========================================================================
# dispatch and lifecycle
# =========================================================================
def test_draft_indicators_are_not_evaluated():
    signal = evaluate_indicator(
        _context(_quiet_series(), _indicator(status=IndicatorStatus.DRAFT)))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "not active" in signal.insufficient_reason


def test_every_declared_test_type_has_an_implementation():
    """All four are now built, so the 'not implemented' branch is unreachable.

    Worth asserting rather than assuming: adding a fifth test type to the
    enum without wiring it would otherwise surface as an indicator that
    silently reports insufficient data forever, which reads like a data
    problem rather than a missing implementation.
    """
    from sentinel.core.detect.tests import _TESTS

    assert set(_TESTS) == set(IndicatorTest)


def test_a_test_missing_its_prerequisites_says_which_one():
    """An unbuilt or unsupplied capability must not masquerade as clean."""
    indicator = _indicator(test_type=IndicatorTest.ENTITY_BEHAVIOUR,
                           entity_kind="vessel")
    signal = evaluate_indicator(_context(_quiet_series(), indicator))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no peer baseline" in signal.insufficient_reason


def test_every_signal_carries_grounded_confidence():
    for indicator in (_indicator(), _sustained_indicator(),
                      _condition(rule="silence", periods=2)):
        signal = evaluate_indicator(
            _context(_quiet_series(seed=59), indicator, with_reference=True))
        assert signal.confidence.reasons


@pytest.mark.parametrize("seed", [61, 67, 71])
def test_quiet_series_never_produce_active_signals(seed):
    """The property the whole design is for: quiet in, quiet out."""
    signal = evaluate_indicator(_context(_quiet_series(seed=seed)))
    assert signal.verdict is not Verdict.ACTIVE


# =========================================================================
# entity_behaviour
# =========================================================================
def _entity_indicator(**overrides) -> Indicator:
    kwargs = dict(
        key="loiter_near_infrastructure", region_key="nld_eez",
        name="Loitering near infrastructure",
        question="Is a vessel behaving unusually for its class?",
        meaning="A vessel class that does not normally linger, lingering.",
        test_type=IndicatorTest.ENTITY_BEHAVIOUR, entity_kind="vessel",
        status=IndicatorStatus.ACTIVE,
        test_config={"event_types": ["loiter"]},
    )
    kwargs.update(overrides)
    return Indicator(**kwargs)


def _fleet_context(indicator=None, only_fishing: bool = False,
                   peers=True, power=POWER):
    from sentinel.entity import PeerBaseline, extract_events
    from sentinel.eval.synthetic.vessels import build_fleet

    fleet = build_fleet()
    events = []
    for _key, group in fleet.positions.groupby("entity_key"):
        events.extend(extract_events(group))
    baseline = PeerBaseline.from_positions(fleet.positions, events)
    if only_fishing:
        events = [e for e in events if e.entity.key.startswith("fish")]
    return IndicatorContext(
        indicator=indicator or _entity_indicator(),
        as_of=AS_OF,
        events=tuple(events),
        peers=baseline if peers else None,
        power=power,
        inputs=ConfidenceInputs(data_coverage=0.99, staleness_days=1,
                                source_reliability=Reliability.C,
                                effective_corroboration=2.0,
                                reconstruction_faithful=True),
    )


def test_entity_behaviour_flags_the_vessel_that_does_not_belong():
    signal = evaluate_indicator(_fleet_context())
    assert signal.verdict is Verdict.ACTIVE
    summaries = " ".join(e.summary for e in signal.evidence)
    assert "cargo-target" in summaries
    assert "cargo vessels show loiter" in summaries


def test_entity_behaviour_does_not_flag_a_fleet_of_trawlers():
    """The failure this whole layer exists to avoid.

    Forty vessels loitering for hours, all of them doing their job. The
    events are real; the verdict must still be quiet.
    """
    signal = evaluate_indicator(_fleet_context(only_fishing=True))
    assert signal.verdict is Verdict.NOT_ACTIVE
    assert "within the normal range" in " ".join(
        e.summary for e in signal.evidence)


def test_entity_behaviour_always_offers_a_non_hostile_explanation():
    """Behaviour is not intent, and the alert must say so unprompted."""
    signal = evaluate_indicator(_fleet_context())
    alternatives = signal.evidence_of(EvidenceKind.ALTERNATIVE)
    assert alternatives
    assert "not intent" in alternatives[0].summary


def test_entity_behaviour_without_a_peer_baseline_is_untested():
    """Unusual 'for its class' is meaningless without a class to compare."""
    signal = evaluate_indicator(_fleet_context(peers=False))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "no peer baseline" in signal.insufficient_reason


def test_entity_behaviour_with_no_observed_events_is_genuinely_quiet():
    """The primitives ran and found nothing to judge — a real null."""
    context = _fleet_context()
    signal = evaluate_indicator(
        IndicatorContext(indicator=context.indicator, as_of=AS_OF,
                         events=(), peers=context.peers, power=POWER,
                         inputs=context.inputs))
    assert signal.verdict is Verdict.NOT_ACTIVE


def test_entity_behaviour_respects_the_declared_event_types():
    indicator = _entity_indicator(test_config={"event_types": ["ais_gap"]})
    signal = evaluate_indicator(_fleet_context(indicator=indicator))
    # The fleet produces no gaps, so an ais_gap indicator sees nothing.
    assert signal.verdict is Verdict.NOT_ACTIVE


def test_entity_behaviour_null_result_still_needs_detection_power():
    """The same rule as every other test: no floor, no claim of quiet."""
    signal = evaluate_indicator(_fleet_context(only_fishing=True, power=None))
    assert signal.verdict is Verdict.INSUFFICIENT_DATA
    assert "detection power" in signal.insufficient_reason


def test_entity_behaviour_ranks_by_how_unusual_rather_than_by_order():
    """The evidence names the most unusual vessel, not the first one seen."""
    signal = evaluate_indicator(_fleet_context())
    first = signal.evidence[0].summary
    assert "cargo-target" in first, (
        "the headline evidence must be the strongest case, not an arbitrary one")
