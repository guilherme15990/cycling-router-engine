"""Consistencia busca x avaliacao final e estabilidade de ranking (ADR-029 D4/D8).

Cobrem a fronteira entre a Camada 03 (``MultiObjectiveAStar`` + guardrails de
circulacao) e a Camada 05 (avaliacao final):

* toda rota que a busca **devolve** e elegivel sob a avaliacao final (mesmo
  contrato de circulacao usado pela expansao e pelo validador independente);
* a avaliacao final **nao reordena** as rotas elegiveis;
* ``recommended`` e a **primeira elegivel** qualquer que seja a posicao da rota
  inelegivel no ranking — e **nenhuma** rota inelegivel pode ser recomendada;
* sem novas restricoes a fronteira Pareto e a ordenacao permanecem estaveis;
* a avaliacao final e a **autoridade**: uma rota gerada fora da busca, com
  violacao, e excluida mesmo que apareca primeiro no ranking.
"""

from __future__ import annotations

import pytest

from evaluation import (
    RouteSafetyState,
    assess_route,
    assess_search_route,
    classify_safety,
    evaluate_candidates,
)
from graph.model import Arc, BicycleAccess, ElevationSample, Node, RoutingGraph, Segment
from graph.restrictions import AccessRule, TurnRestriction
from physical import (
    BicycleModel,
    CyclistModel,
    SimulationState,
    SimulationStatus,
    TraversalResult,
)
from search import (
    MultiObjectiveAStar,
    RouteCandidate,
    route_arcs,
    route_from_state,
    profile_for,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=800.0, sustainable_power_w=250.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
MASS_KG = 87.0
START = SimulationState.from_speed(5.0)


# --------------------------------------------------------------------------
# Infra da busca (stub deterministico, mesma filosofia de test_search_guardrails)
# --------------------------------------------------------------------------
class StubTraversal:
    def __init__(self, table, mass_kg: float = MASS_KG) -> None:
        self.table = table
        self._mass_kg = mass_kg

    @property
    def mass_kg(self) -> float:
        return self._mass_kg

    def traverse(self, arc: Arc, initial_speed_mps: float) -> TraversalResult:
        spec = self.table[arc.id]
        energy = spec.get("energy_j", 1000.0)
        return TraversalResult(
            time_s=spec.get("time_s", 10.0),
            distance_m=spec.get("distance_m", arc.distance_m),
            ascent_m=spec.get("ascent_m", 0.0),
            descent_m=spec.get("descent_m", 0.0),
            initial_speed_mps=float(initial_speed_mps),
            final_speed_mps=spec.get("final_speed_mps", 5.0),
            mechanical_energy_j=energy,
            rider_energy_j=energy / 0.97,
            arc_id=arc.id,
            result_status=spec.get("result_status", SimulationStatus.VALID),
        )


def make_arc(
    arc_id: str,
    source: int,
    target: int,
    *,
    way_id: int | None = None,
    restrictions=(),
    bicycle_access=BicycleAccess.YES,
    distance_m: float = 100.0,
) -> Arc:
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=distance_m,
        osm_way_id=way_id,
        restrictions=tuple(restrictions),
        bicycle_access=bicycle_access,
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arcs, node_ids, turn_restrictions=()) -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    for arc in arcs:
        graph.add_arc(arc)
    for restriction in turn_restrictions:
        graph.add_turn_restriction(restriction)
    return graph


def rule(value: str) -> AccessRule:
    return AccessRule(scope="bicycle", value=value, condition=None, raw=value)


def search(graph, table, profile="fastest"):
    astar = MultiObjectiveAStar(graph, StubTraversal(table), profile_for(profile))
    return astar.search(1, 3)


def restricted_scenarios():
    """Cenarios com guardrails de circulacao ativos (acesso e conversao)."""
    forbidden = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10, restrictions=(rule("no"),)),
            make_arc("a23", 2, 3, way_id=20),
            make_arc("a13", 1, 3, way_id=30),
        ],
        (1, 2, 3),
    )
    forbidden_table = {
        "a12": {"time_s": 100.0},
        "a23": {"time_s": 100.0},
        "a13": {"time_s": 250.0},
    }
    yield forbidden, forbidden_table

    turn = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10),
            make_arc("a23", 2, 3, way_id=20),
            make_arc("a14", 1, 4, way_id=30),
            make_arc("a43", 4, 3, way_id=40),
        ],
        (1, 2, 3, 4),
        turn_restrictions=(
            TurnRestriction(
                id="r1",
                from_way_id=10,
                to_way_id=20,
                restriction_type="no_left_turn",
                via_node_id=2,
            ),
        ),
    )
    turn_table = {
        "a12": {"time_s": 100.0},
        "a23": {"time_s": 100.0},
        "a14": {"time_s": 120.0},
        "a43": {"time_s": 120.0},
    }
    yield turn, turn_table


# --------------------------------------------------------------------------
# Infra da avaliacao (grafos com elevacao; fisica real do simulador completo)
# --------------------------------------------------------------------------
def assessment_arc(arc_id, source, target, *, restrictions=(), points=((0.0, 0.0), (200.0, 0.0))):
    distance = points[-1][0]
    profile = tuple(ElevationSample(distance_m=d, elevation_m=e) for d, e in points)
    ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
    descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        restrictions=tuple(restrictions),
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def assessment_graph(**a12):
    graph = RoutingGraph()
    for node_id in (1, 2, 3):
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    graph.add_arc(assessment_arc("a12", 1, 2, **a12))
    graph.add_arc(assessment_arc("a23", 2, 3))
    return graph


def clean_assessment():
    return assess_route(
        assessment_graph(), ("a12", "a23"), cyclist=CYCLIST, bicycle=BICYCLE, initial_state=START
    )


def forbidden_assessment():
    return assess_route(
        assessment_graph(restrictions=(rule("no"),)),
        ("a12", "a23"),
        cyclist=CYCLIST,
        bicycle=BICYCLE,
        initial_state=START,
    )


def candidate(route_id):
    from search import Route

    route = Route(
        arc_ids=("a12", "a23"),
        time_s=1.0,
        distance_m=400.0,
        ascent_m=0.0,
        descent_m=0.0,
        energy_j=1000.0,
    )
    return RouteCandidate.from_route(route, route_id=route_id)


# --------------------------------------------------------------------------
# Consistencia busca x avaliacao final
# --------------------------------------------------------------------------
@pytest.mark.parametrize("profile", ("fastest", "shortest", "flattest", "balanced"))
def test_search_returns_only_eligible_routes_under_final_evaluation(profile):
    for graph, table in restricted_scenarios():
        result = search(graph, table, profile=profile)
        assert result.reached
        for label in result.labels:
            route = route_from_state(label)
            assessment = assess_search_route(
                graph, route, cyclist=CYCLIST, bicycle=BICYCLE, initial_state=START
            )
            safety = classify_safety(assessment)
            assert safety.eligible, (profile, route.arc_ids, safety.to_dict())
            assert safety.state is not RouteSafetyState.REJECTED


def test_search_and_final_evaluation_share_the_same_circulation_contract():
    graph, table = next(iter(restricted_scenarios()))
    result = search(graph, table)
    # A rota devolvida e legal sob o validador independente da avaliacao final.
    route = route_from_state(result.labels[0])
    assessment = assess_search_route(
        graph, route, cyclist=CYCLIST, bicycle=BICYCLE, initial_state=START
    )
    assert assessment.circulation.compliant
    assert not assessment.circulation.unresolved
    # O arco proibido nunca aparece numa rota devolvida.
    assert "a12" not in set(route_arcs(result.labels[0]))


# --------------------------------------------------------------------------
# Ordenacao estavel e recomendacao
# --------------------------------------------------------------------------
def test_evaluate_candidates_preserves_eligible_order():
    order = ["r0", "r1", "r2", "r3"]
    report = evaluate_candidates(
        [candidate(route_id) for route_id in order],
        assessments={route_id: clean_assessment() for route_id in order},
    )
    assert [item.route_id for item in report.routes] == order
    assert report.recommended.route_id == "r0"


@pytest.mark.parametrize("rejected_index", [0, 1, 2, 3, 4])
def test_no_ineligible_route_recommended_at_any_ranking_position(rejected_index):
    order = [f"r{i}" for i in range(5)]
    assessments = {
        route_id: (forbidden_assessment() if route_id == order[rejected_index] else clean_assessment())
        for route_id in order
    }
    report = evaluate_candidates([candidate(r) for r in order], assessments=assessments)

    expected = [r for r in order if r != order[rejected_index]]
    assert report.recommended is not None
    assert report.recommended.route_id == expected[0]
    # Invariante forte: nenhuma REJECTED aparece como elegivel nem recomendada.
    assert order[rejected_index] not in {item.route_id for item in report.routes}
    assert report.recommended.route_id != order[rejected_index]
    assert report.rejected_count == 1


def test_all_ineligible_recommends_none():
    order = ["r0", "r1"]
    report = evaluate_candidates(
        [candidate(r) for r in order],
        assessments={r: forbidden_assessment() for r in order},
    )
    assert report.recommended is None
    assert report.routes == ()
    assert report.rejected_count == 2


def test_final_evaluation_is_authoritative_for_generated_routes():
    """Rota gerada fora da busca e violadora: excluida mesmo em 1o no ranking."""
    order = ["generated", "searched"]
    assessments = {"generated": forbidden_assessment(), "searched": clean_assessment()}
    report = evaluate_candidates([candidate(r) for r in order], assessments=assessments)
    assert report.excluded[0].route_id == "generated"
    assert report.excluded[0].safety.state is RouteSafetyState.REJECTED
    assert report.recommended.route_id == "searched"


# --------------------------------------------------------------------------
# Estabilidade sem novas restricoes (fronteira congelada da Camada 03)
# --------------------------------------------------------------------------
S1_GRAPH = make_graph(
    [
        make_arc("a12", 1, 2),
        make_arc("a23", 2, 3),
        make_arc("a14", 1, 4),
        make_arc("a43", 4, 3),
    ],
    (1, 2, 3, 4),
)
S1_TABLE = {
    "a12": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a23": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a14": {"time_s": 150.0, "distance_m": 1500.0, "ascent_m": 0.0, "energy_j": 3000.0, "final_speed_mps": 5.0},
    "a43": {"time_s": 150.0, "distance_m": 1500.0, "ascent_m": 0.0, "energy_j": 3000.0, "final_speed_mps": 5.0},
}
S1_FROZEN = [
    (("a12", "a23"), 200.0, 2000.0, 100.0, 0.0, 4000.0, 5.0),
    (("a14", "a43"), 300.0, 3000.0, 0.0, 0.0, 6000.0, 5.0),
]


def _frontier(result):
    return [
        (
            tuple(route_arcs(label)),
            label.time_s,
            label.distance_m,
            label.ascent_m,
            label.descent_m,
            label.energy_j,
            label.physical_state.speed_mps,
        )
        for label in result.labels
    ]


def test_pareto_frontier_unchanged_without_new_restrictions():
    result = search(S1_GRAPH, S1_TABLE)
    assert _frontier(result) == S1_FROZEN


def test_evaluation_does_not_change_frontier_ordering():
    result = search(S1_GRAPH, S1_TABLE)
    candidates = [RouteCandidate.from_route(route_from_state(label)) for label in result.labels]
    report = evaluate_candidates(
        candidates, assessments={c.route_id: clean_assessment() for c in candidates}
    )
    assert [item.route_id for item in report.routes] == [c.route_id for c in candidates]
    assert report.recommended.route_id == candidates[0].route_id


def test_evaluation_is_deterministic():
    candidates = [candidate("r0"), candidate("r1")]
    assessments = {"r0": clean_assessment(), "r1": clean_assessment()}
    first = evaluate_candidates(candidates, assessments=assessments).to_dict()
    second = evaluate_candidates(candidates, assessments=assessments).to_dict()
    assert first == second
