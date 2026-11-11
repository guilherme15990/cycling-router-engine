"""Baselines de regressao adversa da Camada 05 (ADR-029 D8, R-a..R-f).

Cada cenario congela um comportamento **obrigatorio** que nao pode regredir:

* R-a: restricao de circulacao aplicavel **nao resolvida** reroteia a busca e a
  rota proibida **nunca** e devolvida; ausencia de regra **nao** reroteia;
* R-b: inviabilidade fisica **comprovada** rejeita e **nunca** recomenda;
* R-c: circulacao **nao resolvida** rejeita por motivo **distinto** de proibicao;
* R-d: resultados **degradados** permanecem elegiveis e recomendaveis com aviso;
* R-e: **ausencia de rota elegivel** devolve ``recommended=None`` e preserva as
  rejeitadas com motivos (auditoria);
* R-f: operacao **offline** e **reprodutivel**, sem rota inelegivel recomendada.

Nenhum cenario altera a fisica, o ``MultiObjectiveAStar`` nem o ML: apenas o
governo da recomendacao (``evaluate_candidates`` / ``plan_offline_routes``).
"""

from __future__ import annotations

import dataclasses
import math
import pytest
from router.core.data.elevation import CallableElevation
from router.core.evaluation import (
    ReasonKind,
    RouteSafetyState,
    assess_route,
    classify_safety,
    evaluate_candidates,
)
from router.core.data.graph.model import Arc, ElevationSample, Node, RoutingGraph, Segment
from router.core.data.graph.restrictions import AccessRule
from router.offline import is_network_disabled, offline
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics import BicycleModel, CyclistModel, SimulationState
from router.app.planning import plan_offline_routes
from router.core.search import SearchRoute, RouteCandidate

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
START = SimulationState.from_speed(5.0)

ORIGIN = (0.000, 0.000)
DESTINATION = (0.002, 0.002)


# --------------------------------------------------------------------------
# Infra de planejamento (mesma rede quadrada de test_planning)
# --------------------------------------------------------------------------
def _network(*, restrict_direct: bool) -> dict:
    direct_tags = {"highway": "residential", "surface": "asphalt"}
    if restrict_direct:
        # Restricao condicional preservada no grafo (sem avaliador -> nao resolvida).
        direct_tags["bicycle:conditional"] = "no @ (Mo-Fr 07:00-10:00)"
    return {
        "elements": [
            {"type": "node", "id": 1, "lat": 0.000, "lon": 0.000},
            {"type": "node", "id": 2, "lat": 0.002, "lon": 0.000},
            {"type": "node", "id": 3, "lat": 0.000, "lon": 0.002},
            {"type": "node", "id": 4, "lat": 0.002, "lon": 0.002},
            {"type": "way", "id": 101, "nodes": [1, 2], "tags": direct_tags},
            {
                "type": "way",
                "id": 102,
                "nodes": [2, 4],
                "tags": {"highway": "residential", "surface": "asphalt"},
            },
            {
                "type": "way",
                "id": 103,
                "nodes": [1, 3],
                "tags": {"highway": "residential", "surface": "gravel"},
            },
            {
                "type": "way",
                "id": 104,
                "nodes": [3, 4],
                "tags": {"highway": "residential", "surface": "gravel"},
            },
        ]
    }


def _graph(*, restrict_direct: bool) -> RoutingGraph:
    graph, _ = build_graph(
        OsmData.from_payload(_network(restrict_direct=restrict_direct)),
        BuildOptions(),
    )
    attach_elevation(
        graph,
        CallableElevation(lambda lat, lon: 700.0 + 3000.0 * lat, resolution_m=30.0),
        spacing_m=20.0,
    )
    return graph


def _plan(*, restrict_direct: bool = False):
    return plan_offline_routes(
        _graph(restrict_direct=restrict_direct),
        ORIGIN,
        DESTINATION,
        cyclist=CYCLIST,
        bicycle=BICYCLE,
    )


# --------------------------------------------------------------------------
# Infra de avaliacao (grafos com elevacao; fisica real)
# --------------------------------------------------------------------------
def _rule(value: str, *, condition: str | None = None) -> AccessRule:
    raw = value if condition is None else f"{value} @ {condition}"
    return AccessRule(scope="bicycle", value=value, condition=condition, raw=raw)


def _assessment_arc(arc_id, source, target, *, restrictions=()):
    points = ((0.0, 0.0), (200.0, 0.0))
    profile = tuple(
        ElevationSample(distance_m=d, elevation_m=e) for d, e in points
    )
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=200.0,
        elevation_profile=profile,
        elevation_gain_m=0.0,
        elevation_loss_m=0.0,
        restrictions=tuple(restrictions),
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def _assessment_graph(*, restrictions=()):
    graph = RoutingGraph()
    for node_id in (1, 2, 3):
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    graph.add_arc(_assessment_arc("a12", 1, 2, restrictions=restrictions))
    graph.add_arc(_assessment_arc("a23", 2, 3))
    return graph


def _assess(*, restrictions=()):
    return assess_route(
        _assessment_graph(restrictions=restrictions),
        ("a12", "a23"),
        cyclist=CYCLIST,
        bicycle=BICYCLE,
        initial_state=START,
    )


def _candidate(route_id):
    route = SearchRoute(
        arc_ids=("a12", "a23"),
        time_s=1.0,
        distance_m=400.0,
        ascent_m=0.0,
        descent_m=0.0,
        energy_j=1000.0,
    )
    return RouteCandidate.from_route(route, route_id=route_id)


# --------------------------------------------------------------------------
# R-a — restricao aplicavel nao resolvida reroteia; ausencia de regra nao
# --------------------------------------------------------------------------
def test_ra_unresolved_restriction_reroutes_and_is_never_returned():
    plan = _plan(restrict_direct=True)
    assert plan.reached
    assert plan.count >= 1
    # A via direta restrita (w101) nao aparece em nenhuma rota devolvida.
    for route in plan.routes:
        assert "w101:0" not in route.candidate.route.arc_ids
    assert plan.recommended is not None
    assert plan.recommended.eligible


def test_ra_absence_of_rule_does_not_reroute():
    plan = _plan(restrict_direct=False)
    assert plan.reached
    # Sem restricao, a via direta (w101) esta disponivel.
    used = {arc for route in plan.routes for arc in route.candidate.route.arc_ids}
    assert "w101:0" in used


# --------------------------------------------------------------------------
# R-b — inviabilidade fisica comprovada nunca e recomendada
# --------------------------------------------------------------------------
def test_rb_proven_infeasibility_is_rejected_and_never_recommended():
    assessment = _assess()
    broken = dataclasses.replace(
        assessment,
        path=dataclasses.replace(
            assessment.path, metrics=dataclasses.replace(assessment.path.metrics, time_s=math.inf)
        ),
    )
    report = evaluate_candidates(
        [_candidate("inf")], assessments={"inf": broken}
    )
    assert report.recommended is None
    item = report.excluded[0]
    assert item.safety.state is RouteSafetyState.REJECTED
    assert ReasonKind.NON_FINITE_METRIC in {r.kind for r in item.safety.reasons}


# --------------------------------------------------------------------------
# R-c — circulacao nao resolvida: motivo distinto de proibicao
# --------------------------------------------------------------------------
def test_rc_unresolved_circulation_has_distinct_reason():
    assessment = _assess(
        restrictions=(_rule("no", condition="(Mo-Fr 07:00-10:00)"),)
    )
    assert assessment.circulation.unresolved
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.REJECTED
    kinds = {reason.kind for reason in safety.reasons}
    assert ReasonKind.CIRCULATION_UNRESOLVED in kinds
    assert ReasonKind.CIRCULATION_VIOLATION not in kinds


def test_rc_explicit_forbidden_has_violation_reason():
    assessment = _assess(restrictions=(_rule("no"),))
    safety = classify_safety(assessment)
    assert safety.state is RouteSafetyState.REJECTED
    kinds = {reason.kind for reason in safety.reasons}
    assert ReasonKind.CIRCULATION_VIOLATION in kinds
    assert ReasonKind.CIRCULATION_UNRESOLVED not in kinds


# --------------------------------------------------------------------------
# R-d — resultados degradados: elegiveis e recomendaveis com aviso
# --------------------------------------------------------------------------
def test_rd_degraded_routes_are_eligible_and_recommended_with_warning():
    plan = _plan()
    assert plan.reached
    assert plan.rejected_count == 0
    assert all(route.safety_state is RouteSafetyState.DEGRADED for route in plan.routes)
    assert plan.recommended is not None
    assert plan.recommended.recommended is True
    assert any("DEGRADED" in warning for warning in plan.warnings)


# --------------------------------------------------------------------------
# R-e — ausencia de rota elegivel: recomendacao None + auditoria preservada
# --------------------------------------------------------------------------
def test_re_no_eligible_route_keeps_rejected_for_audit():
    assessments = {
        "bad1": _assess(restrictions=(_rule("no"),)),
        "bad2": _assess(
            restrictions=(_rule("no", condition="(Mo-Fr 07:00-10:00)"),)
        ),
    }
    report = evaluate_candidates(
        [_candidate("bad1"), _candidate("bad2")], assessments=assessments
    )
    assert report.recommended is None
    assert report.routes == ()
    assert report.rejected_count == 2
    # Todas as rejeitadas carregam motivo (nada silencioso).
    assert all(item.safety.reasons for item in report.excluded)
    assert any("nenhuma rota elegivel" in warning for warning in report.warnings)


def test_re_no_reachable_route_leaves_routes_empty():
    # Rede sem conexao: nada inventado, ``recommended`` ausente.
    graph, _ = build_graph(
        OsmData.from_payload(
            {
                "elements": [
                    {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
                    {"type": "node", "id": 2, "lat": 0.01, "lon": 0.01},
                ]
            }
        ),
        BuildOptions(),
    )
    plan = plan_offline_routes(
        graph, ORIGIN, DESTINATION, cyclist=CYCLIST, bicycle=BICYCLE
    )
    assert plan.reached is False
    assert plan.routes == ()
    assert plan.recommended is None


# --------------------------------------------------------------------------
# R-f — operacao offline e reprodutibilidade
# --------------------------------------------------------------------------
def test_rf_planning_is_offline_and_reproducible():
    with offline():
        assert is_network_disabled()
        first = _plan()
        second = _plan()
    assert not is_network_disabled()
    assert first.to_dict() == second.to_dict()


def test_rf_no_ineligible_route_is_ever_recommended():
    plan = _plan(restrict_direct=True)
    assert plan.recommended is not None
    assert plan.recommended.safety.state is not RouteSafetyState.REJECTED
    payload = plan.to_dict()
    assert "rejected" in payload and "warnings" in payload
    assert all(item["safety"]["eligible"] for item in payload["routes"])
    assert payload["recommended_route_id"] == plan.routes[0].route_id
