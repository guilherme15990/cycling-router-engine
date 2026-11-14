"""ALT: limites inferiores por landmarks para grafos direcionados."""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from collections.abc import Hashable, Iterable, Sequence

from router.core.data.graph.model import RoutingGraph
from .heuristic import Heuristic


class LandmarkHeuristic(Heuristic):
    """Heurística geométrica combinada com ALT para distância e tempo.

    Os pesos de distância são os comprimentos dos arcos. Os de tempo são
    distância dividida pelo teto de velocidade, logo ambos são limites
    inferiores. Para objetivos sem tabela ALT, mantém o limite da classe base.
    Pré-processa distâncias direcionais de/para cada landmark.
    """

    def __init__(
        self,
        graph: RoutingGraph,
        landmarks: Iterable[Hashable],
        *,
        max_speed_mps: float | None = 22.0,
    ) -> None:
        super().__init__(graph, max_speed_mps=max_speed_mps)
        self.landmarks = tuple(dict.fromkeys(landmarks))
        if not self.landmarks:
            raise ValueError("ALT exige ao menos um landmark")
        for node_id in self.landmarks:
            graph.get_node(node_id)
        self._outgoing: dict[Hashable, list[tuple[Hashable, float, float]]] = defaultdict(list)
        self._incoming: dict[Hashable, list[tuple[Hashable, float, float]]] = defaultdict(list)
        for arc in graph.arcs.values():
            distance = float(arc.distance_m)
            time_s = distance / max_speed_mps if max_speed_mps else 0.0
            self._outgoing[arc.source].append((arc.target, distance, time_s))
            self._incoming[arc.target].append((arc.source, distance, time_s))
        self._from: dict[tuple[Hashable, str], dict[Hashable, float]] = {}
        self._to: dict[tuple[Hashable, str], dict[Hashable, float]] = {}
        for landmark in self.landmarks:
            self._from[(landmark, "distance_m")] = self._dijkstra(landmark, "distance_m", reverse=False)
            self._to[(landmark, "distance_m")] = self._dijkstra(landmark, "distance_m", reverse=True)
            if max_speed_mps is not None:
                self._from[(landmark, "time_s")] = self._dijkstra(landmark, "time_s", reverse=False)
                self._to[(landmark, "time_s")] = self._dijkstra(landmark, "time_s", reverse=True)

    def _dijkstra(self, source: Hashable, objective: str, *, reverse: bool) -> dict[Hashable, float]:
        adjacency = self._incoming if reverse else self._outgoing
        index = 1 if objective == "distance_m" else 2
        distances: dict[Hashable, float] = {source: 0.0}
        heap: list[tuple[float, int, Hashable]] = [(0.0, 0, source)]
        serial = 0
        while heap:
            cost, _, node = heapq.heappop(heap)
            if cost != distances.get(node):
                continue
            for neighbor, distance, time_s in adjacency.get(node, ()):
                candidate = cost + (distance if index == 1 else time_s)
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    serial += 1
                    heapq.heappush(heap, (candidate, serial, neighbor))
        return distances

    def lower_bounds(self, node_id: object, goal_id: object, objectives: Sequence[str]) -> tuple[float, ...]:
        geometric = super().lower_bounds(node_id, goal_id, objectives)
        values: list[float] = []
        for objective, baseline in zip(objectives, geometric):
            best = baseline
            if objective in ("distance_m", "time_s") and (objective != "time_s" or self.max_speed_mps is not None):
                for landmark in self.landmarks:
                    from_l = self._from[(landmark, objective)]
                    to_l = self._to[(landmark, objective)]
                    # Directed triangle-inequality bounds. Missing distances
                    # mean that this landmark contributes no finite bound.
                    if goal_id in from_l and node_id in from_l:
                        best = max(best, from_l[goal_id] - from_l[node_id])
                    if node_id in to_l and goal_id in to_l:
                        best = max(best, to_l[node_id] - to_l[goal_id])
            values.append(max(0.0, best))
        return tuple(values)
