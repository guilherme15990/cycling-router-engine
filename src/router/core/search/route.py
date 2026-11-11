"""Rota reconstruida: sequencia de arcos direcionados + metricas finais.

Etapa **posterior** a busca. Uma ``Route`` e apenas um dado: a busca entrega a
fronteira Pareto de destino e a reconstrucao transforma cada label em uma rota
com metricas finais. A rota nao conhece a fila A* nem recalcula fisica.

``arc_ids`` preserva a **direcao**: uma rua percorrida em sentidos opostos gera
arcos diferentes. ``arc_lengths`` e paralelo a ``arc_ids`` e guarda o
comprimento de cado arco, derivado das diferencas de ``distance_m`` na cadeia
de ``parent`` — e o insumo da metrica de distancia compartilhada.
"""

from __future__ import annotations

from dataclasses import dataclass

from router.core.physics.physical.models import ElevationUncertainty, SimulationStatus

from .astar import SearchResult
from .state import SearchState, route_arcs


@dataclass(frozen=True, slots=True)
class Route:
    """Rota reconstruida: arcos direcionados, comprimentos e metricas.

    ``arc_lengths`` e paralelo a ``arc_ids`` (mesmo comprimento quando
    disponivel). Quando ausente, as metricas de diversidade caem para contagem
    de arcos.
    """

    arc_ids: tuple[str | None, ...]
    time_s: float
    distance_m: float
    ascent_m: float
    descent_m: float
    energy_j: float
    label_id: int = 0
    arc_lengths: tuple[float, ...] = ()

    # Qualidade e limitacao do percurso (ADR-026 §6), agregadas pela busca.
    result_status: SimulationStatus = SimulationStatus.VALID
    elevation_uncertainty: ElevationUncertainty | None = None
    used_min_speed_regularization: bool = False
    stalled: bool = False
    dismount_used: bool = False

    @property
    def is_degraded(self) -> bool:
        """True se algum trecho nao tem confiabilidade plena (status != VALID)."""
        return self.result_status is not SimulationStatus.VALID

    @property
    def arc_set(self) -> frozenset[str | None]:
        return frozenset(self.arc_ids)

    @property
    def arc_count(self) -> int:
        return len(self.arc_ids)

    @property
    def arc_length_map(self) -> dict[str | None, float]:
        """Comprimento total por arco (somo arcos repetidas).

        Usada pela metrica de distancia compartilhada. Arcos sem comprimento
        conhecido entram com ``0.0`` e a metrica recai para contagem.
        """
        lengths = self.arc_lengths
        mapping: dict[str | None, float] = {}
        for index, arc_id in enumerate(self.arc_ids):
            length = float(lengths[index]) if index < len(lengths) else 0.0
            mapping[arc_id] = mapping.get(arc_id, 0.0) + length
        return mapping

    def to_dict(self) -> dict[str, object]:
        return {
            "label_id": self.label_id,
            "arcs": list(self.arc_ids),
            "arc_count": self.arc_count,
            "time_s": round(self.time_s, 3),
            "distance_m": round(self.distance_m, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "energy_j": round(self.energy_j, 3),
            "result_status": self.result_status.value,
            "used_min_speed_regularization": self.used_min_speed_regularization,
            "stalled": self.stalled,
            "dismount_used": self.dismount_used,
            "elevation_uncertainty": (
                None
                if self.elevation_uncertainty is None
                else self.elevation_uncertainty.to_dict()
            ),
        }


def route_arc_lengths(state: SearchState) -> list[float]:
    """Comprimento de cado arco da rota (diferencas de ``distance_m``).

    Paralelo a ``route_arcs``: o custo do arco de entrada de uma label e a
    diferenca entre a distancia acumulada dela e a do ``parent``.
    """
    lengths: list[float] = []
    node: SearchState | None = state
    while node is not None and node.parent is not None:
        lengths.append(node.distance_m - node.parent.distance_m)
        node = node.parent
    lengths.reverse()
    return lengths


def route_from_state(state: SearchState) -> Route:
    """Reconstroi uma ``Route`` de uma label de destino."""
    return Route(
        arc_ids=tuple(route_arcs(state)),
        time_s=state.time_s,
        distance_m=state.distance_m,
        ascent_m=state.ascent_m,
        descent_m=state.descent_m,
        energy_j=state.energy_j,
        label_id=state.label_id,
        arc_lengths=tuple(route_arc_lengths(state)),
        result_status=state.result_status,
        elevation_uncertainty=state.elevation_uncertainty,
        used_min_speed_regularization=state.used_min_speed_regularization,
        stalled=state.stalled,
        dismount_used=state.dismount_used,
    )


def routes_from_result(result: SearchResult) -> tuple[Route, ...]:
    """Converte a fronteira de destino de um ``SearchResult`` em rotas."""
    return tuple(route_from_state(label) for label in result.labels)
