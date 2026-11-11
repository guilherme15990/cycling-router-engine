"""Estimativa de intensidade (Borg/faixas) — computacional e nao calibrada.

Cobrem ``intelligence.intensity`` (ADR-028): RPE deterministico (origem
`toporide`, auditado), cinco faixas de **intensidade** (nao zonas cardiacas),
distribuicao por **distancia** e por **duracao**, e a marcacao explicita de que a
estimativa **nao** e medição nem previsao fisiologica calibrada.
"""

from __future__ import annotations

import json
import math
import pytest
from router.core.physics.models import PhysicsRouteMetrics, TraversalPath, TraversalResult
from router.core.physiology import DEFAULT_BORG_SCALE
from router.core.prediction.intensity import (
    DEFAULT_INTENSITY_COEFFICIENTS,
    INTENSITY_BAND_DEFINITION,
    INTENSITY_BAND_EDGES,
    INTENSITY_BAND_NAMES,
    IntensityCoefficients,
    IntensityEstimate,
    IntensitySegment,
    band_for_rpe,
    estimate_intensity,
    hw_stress,
    segment_rpe,
)

MASS_KG = 90.0
FTP_W = 200.0


def _segment(
    *,
    distance_m: float = 100.0,
    duration_s: float = 20.0,
    rider_energy_j: float = 4000.0,
    grade_pct: float = 0.0,
    traffic_stress: float = 0.0,
) -> IntensitySegment:
    return IntensitySegment(
        distance_m=distance_m,
        duration_s=duration_s,
        rider_energy_j=rider_energy_j,
        grade_pct=grade_pct,
        traffic_stress=traffic_stress,
    )


# --------------------------------------------------------------------------
# Coeficientes / proveniencia
# --------------------------------------------------------------------------
def test_default_coefficients_document_toparide_origin():
    c = DEFAULT_INTENSITY_COEFFICIENTS
    assert c.base_rpe == 6.0
    assert c.scale_gain == 6.0
    assert c.ftp_reference_kg == 75.0
    assert c.grade_free_pct == 5.0
    assert c.grade_exponent == 1.3
    assert c.traffic_weight == 1.5
    assert c.fatigue_weight == 1.5
    assert c.fatigue_window_s == 1200.0


def test_hw_stress_mapping_and_default():
    assert hw_stress("cycleway") == pytest.approx(0.05)
    assert hw_stress("residential") == pytest.approx(0.25)
    assert hw_stress("primary") == pytest.approx(0.95)
    assert hw_stress("trunk") == pytest.approx(1.2)
    assert hw_stress(None) == pytest.approx(0.4)
    assert hw_stress("unmapped-class") == pytest.approx(0.4)


def test_band_edges_cover_borg_range():
    assert len(INTENSITY_BAND_NAMES) == 5
    assert len(INTENSITY_BAND_EDGES) == 4
    assert band_for_rpe(6.0) == "i1"
    assert band_for_rpe(10.0) == "i1"
    assert band_for_rpe(10.1) == "i2"
    assert band_for_rpe(12.0) == "i2"
    assert band_for_rpe(14.0) == "i3"
    assert band_for_rpe(16.0) == "i4"
    assert band_for_rpe(16.1) == "i5"
    assert band_for_rpe(20.0) == "i5"


# --------------------------------------------------------------------------
# RPE por trecho (deterministico, monotonico onde esperado)
# --------------------------------------------------------------------------
def test_segment_rpe_is_deterministic():
    segment = _segment()
    first = segment_rpe(segment, mass_kg=MASS_KG, ftp_w=FTP_W)
    second = segment_rpe(segment, mass_kg=MASS_KG, ftp_w=FTP_W)
    assert first == second


def test_segment_rpe_respects_borg_bounds():
    hard = _segment(rider_energy_j=10_000_000.0, grade_pct=40.0, traffic_stress=1.2)
    rpe = segment_rpe(hard, mass_kg=MASS_KG, ftp_w=FTP_W)
    assert rpe <= DEFAULT_BORG_SCALE.max_score
    assert rpe >= DEFAULT_BORG_SCALE.min_score


def test_segment_rpe_increases_with_intensity():
    easy = segment_rpe(_segment(rider_energy_j=1000.0), mass_kg=MASS_KG, ftp_w=FTP_W)
    hard = segment_rpe(_segment(rider_energy_j=20000.0), mass_kg=MASS_KG, ftp_w=FTP_W)
    assert hard > easy


def test_segment_rpe_increases_with_grade_and_traffic():
    flat = _segment(grade_pct=0.0, traffic_stress=0.0)
    steep = _segment(grade_pct=15.0, traffic_stress=0.0)
    busy = _segment(grade_pct=0.0, traffic_stress=1.2)
    flat_rpe = segment_rpe(flat, mass_kg=MASS_KG, ftp_w=FTP_W)
    steep_rpe = segment_rpe(steep, mass_kg=MASS_KG, ftp_w=FTP_W)
    busy_rpe = segment_rpe(busy, mass_kg=MASS_KG, ftp_w=FTP_W)
    assert steep_rpe >= flat_rpe
    assert busy_rpe >= flat_rpe


def test_segment_rpe_increases_with_fatigue():
    segment = _segment(rider_energy_j=5000.0)
    fresh = segment_rpe(segment, mass_kg=MASS_KG, ftp_w=FTP_W, accumulated_j=0.0)
    tired = segment_rpe(
        segment, mass_kg=MASS_KG, ftp_w=FTP_W, accumulated_j=500_000.0
    )
    assert tired >= fresh


def test_segment_rpe_rejects_non_positive_mass():
    with pytest.raises(ValueError):
        segment_rpe(_segment(), mass_kg=0.0, ftp_w=FTP_W)


def test_intensity_segment_rejects_negative_values():
    with pytest.raises(ValueError):
        _segment(distance_m=-1.0)


# --------------------------------------------------------------------------
# Estimativa de rota
# --------------------------------------------------------------------------
def test_estimate_distributions_sum_to_one():
    segments = (
        _segment(rider_energy_j=1000.0),
        _segment(rider_energy_j=20000.0, grade_pct=10.0),
        _segment(rider_energy_j=4000.0, traffic_stress=1.2),
    )
    estimate = estimate_intensity(segments, mass_kg=MASS_KG, ftp_w=FTP_W)

    assert isinstance(estimate, IntensityEstimate)
    assert sum(estimate.band_distance_fraction.values()) == pytest.approx(1.0)
    assert estimate.band_duration_fraction is not None
    assert sum(estimate.band_duration_fraction.values()) == pytest.approx(1.0)
    assert estimate.band_definition == INTENSITY_BAND_DEFINITION
    assert DEFAULT_BORG_SCALE.min_score <= estimate.mean_rpe <= DEFAULT_BORG_SCALE.max_score
    assert estimate.peak_rpe >= estimate.mean_rpe
    assert estimate.distance_m == pytest.approx(300.0)
    assert estimate.duration_s == pytest.approx(60.0)


def test_estimate_is_not_measurement_and_not_calibrated():
    estimate = estimate_intensity((_segment(),), mass_kg=MASS_KG, ftp_w=FTP_W)
    assert estimate.is_measurement is False
    assert estimate.calibrated is False
    assert estimate.source == "computational_intensity_estimate"


def test_estimate_without_segments_is_floor_and_empty():
    estimate = estimate_intensity((), mass_kg=MASS_KG, ftp_w=FTP_W)
    assert estimate.mean_rpe == pytest.approx(DEFAULT_BORG_SCALE.min_score)
    assert estimate.band_distance_fraction == {}
    assert estimate.band_duration_fraction is None
    assert estimate.distance_m == 0.0


def test_estimate_duration_fraction_none_when_no_time():
    segment = _segment(duration_s=0.0, rider_energy_j=0.0)
    estimate = estimate_intensity((segment,), mass_kg=MASS_KG, ftp_w=FTP_W)
    assert estimate.band_duration_fraction is None


def test_estimate_green_fraction_matches_low_bands():
    segments = (_segment(rider_energy_j=1000.0), _segment(rider_energy_j=20000.0))
    estimate = estimate_intensity(segments, mass_kg=MASS_KG, ftp_w=FTP_W)
    expected = estimate.band_distance_fraction.get(
        "i1", 0.0
    ) + estimate.band_distance_fraction.get("i2", 0.0)
    assert estimate.green_fraction == pytest.approx(expected)


def test_estimate_to_dict_is_json_serializable():
    estimate = estimate_intensity((_segment(),), mass_kg=MASS_KG, ftp_w=FTP_W)
    encoded = json.dumps(estimate.to_dict(), ensure_ascii=False)
    assert '"calibrated": false' in encoded
    assert '"is_measurement": false' in encoded
    assert '"band_distance_fraction"' in encoded


def test_estimate_rejects_bad_fraction_dict():
    with pytest.raises(ValueError):
        IntensityEstimate(
            borg_scale=DEFAULT_BORG_SCALE,
            mean_rpe=10.0,
            peak_rpe=10.0,
            band_distance_fraction={"i1": 0.5, "i2": 0.6},
            band_duration_fraction=None,
            distance_m=100.0,
            duration_s=10.0,
        )


def test_estimate_uses_scale_bounds_for_cr10():
    from router.core.physiology import BorgScale

    estimate = estimate_intensity(
        (_segment(rider_energy_j=1000.0),),
        mass_kg=MASS_KG,
        ftp_w=FTP_W,
        borg_scale=BorgScale.CR10,
    )
    assert BorgScale.CR10.min_score <= estimate.mean_rpe <= BorgScale.CR10.max_score


# --------------------------------------------------------------------------
# Ponte com o resultado fisico
# --------------------------------------------------------------------------
class _StubArc:
    def __init__(self, *, gain_m: float, highway: str | None) -> None:
        self.elevation_gain_m = gain_m
        self.highway = highway


def _path(results: list[TraversalResult]) -> TraversalPath:
    return TraversalPath(
        results=tuple(results),
        metrics=PhysicsRouteMetrics.from_results(results),
        initial_speed_mps=0.0,
        final_speed_mps=0.0,
    )


def test_segments_from_traversal_builds_grade_and_traffic():
    from router.core.prediction.intensity import segments_from_traversal

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
        _StubArc(gain_m=10.0, highway="primary"),
        _StubArc(gain_m=0.0, highway="cycleway"),
    ]
    segments = segments_from_traversal(_path(results), arcs)

    assert len(segments) == 2
    assert segments[0].grade_pct == pytest.approx(10.0)
    assert segments[0].traffic_stress == pytest.approx(0.95)
    assert segments[1].grade_pct == pytest.approx(0.0)
    assert segments[1].traffic_stress == pytest.approx(0.05)


def test_segments_from_traversal_rejects_mismatched_lengths():
    from router.core.prediction.intensity import segments_from_traversal

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
    rpe = segment_rpe(segment, mass_kg=MASS_KG, ftp_w=FTP_W)
    assert math.isfinite(rpe)


def test_coefficients_can_be_overridden():
    tuned = IntensityCoefficients(scale_gain=12.0)
    base = segment_rpe(_segment(), mass_kg=MASS_KG, ftp_w=FTP_W)
    boosted = segment_rpe(
        _segment(), mass_kg=MASS_KG, ftp_w=FTP_W, coefficients=tuned
    )
    assert boosted >= base
