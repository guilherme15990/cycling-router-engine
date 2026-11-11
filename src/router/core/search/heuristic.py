"""Limites inferiores admissiveis para a busca A* multiobjetivo.

Uma heuristica multiobjetivo devolve um vetor de limites inferiores, um por
objetivo. Ela e admissivel quando nenhum limite superestima o custo real
restante.

Aqui:

* distancia: linha reta (haversine) entre o no e o destino;
* tempo: ``distancia / velocidade_maxima`` — so e admissivel se
  ``max_speed_mps`` for realmente um teto de velocidade. Se for ``None``,
  o limite de tempo e ``0`` (sempre admissivel);
* subida: ``max(0, h_destino - h_no)``, quando ha elevacao nos nos.

Nao usamos velocidade media arbitraria: isso nao seria um limite inferior
garantido para o tempo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from router.core.data.geo import haversine_m
from router.core.data.graph.model import RoutingGraph

DEFAULT_MAX_SPEED_MPS = 22.0

SUPPORTED_OBJECTIVES = ("time_s", "distance_m", "ascent_m")


@dataclass(frozen=True, slots=True)
class HeuristicBounds:
    """Limites inferiores por objetivo para alcancar o destino."""

    time_s: float
    distance_m: float
    ascent_m: float

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
        }


class Heuristic:
    """Heuristica geometrica admissivel, independente de perfil."""

    def __init__(
        self,
        graph: RoutingGraph,
        *,
        max_speed_mps: float | None = DEFAULT_MAX_SPEED_MPS,
    ) -> None:
        if max_speed_mps is not None:
            if not math.isfinite(max_speed_mps) or max_speed_mps <= 0.0:
                raise ValueError(
                    "max_speed_mps deve ser positivo e finito ou None "
                    f"(recebido {max_speed_mps!r})"
                )
        self.graph = graph
        self.max_speed_mps = max_speed_mps

    def bounds(self, node_id: object, goal_id: object) -> HeuristicBounds:
        """Limites inferiores do no atual ate o destino."""
        node = self.graph.get_node(node_id)
        goal = self.graph.get_node(goal_id)

        distance = haversine_m(node.coord, goal.coord)
        if self.max_speed_mps is None:
            time_lb = 0.0
        else:
            time_lb = distance / self.max_speed_mps

        ascent = 0.0
        if node.elevation_m is not None and goal.elevation_m is not None:
            ascent = max(0.0, goal.elevation_m - node.elevation_m)

        return HeuristicBounds(
            time_s=time_lb,
            distance_m=distance,
            ascent_m=ascent,
        )

    def values(
        self,
        node_id: object,
        goal_id: object,
        objectives: Sequence[str],
    ) -> tuple[float, ...]:
        """Vetor de limites inferiores na ordem dos objetivos pedidos."""
        bounds = self.bounds(node_id, goal_id)
        available = {
            "time_s": bounds.time_s,
            "distance_m": bounds.distance_m,
            "ascent_m": bounds.ascent_m,
        }
        try:
            return tuple(float(available[objective]) for objective in objectives)
        except KeyError as exc:
            raise ValueError(
                f"objetivo sem limite inferior conhecido: {exc.args[0]!r} "
                f"(suportados: {', '.join(SUPPORTED_OBJECTIVES)})"
            ) from None

    def lower_bounds(
        self,
        node_id: object,
        goal_id: object,
        objectives: Sequence[str],
    ) -> tuple[float, ...]:
        """Vetor de limites inferiores, com ``0.0`` para objetivos sem limite.

        Diferente de ``values`` (estrito, usado por quem exige limite conhecido),
        esta variante devolve ``0.0`` para objetivos que a heuristica geometrica
        nao cobre (por exemplo ``energy_j``). ``0.0`` e sempre admissivel, porque
        nenhum objetivo e negativo.
        """
        bounds = self.bounds(node_id, goal_id)
        available = {
            "time_s": bounds.time_s,
            "distance_m": bounds.distance_m,
            "ascent_m": bounds.ascent_m,
        }
        return tuple(float(available.get(objective, 0.0)) for objective in objectives)
