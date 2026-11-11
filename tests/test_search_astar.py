import math
import pytest
from router.core.data.graph.model import RoutingGraph, Arc, Segment, GraphError, Node
from router.core.physics.physical import (
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    TraversalResult,
)
from router.core.search import (
    UNKNOWN_SPEED,
    Heuristic,
    LexicographicQueue,
    MultiObjectiveAStar,
    Objective,
    PhysicalTraversalModel,
    SearchConfig,
    SearchProfile,
    TraversalModel,
    model_max_speed,
    profile_for,
)

MASS_KG = 87.0


# --------------------------------------------------------------------------
# Infra de teste: grafo pequeno + stub do modelo fisico
# --------------------------------------------------------------------------
def make_arc(
    arc_id: str, source: int, target: int, distance_m: float = 100.0
) -> Arc:
    segment = Segment(id=f"{arc_id}:0", distance_m=distance_m)
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arc_specs, node_ids=(1, 2, 3)) -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.001 * node_id))
    for spec in arc_specs:
        graph.add_arc(make_arc(*spec))
    return graph


class StubTraversal:
    """Modelo fisico deterministico: tabela por ``arc.id``."""

    def __init__(self, table, mass_kg: float = MASS_KG) -> None:
        self.table = table
        self._mass_kg = mass_kg
        self.calls: list[tuple[str, float]] = []

    @property
    def mass_kg(self) -> float:
        return self._mass_kg

    def traverse(self, arc: Arc, initial_speed_mps: float) -> TraversalResult:
        self.calls.append((arc.id, float(initial_speed_mps)))
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
        )


def custom_profile(*objectives) -> SearchProfile:
    return SearchProfile("custom", tuple(objectives))


# --------------------------------------------------------------------------
# Fila lexicografica
# --------------------------------------------------------------------------
def test_lexicographic_queue_orders_and_ties():
    queue = LexicographicQueue()
    assert queue.is_empty
    assert queue.peek_priority() is None

    queue.push("a", (1.0, 0.0))
    queue.push("b", (0.5, 9.0))
    queue.push("c", (0.5, 1.0))

    assert queue.peek_priority() == (0.5, 1.0)
    assert queue.pop()[0] == "c"
    assert queue.pop()[0] == "b"
    assert queue.pop()[0] == "a"
    assert queue.is_empty

    with pytest.raises(IndexError):
        queue.pop()


def test_stub_and_physical_model_satisfy_protocol():
    assert isinstance(StubTraversal({}), TraversalModel)


# --------------------------------------------------------------------------
# Heuristica com fallback
# --------------------------------------------------------------------------
def test_heuristic_lower_bounds_fallback_zero():
    from router.core.search import Heuristic

    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    heuristic = Heuristic(graph, max_speed_mps=10.0)

    bounds = heuristic.lower_bounds(1, 2, ("time_s", "energy_j", "descent_m"))
    assert bounds[0] == pytest.approx(heuristic.bounds(1, 2).time_s)
    assert bounds[1] == 0.0
    assert bounds[2] == 0.0

    # ``values`` permanece estrito: objetivo sem limite conhecido -> erro.
    with pytest.raises(ValueError):
        heuristic.values(1, 2, ("energy_j",))


# --------------------------------------------------------------------------
# Fronteira com trade-off e poda de dominada
# --------------------------------------------------------------------------
def test_astar_keeps_non_dominated_tradeoff():
    graph = make_graph(
        [("direct", 1, 3), ("b1", 1, 2), ("b2", 2, 3)]
    )
    traversal = StubTraversal(
        {
            "direct": {"time_s": 100.0, "distance_m": 1200.0},
            "b1": {"time_s": 90.0, "distance_m": 400.0},
            "b2": {"time_s": 50.0, "distance_m": 500.0},
        }
    )
    profile = custom_profile(Objective.TIME, Objective.DISTANCE)
    astar = MultiObjectiveAStar(graph, traversal, profile)

    result = astar.search(1, 3)

    assert result.reached
    assert result.count == 2
    assert not result.truncated
    # Ordenado por (tempo, distancia).
    assert result.labels[0].time_s == pytest.approx(100.0)
    assert result.labels[1].time_s == pytest.approx(140.0)
    assert result.arc_sequences() == (("direct",), ("b1", "b2"))


def test_astar_prunes_dominated_label():
    graph = make_graph([("direct", 1, 3), ("b1", 1, 2), ("b2", 2, 3)])
    traversal = StubTraversal(
        {
            "direct": {"time_s": 100.0, "distance_m": 900.0},
            "b1": {"time_s": 60.0, "distance_m": 500.0},
            "b2": {"time_s": 60.0, "distance_m": 500.0},
        }
    )
    profile = custom_profile(Objective.TIME, Objective.DISTANCE)
    astar = MultiObjectiveAStar(graph, traversal, profile)

    result = astar.search(1, 3)

    assert result.count == 1
    winner = result.labels[0]
    assert winner.time_s == pytest.approx(100.0)
    assert winner.distance_m == pytest.approx(900.0)
    assert result.arc_sequences() == (("direct",),)


def test_astar_propagates_final_speed_between_arcs():
    graph = make_graph([("e0", 1, 2), ("e1", 2, 3)])
    traversal = StubTraversal(
        {
            "e0": {"time_s": 10.0, "final_speed_mps": 4.0},
            "e1": {"time_s": 10.0, "final_speed_mps": 4.0},
        }
    )
    astar = MultiObjectiveAStar(graph, traversal, "fastest")

    result = astar.search(1, 3)

    assert result.reached
    assert traversal.calls == [("e0", 0.0), ("e1", 4.0)]
    assert result.labels[0].speed_mps == pytest.approx(4.0)


def test_astar_skips_non_finite_traversal():
    graph = make_graph([("bad", 1, 2), ("good", 1, 3)])
    traversal = StubTraversal(
        {
            "bad": {"time_s": math.inf},
            "good": {"time_s": 12.0, "distance_m": 700.0},
        }
    )
    astar = MultiObjectiveAStar(graph, traversal, "fastest")

    result = astar.search(1, 3)

    assert result.reached
    assert result.arc_sequences() == (("good",),)


# --------------------------------------------------------------------------
# Criterio de parada e limites
# --------------------------------------------------------------------------
def test_astar_origin_equals_destination():
    graph = make_graph([("e0", 1, 2), ("e1", 2, 3)])
    traversal = StubTraversal({"e0": {}, "e1": {}})
    astar = MultiObjectiveAStar(graph, traversal, "fastest")

    result = astar.search(1, 1)

    assert result.count == 1
    assert result.labels[0].node_id == 1
    assert result.labels[0].time_s == 0.0
    assert result.arc_sequences() == ((),)


def test_astar_max_expansions_truncates():
    graph = make_graph([("e0", 1, 2), ("e1", 2, 3)])
    traversal = StubTraversal({"e0": {}, "e1": {}})

    limited = MultiObjectiveAStar(
        graph,
        traversal,
        "fastest",
        config=SearchConfig(max_expansions=1),
    )
    result = limited.search(1, 3)

    assert result.truncated is True
    assert not result.reached
    assert result.expanded == 1
    # Interrupcao por expansao nao e poda de fronteira.
    assert (result.frontier_truncated, result.dropped_labels) == (False, 0)

    enough = MultiObjectiveAStar(
        graph,
        traversal,
        "fastest",
        config=SearchConfig(max_expansions=2),
    )
    ok = enough.search(1, 3)

    assert ok.truncated is False
    assert ok.reached
    assert ok.expanded == 2
    assert (ok.frontier_truncated, ok.dropped_labels) == (False, 0)


def test_astar_frontier_and_expansion_caps_are_independent_signals():
    """``max_expansions`` e ``max_labels_per_key`` sao indicadores independentes.

    Um grafo com dois arcos paralelos ``1->2`` em **trade-off** (mesmo bucket)
    permite que a poda de fronteira atue; uma cadeia longa permite que o limite
    de expansao atue. Cada config liga **so** o seu proprio sinal, e a
    combinacao liga os dois.
    """
    graph = make_graph(
        [("a12a", 1, 2), ("a12b", 1, 2), ("a23", 2, 3), ("a34", 3, 4)],
        node_ids=(1, 2, 3, 4),
    )
    table = {
        "a12a": {"time_s": 90.0, "ascent_m": 100.0},
        "a12b": {"time_s": 100.0, "ascent_m": 50.0},
        "a23": {"time_s": 100.0},
        "a34": {"time_s": 100.0},
    }

    def run(**cfg):
        astar = MultiObjectiveAStar(
            graph, StubTraversal(table), "fastest", config=SearchConfig(**cfg)
        )
        return astar.search(1, 4)

    # Só a poda de fronteira: a busca continua completa.
    only_frontier = run(max_labels_per_node=1)
    assert only_frontier.frontier_truncated is True
    assert only_frontier.dropped_labels >= 1
    assert only_frontier.truncated is False

    # Só o limite de expansao: a fronteira nao e podada.
    only_expansions = run(max_expansions=1)
    assert only_expansions.truncated is True
    assert (only_expansions.frontier_truncated, only_expansions.dropped_labels) == (
        False,
        0,
    )

    # Ambos ligados: os dois sinais aparecem juntos e permanecem distinguiveis.
    both = run(max_expansions=1, max_labels_per_node=1)
    assert both.truncated is True
    assert both.frontier_truncated is True
    assert both.dropped_labels >= 1


def test_astar_validation():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    traversal = StubTraversal({"e0": {}})

    with pytest.raises(ValueError):
        MultiObjectiveAStar(graph, traversal, "turbo")
    with pytest.raises(ValueError):
        MultiObjectiveAStar(graph, traversal, "fastest", initial_speed_mps=-1.0)

    astar = MultiObjectiveAStar(graph, traversal, "fastest")
    with pytest.raises(GraphError):
        astar.search(1, 99)


# --------------------------------------------------------------------------
# Integracao com o modelo fisico real
# --------------------------------------------------------------------------
def test_astar_with_physical_traversal_model():
    graph = make_graph(
        [("e0", 1, 2, 120.0), ("e1", 2, 3, 120.0)]
    )
    cyclist = CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )
    bicycle = BicycleModel()
    model = PhysicalTraversalModel(
        PhysicalTraversal(step_m=5.0), cyclist, bicycle
    )
    assert model.mass_kg == pytest.approx(cyclist.mass_kg + bicycle.mass_kg)

    from router.core.search import Heuristic

    astar = MultiObjectiveAStar(
        graph,
        model,
        profile_for("fastest"),
        heuristic=Heuristic(graph, max_speed_mps=None),
    )
    result = astar.search(1, 3)

    assert result.reached
    assert result.count >= 1
    label = result.labels[0]
    assert label.time_s > 0.0
    assert label.distance_m == pytest.approx(240.0)
    assert label.energy_j > 0.0
    assert label.speed_mps > 0.0
    assert result.arc_sequences()[0] == ("e0", "e1")
    assert result.to_dict()["profile"] == "fastest"


# --------------------------------------------------------------------------
# Teto de velocidade do modelo x heuristica admissivel
# --------------------------------------------------------------------------
def _physical_model(environment: Environment | None = None):
    cyclist = CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )
    return PhysicalTraversalModel(
        PhysicalTraversal(step_m=5.0), cyclist, BicycleModel(), environment
    )


def test_physical_model_announces_environment_speed_ceiling():
    assert _physical_model(Environment(safe_max_speed_mps=30.0)).max_speed_mps == 30.0
    # Sem ambiente, o modelo fisico usa ``Environment()`` por padrao.
    assert _physical_model().max_speed_mps == pytest.approx(22.0)
    # Ambiente ilimitado -> teto ``None``.
    assert _physical_model(Environment(safe_max_speed_mps=None)).max_speed_mps is None


def test_model_max_speed_reads_optional_attribute():
    assert model_max_speed(_physical_model(Environment(safe_max_speed_mps=18.0))) == 18.0
    # Modelo ilimitado declarado -> None (distinto de desconhecido).
    assert model_max_speed(_physical_model(Environment(safe_max_speed_mps=None))) is None
    # Modelos sem o atributo -> teto desconhecido (sentinela).
    assert model_max_speed(StubTraversal({})) is UNKNOWN_SPEED


def test_default_heuristic_uses_model_speed_ceiling():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    model = _physical_model(Environment(safe_max_speed_mps=15.0))

    astar = MultiObjectiveAStar(graph, model, profile_for("fastest"))

    assert astar.heuristic.max_speed_mps == 15.0


def test_default_heuristic_is_admissible_without_announced_ceiling():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    # Stub nao anuncia teto -> limite de tempo 0 (sempre admissivel).
    astar = MultiObjectiveAStar(graph, StubTraversal({"e0": {}}), "fastest")

    assert astar.heuristic.max_speed_mps is None


def test_inadmissible_explicit_heuristic_is_rejected():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    model = _physical_model(Environment(safe_max_speed_mps=22.0))

    with pytest.raises(ValueError):
        MultiObjectiveAStar(
            graph,
            model,
            profile_for("fastest"),
            heuristic=Heuristic(graph, max_speed_mps=10.0),
        )


def test_admissible_explicit_heuristic_is_accepted():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    model = _physical_model(Environment(safe_max_speed_mps=22.0))

    # Teto igual ao do modelo: admissivel.
    equal = MultiObjectiveAStar(
        graph,
        model,
        profile_for("fastest"),
        heuristic=Heuristic(graph, max_speed_mps=22.0),
    )
    assert equal.heuristic.max_speed_mps == 22.0

    # Teto maior: admissivel (conservador).
    higher = MultiObjectiveAStar(
        graph,
        model,
        profile_for("fastest"),
        heuristic=Heuristic(graph, max_speed_mps=40.0),
    )
    assert higher.heuristic.max_speed_mps == 40.0

    # ``None``: sem limite de tempo, sempre admissivel.
    disabled = MultiObjectiveAStar(
        graph,
        model,
        profile_for("fastest"),
        heuristic=Heuristic(graph, max_speed_mps=None),
    )
    assert disabled.heuristic.max_speed_mps is None


def test_declared_unlimited_model_defaults_to_no_time_bound():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    model = _physical_model(Environment(safe_max_speed_mps=None))

    astar = MultiObjectiveAStar(graph, model, profile_for("fastest"))

    assert astar.heuristic.max_speed_mps is None


def test_finite_heuristic_rejected_when_model_declares_unlimited():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    model = _physical_model(Environment(safe_max_speed_mps=None))

    with pytest.raises(ValueError):
        MultiObjectiveAStar(
            graph,
            model,
            profile_for("fastest"),
            heuristic=Heuristic(graph, max_speed_mps=22.0),
        )


def test_finite_heuristic_allowed_when_model_ceiling_unknown():
    graph = make_graph([("e0", 1, 2)], node_ids=(1, 2))
    # Stub nao anuncia teto: nao podemos punir a escolha do usuario.
    astar = MultiObjectiveAStar(
        graph,
        StubTraversal({"e0": {}}),
        profile_for("fastest"),
        heuristic=Heuristic(graph, max_speed_mps=10.0),
    )

    assert astar.heuristic.max_speed_mps == 10.0
