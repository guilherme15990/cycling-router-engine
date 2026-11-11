"""Testes comportamentais dos guardrails de circulacao na busca (ADR-026).

Cobrem a etapa comportamental: ligacao do contrato na expansao do
``MultiObjectiveAStar`` (§4), a seguranca da dominancia (§5) e a propagacao de
qualidade/limitacao ate a candidata (§6). A inercia (zero restricoes) e coberta
por ``tests/test_search_reference.py`` (fronteira S1-S7 inalterada) e reforcada
aqui.

Ref.: ``docs/adr/026-contrato-circulacao.md`` §4-§6.
"""

import pytest
from router.core.data.graph.model import Arc, BicycleAccess, Node, RoutingGraph, Segment
from router.core.data.graph.restrictions import AccessRule, TurnRestriction
from router.core.physics.physical import ElevationUncertainty, SimulationStatus, TraversalResult
from router.core.search import (
    MultiObjectiveAStar,
    PhysicalState,
    RouteCandidate,
    SearchConfig,
    SearchState,
    SpeedBucketDominance,
    constrained_nodes,
    profile_for,
    route_arcs,
    route_from_state,
    validate_route,
)

MASS_KG = 87.0


# --------------------------------------------------------------------------
# Infra deterministica
# --------------------------------------------------------------------------
def make_arc(
    arc_id: str,
    source: int,
    target: int,
    *,
    way_id: int | None = None,
    restrictions: tuple[AccessRule, ...] = (),
    bicycle_access: BicycleAccess = BicycleAccess.YES,
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


class StubTraversal:
    """Modelo fisico tabelado por ``arc.id`` (deterministico)."""

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
            stalled=spec.get("stalled", False),
            result_status=spec.get("result_status", SimulationStatus.VALID),
            used_min_speed_regularization=spec.get(
                "used_min_speed_regularization", False
            ),
            elevation_uncertainty=spec.get("elevation_uncertainty"),
        )


def search(graph, table, profile="fastest", **cfg):
    config = SearchConfig(**cfg) if cfg else None
    astar = MultiObjectiveAStar(
        graph, StubTraversal(table), profile_for(profile), config=config
    )
    return astar.search(1, 3)


def rule(scope: str, value: str) -> AccessRule:
    return AccessRule(scope=scope, value=value, condition=None, raw=value)


def _arrival_label(*, node: int, incoming: str, speed_mps: float):
    return SearchState(
        node_id=node,
        physical_state=PhysicalState.from_speed(MASS_KG, speed_mps),
        incoming_arc_id=incoming,
    )


# --------------------------------------------------------------------------
# Inercia — o guardrail nao altera nada sem restricoes
# --------------------------------------------------------------------------
def test_guardrails_inactive_without_restrictions():
    graph = make_graph(
        [make_arc("a12", 1, 2, way_id=10), make_arc("a23", 2, 3, way_id=20)],
        (1, 2, 3),
    )
    astar = MultiObjectiveAStar(graph, StubTraversal({}), profile_for("fastest"))
    assert astar._guardrails_active is False
    assert constrained_nodes(graph) == frozenset()


def test_dismount_is_not_a_restriction_and_is_used():
    graph = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10, bicycle_access=BicycleAccess.DISMOUNT),
            make_arc("a23", 2, 3, way_id=20),
        ],
        (1, 2, 3),
    )
    table = {"a12": {"time_s": 100.0}, "a23": {"time_s": 100.0}}
    result = search(graph, table)
    assert result.reached
    assert tuple(route_arcs(result.labels[0])) == ("a12", "a23")


def test_dominance_key_unchanged_without_constrained_nodes():
    # Guardrail inerte: sem nos restritos, a chave volta a ser ``(no, bucket)``.
    label = _arrival_label(node=2, incoming="a12x", speed_mps=5.0)
    dominance = SpeedBucketDominance(profile_for("fastest").objective_set)
    assert dominance.key(label) == (2, 10)


def test_dominance_key_includes_arrival_class_at_constrained_node():
    # No restrito: a chave diferencia chegadas por arcos diferentes.
    label_x = _arrival_label(node=2, incoming="a12x", speed_mps=5.0)
    label_y = _arrival_label(node=2, incoming="a12y", speed_mps=5.0)
    dominance = SpeedBucketDominance(
        profile_for("fastest").objective_set, arrival_class_nodes={2}
    )
    assert dominance.key(label_x) == (2, 10, "a12x")
    assert dominance.key(label_y) == (2, 10, "a12y")
    # Sem classe de chegada, chegar ao mesmo no/bucket seria "o mesmo contexto".
    assert dominance.key(label_x) != dominance.key(label_y)


# --------------------------------------------------------------------------
# Guardrail de acesso (G1)
# --------------------------------------------------------------------------
def forbidden_arc_graph():
    # Via 2 e mais rapida que o arco direto; com a12 proibido, sobra o direto.
    arcs = [
        make_arc(
            "a12", 1, 2, way_id=10, restrictions=(rule("bicycle", "no"),)
        ),
        make_arc("a23", 2, 3, way_id=20),
        make_arc("a13", 1, 3, way_id=30),
    ]
    graph = make_graph(arcs, (1, 2, 3))
    table = {
        "a12": {"time_s": 100.0, "ascent_m": 0.0},
        "a23": {"time_s": 100.0, "ascent_m": 0.0},
        "a13": {"time_s": 250.0, "ascent_m": 0.0},
    }
    return graph, table


def test_forbidden_arc_is_avoided():
    graph, table = forbidden_arc_graph()

    # Sem restricao, a via 2 (t=200) vence o arco direto (t=250).
    open_graph = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10),
            make_arc("a23", 2, 3, way_id=20),
            make_arc("a13", 1, 3, way_id=30),
        ],
        (1, 2, 3),
    )
    assert tuple(route_arcs(search(open_graph, table).labels[0])) == ("a12", "a23")

    # Com bicycle=no em a12, sobra o arco direto.
    result = search(graph, table)
    assert result.reached
    assert tuple(route_arcs(result.labels[0])) == ("a13",)


# --------------------------------------------------------------------------
# Guardrail de conversao (G2) — via_node
# --------------------------------------------------------------------------
def test_no_left_turn_is_respected():
    arcs = [
        make_arc("a12", 1, 2, way_id=10),
        make_arc("a23", 2, 3, way_id=20),
        make_arc("a14", 1, 4, way_id=30),
        make_arc("a43", 4, 3, way_id=40),
    ]
    table = {
        "a12": {"time_s": 100.0},
        "a23": {"time_s": 100.0},
        "a14": {"time_s": 120.0},
        "a43": {"time_s": 120.0},
    }
    restriction = TurnRestriction(
        id="r1",
        from_way_id=10,
        to_way_id=20,
        restriction_type="no_left_turn",
        via_node_id=2,
    )

    open_graph = make_graph(arcs, (1, 2, 3, 4))
    assert tuple(route_arcs(search(open_graph, table).labels[0])) == ("a12", "a23")

    blocked = make_graph(arcs, (1, 2, 3, 4), turn_restrictions=(restriction,))
    result = search(blocked, table)
    assert result.reached
    assert tuple(route_arcs(result.labels[0])) == ("a14", "a43")


def test_only_straight_on_is_respected():
    arcs = [
        make_arc("a12", 1, 2, way_id=10),
        make_arc("a23", 2, 3, way_id=20),
        make_arc("a24", 2, 4, way_id=30),
        make_arc("a43", 4, 3, way_id=40),
    ]
    table = {
        "a12": {"time_s": 100.0},
        "a23": {"time_s": 100.0},
        "a24": {"time_s": 10.0},
        "a43": {"time_s": 10.0},
    }
    restriction = TurnRestriction(
        id="r2",
        from_way_id=10,
        to_way_id=20,
        restriction_type="only_straight_on",
        via_node_id=2,
    )

    open_graph = make_graph(arcs, (1, 2, 3, 4))
    assert tuple(route_arcs(search(open_graph, table).labels[0])) == (
        "a12",
        "a24",
        "a43",
    )

    blocked = make_graph(arcs, (1, 2, 3, 4), turn_restrictions=(restriction,))
    result = search(blocked, table)
    assert result.reached
    assert tuple(route_arcs(result.labels[0])) == ("a12", "a23")


# --------------------------------------------------------------------------
# Seguranca da dominancia (§5) — a classe de chegada preserva a rota legal
# --------------------------------------------------------------------------
def test_dominance_keeps_arrival_with_distinct_legal_context():
    # Duas chegadas ao no 2 (a12x melhor que a12y) e uma saida a23w.
    # A conversao a12x->a23w e proibida; a12y->a23w e a UNICA legal.
    # Sem a classe de chegada, a12y seria dominada por a12x e a rota se perderia.
    arcs = [
        make_arc("a12x", 1, 2, way_id=10),
        make_arc("a12y", 1, 2, way_id=11),
        make_arc("a23w", 2, 3, way_id=20),
    ]
    table = {
        "a12x": {"time_s": 100.0},
        "a12y": {"time_s": 110.0},
        "a23w": {"time_s": 10.0},
    }
    restriction = TurnRestriction(
        id="r3",
        from_way_id=10,
        to_way_id=20,
        restriction_type="no_left_turn",
        via_node_id=2,
    )
    graph = make_graph(arcs, (1, 2, 3), turn_restrictions=(restriction,))
    assert constrained_nodes(graph) == frozenset({2})

    result = search(graph, table)
    assert result.reached
    assert tuple(route_arcs(result.labels[0])) == ("a12y", "a23w")


# --------------------------------------------------------------------------
# Propriedade — nenhuma rota retornada viola a circulacao
# --------------------------------------------------------------------------
def restricted_scenarios():
    forbidden = forbidden_arc_graph()
    yield forbidden

    left_arcs = [
        make_arc("a12", 1, 2, way_id=10),
        make_arc("a23", 2, 3, way_id=20),
        make_arc("a14", 1, 4, way_id=30),
        make_arc("a43", 4, 3, way_id=40),
    ]
    left_table = {
        "a12": {"time_s": 100.0},
        "a23": {"time_s": 100.0},
        "a14": {"time_s": 120.0},
        "a43": {"time_s": 120.0},
    }
    yield (
        make_graph(
            left_arcs,
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
        ),
        left_table,
    )


@pytest.mark.parametrize("profile", ("fastest", "shortest", "flattest", "balanced"))
def test_no_returned_route_violates_circulation(profile):
    for graph, table in restricted_scenarios():
        result = search(graph, table, profile=profile)
        for label in result.labels:
            validation = validate_route(graph, tuple(route_arcs(label)))
            assert validation.compliant, validation.to_dict()


# --------------------------------------------------------------------------
# Propagacao de qualidade e limitacao (§6)
# --------------------------------------------------------------------------
def two_arc_graph(**a12):
    graph = make_graph(
        [make_arc("a12", 1, 2, way_id=10), make_arc("a23", 2, 3, way_id=20)],
        (1, 2, 3),
    )
    table = {
        "a12": {"time_s": 100.0, **a12},
        "a23": {"time_s": 100.0},
    }
    return graph, table


def test_result_status_propagates_as_worst_case():
    graph, table = two_arc_graph(result_status=SimulationStatus.DEGRADED)
    result = search(graph, table)
    label = result.labels[0]
    assert label.result_status is SimulationStatus.DEGRADED

    route = route_from_state(label)
    assert route.result_status is SimulationStatus.DEGRADED
    assert route.is_degraded
    assert RouteCandidate.from_route(route).result_status is SimulationStatus.DEGRADED


def test_boolean_limitations_propagate_as_or():
    graph, table = two_arc_graph(
        stalled=True, used_min_speed_regularization=True
    )
    result = search(graph, table)
    route = route_from_state(result.labels[0])
    assert route.stalled
    assert route.used_min_speed_regularization
    candidate = RouteCandidate.from_route(route)
    assert candidate.stalled
    assert candidate.used_min_speed_regularization


def test_dismount_used_propagates_from_arc():
    graph = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10, bicycle_access=BicycleAccess.DISMOUNT),
            make_arc("a23", 2, 3, way_id=20),
        ],
        (1, 2, 3),
    )
    table = {"a12": {"time_s": 100.0}, "a23": {"time_s": 100.0}}
    route = route_from_state(search(graph, table).labels[0])
    assert route.dismount_used
    assert RouteCandidate.from_route(route).dismount_used


def test_elevation_uncertainty_combines_along_route():
    unsupported = ElevationUncertainty.unsupported(100.0)
    observed = ElevationUncertainty.fully_observed(100.0)
    graph = make_graph(
        [make_arc("a12", 1, 2, way_id=10), make_arc("a23", 2, 3, way_id=20)],
        (1, 2, 3),
    )
    table = {
        "a12": {"time_s": 100.0, "elevation_uncertainty": unsupported},
        "a23": {"time_s": 100.0, "elevation_uncertainty": observed},
    }
    route = route_from_state(search(graph, table).labels[0])
    combined = route.elevation_uncertainty
    assert combined is not None
    assert combined.total_distance_m == pytest.approx(200.0)
    assert combined.missing_distance_m == pytest.approx(100.0)
    assert combined.observed_distance_m == pytest.approx(100.0)
