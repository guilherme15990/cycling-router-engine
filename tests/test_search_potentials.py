"""Physical search optimizations must preserve objective fronts and speed."""
from dataclasses import replace
import math

import pytest

from router.core.search.astar import MultiObjectiveAStar
from router.core.search.config import SearchConfig
from router.core.search.heuristic import Heuristic
from router.core.search.potentials import ReverseCostHeuristic
from router.core.search.traversal_model import PhysicalTraversalModel
from router.core.physics import PhysicalTraversal, CyclistModel, BicycleModel, Environment
from router.core.physics.models import SimulationStatus
from router.core.data.graph.model import Arc, Segment, RoutingGraph, Node, ElevationSample
from test_search_astar import make_graph, StubTraversal


class ZeroHeuristic(Heuristic):
    def lower_bounds(self, node_id, goal_id, objectives):
        return (0.0,) * len(objectives)


class DynamicModel(StubTraversal):
    max_speed_mps = 30.0

    def arc_lower_bounds(self, arc):
        return {"distance_m": arc.distance_m, "ascent_m": 0.0, "time_s": 0.0}

    def traverse(self, arc, initial_speed_mps):
        result = super().traverse(arc, initial_speed_mps)
        if arc.id == "finish":
            # The topographically cheaper arrival cannot traverse the exit.
            return replace(result, time_s=math.inf if initial_speed_mps < 3 else 4.0)
        return result


def objective_front(result):
    values = {result.profile.values(label) for label in result.labels}
    return {v for v in values if not any(
        all(a <= b for a, b in zip(other, v)) and any(a < b for a, b in zip(other, v))
        for other in values
    )}


@pytest.mark.parametrize("profile", ["fastest", "shortest", "flattest", "balanced", "electric"])
def test_potentials_and_pruning_preserve_reference_physical_front(profile):
    graph = make_graph([
        ("slow", 1, 2, 90), ("fast", 1, 2, 100),
        ("finish", 2, 4, 100), ("direct", 1, 4, 300),
        ("dead", 1, 3, 10),
    ], (1, 2, 3, 4))
    table = {
        "slow": {"time_s": 3, "final_speed_mps": 1, "energy_j": 10},
        "fast": {"time_s": 5, "final_speed_mps": 8, "energy_j": 20},
        "finish": {"time_s": 4, "energy_j": 30},
        "direct": {"time_s": 5, "energy_j": 100, "ascent_m": 10},
        "dead": {"time_s": 1},
    }
    reference = MultiObjectiveAStar(
        graph, DynamicModel(table), profile, config=SearchConfig(exact_speed=True),
        heuristic=ZeroHeuristic(graph, max_speed_mps=None), goal_pruning=False,
    ).search(1, 4)
    model = DynamicModel(table)
    optimized = MultiObjectiveAStar(
        graph, model, profile, config=SearchConfig(exact_speed=True), goal_pruning=True,
    ).search(1, 4)
    assert objective_front(optimized) == objective_front(reference)
    assert any(arc == "finish" and speed == 8 for arc, speed in model.calls)
    assert optimized.state_approximated is False
    assert all("slow" not in path for path in optimized.arc_sequences())
    assert optimized.expanded <= reference.expanded


def test_equal_cost_distinct_routes_survive_goal_pruning():
    graph = make_graph([("a", 1, 2), ("b", 1, 2), ("finish", 2, 3)])
    model = StubTraversal({"a": {}, "b": {}, "finish": {}})
    result = MultiObjectiveAStar(
        graph, model, "balanced", goal_pruning=True,
        heuristic=ZeroHeuristic(graph, max_speed_mps=None),
    ).search(1, 3)
    assert set(result.arc_sequences()) == {("a", "finish"), ("b", "finish")}
    assert result.state_approximated is True


def test_unavailable_terrain_cannot_dominate_usable_physical_state():
    class MissingTerrain(StubTraversal):
        def traverse(self, arc, speed):
            result = super().traverse(arc, speed)
            return replace(result, result_status=SimulationStatus.UNAVAILABLE) if arc.id == "missing" else result

    graph = make_graph([("missing", 1, 2, 50), ("usable", 1, 2, 100), ("end", 2, 3, 100)])
    model = MissingTerrain({"missing": {"time_s": 1}, "usable": {"time_s": 2}, "end": {}})
    result = MultiObjectiveAStar(
        graph, model, "shortest", goal_pruning=True,
        heuristic=ZeroHeuristic(graph, max_speed_mps=None),
    ).search(1, 3)
    assert ("usable", "end") in result.arc_sequences()


def physical_model():
    return PhysicalTraversalModel(
        PhysicalTraversal(step_m=20),
        CyclistModel(mass_kg=75, max_power_w=900, sustainable_power_w=220, frontal_area_m2=.5),
        BicycleModel(), Environment(),
    )


@pytest.mark.parametrize("profile", ["fastest", "shortest", "flattest", "balanced", "electric"])
def test_real_terrain_physics_matches_completed_reference(profile):
    graph = RoutingGraph()
    heights = {1: 700, 2: 740, 3: 710, 4: 720}
    for node, height in heights.items():
        graph.add_node(Node(node, 0, node * .0001, elevation_m=height))
    for arc_id, source, target, distance in (
        ("steep-up", 1, 2, 100), ("steep-down", 2, 4, 100),
        ("gentle-up", 1, 3, 300), ("gentle-exit", 3, 4, 300),
    ):
        delta = heights[target] - heights[source]
        graph.add_arc(Arc(arc_id, source, target, (Segment(
            arc_id, distance, elevation_gain_m=max(0, delta), elevation_loss_m=max(0, -delta),
            elevation_profile=(ElevationSample(0, heights[source]), ElevationSample(distance, heights[target])),
        ),)))
    reference = MultiObjectiveAStar(
        graph, physical_model(), profile, config=SearchConfig(exact_speed=True),
        heuristic=ZeroHeuristic(graph, max_speed_mps=None), goal_pruning=False,
    ).search(1, 4)
    optimized = MultiObjectiveAStar(
        graph, physical_model(), profile, config=SearchConfig(exact_speed=True), goal_pruning=True,
    ).search(1, 4)
    assert objective_front(optimized) == objective_front(reference)
    assert all(label.energy_j > 0 and label.time_s > 0 for label in optimized.labels)


def test_exact_cache_invalidation_and_disabled_reference():
    model = physical_model()
    arc = Arc("same-id", 1, 2, (Segment("s", 100),))
    first = model.traverse(arc, 4.0)
    assert model.traverse(arc, 4.0) is first
    assert model.traversal_cache_hits == 1
    changed = replace(arc, segments=(Segment("new", 200),))
    assert model.traverse(changed, 4).distance_m == 200
    model.environment = Environment(headwind_mps=5)
    windy = model.traverse(arc, 4)
    assert windy.time_s != first.time_s
    model.cache_enabled = False
    assert model.traverse(arc, 4) == windy
    model.traversal.step_m = 5
    assert model.traverse(arc, 4) != windy


def test_physical_reverse_bounds_are_admissible_for_different_speeds():
    graph = make_graph([("a", 1, 2, 100), ("b", 2, 3, 200)])
    model = physical_model()
    heuristic = ReverseCostHeuristic(graph, model.arc_lower_bounds, max_speed_mps=model.max_speed_mps)
    for speed in (0, 3, 10):
        results = []
        for arc in graph.arcs.values():
            result = model.traverse(arc, speed)
            speed = result.final_speed_mps
            results.append(result)
        lower = heuristic.lower_bounds(1, 3, ("distance_m", "ascent_m", "time_s", "energy_j"))
        actual = (sum(r.distance_m for r in results), sum(r.ascent_m for r in results),
                  sum(r.time_s for r in results), sum(r.mechanical_energy_j for r in results))
        assert all(a <= b for a, b in zip(lower, actual))
    assert not heuristic.can_reach(3, 1)


def test_large_snap_boxes_preserve_spatial_candidates_without_grid_explosion():
    from router.core.data.snapping import _GridIndex
    index = _GridIndex(64, -17.8)
    index.add_box("long-arc", (-18, -51, -17, -50))
    index.add_point("nearby", -17.8, -50.9)
    assert len(index._large_boxes) == 1
    assert len(index._cells) == 1
    assert index.query(-17.8, -50.9, 100) == {"long-arc", "nearby"}
    assert index.query(-18, -51, 0) == {"long-arc"}
    assert index.query(-20, -54, 100) == set()
