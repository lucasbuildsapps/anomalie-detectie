"""The shared data contract.

Every layer — ingestion, entity engine, baselines, detection tests, regional
modules, UI — reads and writes these types. Regions extend the model through
open string fields (`event_type`, `entity_kind`, `region_key`, `area_key`),
never by adding members to the enums or fields to these classes. If a region
cannot be expressed without changing core, that is a design conversation, not
a patch.

Validation lives in the types themselves so the architecture's rules fail at
construction rather than in review. Most notably: a null result cannot exist
without detection power, an active signal cannot exist without an effect
size, and an unmonitored region cannot be rendered as a quiet one.
"""
from sentinel.core.contracts.entity import (
    Entity,
    EntityIdentifier,
    EntityRef,
)
from sentinel.core.contracts.enums import (
    BaselineKind,
    ConfidenceLevel,
    Credibility,
    Direction,
    EvidenceKind,
    IndicatorStatus,
    IndicatorTest,
    LikelihoodBand,
    MonitoringStatus,
    Producer,
    Reliability,
    Verdict,
)
from sentinel.core.contracts.indicator import Baseline, Indicator
from sentinel.core.contracts.judgement import (
    Assessment,
    Confidence,
    ConfidenceInputs,
    DetectionPower,
    Evidence,
    RegionStatus,
    Signal,
)
from sentinel.core.contracts.observation import Event, Observation
from sentinel.core.contracts.primitives import DateRange, GeoPoint
from sentinel.core.contracts.provenance import Lineage, Source

__all__ = [
    "Assessment",
    "Baseline",
    "BaselineKind",
    "Confidence",
    "ConfidenceInputs",
    "ConfidenceLevel",
    "Credibility",
    "DateRange",
    "DetectionPower",
    "Direction",
    "Entity",
    "EntityIdentifier",
    "EntityRef",
    "Event",
    "Evidence",
    "EvidenceKind",
    "GeoPoint",
    "Indicator",
    "IndicatorStatus",
    "IndicatorTest",
    "LikelihoodBand",
    "Lineage",
    "MonitoringStatus",
    "Observation",
    "Producer",
    "RegionStatus",
    "Reliability",
    "Signal",
    "Source",
    "Verdict",
]
