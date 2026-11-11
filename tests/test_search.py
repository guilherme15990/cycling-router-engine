from dataclasses import dataclass

import pytest

from graph.model import RoutingGraph, Node
from physical import RouteMetrics
from search import (
    PROFILE_NAMES,
    PROFILE_OBJECTIVES,
    FrontierMap,
    Heuristic,
    ObjectiveSet,
    ParetoFrontier,
    SearchConfig,
    dominates,
    epsilon_dominates,
    objectives_for,
)


@dataclass
class Label:
    """Label minima para exercitar a fronteira (velocidade fica para depois)."""

    time_s: float
    distance_m: float
    ascent_m: float
    active: bool = True


def make_graph() -> RoutingGraph:
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0, elevation_m=100.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001, elevation_m=130.0))
    graph.add_node(Node(id=3, lat=0.0, lon=0.002, elevation_m=None))
    return graph


# --------------------------------------------------------------------------
# Objetivos
# --------------------------------------------------------------------------
def test_objective_set_validation():
    with pytest.raises(ValueError):
        ObjectiveSet("", ("time_s",))
    with pytest.raises(ValueError):
        ObjectiveSet("x", ())
    with pytest.raises(ValueError):
        ObjectiveSet("x", ("time_s", "time_s"))


def test_objective_set_values_from_mapping_and_object():
    objectives = ObjectiveSet("t", ("time_s", "distance_m"))

    assert objectives.values({"time_s": 10, "distance_m": 5.0}) == (10.0, 5.0)

    metrics = RouteMetrics(time_s=12.0, distance_m=7.5, ascent_m=1.0)
    assert objectives.values(metrics) == (12.0, 7.5)
    assert len(objectives) == 2
    assert list(objectives) == ["time_s", "distance_m"]


def test_dominates_semantics():
    assert dominates((1.0, 2.0), (2.0, 3.0))
    assert dominates((1.0, 2.0), (2.0, 2.0))
    assert not dominates((1.0, 2.0), (1.0, 2.0))  # igualdade nao domina
    assert not dominates((1.0, 3.0), (2.0, 2.0))  # troca de vantagem
    assert not dominates((2.0, 3.0), (1.0, 2.0))  # pior em tudo

    with pytest.raises(ValueError):
        dominates((1.0,), (1.0, 2.0))


def test_epsilon_dominance_is_tolerant():
    # Dominancia estrita independe da tolerancia.
    assert dominates((1.0,), (1.05,)) is True

    # Diferenca dentro do epsilon: nao domina.
    assert epsilon_dominates((1.0,), (1.005,), 0.01) is False
    # Diferenca acima do epsilon: domina.
    assert epsilon_dominates((1.0,), (1.05,), 0.01) is True

    # epsilon = 0 recai na dominancia estrita.
    assert epsilon_dominates((1.0,), (2.0,), 0.0) is True
    with pytest.raises(ValueError):
        epsilon_dominates((1.0,), (2.0,), -0.1)


def test_profile_objectives_are_distinct_and_lookup_normalizes():
    assert set(PROFILE_NAMES) == {
        "fastest",
        "shortest",
        "flattest",
        "balanced",
        "electric",
    }
    for name, objectives in PROFILE_OBJECTIVES.items():
        assert objectives.name == name
        assert len(objectives) >= 2

    # Fastest e Shortest precisam de conjuntos diferentes, senao a fronteira
    # matematica seria a mesma.
    assert (
        PROFILE_OBJECTIVES["fastest"].objectives
        != PROFILE_OBJECTIVES["shortest"].objectives
    )

    assert objectives_for(" Fastest ").name == "fastest"
    with pytest.raises(ValueError):
        objectives_for("turbo")


# --------------------------------------------------------------------------
# Fronteira de Pareto
# --------------------------------------------------------------------------
def test_frontier_keeps_non_dominated():
    objectives = ObjectiveSet("t", ("time_s", "distance_m", "ascent_m"))
    frontier = ParetoFrontier(objectives)

    a = Label(100.0, 1000.0, 20.0)
    b = Label(105.0, 950.0, 25.0)

    assert frontier.insert(a) is True
    assert frontier.insert(b) is True
    assert len(frontier) == 2
    assert a.active and b.active


def test_frontier_rejects_and_discards_dominated():
    objectives = ObjectiveSet("t", ("time_s", "distance_m", "ascent_m"))
    frontier = ParetoFrontier(objectives)

    better = Label(100.0, 1000.0, 20.0)
    worse = Label(102.0, 1010.0, 21.0)

    assert frontier.insert(better) is True
    assert frontier.insert(worse) is False
    assert len(frontier) == 1
    assert frontier.items == (better,)
    assert worse.active is False  # label descartada fica inativa


def test_frontier_replaces_dominated_existing():
    objectives = ObjectiveSet("t", ("time_s", "distance_m", "ascent_m"))
    frontier = ParetoFrontier(objectives)

    old = Label(110.0, 1000.0, 25.0)
    new = Label(100.0, 900.0, 20.0)

    frontier.insert(old)
    assert frontier.insert(new) is True
    assert frontier.items == (new,)
    assert old.active is False
    assert new.active is True


def test_frontier_dominated_by_probe():
    objectives = ObjectiveSet("t", ("time_s", "distance_m"))
    frontier = ParetoFrontier(objectives)
    frontier.insert(Label(100.0, 500.0, 0.0))

    assert frontier.dominated_by(Label(120.0, 600.0, 0.0)) is True
    assert frontier.dominated_by(Label(100.0, 400.0, 0.0)) is False


def test_frontier_custom_key():
    objectives = ObjectiveSet("t", ("0", "1"))  # nomes so rotulam a ordem
    frontier = ParetoFrontier(
        objectives, key=lambda item: (item["time"], item["dist"])
    )

    frontier.insert({"time": 1.0, "dist": 5.0})
    frontier.insert({"time": 2.0, "dist": 4.0})
    assert len(frontier) == 2
    frontier.insert({"time": 3.0, "dist": 6.0})
    assert len(frontier) == 2


def test_frontier_truncates_by_max_size():
    objectives = ObjectiveSet("t", ("time_s", "distance_m"))
    frontier = ParetoFrontier(objectives, max_size=2)

    kept_a = Label(1.0, 3.0, 0.0)
    kept_b = Label(2.0, 2.0, 0.0)
    dropped = Label(3.0, 1.0, 0.0)

    for label in (kept_a, kept_b, dropped):
        assert frontier.insert(label) is True

    assert len(frontier) == 2
    assert dropped.active is False


def test_frontier_map_separates_keys():
    objectives = ObjectiveSet("t", ("time_s", "distance_m"))
    frontiers = FrontierMap(objectives)

    assert frontiers.insert("n1", Label(10.0, 100.0, 0.0)) is True
    assert frontiers.insert("n1", Label(11.0, 110.0, 0.0)) is False
    assert frontiers.insert("n2", Label(20.0, 200.0, 0.0)) is True

    assert len(frontiers) == 2
    assert frontiers.total_items == 2
    assert len(frontiers.items("n1")) == 1
    assert len(frontiers.items("n2")) == 1
    assert len(frontiers.items("missing")) == 0
    assert "n1" in frontiers

    payload = frontiers.to_dict()
    assert payload["nodes"] == 2
    assert payload["labels"] == 2


def test_frontier_validation():
    objectives = ObjectiveSet("t", ("time_s",))
    with pytest.raises(ValueError):
        ParetoFrontier(objectives, epsilon=-1.0)
    with pytest.raises(ValueError):
        ParetoFrontier(objectives, max_size=0)


# --------------------------------------------------------------------------
# Heuristica
# --------------------------------------------------------------------------
def test_heuristic_bounds_are_admissible_lower_bounds():
    graph = make_graph()
    heuristic = Heuristic(graph, max_speed_mps=22.0)

    bounds = heuristic.bounds(1, 2)
    assert bounds.distance_m == pytest.approx(111.19, abs=0.5)
    assert bounds.time_s == pytest.approx(bounds.distance_m / 22.0)
    assert bounds.ascent_m == pytest.approx(30.0)
    assert bounds.to_dict()["distance_m"] == bounds.distance_m


def test_heuristic_without_elevation_or_speed_limit():
    graph = make_graph()

    # Sem teto de velocidade, o limite de tempo e 0 (sempre admissivel).
    fast = Heuristic(graph, max_speed_mps=None)
    assert fast.bounds(1, 2).time_s == 0.0

    # Sem elevacao nos nos, o limite de subida e 0.
    assert fast.bounds(3, 2).ascent_m == 0.0


def test_heuristic_values_follow_objectives():
    graph = make_graph()
    heuristic = Heuristic(graph, max_speed_mps=10.0)

    values = heuristic.values(1, 2, ("time_s", "ascent_m"))
    assert values[1] == pytest.approx(30.0)

    with pytest.raises(ValueError):
        heuristic.values(1, 2, ("energy_j",))


def test_heuristic_validation():
    graph = make_graph()
    with pytest.raises(ValueError):
        Heuristic(graph, max_speed_mps=0.0)
    with pytest.raises(ValueError):
        Heuristic(graph, max_speed_mps=-5.0)


# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------
def test_search_config_defaults_and_validation():
    config = SearchConfig()
    assert config.max_labels_per_node is None
    assert config.speed_step_mps == 0.5
    assert config.epsilon_dominance == 0.0
    assert config.to_dict()["max_expansions"] is None

    with pytest.raises(ValueError):
        SearchConfig(speed_step_mps=0.0)
    with pytest.raises(ValueError):
        SearchConfig(epsilon_dominance=-0.1)
    with pytest.raises(ValueError):
        SearchConfig(max_labels_per_node=0)
    with pytest.raises(ValueError):
        SearchConfig(max_expansions=-1)
