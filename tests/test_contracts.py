"""Contract validation.

The value of this layer is that architectural rules fail at construction
rather than in code review. So these tests are mostly about what the types
*refuse* to build. A contract whose invariants are untested is a comment.
"""
from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

from sentinel.core.contracts import (
    Assessment,
    Baseline,
    BaselineKind,
    Confidence,
    ConfidenceInputs,
    ConfidenceLevel,
    Credibility,
    DateRange,
    DetectionPower,
    Direction,
    Entity,
    EntityIdentifier,
    EntityRef,
    Event,
    Evidence,
    EvidenceKind,
    GeoPoint,
    Indicator,
    IndicatorStatus,
    IndicatorTest,
    LikelihoodBand,
    Lineage,
    MonitoringStatus,
    Observation,
    Producer,
    RegionStatus,
    Reliability,
    Signal,
    Source,
    Verdict,
)

T0 = datetime(2024, 6, 1, 12, 0)
LATER = T0 + timedelta(hours=6)
PERIOD = DateRange(datetime(2015, 1, 1), datetime(2019, 12, 31))


def _confidence(level=ConfidenceLevel.MODERATE) -> Confidence:
    return Confidence(level=level, reasons=("test fixture",))


def _power(floor: float = 1.5, confounded: bool = False) -> DetectionPower:
    return DetectionPower(scenario_kind="sustained_increase",
                          floor_magnitude=floor, confounded=confounded)


# =========================================================================
# primitives
# =========================================================================
def test_geopoint_rejects_impossible_coordinates():
    with pytest.raises(ValueError, match="latitude"):
        GeoPoint(lat=91.0, lon=0.0)
    with pytest.raises(ValueError, match="longitude"):
        GeoPoint(lat=0.0, lon=181.0)


def test_geopoint_precision_drives_is_precise():
    assert GeoPoint(52.0, 4.0, precision_m=10).is_precise
    assert not GeoPoint(52.0, 4.0, precision_m=50_000).is_precise
    assert not GeoPoint(52.0, 4.0).is_precise, "unknown precision is not precise"


def test_daterange_rejects_reversed_bounds():
    with pytest.raises(ValueError, match="before it starts"):
        DateRange(LATER, T0)


def test_daterange_overlap_and_containment():
    a = DateRange(datetime(2024, 1, 1), datetime(2024, 6, 30))
    b = DateRange(datetime(2024, 6, 1), datetime(2024, 12, 31))
    c = DateRange(datetime(2025, 1, 1), datetime(2025, 6, 30))
    assert a.overlaps(b) and not a.overlaps(c)
    assert a.contains(datetime(2024, 3, 1))


# =========================================================================
# provenance
# =========================================================================
def test_source_requires_key_and_name():
    with pytest.raises(ValueError, match="key cannot be empty"):
        Source(key="", name="x")
    with pytest.raises(ValueError, match="human-readable name"):
        Source(key="k", name="  ")


def test_ungraded_source_contributes_nothing():
    """An ungraded source must not silently count as a good one."""
    source = Source(key="s", name="Some feed")
    assert not source.is_graded
    assert source.grading == "ungraded"
    assert "no confidence" in source.describe()


def test_source_grading_pair():
    source = Source(key="s", name="Feed", reliability=Reliability.B,
                    credibility=Credibility.C2)
    assert source.is_graded and source.grading == "B2"


def test_ungradable_letters_do_not_count_as_graded():
    """F and 6 mean 'cannot be judged', not 'judged and fine'."""
    source = Source(key="s", name="Feed", reliability=Reliability.F,
                    credibility=Credibility.C6)
    assert not source.is_graded


def test_derived_lineage_must_name_its_method():
    with pytest.raises(ValueError, match="name the method"):
        Lineage(producer=Producer.ENTITY_ENGINE)
    assert Lineage(producer=Producer.ENTITY_ENGINE,
                   method="loiter.v1").is_derived


def test_ingested_lineage_needs_no_method():
    assert not Lineage(source_keys=("ais",)).is_derived


def test_with_source_is_idempotent_and_immutable():
    base = Lineage(source_keys=("a",))
    assert base.with_source("a") is base
    extended = base.with_source("b")
    assert extended.source_keys == ("a", "b")
    assert base.source_keys == ("a",)


# =========================================================================
# observation / event  — the two-layer bridge
# =========================================================================
def test_observation_rejects_arrival_before_occurrence():
    """Knowing something before it happens means a clock is wrong."""
    with pytest.raises(ValueError, match="precedes"):
        Observation(event_time=LATER, ingested_at=T0, value=1.0,
                    source_key="s")


def test_observation_known_at_applies_both_filters():
    obs = Observation(event_time=T0, ingested_at=LATER, value=1.0,
                      source_key="s")
    assert not obs.known_at(T0), "not yet arrived"
    assert obs.known_at(LATER)


def test_reporting_lag_is_nan_when_arrival_was_assumed():
    obs = Observation(event_time=T0, ingested_at=T0, value=1.0,
                      source_key="s", ingest_estimated=True)
    assert math.isnan(obs.reporting_lag)


def test_reporting_lag_measured_when_arrival_observed():
    obs = Observation(event_time=T0, ingested_at=LATER, value=1.0,
                      source_key="s")
    assert obs.reporting_lag == pytest.approx(6.0)


def test_event_requires_type_and_region():
    with pytest.raises(ValueError, match="must have a type"):
        Event(event_type="", event_time=T0, ingested_at=T0,
              region_key="r", lineage=Lineage())
    with pytest.raises(ValueError, match="belong to a region"):
        Event(event_type="strike", event_time=T0, ingested_at=T0,
              region_key=" ", lineage=Lineage())


def test_derived_event_must_reference_its_entity():
    """Entity-engine output without an entity is unreviewable."""
    derived = Lineage(producer=Producer.ENTITY_ENGINE, method="loiter.v1")
    with pytest.raises(ValueError, match="must reference the entity"):
        Event(event_type="loiter", event_time=T0, ingested_at=T0,
              region_key="nld_eez", lineage=derived)

    ok = Event(event_type="loiter", event_time=T0, ingested_at=T0,
               region_key="nld_eez", lineage=derived,
               entity=EntityRef("vessel", "v1"))
    assert ok.is_derived


def test_region_specific_types_need_no_core_change():
    """The modularity claim, exercised rather than asserted."""
    for region, kind in (("nld_eez", "dark_rendezvous"),
                         ("indo_pacific", "adiz_incursion"),
                         ("caribbean", "route_deviation")):
        event = Event(event_type=kind, event_time=T0, ingested_at=T0,
                      region_key=region, lineage=Lineage(source_keys=("x",)))
        assert event.event_type == kind


# =========================================================================
# entity — identity over time
# =========================================================================
def test_entity_ref_rejects_blanks():
    with pytest.raises(ValueError, match="kind cannot be empty"):
        EntityRef("", "k")
    with pytest.raises(ValueError, match="key cannot be empty"):
        EntityRef("vessel", "")


def test_identifier_confidence_must_be_a_fraction():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        EntityIdentifier(scheme="mmsi", value="1", confidence=1.5)


def test_identifier_validity_window_must_be_ordered():
    with pytest.raises(ValueError, match="expires before"):
        EntityIdentifier(scheme="mmsi", value="1",
                         valid_from=LATER, valid_to=T0)


def test_identifier_resolution_is_as_of():
    """A reflagged vessel is the same entity with a different claim."""
    entity = Entity(
        ref=EntityRef("vessel", "v1"),
        identifiers=(
            EntityIdentifier("mmsi", "244000000", valid_to=T0),
            EntityIdentifier("mmsi", "636000000", valid_from=LATER),
        ),
    )
    assert entity.identifier_at(T0, "mmsi").value == "244000000"
    assert entity.identifier_at(LATER, "mmsi").value == "636000000"


def test_most_trusted_identifier_wins():
    entity = Entity(
        ref=EntityRef("vessel", "v1"),
        identifiers=(
            EntityIdentifier("mmsi", "inferred", confidence=0.4),
            EntityIdentifier("mmsi", "broadcast", confidence=1.0),
        ),
    )
    assert entity.identifier_at(T0, "mmsi").value == "broadcast"


def test_conflicting_identity_is_detectable_but_not_a_verdict():
    """A fact about the record. Whether it is a spoof is an indicator's job."""
    entity = Entity(
        ref=EntityRef("vessel", "v1"),
        identifiers=(EntityIdentifier("mmsi", "a"),
                     EntityIdentifier("mmsi", "b")),
    )
    assert entity.has_conflicting_identity(T0, "mmsi")
    assert not entity.has_conflicting_identity(T0, "imo")


def test_entity_seen_dates_must_be_ordered():
    with pytest.raises(ValueError, match="last seen before"):
        Entity(ref=EntityRef("vessel", "v"), first_seen=LATER, last_seen=T0)


# =========================================================================
# indicator — pre-registration enforced
# =========================================================================
def _indicator(**overrides) -> Indicator:
    kwargs = dict(
        key="tempo", region_key="euro_atlantic", name="Strike tempo",
        question="Is the strike tempo above its reference level?",
        meaning="A sustained rise suggests a shift in operational posture.",
        test_type=IndicatorTest.LEVEL_DEVIATION,
    )
    kwargs.update(overrides)
    return Indicator(**kwargs)


def test_indicator_requires_a_question():
    with pytest.raises(ValueError, match="not testable"):
        _indicator(question="   ")


def test_indicator_requires_a_meaning():
    """Pre-registration is the point: say why it would matter, in advance."""
    with pytest.raises(ValueError, match="not worth raising"):
        _indicator(meaning="")


def test_sustained_divergence_requires_a_declared_reference():
    with pytest.raises(ValueError, match="declared reference period"):
        _indicator(test_type=IndicatorTest.SUSTAINED_DIVERGENCE)
    assert _indicator(test_type=IndicatorTest.SUSTAINED_DIVERGENCE,
                      reference_period=PERIOD).reference_period == PERIOD


def test_entity_behaviour_requires_an_entity_kind():
    with pytest.raises(ValueError, match="which kind of entity"):
        _indicator(test_type=IndicatorTest.ENTITY_BEHAVIOUR)
    assert _indicator(test_type=IndicatorTest.ENTITY_BEHAVIOUR,
                      entity_kind="vessel").entity_kind == "vessel"


def test_indicator_scope_reads_sensibly():
    indicator = _indicator(area_key="north_sea",
                           test_type=IndicatorTest.ENTITY_BEHAVIOUR,
                           entity_kind="vessel")
    assert indicator.scope == "euro_atlantic / north_sea / vessels"


def test_indicator_status_defaults_to_draft():
    assert _indicator().status is IndicatorStatus.DRAFT
    assert _indicator(status=IndicatorStatus.ACTIVE).is_active


# =========================================================================
# baseline
# =========================================================================
def test_fixed_reference_baseline_must_declare_its_period():
    with pytest.raises(ValueError, match="cannot be reviewed"):
        Baseline(kind=BaselineKind.FIXED_REFERENCE, as_of=T0)
    baseline = Baseline(kind=BaselineKind.FIXED_REFERENCE, as_of=T0,
                        reference_period=PERIOD)
    assert baseline.is_declared


def test_adaptive_baseline_needs_no_period():
    assert not Baseline(kind=BaselineKind.ADAPTIVE, as_of=T0).is_declared


def test_coverage_must_be_a_fraction():
    with pytest.raises(ValueError, match="fraction"):
        Baseline(kind=BaselineKind.ADAPTIVE, as_of=T0, coverage_oos=1.5)


# =========================================================================
# detection power
# =========================================================================
def test_confounded_power_cannot_also_report_a_floor():
    """The exact error the chance-calibration work uncovered."""
    with pytest.raises(ValueError, match="cannot also report a floor"):
        DetectionPower(scenario_kind="x", floor_magnitude=1.25,
                       confounded=True)


def test_confounded_power_is_not_quotable():
    power = DetectionPower(scenario_kind="x", floor_magnitude=float("nan"),
                           confounded=True)
    assert not power.is_quotable
    assert "not measurable" in power.describe()


def test_unmeasured_floor_is_not_quotable():
    power = DetectionPower(scenario_kind="x", floor_magnitude=float("nan"))
    assert not power.is_quotable
    assert "little weight" in power.describe()


def test_real_floor_is_quotable():
    power = _power(1.5)
    assert power.is_quotable
    assert "1.5x or larger" in power.describe()


# =========================================================================
# confidence
# =========================================================================
def test_confidence_without_reasons_is_rejected():
    with pytest.raises(ValueError, match="cannot be reviewed"):
        Confidence(level=ConfidenceLevel.HIGH, reasons=())


def test_confidence_inputs_reject_non_fractions():
    with pytest.raises(ValueError, match="fraction"):
        ConfidenceInputs(data_coverage=1.4)


def test_confidence_inputs_count_what_was_supplied():
    assert ConfidenceInputs().n_supplied == 0
    assert ConfidenceInputs(data_coverage=0.9,
                            staleness_days=2).n_supplied == 2


def test_series_length_is_not_a_confidence_input():
    """v1's confidence was effectively a length check. It must not return."""
    fields = set(vars(ConfidenceInputs()))
    for banned in ("n_periods", "series_length", "n_rows"):
        assert banned not in fields


# =========================================================================
# evidence
# =========================================================================
def test_alternative_evidence_cannot_support_the_reading():
    with pytest.raises(ValueError, match="does not support it"):
        Evidence(kind=EvidenceKind.ALTERNATIVE,
                 summary="reporting change", weight=0.5)


def test_evidence_weight_is_bounded():
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        Evidence(kind=EvidenceKind.CONTEXT, summary="x", weight=2.0)


def test_evidence_must_say_something():
    with pytest.raises(ValueError, match="must say something"):
        Evidence(kind=EvidenceKind.CONTEXT, summary="  ")


# =========================================================================
# signal — the structural enforcement of the null result
# =========================================================================
def test_null_result_requires_detection_power():
    """Non-negotiable #2, enforced at construction."""
    with pytest.raises(ValueError, match="detection power"):
        Signal(indicator_key="tempo", as_of=T0, verdict=Verdict.NOT_ACTIVE,
               confidence=_confidence())


def test_null_result_with_power_is_constructible_and_speaks():
    signal = Signal(indicator_key="tempo", as_of=T0,
                    verdict=Verdict.NOT_ACTIVE, confidence=_confidence(),
                    detection_power=_power(1.5))
    assert signal.is_null_result
    text = signal.null_statement()
    assert "No significant deviation" in text
    assert "1.5x or larger" in text


def test_active_signal_requires_effect_size_and_direction():
    with pytest.raises(ValueError, match="not actionable"):
        Signal(indicator_key="tempo", as_of=T0, verdict=Verdict.ACTIVE,
               confidence=_confidence())
    with pytest.raises(ValueError, match="not actionable"):
        Signal(indicator_key="tempo", as_of=T0, verdict=Verdict.ACTIVE,
               confidence=_confidence(), effect_size=2.0)


def test_insufficient_data_must_say_why():
    """Otherwise it is indistinguishable from a quiet result."""
    with pytest.raises(ValueError, match="does not say why"):
        Signal(indicator_key="tempo", as_of=T0,
               verdict=Verdict.INSUFFICIENT_DATA, confidence=_confidence())
    ok = Signal(indicator_key="tempo", as_of=T0,
                verdict=Verdict.INSUFFICIENT_DATA, confidence=_confidence(),
                insufficient_reason="feed stale for 40 days")
    assert not ok.is_null_result


def test_null_statement_refuses_non_null_signals():
    active = Signal(indicator_key="t", as_of=T0, verdict=Verdict.ACTIVE,
                    confidence=_confidence(), effect_size=2.0,
                    direction=Direction.ABOVE)
    with pytest.raises(ValueError, match="not a null result"):
        active.null_statement()


def test_evidence_does_not_change_the_verdict():
    """The core of retiring the voting model.

    Overwhelmingly negative evidence must leave an active verdict active.
    Evidence informs confidence and offers alternatives; it does not vote.
    """
    signal = Signal(
        indicator_key="tempo", as_of=T0, verdict=Verdict.ACTIVE,
        confidence=_confidence(), effect_size=3.0, direction=Direction.ABOVE,
        evidence=(
            Evidence(EvidenceKind.ALTERNATIVE, "new source came online", -1.0),
            Evidence(EvidenceKind.ALTERNATIVE, "holiday reporting spike", -1.0),
        ),
    )
    assert signal.verdict is Verdict.ACTIVE
    assert signal.net_evidence_weight == -2.0
    assert len(signal.alternatives) == 2


# =========================================================================
# assessment — analyst trust enforced
# =========================================================================
def _active_signal() -> Signal:
    return Signal(indicator_key="tempo", as_of=T0, verdict=Verdict.ACTIVE,
                  confidence=_confidence(), effect_size=2.4,
                  direction=Direction.ABOVE)


def test_active_assessment_requires_an_alternative_explanation():
    with pytest.raises(ValueError, match="alternative explanation"):
        Assessment(signal=_active_signal(), statement="Tempo rose",
                   baseline_description="2015-2019 mean",
                   follow_up=("task collection",))


def test_active_assessment_requires_a_follow_up():
    with pytest.raises(ValueError, match="follow-up"):
        Assessment(signal=_active_signal(), statement="Tempo rose",
                   baseline_description="2015-2019 mean",
                   alternatives=("reporting change",))


def test_assessment_requires_a_baseline_description():
    """A deviation without a stated baseline cannot be checked."""
    with pytest.raises(ValueError, match="compared against"):
        Assessment(signal=_active_signal(), statement="Tempo rose",
                   baseline_description=" ",
                   alternatives=("x",), follow_up=("y",))


def test_null_assessment_needs_no_alternatives():
    """Only active readings must be argued against."""
    null = Signal(indicator_key="tempo", as_of=T0,
                  verdict=Verdict.NOT_ACTIVE, confidence=_confidence(),
                  detection_power=_power())
    assessment = Assessment(signal=null, statement="No change observed",
                            baseline_description="2015-2019 mean")
    assert assessment.likelihood is None


def test_probability_maps_to_the_estimative_band():
    assessment = Assessment(
        signal=_active_signal(), statement="Tempo rose",
        baseline_description="2015-2019 mean", probability=0.95,
        alternatives=("reporting change",), follow_up=("task collection",))
    assert assessment.likelihood is LikelihoodBand.VERY_LIKELY


def test_format_keeps_likelihood_and_confidence_in_separate_sentences():
    """ICD 203: the reader must be able to tell which thing is uncertain."""
    assessment = Assessment(
        signal=_active_signal(), statement="Strike tempo rose sharply",
        baseline_description="2015-2019 mean", probability=0.93,
        alternatives=("a new reporting source came online",),
        follow_up=("confirm against a second source",))
    lines = assessment.format().split("\n")

    likelihood_lines = [ln for ln in lines if "very likely" in ln]
    confidence_lines = [ln for ln in lines if "Confidence is" in ln]
    assert len(likelihood_lines) == 1
    assert len(confidence_lines) == 1
    assert likelihood_lines[0] != confidence_lines[0]
    assert "Alternative explanations" in assessment.format()
    assert "Recommended follow-up" in assessment.format()


@pytest.mark.parametrize("probability,expected", [
    (0.02, LikelihoodBand.VERY_UNLIKELY),
    (0.25, LikelihoodBand.UNLIKELY),
    (0.50, LikelihoodBand.ROUGHLY_EVEN),
    (0.75, LikelihoodBand.LIKELY),
    (0.97, LikelihoodBand.VERY_LIKELY),
])
def test_likelihood_bands_follow_the_standard_ranges(probability, expected):
    assert LikelihoodBand.from_probability(probability) is expected


# =========================================================================
# region status — the honest empty tab
# =========================================================================
def test_unmonitored_region_must_declare_activation_requirements():
    with pytest.raises(ValueError, match="reads as 'quiet'"):
        RegionStatus(region_key="mena", name="MENA",
                     monitoring=MonitoringStatus.NOT_MONITORED)


def test_unmonitored_region_cannot_carry_signals():
    with pytest.raises(ValueError, match="not monitored but"):
        RegionStatus(
            region_key="mena", name="MENA",
            monitoring=MonitoringStatus.NOT_MONITORED,
            activation_requirements=("ACLED licence",),
            signals=(Signal("x", T0, Verdict.NOT_ACTIVE, _confidence(),
                            detection_power=_power()),),
        )


def test_unmonitored_region_never_reads_as_quiet():
    """The product risk this type exists to prevent."""
    region = RegionStatus(region_key="mena", name="MENA",
                          monitoring=MonitoringStatus.NOT_MONITORED,
                          activation_requirements=("an ACLED licence",))
    headline = region.headline()
    assert "not monitored" in headline
    assert "an ACLED licence" in headline
    assert not region.is_quiet


def test_data_only_region_says_nothing_is_being_tested():
    region = RegionStatus(region_key="caribbean", name="Caribbean",
                          monitoring=MonitoringStatus.DATA_ONLY)
    assert "no indicators defined" in region.headline()
    assert not region.is_quiet


def test_monitored_and_quiet_region_says_so_with_counts():
    signals = tuple(
        Signal(f"ind{i}", T0, Verdict.NOT_ACTIVE, _confidence(),
               detection_power=_power())
        for i in range(3)
    )
    region = RegionStatus(region_key="euro_atlantic", name="Euro-Atlantic",
                          monitoring=MonitoringStatus.MONITORED,
                          signals=signals)
    assert region.is_quiet
    assert "3 indicators tested and quiet" in region.headline()


def test_untested_indicators_are_reported_separately_from_quiet_ones():
    signals = (
        Signal("a", T0, Verdict.NOT_ACTIVE, _confidence(),
               detection_power=_power()),
        Signal("b", T0, Verdict.INSUFFICIENT_DATA, _confidence(),
               insufficient_reason="feed down"),
    )
    region = RegionStatus(region_key="r", name="R",
                          monitoring=MonitoringStatus.MONITORED,
                          signals=signals)
    assert "1 indicators tested and quiet" in region.headline()
    assert "1 could not be tested" in region.headline()


def test_region_where_nothing_could_be_tested_is_not_quiet():
    """The "we could not look" / "nothing is happening" confusion, one level up.

    Absence of active signals is not evidence of calm when no indicator
    produced a verdict at all.
    """
    signals = tuple(
        Signal(f"i{i}", T0, Verdict.INSUFFICIENT_DATA, _confidence(),
               insufficient_reason="feed down")
        for i in range(3)
    )
    region = RegionStatus(region_key="r", name="R",
                          monitoring=MonitoringStatus.MONITORED,
                          signals=signals)
    assert not region.is_quiet
    assert region.tested == ()
    headline = region.headline()
    assert "nothing could be tested" in headline
    assert "not a quiet result" in headline
    assert "no significant activity" not in headline


def test_active_region_leads_with_what_is_active():
    signals = (
        Signal("tempo", T0, Verdict.ACTIVE, _confidence(), effect_size=2.0,
               direction=Direction.ABOVE),
        Signal("quiet", T0, Verdict.NOT_ACTIVE, _confidence(),
               detection_power=_power()),
    )
    region = RegionStatus(region_key="r", name="R",
                          monitoring=MonitoringStatus.MONITORED,
                          signals=signals)
    assert not region.is_quiet
    assert "1 of 2 indicators active" in region.headline()
    assert "tempo" in region.headline()


def test_monitored_region_with_no_signals_is_not_called_quiet():
    """Monitored but nothing ran is a third thing again."""
    region = RegionStatus(region_key="r", name="R",
                          monitoring=MonitoringStatus.MONITORED)
    assert not region.is_quiet
    assert "no indicator produced a verdict" in region.headline()


# =========================================================================
# immutability
# =========================================================================
@pytest.mark.parametrize("obj", [
    GeoPoint(52.0, 4.0),
    EntityRef("vessel", "v1"),
    Lineage(source_keys=("a",)),
    _confidence(),
    _power(),
])
def test_contract_objects_are_frozen(obj):
    """Judgements must not be mutable after construction: a value that can
    change after validation has not really been validated."""
    field_name = next(iter(vars(obj)))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, None)
