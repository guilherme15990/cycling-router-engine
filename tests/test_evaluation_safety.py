"""Estado de seguranca da Camada 05 (ADR-029 D2-D7).

Cobrem, sem recalcular fisica nem fisiologia:

* ``RouteSafetyState`` (``VALIDATED``/``DEGRADED``/``REJECTED``) como estado
  **independente** de ``AssessmentOutcome`` (D2);
* ausencia de modelo fisiologico calibrado e estimativa de intensidade como
  **advertencias** que nunca alteram o estado (D2/D6);
* proibicao explicita e restricao nao resolvida como causas de ``REJECTED``, com
  motivos distintos, e ausencia de regra **nao** como proibicao (D3);
* inviabilidade fisica comprovada como ``REJECTED`` e incerteza nao impeditiva
  como ``DEGRADED`` (D5);
* ``evaluate_candidates`` reutilizando avaliacoes ja calculadas e escolhendo a
  **primeira elegivel** (D4);
* consistencia energetica como diagnostico opcional reutilizado (D7).
"""

from __future__ import annotations

import dataclasses
import math
import pytest
from router.core.evaluation import (
    EnergyConsistency,
    EvaluationReport,
    Reason,
    ReasonKind,
    RouteSafety,
    RouteSafetyState,
    assess_route,
    classify_safety,
    energy_consistency,
    evaluate_candidates,
)
from router.core.data.graph.model import (
    Arc,
    BicycleAccess,
    ElevationSample,
    Node,
    RoutingGraph,
    Segment,
)
from router.core.data.graph.restrictions import AccessRule
from router.core.physics.physical import (
    BicycleModel,
    CyclistModel,
    RouteMetrics,
    SimulationState,
    SimulationStatus,
    TraversalPath,
)
from router.core.physics.physiology import (
    DEFAULT_BORG_SCALE,
    PhysiologicalPrediction,
    PredictionStatus,
)
from router.core.search import Route, RouteCandidate

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
MASS_KG = CYCLIST.mass_kg + BICYCLE.mass_kg


# --------------------------------------------------------------------------
# Infra (grafos pequenos; a fisica real roda via simulador completo)
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
        [make_arc("a12", 1, 2, **a12), make_arc("a23", 2, 3)],
        (1, 2, 3),
    )


def forbidden_rule():
    return AccessRule(scope="bicycle", value="no", condition=None, raw="no")


def unresolved_rule():
    """Regra condicional sem avaliador -> resolucao conservadora (nao resolvida)."""
    return AccessRule(
        scope="bicycle",
        value="no",
        condition="(Mo-Fr 07:00-10:00)",
        raw="no @ (Mo-Fr 07:00-10:00)",
    )


def assess(graph, arc_ids=("a12", "a23"), **kwargs):
    return assess_route(graph, arc_ids, cyclist=CYCLIST, bicycle=BICYCLE, **kwargs)


def moving():
    """Estado inicial acima do piso: evita ``min_speed_regularization``."""
    return {"initial_state": SimulationState.from_speed(5.0)}


def validated_assessment():
    """Rota fisicamente valida e legal -> ``VALIDATED`` (sem motivos)."""
    return assess(two_arc_graph(), **moving())


class _CalibratedStubPredictor:
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


# --------------------------------------------------------------------------
# D2 — estado de seguranca
# --------------------------------------------------------------------------
def test_validated_route_has_no_determining_reasons():
    safety = classify_safety(validated_assessment())
    assert safety.state is RouteSafetyState.VALIDATED
    assert safety.reasons == ()
    assert safety.validated
    assert safety.eligible
    assert not safety.rejected and not safety.degraded


def test_state_is_independent_from_assessment_outcome():
    """``VALIDATED`` coexiste com ``PHYSICAL_ONLY`` (sem modelo calibrado)."""
    assessment = validated_assessment()
    assert assessment.outcome.value == "physical_only"
    assert classify_safety(assessment).state is RouteSafetyState.VALIDATED


def test_absence_of_calibrated_physiology_is_advisory_not_state():
    safety = classify_safety(validated_assessment())
    kinds = {reason.kind for reason in safety.advisories}
    assert ReasonKind.PHYSIOLOGY_UNAVAILABLE in kinds
    # Nenhuma advertencia determina estado.
    assert safety.state is RouteSafetyState.VALIDATED
    assert all(reason.kind not in _STATE_KINDS for reason in safety.advisories)


def test_intensity_estimate_is_always_advisory():
    safety = classify_safety(validated_assessment())
    kinds = {reason.kind for reason in safety.advisories}
    assert ReasonKind.INTENSITY_NOT_CALIBRATED in kinds
    assert safety.state is RouteSafetyState.VALIDATED


def test_calibrated_physiology_does_not_upgrade_or_downgrade():
    assessment = assess(two_arc_graph(), predictor=_CalibratedStubPredictor(), **moving())
    assert assessment.outcome.value == "physiological"
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.VALIDATED
    # Advertencia fisiologica ausente com previsao calibrada.
    assert all(
        reason.kind is not ReasonKind.PHYSIOLOGY_UNAVAILABLE
        for reason in safety.advisories
    )


# --------------------------------------------------------------------------
# D3 — circulacao
# --------------------------------------------------------------------------
def test_explicit_forbidden_rule_rejects():
    assessment = assess(two_arc_graph(restrictions=(forbidden_rule(),)), **moving())
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.REJECTED
    assert not safety.eligible
    kinds = {reason.kind for reason in safety.reasons}
    assert ReasonKind.CIRCULATION_VIOLATION in kinds
    assert ReasonKind.CIRCULATION_UNRESOLVED not in kinds


def test_unresolved_restriction_rejects_with_distinct_reason():
    assessment = assess(
        two_arc_graph(restrictions=(unresolved_rule(),)), **moving()
    )
    assert assessment.circulation.unresolved
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.REJECTED
    kinds = {reason.kind for reason in safety.reasons}
    assert ReasonKind.CIRCULATION_UNRESOLVED in kinds
    assert ReasonKind.CIRCULATION_VIOLATION not in kinds


def test_absence_of_rule_is_not_prohibition_nor_unresolved():
    assessment = assess(two_arc_graph(), **moving())
    assert assessment.circulation.compliant
    assert not assessment.circulation.unresolved
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.VALIDATED
    assert all(
        reason.kind
        not in {ReasonKind.CIRCULATION_VIOLATION, ReasonKind.CIRCULATION_UNRESOLVED}
        for reason in safety.reasons
    )


# --------------------------------------------------------------------------
# D5 — viabilidade fisica e incerteza
# --------------------------------------------------------------------------
def test_degraded_simulation_degrades_but_stays_eligible():
    graph = make_graph(
        [
            make_arc("a12", 1, 2, points=None, distance_m=200.0),
            make_arc("a23", 2, 3, points=None, distance_m=200.0),
        ],
        (1, 2, 3),
    )
    assessment = assess(graph, **moving())
    assert assessment.result_status is SimulationStatus.DEGRADED
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.DEGRADED
    assert safety.eligible
    kinds = {reason.kind for reason in safety.reasons}
    assert kinds & {ReasonKind.SIMULATION_DEGRADED, ReasonKind.ELEVATION_MISSING}


def test_dismount_degrades_but_stays_eligible():
    assessment = assess(
        two_arc_graph(bicycle_access=BicycleAccess.DISMOUNT), **moving()
    )
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.DEGRADED
    assert ReasonKind.DISMOUNT_USED in {r.kind for r in safety.reasons}


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda path: dataclasses.replace(
                path, metrics=dataclasses.replace(path.metrics, time_s=math.inf)
            ),
            ReasonKind.NON_FINITE_METRIC,
        ),
        (
            lambda path: dataclasses.replace(path, final_speed_mps=-1.0),
            ReasonKind.NEGATIVE_SPEED,
        ),
        (
            lambda path: dataclasses.replace(
                path, metrics=dataclasses.replace(path.metrics, distance_m=0.0)
            ),
            ReasonKind.NON_POSITIVE_DISTANCE,
        ),
    ],
)
def test_proven_physical_infeasibility_rejects(mutate, expected):
    assessment = validated_assessment()
    broken = dataclasses.replace(assessment, path=mutate(assessment.path))
    safety = classify_safety(broken)
    assert safety.state is RouteSafetyState.REJECTED
    assert not safety.eligible
    assert expected in {reason.kind for reason in safety.reasons}


# --------------------------------------------------------------------------
# D4 — evaluate_candidates reutiliza avaliacoes e escolhe a 1a elegivel
# --------------------------------------------------------------------------
def _candidate(route_id, arc_ids):
    route = Route(
        arc_ids=arc_ids,
        time_s=1.0,
        distance_m=200.0 * len(arc_ids),
        ascent_m=0.0,
        descent_m=0.0,
        energy_j=1000.0,
    )
    return RouteCandidate.from_route(route, route_id=route_id)


def test_evaluate_candidates_reuses_existing_assessment():
    candidate = _candidate("c1", ("a12", "a23"))
    assessment = validated_assessment()
    report = evaluate_candidates([candidate], assessments={candidate.route_id: assessment})
    assert isinstance(report, EvaluationReport)
    assert len(report.routes) == 1
    # Reutilizado **como esta** (mesma identidade, sem recalculo).
    assert report.routes[0].assessment is assessment
    assert report.recommended is report.routes[0]
    assert report.recommended.recommended is True


def test_rejected_route_is_never_recommended_regardless_of_position():
    bad = _candidate("bad", ("a12", "a23"))
    good = _candidate("good", ("a12", "a23"))
    assessments = {
        "bad": assess(two_arc_graph(restrictions=(forbidden_rule(),)), **moving()),
        "good": validated_assessment(),
    }
    # A rota inelegivel esta **primeiro** no ranking.
    report = evaluate_candidates([bad, good], assessments=assessments)
    assert report.recommended is not None
    assert report.recommended.route_id == "good"
    assert report.recommended.recommended is True
    # Nenhuma rota REJECTED aparece como elegivel nem como recomendada.
    assert all(item.safety.eligible for item in report.routes)
    assert report.recommended.route_id not in {
        item.route_id for item in report.excluded
    }
    assert report.excluded[0].route_id == "bad"


def test_no_eligible_route_recommends_none():
    bad = _candidate("bad", ("a12", "a23"))
    assessments = {
        "bad": assess(two_arc_graph(restrictions=(forbidden_rule(),)), **moving())
    }
    report = evaluate_candidates([bad], assessments=assessments)
    assert report.recommended is None
    assert report.routes == ()
    assert report.excluded[0].route_id == "bad"
    assert any("nenhuma rota elegivel" in warning for warning in report.warnings)


def test_missing_assessment_is_ineligibility_not_infeasibility():
    candidate = _candidate("c1", ("a12", "a23"))
    report = evaluate_candidates([candidate], assessments={})
    item = report.excluded[0]
    assert item.safety.state is RouteSafetyState.REJECTED
    assert ReasonKind.EVALUATION_MISSING in {r.kind for r in item.safety.reasons}
    # Motivo explicito: inelegivel, nao prova de inviabilidade.
    assert any("nao e prova de inviabilidade" in r.detail for r in item.safety.reasons)


def test_degraded_route_can_be_recommended_with_warning():
    degraded = _candidate("deg", ("a12", "a23"))
    assessments = {"deg": assess(two_arc_graph(bicycle_access=BicycleAccess.DISMOUNT), **moving())}
    report = evaluate_candidates([degraded], assessments=assessments)
    assert report.recommended is not None
    assert report.recommended.safety.state is RouteSafetyState.DEGRADED
    assert any("DEGRADED" in warning for warning in report.warnings)


# --------------------------------------------------------------------------
# Serializacao e determinismo
# --------------------------------------------------------------------------
def test_serialization_roundtrip_is_json_friendly():
    import json
    report = evaluate_candidates(
        [_candidate("c1", ("a12", "a23"))],
        assessments={"c1": validated_assessment()},
    )
    payload = report.to_dict()
    json.dumps(payload)  # nao lanca
    assert payload["recommended_route_id"] == "c1"
    assert payload["eligible_count"] == 1
    assert payload["routes"][0]["safety"]["state"] == "validated"


def test_classification_is_deterministic():
    first = classify_safety(validated_assessment())
    second = classify_safety(validated_assessment())
    assert first == second


# --------------------------------------------------------------------------
# D7 — consistencia energetica (diagnostico opcional, reutilizado)
# --------------------------------------------------------------------------
def test_energy_diagnostic_reuses_traversal_balance():
    assessment = validated_assessment()
    assert isinstance(assessment.energy, EnergyConsistency)
    expected = sum(
        result.energy_balance(MASS_KG).wheel_work_j
        for result in assessment.path.results
    )
    assert assessment.energy.wheel_work_j == pytest.approx(expected)
    assert assessment.energy.mass_kg == pytest.approx(MASS_KG)
    assert "energy" in assessment.to_dict()


def test_energy_diagnostic_absence_does_not_reject():
    assessment = validated_assessment()
    without = dataclasses.replace(assessment, energy=None)
    assert without.energy is None
    assert classify_safety(without).state is RouteSafetyState.VALIDATED


def test_energy_consistency_without_wheel_work_has_no_false_zero():
    """Sem trabalho na roda o relativo e ``None`` (nunca zero inventado)."""
    result = validated_assessment().path
    empty = TraversalPath(
        results=(),
        metrics=RouteMetrics(),
        initial_speed_mps=0.0,
        final_speed_mps=0.0,
    )
    del result
    diagnostic = energy_consistency(empty, MASS_KG)
    assert diagnostic.wheel_work_j == 0.0
    assert diagnostic.relative_residual is None


# --------------------------------------------------------------------------
# Invariantes de motivo
# --------------------------------------------------------------------------
def test_every_rejection_carries_a_reason():
    assessment = assess(two_arc_graph(restrictions=(forbidden_rule(),)), **moving())
    safety = classify_safety(assessment)
    assert safety.rejected
    assert safety.reasons  # nunca um estado sem motivo auditavel


def test_reason_and_safety_serialize():
    reason = Reason(ReasonKind.STALLED, "detalhe", arc_id="a12")
    assert reason.to_dict()["kind"] == "stalled"
    safety = RouteSafety(RouteSafetyState.DEGRADED, (reason,))
    payload = safety.to_dict()
    assert payload["state"] == "degraded"
    assert payload["eligible"] is True
    assert payload["reasons"][0]["arc_id"] == "a12"


#: Conjunto dos motivos que **determinam** estado (nao informativos).
_STATE_KINDS = {
    ReasonKind.CIRCULATION_VIOLATION,
    ReasonKind.CIRCULATION_UNRESOLVED,
    ReasonKind.NON_FINITE_METRIC,
    ReasonKind.NEGATIVE_SPEED,
    ReasonKind.NON_POSITIVE_DISTANCE,
    ReasonKind.ELEVATION_MISSING,
    ReasonKind.SIMULATION_DEGRADED,
    ReasonKind.SIMULATION_UNAVAILABLE,
    ReasonKind.MIN_SPEED_REGULARIZATION,
    ReasonKind.STALLED,
    ReasonKind.DISMOUNT_USED,
    ReasonKind.EVALUATION_MISSING,
}
