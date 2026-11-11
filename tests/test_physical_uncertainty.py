"""Testes da incerteza altimetrica (incremento 3 do ADR-025, decisao 2).

Cobrem:

* o contrato ``ElevationUncertainty`` (validacao, ``coverage``, ``combine``);
* a classificacao por origem no ``ArcProfile`` (observado/interpolado/estimado/
  ausente);
* a politica ``SimulationStatus`` (``VALID``/``DEGRADED``/``UNAVAILABLE``) e
  ``require_elevation``;
* a propagacao ate ``TraversalResult`` e ``TraversalPath``.

Regra central: elevacao ausente **nunca** vira terreno plano em silencio. O
numero pode coincidir com o de um plano, mas sob hipotese explicita e marcado
como degradado.
"""

import pytest
from router.core.data.graph.model import (
    BicycleAccess,
    Arc,
    ElevationQuality,
    ElevationSample,
    Segment,
)
from router.core.data.graph.surface import Surface, SurfaceStatus
from router.core.physics import (
    BicycleModel,
    CyclistModel,
    ElevationUncertainty,
    PhysicalTraversal,
    SimulationStatus,
    classify_status,
    worst_status,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()


def make_arc(samples=(), *, distance_m=100.0, arc_id="e0", dismount=False):
    """Arco de um segmento com amostras de elevacao de qualidade explicita."""
    profile = tuple(
        ElevationSample(
            distance_m=d,
            elevation_m=e,
            quality=q,
        )
        for d, e, q in samples
    )
    ascent = 0.0
    descent = 0.0
    known = [(d, e) for d, e, q in samples if e is not None]
    for (d0, e0), (d1, e1) in zip(known, known[1:]):
        if e1 >= e0:
            ascent += e1 - e0
        else:
            descent += e0 - e1
    segment = Segment(
        id="s0",
        distance_m=distance_m,
        surface=Surface.ASPHALT,
        surface_status=SurfaceStatus.MAPPED,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        elevation_profile=profile,
        bicycle_access=(
            BicycleAccess.DISMOUNT if dismount else BicycleAccess.YES
        ),
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


def profile_of(arc, *, step_m=5.0):
    return PhysicalTraversal(step_m=step_m).profile(arc)


# ==========================================================================
# Contrato ElevationUncertainty
# ==========================================================================
def test_uncertainty_rejects_negative_and_oversummed_distances():
    with pytest.raises(ValueError):
        ElevationUncertainty(total_distance_m=100.0, missing_distance_m=-1.0)
    with pytest.raises(ValueError):
        ElevationUncertainty(total_distance_m=-1.0)
    # A soma das origens nao pode exceder o total.
    with pytest.raises(ValueError):
        ElevationUncertainty(
            total_distance_m=100.0,
            observed_distance_m=60.0,
            missing_distance_m=50.0,
        )


def test_uncertainty_coverage_and_helpers():
    full = ElevationUncertainty.fully_observed(100.0)
    assert full.coverage == pytest.approx(1.0)
    assert full.is_fully_observed
    assert not full.has_gap
    assert not full.assumes_flat_terrain

    empty = ElevationUncertainty.unsupported(100.0)
    assert empty.coverage == pytest.approx(0.0)
    assert empty.assumes_flat_terrain
    assert not empty.is_fully_observed

    half = ElevationUncertainty(total_distance_m=100.0, missing_distance_m=25.0)
    assert half.coverage == pytest.approx(0.75)

    # Sem distancia nao ha cobertura definida (nunca 0 disfarcado).
    assert ElevationUncertainty(total_distance_m=0.0).coverage is None


def test_uncertainty_combine_sums_each_origin():
    a = ElevationUncertainty(
        total_distance_m=100.0, observed_distance_m=60.0, missing_distance_m=40.0
    )
    b = ElevationUncertainty(
        total_distance_m=50.0, interpolated_distance_m=50.0
    )
    combined = a.combine(b)
    assert combined.total_distance_m == pytest.approx(150.0)
    assert combined.observed_distance_m == pytest.approx(60.0)
    assert combined.missing_distance_m == pytest.approx(40.0)
    assert combined.interpolated_distance_m == pytest.approx(50.0)


# ==========================================================================
# Classificacao no ArcProfile
# ==========================================================================
def test_fully_observed_profile_is_valid():
    arc = make_arc(
        [
            (0.0, 0.0, ElevationQuality.DEM_SAMPLED),
            (100.0, 5.0, ElevationQuality.DEM_SAMPLED),
        ]
    )
    uncertainty = profile_of(arc).elevation_uncertainty
    assert uncertainty.observed_distance_m == pytest.approx(100.0)
    assert uncertainty.missing_distance_m == 0.0
    assert uncertainty.is_fully_observed
    assert classify_status(uncertainty) is SimulationStatus.VALID


def test_interpolated_profile_is_degraded_but_covered():
    arc = make_arc(
        [
            (0.0, 0.0, ElevationQuality.INTERPOLATED),
            (100.0, 5.0, ElevationQuality.INTERPOLATED),
        ]
    )
    uncertainty = profile_of(arc).elevation_uncertainty
    assert uncertainty.interpolated_distance_m == pytest.approx(100.0)
    assert uncertainty.observed_distance_m == 0.0
    assert uncertainty.missing_distance_m == 0.0
    assert uncertainty.coverage == pytest.approx(1.0)
    assert not uncertainty.is_fully_observed
    assert classify_status(uncertainty) is SimulationStatus.DEGRADED


def test_estimated_profile_is_degraded():
    arc = make_arc(
        [
            (0.0, 0.0, ElevationQuality.ESTIMATED),
            (100.0, 5.0, ElevationQuality.ESTIMATED),
        ]
    )
    uncertainty = profile_of(arc).elevation_uncertainty
    assert uncertainty.estimated_distance_m == pytest.approx(100.0)
    assert classify_status(uncertainty) is SimulationStatus.DEGRADED


def test_mixed_observation_counts_per_interval():
    """Intervalo observado + intervalo estimado: cada trecho na sua origem."""
    arc = make_arc(
        [
            (0.0, 0.0, ElevationQuality.DEM_SAMPLED),
            (40.0, 2.0, ElevationQuality.DEM_SAMPLED),
            (60.0, 3.0, ElevationQuality.ESTIMATED),
            (100.0, 5.0, ElevationQuality.ESTIMATED),
        ]
    )
    uncertainty = profile_of(arc).elevation_uncertainty
    assert uncertainty.observed_distance_m == pytest.approx(40.0)
    assert uncertainty.estimated_distance_m == pytest.approx(60.0)
    assert uncertainty.missing_distance_m == 0.0
    assert classify_status(uncertainty) is SimulationStatus.DEGRADED


def test_gap_between_observed_samples_is_missing():
    """Amostras observadas nas pontas, lacuna no meio: a lacuna e 'missing'."""
    segment = Segment(
        id="s0",
        distance_m=100.0,
        elevation_profile=(
            ElevationSample(
                distance_m=0.0, elevation_m=0.0, quality=ElevationQuality.DEM_SAMPLED
            ),
            ElevationSample(
                distance_m=20.0, elevation_m=1.0, quality=ElevationQuality.DEM_SAMPLED
            ),
            ElevationSample(
                distance_m=80.0, elevation_m=None, quality=ElevationQuality.MISSING
            ),
            ElevationSample(
                distance_m=100.0, elevation_m=5.0, quality=ElevationQuality.DEM_SAMPLED
            ),
        ),
    )
    arc = Arc(id="e0", source=1, target=2, segments=(segment,))
    uncertainty = profile_of(arc).elevation_uncertainty
    # A lacuna em 80 interrompe a cobertura: so 0..20 e sustentado (ambas as
    # pontas observadas). O restante (20..100) fica ausente para a cobertura,
    # mesmo que a engine interpole a inclinacao entre as amostras conhecidas.
    assert uncertainty.observed_distance_m == pytest.approx(20.0)
    assert uncertainty.missing_distance_m == pytest.approx(80.0)
    assert classify_status(uncertainty) is SimulationStatus.DEGRADED


def test_no_elevation_is_fully_missing():
    arc = make_arc(distance_m=100.0)
    uncertainty = profile_of(arc).elevation_uncertainty
    assert uncertainty.missing_distance_m == pytest.approx(100.0)
    assert uncertainty.coverage == pytest.approx(0.0)
    assert uncertainty.assumes_flat_terrain


# ==========================================================================
# Politica de status
# ==========================================================================
def test_require_elevation_escalates_to_unavailable():
    uncertainty = ElevationUncertainty.unsupported(100.0)
    assert (
        classify_status(uncertainty, require_elevation=True)
        is SimulationStatus.UNAVAILABLE
    )
    # Sem exigencia, a hipotese de plano e apenas degradada.
    assert classify_status(uncertainty) is SimulationStatus.DEGRADED


def test_worst_status_orders_by_severity():
    assert (
        worst_status(
            SimulationStatus.VALID,
            SimulationStatus.DEGRADED,
        )
        is SimulationStatus.DEGRADED
    )
    assert (
        worst_status(
            SimulationStatus.DEGRADED,
            SimulationStatus.UNAVAILABLE,
            SimulationStatus.VALID,
        )
        is SimulationStatus.UNAVAILABLE
    )
    assert worst_status() is SimulationStatus.VALID


# ==========================================================================
# Propagacao ate o resultado
# ==========================================================================
def traverse(arc, *, require_elevation=False, step_m=5.0):
    return PhysicalTraversal(
        step_m=step_m, require_elevation=require_elevation
    ).traverse(arc, CYCLIST, BICYCLE)


def test_traverse_attaches_uncertainty_and_status():
    arc = make_arc(
        [
            (0.0, 0.0, ElevationQuality.DEM_SAMPLED),
            (100.0, 2.0, ElevationQuality.DEM_SAMPLED),
        ]
    )
    result = traverse(arc)
    assert result.result_status is SimulationStatus.VALID
    assert result.elevation_uncertainty.observed_distance_m == pytest.approx(100.0)
    assert result.to_dict()["result_status"] == "valid"
    assert result.to_dict()["elevation_uncertainty"]["coverage"] == pytest.approx(1.0)


def test_traverse_require_elevation_marks_unavailable():
    arc = make_arc(distance_m=100.0)
    result = traverse(arc, require_elevation=True)
    assert result.result_status is SimulationStatus.UNAVAILABLE


def test_traverse_path_combines_uncertainty_and_worst_status():
    observed = make_arc(
        [
            (0.0, 0.0, ElevationQuality.DEM_SAMPLED),
            (100.0, 2.0, ElevationQuality.DEM_SAMPLED),
        ],
        arc_id="a",
    )
    unknown = make_arc(distance_m=100.0, arc_id="b")
    path = PhysicalTraversal(step_m=5.0).traverse_path(
        [observed, unknown], CYCLIST, BICYCLE
    )
    assert path.result_status is SimulationStatus.DEGRADED
    assert path.elevation_uncertainty.total_distance_m == pytest.approx(200.0)
    assert path.elevation_uncertainty.observed_distance_m == pytest.approx(100.0)
    assert path.elevation_uncertainty.missing_distance_m == pytest.approx(100.0)
    assert path.to_dict()["result_status"] == "degraded"


def test_flat_arc_from_profile_is_valid():
    """Um arco com perfil explicitamente plano nao e degradado."""
    arc = make_arc(
        [
            (0.0, 3.0, ElevationQuality.DEM_SAMPLED),
            (100.0, 3.0, ElevationQuality.DEM_SAMPLED),
        ]
    )
    result = traverse(arc)
    assert result.result_status is SimulationStatus.VALID
    assert result.ascent_m == 0.0
