"""Euro-Atlantic: strike tempo against a declared reference.

Chosen as the first fully-built region because it carries the least data risk
— the demo dataset already exists and published chronologies allow
retrospective validation independent of anything the tool flagged. That lets
the core corrections be proven before the harder maritime work begins.

The reference period is the load-bearing declaration here. "Normal" for this
region is not the recent past — the recent past is the war. It has to be
written down by an analyst, dated, and reviewed, which is exactly what
`Indicator` refuses to build a sustained-divergence test without.
"""
from __future__ import annotations

from datetime import datetime

from sentinel.core.contracts import (
    Credibility,
    DateRange,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    MonitoringStatus,
    Reliability,
    Source,
)
from sentinel.regions.base import GeoScope, RegionModule

KEY = "euro_atlantic"

#: Declared peacetime normal. A placeholder until an analyst signs off on it:
#: the value of a fixed reference comes from a human standing behind the
#: dates, not from the code having a default.
REFERENCE_PERIOD = DateRange(datetime(2015, 1, 1), datetime(2021, 12, 31))

SOURCES = (
    Source(
        key="acled",
        name="ACLED conflict events",
        kind="event_db",
        reliability=Reliability.B,
        credibility=Credibility.C2,
        url="https://acleddata.com",
        licence="ACLED terms; non-commercial use requires registration",
        redistribution_allowed=False,
    ),
    Source(
        key="demo_missile_attacks",
        name="Russian missile attacks on Ukraine (open dataset)",
        kind="event_db",
        reliability=Reliability.C,
        credibility=Credibility.C3,
        licence="open data",
        redistribution_allowed=True,
    ),
)

INDICATORS = (
    Indicator(
        key="strike_tempo_sustained",
        region_key=KEY,
        name="Sustained strike tempo",
        question=("Has the rate of strikes run above the declared reference "
                  "level long enough to represent a shift rather than a "
                  "fluctuation?"),
        meaning=("A sustained rise suggests a change in operational posture "
                 "or resourcing, not a single operation. It is the signal a "
                 "purely adaptive baseline loses, because the elevated level "
                 "becomes its new normal."),
        test_type=IndicatorTest.SUSTAINED_DIVERGENCE,
        reference_period=REFERENCE_PERIOD,
        status=IndicatorStatus.ACTIVE,
        test_config={"aggregation": "daily"},
    ),
    Indicator(
        key="strike_tempo_spike",
        region_key=KEY,
        name="Single-period strike surge",
        question="Is today's strike count unusual against the recent baseline?",
        meaning=("A one-off surge, which may indicate a discrete operation. "
                 "Distinct from a sustained shift and deliberately tested "
                 "separately, so one indicator answers one question."),
        test_type=IndicatorTest.LEVEL_DEVIATION,
        status=IndicatorStatus.ACTIVE,
        test_config={"threshold": 3.5, "aggregation": "daily"},
    ),
    Indicator(
        key="reporting_silence",
        region_key=KEY,
        name="Reporting silence",
        question="Has reporting stopped where it is normally continuous?",
        meaning=("Either activity genuinely ceased or collection failed. Both "
                 "matter and the tool cannot tell them apart, so the alert "
                 "must say so. Absence is a classic warning signal and a "
                 "systematic blind spot of anything built around peaks."),
        test_type=IndicatorTest.CONDITION,
        status=IndicatorStatus.ACTIVE,
        test_config={"rule": "silence", "periods": 5},
    ),
)

MODULE = RegionModule(
    key=KEY,
    name="Euro-Atlantic",
    status=MonitoringStatus.MONITORED,
    geography=GeoScope(lat_min=44.0, lat_max=53.0, lon_min=22.0, lon_max=41.0,
                       areas=("north", "east", "south", "centre")),
    sources=SOURCES,
    indicators=INDICATORS,
    reference_period=REFERENCE_PERIOD,
    summary=("Strike tempo and reporting continuity, measured against a "
             "declared pre-2022 reference rather than against the recent "
             "past."),
)
