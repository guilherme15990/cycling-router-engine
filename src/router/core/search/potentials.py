"""Directed lower bounds from the traversal model's relaxed arc costs."""
from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Hashable, Mapping, Sequence

from router.core.data.graph.model import Arc, RoutingGraph
from .heuristic import Heuristic, HeuristicBounds


class ReverseCostHeuristic(Heuristic):
    """Reverse Dijkstra per additive objective, ignoring access/turn constraints.

    Costs must be certified lower bounds for every entry speed. Relaxing turn
    restrictions can only decrease them. No physical transition is replaced.
    Energy stays zero until a model supplies a proven nonnegative lower bound.
    """

    def __init__(self, graph: RoutingGraph, arc_bounds: Callable[[Arc], Mapping[str, float]], *, max_speed_mps: float | None = None):
        super().__init__(graph, max_speed_mps=max_speed_mps)
        self._arc_bounds = arc_bounds
        self._incoming = None
        self._goal = None
        self._tables: dict[str, dict[Hashable, float]] = {}

    def invalidate(self) -> None:
        """Each search prepares bounds for its current graph and scenario."""
        self._incoming = None
        self._goal = None
        self._tables.clear()

    def prepare(self, goal: Hashable, should_cancel: Callable[[], bool] | None = None) -> None:
        from .exceptions import SearchCancelled
        def check():
            if should_cancel is not None and should_cancel():
                raise SearchCancelled("Busca cancelada pelo usuário")
        check()
        if self._goal == goal:
            return
        if self._incoming is None:
            incoming = {}
            for index, arc in enumerate(self.graph.arcs.values()):
                if index % 256 == 0:
                    check()
                costs = dict(self._arc_bounds(arc))
                if any(not math.isfinite(v) or v < 0 for v in costs.values()):
                    raise ValueError("arc lower bounds must be finite and nonnegative")
                incoming.setdefault(arc.target, []).append((arc.source, costs))
            self._incoming = incoming
        tables = {}
        for objective in ("distance_m", "ascent_m", "time_s", "energy_j"):
            distances = {goal: 0.0}
            queue = [(0.0, 0, goal)]
            serial = iterations = 0
            while queue:
                iterations += 1
                if iterations % 256 == 0:
                    check()
                cost, _, node = heapq.heappop(queue)
                if cost != distances.get(node):
                    continue
                for source, costs in self._incoming.get(node, ()):
                    candidate = cost + costs.get(objective, 0.0)
                    if candidate < distances.get(source, math.inf):
                        distances[source] = candidate
                        serial += 1
                        heapq.heappush(queue, (candidate, serial, source))
            tables[objective] = distances
        self._tables, self._goal = tables, goal

    def lower_bounds(self, node_id: object, goal_id: object, objectives: Sequence[str]) -> tuple[float, ...]:
        self.prepare(goal_id)
        values = []
        for objective in objectives:
            value = self._tables.get(objective, {}).get(node_id, 0.0)
            # Conservative slack covers summation order and floating point.
            values.append(max(0.0, value - 1e-9 * max(1.0, value)))
        return tuple(values)

    def can_reach(self, node_id: object, goal_id: object) -> bool:
        self.prepare(goal_id)
        return node_id in self._tables["distance_m"]

    def bounds(self, node_id: object, goal_id: object) -> HeuristicBounds:
        time, distance, ascent = self.lower_bounds(node_id, goal_id, ("time_s", "distance_m", "ascent_m"))
        return HeuristicBounds(time, distance, ascent)
