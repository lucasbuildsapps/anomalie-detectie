"""Evaluation harness: prove when the system fails, not only when it works."""
from sentinel.eval.budget import (
    AlertBudget,
    BudgetCalibration,
    ThresholdOption,
    calibrate_indicator,
    sweep_thresholds,
)
from sentinel.eval.episodes import (
    Episode,
    MatchResult,
    match_episodes,
    to_episodes,
)
from sentinel.eval.harness import (
    PowerCurve,
    ScenarioScore,
    detection_floor,
    false_alarm_rate,
    power_curve,
    run_scenario,
    run_suite,
)
from sentinel.eval.retrospective import (
    RetrospectiveReport,
    TruthEvent,
    WarningResult,
    events_from_chronology,
    replay,
)
from sentinel.eval.synthetic import SCENARIO_KINDS, Scenario, ScenarioGenerator

__all__ = [
    "SCENARIO_KINDS",
    "AlertBudget",
    "BudgetCalibration",
    "Episode",
    "MatchResult",
    "PowerCurve",
    "RetrospectiveReport",
    "Scenario",
    "ScenarioGenerator",
    "ScenarioScore",
    "ThresholdOption",
    "TruthEvent",
    "WarningResult",
    "calibrate_indicator",
    "detection_floor",
    "events_from_chronology",
    "false_alarm_rate",
    "match_episodes",
    "power_curve",
    "replay",
    "run_scenario",
    "run_suite",
    "sweep_thresholds",
    "to_episodes",
]
