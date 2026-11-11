"""Avaliacao fisica completa de rota + classificacao (Camada 05, ADR-026 §5).

Cobrem:

* o pipeline declarado no ADR-025 §10 / ADR-026 §3 (circulacao -> simulador
  completo -> ``EffortFeatures`` -> preditor -> classificacao);
* a **classificacao explicita** e a declaracao de ausencia de modelo calibrado
  (nunca um valor inventado);
* erros de dados (rota vazia/desconhecida/descontinua) sem mascarar;
* propagacao de qualidade/limitacao (elevacao ausente, desmontagem);
* que o simulador usado e o **completo**, nao o estimador aproximado.
"""

import ast
import inspect
import pytest
import router.core.evaluation.assessment as assessment_module
from router.core.evaluation import (
    AssessmentOutcome,
    RouteAssessmentError,
    assess_candidate,
    assess_candidates,
    assess_route,
    assess_search_route,
)
from router.core.data.graph.model import Arc, BicycleAccess, ElevationSample, Node, RoutingGraph, Segment
from router.core.data.graph.restrictions import AccessRule
from router.core.prediction.ml import MetabolicEnergyPredictor, UnavailablePredictor
from router.core.physics.physical import BicycleModel, CyclistModel, SimulationStatus
from router.core.physics.physiology import (
    DEFAULT_BORG_SCALE,
    MetabolicParameters,
    PhysiologicalPrediction,
    PredictionStatus,
)
from router.core.search import Route, RouteCandidate

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()


# --------------------------------------------------------------------------
# Infra
# --------------------------------------------------------------------------
def make_arc(
    arc_id,
    source,
    target,
    *,
    points=((0.0, 0.0), (200.0, 0.0)),
    distance_m=None,
    restrictions=(),
    bicycle_access=BicycleAccess.YES,
):
    if points is None:
        distance = float(distance_m if distance_m is not None else 200.0)
        profile = ()
        ascent = descent = 0.0
    else:
        distance = points[-1][0]
        profile = tuple(
            ElevationSample(distance_m=d, elevation_m=e) for d, e in points
        )
        ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
        descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        restrictions=tuple(restrictions),
        bicycle_access=bicycle_access,
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arcs, node_ids):
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    for arc in arcs:
        graph.add_arc(arc)
    return graph


def two_arc_graph(**a12):
    return make_graph(
        [
            make_arc("a12", 1, 2, **a12),
            make_arc("a23", 2, 3),
        ],
        (1, 2, 3),
    )


def forbidden_rule():
    return AccessRule(scope="bicycle", value="no", condition=None, raw="no")


class _CalibratedStubPredictor:
    """Preditor **calibrado** de teste: produz RPE e declara metodo de validacao."""

    model_version = "stub@1"
    borg_scale = DEFAULT_BORG_SCALE
    required_inputs = ()

    def predict(self, features, rider, context):
        return PhysiologicalPrediction(
            borg_scale=DEFAULT_BORG_SCALE,
            predicted_rpe=13.0,
            heart_rate_zone_distribution=None,
            model_version=self.model_version,
            status=PredictionStatus.AVAILABLE,
        )


def metabolic_params():
    return MetabolicParameters(
        gross_efficiency=0.25, version="v1", provenance="laboratorio"
    )


def assess(graph, arc_ids=("a12", "a23"), **kwargs):
    return assess_route(graph, arc_ids, cyclist=CYCLIST, bicycle=BICYCLE, **kwargs)


# --------------------------------------------------------------------------
# Classificacao explicita: ausencia de modelo calibrado
# --------------------------------------------------------------------------
def test_route_without_predictor_is_physical_only():
    assessment = assess(two_arc_graph())
    assert assessment.outcome is AssessmentOutcome.PHYSICAL_ONLY
    assert assessment.prediction.status is PredictionStatus.UNAVAILABLE
    assert assessment.prediction.predicted_rpe is None
    assert not assessment.physiologically_available
    assert "physiology_unavailable" in assessment.limitations


def test_unavailable_predictor_is_model_not_calibrated():
    assessment = assess(two_arc_graph(), predictor=UnavailablePredictor())
    assert assessment.prediction.status is PredictionStatus.MODEL_NOT_CALIBRATED
    assert assessment.outcome is AssessmentOutcome.PHYSICAL_ONLY
    assert not assessment.physiologically_available
    assert "physiology_model_not_calibrated" in assessment.limitations
    # Nada fisiologico e inventado.
    assert assessment.prediction.predicted_rpe is None


def test_calibrated_predictor_flips_outcome_to_physiological():
    assessment = assess(two_arc_graph(), predictor=_CalibratedStubPredictor())
    assert assessment.outcome is AssessmentOutcome.PHYSIOLOGICAL
    assert assessment.physiologically_available
    assert assessment.prediction.predicted_rpe == 13.0
    assert not any(
        code.startswith("physiology_") for code in assessment.limitations
    )


def test_outcome_reason_explains_absence():
    assessment = assess(two_arc_graph())
    assert "unavailable" in assessment.outcome_reason


def test_metabolic_predictor_fills_only_metabolic_energy():
    assessment = assess(
        two_arc_graph(),
        predictor=MetabolicEnergyPredictor(metabolic=metabolic_params()),
    )
    prediction = assessment.prediction
    assert prediction.metabolic_energy_j == pytest.approx(
        assessment.features.rider_work_j / 0.25, rel=1e-12
    )
    # RPE/FC continuam ausentes: o estado fisiologico nao esta calibrado.
    assert prediction.predicted_rpe is None
    assert prediction.status is PredictionStatus.MODEL_NOT_CALIBRATED
    assert assessment.outcome is AssessmentOutcome.PHYSICAL_ONLY


# --------------------------------------------------------------------------
# Guardrail de circulacao (mesmo contrato da busca)
# --------------------------------------------------------------------------
def test_route_violating_circulation_is_rejected():
    graph = two_arc_graph(restrictions=(forbidden_rule(),))
    assessment = assess(graph)
    assert assessment.outcome is AssessmentOutcome.REJECTED
    assert not assessment.circulation.compliant
    assert "circulation_violation" in assessment.limitations
    # A fisica continua sendo reportada (nao ha mascaramento por rejeicao).
    assert assessment.metrics.distance_m > 0.0


def test_route_without_restrictions_is_compliant():
    assessment = assess(two_arc_graph())
    assert assessment.circulation.compliant
    assert "circulation_violation" not in assessment.limitations


# --------------------------------------------------------------------------
# Erros de dados (nunca mascarados)
# --------------------------------------------------------------------------
def test_empty_route_raises():
    with pytest.raises(RouteAssessmentError):
        assess(two_arc_graph(), arc_ids=())


def test_unknown_arc_raises():
    with pytest.raises(RouteAssessmentError) as excinfo:
        assess(two_arc_graph(), arc_ids=("a12", "nope"))
    assert "nope" in str(excinfo.value)


def test_none_arc_id_raises():
    with pytest.raises(RouteAssessmentError):
        assess(two_arc_graph(), arc_ids=("a12", None))


def test_discontinuous_route_raises():
    graph = make_graph(
        [
            make_arc("a12", 1, 2),
            make_arc("a34", 3, 4),  # nao continua a partir de a12 (termina em 2)
        ],
        (1, 2, 3, 4),
    )
    with pytest.raises(RouteAssessmentError) as excinfo:
        assess(graph, arc_ids=("a12", "a34"))
    assert "descontinua" in str(excinfo.value)


# --------------------------------------------------------------------------
# Fidelidade ao simulador completo e propagacao de limitacao
# --------------------------------------------------------------------------
def test_features_match_full_simulation_metrics():
    assessment = assess(two_arc_graph())
    assert assessment.arc_ids == ("a12", "a23")
    assert assessment.path.arc_ids == ("a12", "a23")
    assert assessment.features.duration_s == pytest.approx(
        assessment.metrics.time_s
    )
    assert assessment.features.distance_m == pytest.approx(
        assessment.metrics.distance_m
    )
    assert assessment.features.result_status is assessment.path.result_status


def test_degraded_height_data_is_propagated_not_masked():
    # Arco sem perfil de elevacao: simulacao degradada + limitacao explicita.
    graph = make_graph(
        [
            make_arc("a12", 1, 2, points=None, distance_m=200.0),
            make_arc("a23", 2, 3, points=None, distance_m=200.0),
        ],
        (1, 2, 3),
    )
    assessment = assess(graph)
    assert assessment.result_status is SimulationStatus.DEGRADED
    assert "elevation_missing" in assessment.limitations
    assert "simulation_degraded" in assessment.limitations


def test_dismount_route_reports_limitation():
    graph = two_arc_graph(bicycle_access=BicycleAccess.DISMOUNT)
    assessment = assess(graph)
    assert assessment.circulation.dismount_used
    assert "dismount_used" in assessment.limitations


# --------------------------------------------------------------------------
# Serializacao e determinismo
# --------------------------------------------------------------------------
def test_to_dict_reports_explicit_absence():
    payload = assess(two_arc_graph()).to_dict()
    assert payload["outcome"] == "physical_only"
    assert payload["physiologically_available"] is False
    assert payload["prediction"]["is_measurement"] is False
    assert payload["prediction"]["predicted_rpe"] is None
    assert payload["prediction"]["status"] == "unavailable"
    assert "physiology_unavailable" in payload["limitations"]


def test_assessment_is_deterministic():
    graph = two_arc_graph()
    assert assess(graph).to_dict() == assess(graph).to_dict()


# --------------------------------------------------------------------------
# Integracao com as estruturas da busca (Camada 03)
# --------------------------------------------------------------------------
def test_assess_search_route_uses_route_arcs():
    route = Route(
        arc_ids=("a12", "a23"),
        time_s=1.0,
        distance_m=400.0,
        ascent_m=0.0,
        descent_m=0.0,
        energy_j=1000.0,
    )
    assessment = assess_search_route(two_arc_graph(), route, cyclist=CYCLIST, bicycle=BICYCLE)
    assert assessment.arc_ids == ("a12", "a23")


def test_assess_candidates_preserves_order():
    graph = two_arc_graph()
    routes = [
        Route(arc_ids=("a12", "a23"), time_s=1.0, distance_m=400.0, ascent_m=0.0, descent_m=0.0, energy_j=1.0),
        Route(arc_ids=("a12",), time_s=1.0, distance_m=200.0, ascent_m=0.0, descent_m=0.0, energy_j=1.0, label_id=1),
    ]
    candidates = [RouteCandidate.from_route(route) for route in routes]
    assessments = assess_candidates(graph, candidates, cyclist=CYCLIST, bicycle=BICYCLE)
    assert len(assessments) == 2
    assert assessments[0].arc_ids == ("a12", "a23")
    assert assessments[1].arc_ids == ("a12",)


def test_assess_candidate_matches_assess_route():
    graph = two_arc_graph()
    route = Route(arc_ids=("a12", "a23"), time_s=1.0, distance_m=400.0, ascent_m=0.0, descent_m=0.0, energy_j=1.0)
    candidate = RouteCandidate.from_route(route)
    assert (
        assess_candidate(graph, candidate, cyclist=CYCLIST, bicycle=BICYCLE).to_dict()
        == assess(graph).to_dict()
    )


# --------------------------------------------------------------------------
# Invariantes de arquitetura
# --------------------------------------------------------------------------
def test_assessment_uses_full_simulator_not_approximate_estimator():
    """A avaliacao nunca usa o estimador aproximado (so o simulador completo)."""
    tree = ast.parse(inspect.getsource(assessment_module))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not any("search.approximate" in module for module in modules)
    assert "router.core.physics.physical.traversal" in modules


# --------------------------------------------------------------------------
# Estimativa de intensidade (ADR-028): separada, nao calibrada
# --------------------------------------------------------------------------
def test_assessment_carries_uncalibrated_intensity_estimate():
    assessment = assess(two_arc_graph())

    estimate = assessment.intensity
    assert estimate.calibrated is False
    assert estimate.is_measurement is False
    assert estimate.source == "computational_intensity_estimate"
    assert DEFAULT_BORG_SCALE.min_score <= estimate.mean_rpe <= DEFAULT_BORG_SCALE.max_score
    assert sum(estimate.band_distance_fraction.values()) == pytest.approx(1.0)
    assert estimate.band_duration_fraction is not None
    assert sum(estimate.band_duration_fraction.values()) == pytest.approx(1.0)


def test_assessment_intensity_does_not_fill_physiological_contract():
    """A estimativa de intensidade nunca preenche a previsao fisiologica."""
    assessment = assess(two_arc_graph())
    assert assessment.prediction.predicted_rpe is None
    assert assessment.prediction.heart_rate_zone_distribution is None
    assert assessment.prediction.status is PredictionStatus.UNAVAILABLE


def test_assessment_intensity_present_even_with_calibrated_predictor():
    assessment = assess(two_arc_graph(), predictor=_CalibratedStubPredictor())
    assert assessment.prediction.status is PredictionStatus.AVAILABLE
    # A estimativa de intensidade e independente da previsao fisiologica.
    assert assessment.intensity.calibrated is False
    assert "intensity" in assessment.to_dict()

