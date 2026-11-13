"""Intensidade mecânica e resposta fisiológica provisória (ADR-034).

Cobrem a separação entre física da rota, intensidade mecânica relativa,
referência de potência, fadiga CP/W', RPE, FC e zonas. A estimativa continua
não calibrada e nunca preenche o contrato fisiológico.
"""

from __future__ import annotations

import json
import math

import pytest

from router.core.physics import CyclistModel
from router.core.physics.models import (
    PhysicsRouteMetrics,
    TraversalPath,
    TraversalResult,
)
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    ActivityLevel,
    BorgScale,
    CapacityProfile,
    HeartRateMaxSource,
    ParameterSource,
    PowerReference,
    ReferencePowerKind,
    RiderModel,
    RiderProfile,
    estimate_reference_power,
    tanaka_max_heart_rate,
)
from router.core.prediction.intensity import (
    INTENSITY_BAND_EDGES,
    INTENSITY_BAND_LABELS,
    INTENSITY_BAND_NAMES,
    IntensityEstimate,
    band_for_rpe,
    estimate_intensity,
    hw_stress,
    segment_rpe,
    segments_from_traversal,
)
from router.core.prediction.mechanical import (
    DEFAULT_PROPULSION,
    PropulsionMode,
    PropulsionModel,
    RouteSegment,
    estimate_mechanical_intensity,
)
from router.core.prediction.response import (
    FatigueModel,
    ResponseConfig,
    estimate_rider_response,
)
from router.core.prediction.rider import rider_model_from_cyclist


def _rider(
    *,
    mass_kg: float = 75.0,
    w_per_kg: float = 2.0,
    age: float | None = 40.0,
    resting: float | None = None,
    ftp_w: float | None = None,
    critical_power_w: float | None = None,
    w_prime_j: float | None = None,
) -> RiderModel:
    if ftp_w is not None:
        reference = PowerReference.from_absolute(
            ftp_w,
            rider_mass_kg=mass_kg,
            kind=ReferencePowerKind.FTP,
            source=ParameterSource.MEASURED,
        )
    else:
        reference = PowerReference.from_specific(
            w_per_kg,
            rider_mass_kg=mass_kg,
            kind=ReferencePowerKind.CAPACITY_PROFILE,
            source=ParameterSource.PROFILE_ESTIMATED,
        )
    if age is None:
        heart_rate = {}
    elif resting is None:
        heart_rate = {
            "max_heart_rate_bpm": tanaka_max_heart_rate(age),
            "max_source": HeartRateMaxSource.TANAKA_POPULATION,
        }
    else:
        heart_rate = {
            "max_heart_rate_bpm": tanaka_max_heart_rate(age),
            "resting_heart_rate_bpm": resting,
            "max_source": HeartRateMaxSource.TANAKA_POPULATION,
        }
    from router.core.physiology import HeartRateParameters

    return RiderModel(
        rider_mass_kg=mass_kg,
        reference=reference,
        age_years=age,
        heart_rate=HeartRateParameters(**heart_rate),
        critical_power_w=critical_power_w,
        w_prime_j=w_prime_j,
    )


def _segment(
    *,
    distance_m: float = 1000.0,
    duration_s: float = 200.0,
    rider_energy_j: float = 40_000.0,
    mechanical_energy_j: float = 36_000.0,
    ascent_m: float = 0.0,
    descent_m: float = 0.0,
    traffic_stress: float = 0.0,
) -> RouteSegment:
    return RouteSegment(
        distance_m=distance_m,
        duration_s=duration_s,
        rider_energy_j=rider_energy_j,
        mechanical_energy_j=mechanical_energy_j,
        ascent_m=ascent_m,
        descent_m=descent_m,
        traffic_stress=traffic_stress,
    )


# ==========================================================================
# Perfis, atividade e referência de potência
# ==========================================================================
def test_six_capacity_profiles_have_declared_w_per_kg():
    assert CapacityProfile.SEDENTARY.reference_w_per_kg == 1.0
    assert CapacityProfile.BEGINNER.reference_w_per_kg == 1.5
    assert CapacityProfile.RECREATIONAL.reference_w_per_kg == 2.0
    assert CapacityProfile.TRAINED.reference_w_per_kg == 3.0
    assert CapacityProfile.COMPETITIVE.reference_w_per_kg == 4.2
    assert CapacityProfile.ELITE.reference_w_per_kg == 5.5


def test_six_activity_levels_exist_and_document_weekly_ranges():
    levels = list(ActivityLevel)
    assert len(levels) == 6
    for level in levels:
        low, high = level.weekly_active_minutes
        assert 0 <= low < high


def test_reference_power_uses_rider_mass_times_specific_capacity():
    reference = estimate_reference_power(
        80.0, capacity_profile=CapacityProfile.TRAINED
    )
    assert reference.power_w == pytest.approx(80.0 * 3.0)
    assert reference.w_per_kg == pytest.approx(3.0)
    assert reference.source is ParameterSource.PROFILE_ESTIMATED
    assert reference.kind is ReferencePowerKind.CAPACITY_PROFILE


def test_measured_ftp_has_priority_and_is_not_cp():
    reference = estimate_reference_power(
        80.0,
        ftp_w=250.0,
        critical_power_w=230.0,
        capacity_profile=CapacityProfile.ELITE,
    )
    assert reference.power_w == pytest.approx(250.0)
    assert reference.kind is ReferencePowerKind.FTP
    assert reference.source is ParameterSource.MEASURED


def test_measured_critical_power_is_distinct_from_ftp():
    reference = estimate_reference_power(80.0, critical_power_w=230.0)
    assert reference.kind is ReferencePowerKind.CRITICAL_POWER
    assert reference.source is ParameterSource.MEASURED
    assert reference.power_w == pytest.approx(230.0)


def test_calibrated_and_provisional_sources_have_explicit_provenance():
    calibrated = estimate_reference_power(
        80.0, calibrated_reference_power_w=240.0
    )
    assert calibrated.source is ParameterSource.CALIBRATED_MODEL_ESTIMATED
    provisional = estimate_reference_power(80.0, provisional_power_w=160.0)
    assert provisional.kind is ReferencePowerKind.SUSTAINABLE_POWER
    assert provisional.source is ParameterSource.PROVISIONAL_DEFAULT


def test_zero_provisional_power_is_unavailable_not_invalid():
    """Fisica sem potencia sustentavel ainda produz referencia provisoria."""
    reference = estimate_reference_power(80.0, provisional_power_w=0.0)
    assert reference.source is ParameterSource.PROVISIONAL_DEFAULT
    assert reference.kind is ReferencePowerKind.CAPACITY_PROFILE
    with pytest.raises(ValueError):
        estimate_reference_power(80.0, provisional_power_w=-1.0)


def test_rider_model_factory_falls_back_when_sustainable_power_is_zero():
    cyclist = CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=0.0,
        frontal_area_m2=0.5,
    )
    rider = rider_model_from_cyclist(cyclist)
    assert rider.reference_power_w > 0.0
    assert rider.reference.source is ParameterSource.PROVISIONAL_DEFAULT
    estimate = estimate_intensity((_segment(),), rider=rider)
    assert estimate.mean_rpe >= DEFAULT_BORG_SCALE.min_score


def test_rider_model_factory_builds_reference_and_heart_rate():
    cyclist = CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )
    profile = RiderProfile(
        age_years=40.0,
        resting_heart_rate_bpm=60.0,
        ftp_w=280.0,
        critical_power_w=250.0,
        w_prime_j=20_000.0,
    )
    rider = rider_model_from_cyclist(cyclist, profile)
    assert rider.reference_power_w == pytest.approx(280.0)
    assert rider.reference.source is ParameterSource.MEASURED
    assert rider.heart_rate_max_bpm == pytest.approx(180.0)
    assert rider.heart_rate.heart_rate_reserve_bpm == pytest.approx(120.0)
    assert rider.heart_rate.max_source is HeartRateMaxSource.TANAKA_POPULATION
    assert rider.has_critical_power_model is True


def test_tanaka_equation_is_population_estimate():
    assert tanaka_max_heart_rate(40.0) == pytest.approx(180.0)
    assert tanaka_max_heart_rate(20.0) == pytest.approx(194.0)
    with pytest.raises(ValueError):
        tanaka_max_heart_rate(4.0)


def test_heart_rate_reserve_requires_both_max_and_resting():
    from router.core.physiology import HeartRateParameters

    only_max = HeartRateParameters(
        max_heart_rate_bpm=180.0,
        max_source=HeartRateMaxSource.TANAKA_POPULATION,
    )
    assert only_max.heart_rate_reserve_bpm is None
    both = HeartRateParameters(
        max_heart_rate_bpm=180.0,
        resting_heart_rate_bpm=60.0,
        max_source=HeartRateMaxSource.TANAKA_POPULATION,
    )
    assert both.heart_rate_reserve_bpm == pytest.approx(120.0)


# ==========================================================================
# Intensidade mecânica: massa, inclinação e tráfego
# ==========================================================================
def test_mechanical_intensity_is_relative_power_and_has_no_system_mass():
    rider = _rider(mass_kg=75.0, w_per_kg=2.0)
    segment = _segment(rider_energy_j=30_000.0, duration_s=200.0)
    mechanical = estimate_mechanical_intensity((segment,), rider=rider)
    assert mechanical.mean_power_w == pytest.approx(150.0)
    assert mechanical.relative_intensity == pytest.approx(1.0)
    assert mechanical.specific_power_w_per_kg == pytest.approx(2.0)


def test_same_relative_intensity_for_different_rider_masses():
    light = _rider(mass_kg=50.0, w_per_kg=2.0)
    heavy = _rider(mass_kg=90.0, w_per_kg=2.0)
    light_segment = _segment(rider_energy_j=70.0 * 100.0, duration_s=100.0)
    heavy_segment = _segment(rider_energy_j=126.0 * 100.0, duration_s=100.0)
    light_mech = estimate_mechanical_intensity((light_segment,), rider=light)
    heavy_mech = estimate_mechanical_intensity((heavy_segment,), rider=heavy)
    assert light_mech.relative_intensity == pytest.approx(0.7)
    assert heavy_mech.relative_intensity == pytest.approx(0.7)


def test_grade_is_not_double_counted_in_mechanical_intensity_or_rpe():
    rider = _rider()
    flat = _segment(ascent_m=0.0)
    steep = _segment(ascent_m=100.0)
    flat_mech = estimate_mechanical_intensity((flat,), rider=rider)
    steep_mech = estimate_mechanical_intensity((steep,), rider=rider)
    assert flat_mech.relative_intensity == pytest.approx(
        steep_mech.relative_intensity
    )
    assert segment_rpe(flat, rider=rider) == pytest.approx(
        segment_rpe(steep, rider=rider)
    )


def test_traffic_is_isolated_from_mechanical_intensity_and_rpe():
    rider = _rider()
    quiet = _segment(traffic_stress=0.0)
    busy = _segment(traffic_stress=1.2)
    quiet_estimate = estimate_intensity((quiet,), rider=rider)
    busy_estimate = estimate_intensity((busy,), rider=rider)
    assert quiet_estimate.relative_intensity == pytest.approx(
        busy_estimate.relative_intensity
    )
    assert quiet_estimate.mean_rpe == pytest.approx(busy_estimate.mean_rpe)
    assert quiet_estimate.traffic_stress_mean == pytest.approx(0.0)
    assert busy_estimate.traffic_stress_mean == pytest.approx(1.2)


def test_segmentation_does_not_change_aggregate_intensity_without_cp():
    rider = _rider(ftp_w=300.0)
    whole = _segment(
        distance_m=3000.0,
        duration_s=600.0,
        rider_energy_j=180_000.0,
        mechanical_energy_j=165_000.0,
    )
    parts = tuple(
        _segment(
            distance_m=1000.0,
            duration_s=200.0,
            rider_energy_j=60_000.0,
            mechanical_energy_j=55_000.0,
        )
        for _ in range(3)
    )
    whole_estimate = estimate_intensity((whole,), rider=rider)
    parts_estimate = estimate_intensity(parts, rider=rider)
    assert whole_estimate.relative_intensity == pytest.approx(
        parts_estimate.relative_intensity
    )
    assert whole_estimate.mean_rpe == pytest.approx(parts_estimate.mean_rpe)
    assert whole_estimate.peak_rpe == pytest.approx(parts_estimate.peak_rpe)


def test_rpe_is_monotone_with_relative_intensity():
    rider = _rider(ftp_w=200.0)
    easy = segment_rpe(_segment(rider_energy_j=20_000.0), rider=rider)
    hard = segment_rpe(_segment(rider_energy_j=50_000.0), rider=rider)
    assert hard > easy


# ==========================================================================
# Fadiga CP/W'
# ==========================================================================
def _cp_rider() -> RiderModel:
    return _rider(ftp_w=300.0, critical_power_w=250.0, w_prime_j=20_000.0)


def test_cp_w_prime_model_depletes_and_recovers():
    rider = _cp_rider()
    hard = _segment(rider_energy_j=400.0 * 60.0, duration_s=60.0)
    recovery = _segment(rider_energy_j=100.0 * 120.0, duration_s=120.0)
    hard_only = tuple(hard for _ in range(4))
    intermittent = (hard, recovery, hard, recovery)
    hard_estimate = estimate_intensity(hard_only, rider=rider)
    intermittent_estimate = estimate_intensity(intermittent, rider=rider)
    assert hard_estimate.rider_response.fatigue_model is FatigueModel.CP_W_PRIME
    assert intermittent_estimate.rider_response.fatigue_fraction_end < (
        hard_estimate.rider_response.fatigue_fraction_end
    )
    assert intermittent_estimate.mean_rpe < hard_estimate.mean_rpe


def test_without_cp_and_w_prime_fatigue_is_disabled():
    rider = _rider(ftp_w=300.0)
    estimate = estimate_intensity((_segment(),), rider=rider)
    assert estimate.rider_response.fatigue_model is FatigueModel.NONE
    assert estimate.rider_response.fatigue_fraction_end == 0.0


# ==========================================================================
# FC, zonas e pico da rota
# ==========================================================================
def test_absolute_heart_rate_requires_max_and_resting():
    no_rest = _rider(age=40.0, resting=None)
    estimate = estimate_intensity((_segment(),), rider=no_rest)
    response = estimate.rider_response
    assert response.segment_heart_rate_bpm == (None,)
    assert response.mean_heart_rate_bpm is None
    assert response.heart_rate_zone_distribution is not None
    assert response.heart_rate_zone_definition == "hr-zones-hrmax@1"

    with_rest = _rider(age=40.0, resting=60.0)
    estimate = estimate_intensity((_segment(),), rider=with_rest)
    response = estimate.rider_response
    assert response.segment_heart_rate_bpm[0] is not None
    assert response.mean_heart_rate_bpm is not None
    assert response.peak_heart_rate_bpm is not None
    assert response.heart_rate_reserve_bpm == pytest.approx(120.0)
    assert response.heart_rate_zone_definition_karvonen == "hr-zones-karvonen@1"


def test_physiological_max_heart_rate_is_not_route_peak():
    rider = _rider(age=40.0, resting=60.0)
    estimate = estimate_intensity(
        (_segment(rider_energy_j=20_000.0), _segment(rider_energy_j=80_000.0)),
        rider=rider,
    )
    response = estimate.rider_response
    assert response.heart_rate_max_bpm == pytest.approx(180.0)
    assert response.peak_heart_rate_bpm is not None
    assert response.peak_heart_rate_bpm <= response.heart_rate_max_bpm
    assert response.peak_heart_rate_bpm >= response.mean_heart_rate_bpm


def test_no_age_and_no_measured_max_means_no_hr_and_no_zones():
    rider = _rider(age=None)
    estimate = estimate_intensity((_segment(),), rider=rider)
    response = estimate.rider_response
    assert response.heart_rate_max_bpm is None
    assert response.segment_heart_rate_bpm == (None,)
    assert response.heart_rate_zone_distribution is None


# ==========================================================================
# Propulsão
# ==========================================================================
def test_default_propulsion_has_no_assist():
    rider = _rider()
    segment = _segment(rider_energy_j=40_000.0, mechanical_energy_j=36_000.0)
    mechanical = estimate_mechanical_intensity(
        (segment,), rider=rider, propulsion=DEFAULT_PROPULSION
    )
    assert mechanical.physics.motor_wheel_work_j == 0.0
    assert mechanical.physics.rider_crank_work_j == pytest.approx(40_000.0)


def test_assist_fraction_reduces_rider_crank_work():
    rider = _rider()
    segment = _segment(rider_energy_j=40_000.0, mechanical_energy_j=36_000.0)
    assist = PropulsionModel(
        mode=PropulsionMode.ASSIST_FRACTION, assist_fraction=0.25
    )
    mechanical = estimate_mechanical_intensity(
        (segment,), rider=rider, propulsion=assist
    )
    assert mechanical.physics.motor_wheel_work_j == pytest.approx(9_000.0)
    assert mechanical.physics.rider_crank_work_j == pytest.approx(30_000.0)


def test_assist_target_respects_motor_cap():
    rider = _rider()
    segment = _segment(
        rider_energy_j=40_000.0,
        mechanical_energy_j=36_000.0,
        duration_s=100.0,
    )
    assist = PropulsionModel(
        mode=PropulsionMode.ASSIST_TARGET_RIDER_POWER,
        target_rider_power_w=150.0,
        max_motor_power_w=250.0,
    )
    mechanical = estimate_mechanical_intensity(
        (segment,), rider=rider, propulsion=assist
    )
    assert mechanical.physics.motor_wheel_work_j <= 250.0 * 100.0
    assert mechanical.mean_power_w <= 150.0 + 1e-6


# ==========================================================================
# Rota, faixas, serialização e ponte com a física
# ==========================================================================
def test_estimate_distributions_and_borg_scale():
    rider = _rider()
    estimate = estimate_intensity(
        (
            _segment(rider_energy_j=10_000.0, distance_m=500.0),
            _segment(rider_energy_j=80_000.0, distance_m=500.0, ascent_m=40.0),
        ),
        rider=rider,
    )
    assert isinstance(estimate, IntensityEstimate)
    assert sum(estimate.band_distance_fraction.values()) == pytest.approx(1.0)
    assert estimate.band_duration_fraction is not None
    assert sum(estimate.band_duration_fraction.values()) == pytest.approx(1.0)
    assert DEFAULT_BORG_SCALE.min_score <= estimate.mean_rpe <= (
        DEFAULT_BORG_SCALE.max_score
    )
    assert estimate.peak_rpe >= estimate.mean_rpe
    assert estimate.distance_m == pytest.approx(1000.0)
    assert estimate.duration_s == pytest.approx(400.0)


def test_estimate_is_not_measurement_and_not_calibrated():
    estimate = estimate_intensity((_segment(),), rider=_rider())
    assert estimate.is_measurement is False
    assert estimate.calibrated is False
    assert estimate.to_dict()["calibrated"] is False
    assert estimate.to_dict()["is_measurement"] is False


def test_estimate_without_segments_is_floor_and_empty():
    estimate = estimate_intensity((), rider=_rider())
    assert estimate.mean_rpe == pytest.approx(DEFAULT_BORG_SCALE.min_score)
    assert estimate.band_distance_fraction == {}
    assert estimate.band_duration_fraction is None
    assert estimate.distance_m == 0.0
    assert estimate.segment_rpes == ()


def test_estimate_duration_fraction_none_when_no_time():
    segment = _segment(duration_s=0.0, rider_energy_j=0.0)
    estimate = estimate_intensity((segment,), rider=_rider())
    assert estimate.band_duration_fraction is None


def test_estimate_to_dict_is_json_serializable_and_additive():
    estimate = estimate_intensity((_segment(),), rider=_rider(age=40.0, resting=60.0))
    encoded = json.dumps(estimate.to_dict(), ensure_ascii=False)
    assert '"calibrated": false' in encoded
    assert '"is_measurement": false' in encoded
    assert '"mechanical"' in encoded
    assert '"rider_response"' in encoded
    assert '"mean_rpe_time_weighted"' in encoded


def test_estimate_to_feature_row_has_rpe_and_hr_features():
    estimate = estimate_intensity(
        (_segment(),), rider=_rider(age=40.0, resting=60.0)
    )
    row = estimate.to_feature_row()
    assert "rpe_mean" in row
    assert "estimated_hr_mean_bpm" in row
    assert "mechanical_relative_intensity" in row


def test_estimate_uses_cr10_scale_bounds():
    estimate = estimate_intensity(
        (_segment(),), rider=_rider(), borg_scale=BorgScale.CR10
    )
    assert BorgScale.CR10.min_score <= estimate.mean_rpe <= (
        BorgScale.CR10.max_score
    )


def test_bands_cover_borg_range():
    assert len(INTENSITY_BAND_NAMES) == 5
    assert INTENSITY_BAND_EDGES == (9.0, 12.0, 14.0, 16.0)
    assert band_for_rpe(6.0) == "i1"
    assert band_for_rpe(9.0) == "i1"
    assert band_for_rpe(9.1) == "i2"
    assert band_for_rpe(10.1) == "i2"
    assert band_for_rpe(12.0) == "i2"
    assert band_for_rpe(12.1) == "i3"
    assert band_for_rpe(14.0) == "i3"
    assert band_for_rpe(14.1) == "i4"
    assert band_for_rpe(16.0) == "i4"
    assert band_for_rpe(16.1) == "i5"
    assert band_for_rpe(20.0) == "i5"


def test_band_labels_cover_every_band():
    assert set(INTENSITY_BAND_LABELS) == set(INTENSITY_BAND_NAMES)
    assert INTENSITY_BAND_LABELS["i1"] == "muito leve"
    assert INTENSITY_BAND_LABELS["i5"] == "muito intenso"


def test_hw_stress_mapping_and_default():
    assert hw_stress("cycleway") == pytest.approx(0.05)
    assert hw_stress("primary") == pytest.approx(0.95)
    assert hw_stress(None) == pytest.approx(0.4)
    assert hw_stress("unmapped-class") == pytest.approx(0.4)


class _StubArc:
    def __init__(self, *, highway: str | None) -> None:
        self.highway = highway


def _path(results: list[TraversalResult]) -> TraversalPath:
    return TraversalPath(
        results=tuple(results),
        metrics=PhysicsRouteMetrics.from_results(results),
        initial_speed_mps=0.0,
        final_speed_mps=0.0,
    )


def test_segments_from_traversal_carry_physical_work_and_traffic():
    results = [
        TraversalResult(
            time_s=20.0,
            distance_m=100.0,
            ascent_m=10.0,
            descent_m=0.0,
            initial_speed_mps=0.0,
            final_speed_mps=5.0,
            mechanical_energy_j=3000.0,
            rider_energy_j=3600.0,
            arc_id="a0",
        ),
        TraversalResult(
            time_s=20.0,
            distance_m=100.0,
            ascent_m=0.0,
            descent_m=0.0,
            initial_speed_mps=5.0,
            final_speed_mps=5.0,
            mechanical_energy_j=3000.0,
            rider_energy_j=3600.0,
            arc_id="a1",
        ),
    ]
    arcs = [
        _StubArc(highway="primary"),
        _StubArc(highway="cycleway"),
    ]
    segments = segments_from_traversal(_path(results), arcs)
    assert len(segments) == 2
    assert segments[0].ascent_m == pytest.approx(10.0)
    assert segments[0].mechanical_energy_j == pytest.approx(3000.0)
    assert segments[0].traffic_stress == pytest.approx(0.95)
    assert segments[1].traffic_stress == pytest.approx(0.05)


def test_segments_from_traversal_rejects_mismatched_lengths():
    results = [
        TraversalResult(
            time_s=1.0,
            distance_m=1.0,
            ascent_m=0.0,
            descent_m=0.0,
            initial_speed_mps=0.0,
            final_speed_mps=1.0,
            mechanical_energy_j=1.0,
            rider_energy_j=1.0,
            arc_id="a0",
        )
    ]
    with pytest.raises(ValueError):
        segments_from_traversal(_path(results), [])


def test_estimate_is_finite_for_zero_work_segment():
    segment = _segment(rider_energy_j=0.0, duration_s=0.0)
    estimate = estimate_intensity((segment,), rider=_rider())
    assert math.isfinite(estimate.mean_rpe)


def test_response_config_can_be_overridden():
    rider = _rider()
    default = segment_rpe(_segment(), rider=rider)
    strict = segment_rpe(
        _segment(),
        rider=rider,
        config=ResponseConfig(fatigue_rpe_gain=0.0),
    )
    assert math.isfinite(default)
    assert math.isfinite(strict)
