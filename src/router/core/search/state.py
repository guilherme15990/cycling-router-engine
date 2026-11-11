"""Estado fisico e label de busca.

O motor fisico continua sendo a unica fonte de tempo e energia. A busca apenas
acumula o resultado da travessia e carrega o estado necessario para simular o
proximo trecho.

Convencao de energia (``energy_j``): energia **mecanica** requerida para vencer
resistencias e variacoes de energia potencial (``TraversalResult.
mechanical_energy_j``). Nao e energia metabolica do ciclista nem consumo
eletrico de bateria. Isso mantem a metrica separada da energia cinetica
armazenada no estado fisico.

A massa total (ciclista + bicicleta) e uma constante da configuracao da busca
nesta versao; por isso a energia cinetica e derivada da velocidade.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field

from router.core.data.graph.model import Arc, BicycleAccess
from router.core.physics.physical.energy import kinetic_energy_j
from router.core.physics.physical.models import (
    ElevationUncertainty,
    SimulationStatus,
    TraversalResult,
    worst_status,
)


@dataclass(frozen=True, slots=True)
class PhysicalState:
    """Estado fisico necessario para a proxima travessia.

    ``kinetic_energy_j`` e derivado da massa total e da velocidade — nao e
    escolhido de forma independente, para evitar inconsistencia.
    """

    speed_mps: float
    kinetic_energy_j: float

    @classmethod
    def from_speed(cls, mass_kg: float, speed_mps: float) -> "PhysicalState":
        speed = max(0.0, float(speed_mps))
        return cls(
            speed_mps=speed,
            kinetic_energy_j=kinetic_energy_j(mass_kg, speed),
        )


@dataclass(slots=True)
class SearchState:
    """Label de busca: metricas acumuladas + estado fisico + reconstrucao.

    ``active`` permite invalidar uma label que ainda esta na fila mas ja foi
    removida da fronteira Pareto por dominancia.
    """

    node_id: Hashable
    physical_state: PhysicalState
    time_s: float = 0.0
    distance_m: float = 0.0
    ascent_m: float = 0.0
    descent_m: float = 0.0
    energy_j: float = 0.0
    parent: "SearchState | None" = None
    incoming_arc_id: str | None = None
    label_id: int = 0
    active: bool = True

    # Qualidade e limitacao agregadas do percurso (ADR-026 §6). Agregacao
    # monotona: uma rota nunca "melhora" o status de um arco.
    result_status: SimulationStatus = SimulationStatus.VALID
    elevation_uncertainty: "ElevationUncertainty | None" = None
    used_min_speed_regularization: bool = False
    stalled: bool = False
    dismount_used: bool = False

    @property
    def speed_mps(self) -> float:
        return self.physical_state.speed_mps

    def objective_mapping(self) -> dict[str, float]:
        """Metricas disponiveis como objetivos da busca."""
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
            "descent_m": self.descent_m,
            "energy_j": self.energy_j,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "label_id": self.label_id,
            "node_id": self.node_id,
            "time_s": round(self.time_s, 3),
            "distance_m": round(self.distance_m, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "energy_j": round(self.energy_j, 3),
            "speed_mps": round(self.physical_state.speed_mps, 3),
            "kinetic_energy_j": round(
                self.physical_state.kinetic_energy_j, 3
            ),
            "incoming_arc_id": self.incoming_arc_id,
            "active": self.active,
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


def initial_state(
    node_id: Hashable,
    mass_kg: float,
    initial_speed_mps: float = 0.0,
    *,
    label_id: int = 0,
) -> SearchState:
    """Label inicial na origem, sem custo acumulado."""
    return SearchState(
        node_id=node_id,
        physical_state=PhysicalState.from_speed(mass_kg, initial_speed_mps),
        label_id=label_id,
    )


def advance_state(
    current: SearchState,
    arc: Arc,
    result: TraversalResult,
    *,
    mass_kg: float,
    label_id: int,
) -> SearchState:
    """Acumula a travessia de ``arc`` em uma nova label.

    A busca nao recalcula velocidade: usa ``result.final_speed_mps`` como
    estado fisico de saida. A qualidade e a limitacao do trecho (status, incerteza
    altimetrica, regularizacao de baixa velocidade, ``stalled``, desmontagem) sao
    agregadas de forma **monotona** (ADR-026 §6).
    """
    return SearchState(
        node_id=arc.target,
        physical_state=PhysicalState.from_speed(
            mass_kg, result.final_speed_mps
        ),
        time_s=current.time_s + result.time_s,
        distance_m=current.distance_m + result.distance_m,
        ascent_m=current.ascent_m + result.ascent_m,
        descent_m=current.descent_m + result.descent_m,
        energy_j=current.energy_j + result.mechanical_energy_j,
        parent=current,
        incoming_arc_id=arc.id,
        label_id=label_id,
        result_status=worst_status(
            current.result_status, result.result_status
        ),
        elevation_uncertainty=_combine_uncertainty(
            current.elevation_uncertainty, result.elevation_uncertainty
        ),
        used_min_speed_regularization=(
            current.used_min_speed_regularization
            or result.used_min_speed_regularization
        ),
        stalled=current.stalled or result.stalled,
        dismount_used=(
            current.dismount_used
            or arc.bicycle_access is BicycleAccess.DISMOUNT
        ),
    )


def _combine_uncertainty(
    current: ElevationUncertainty | None,
    incoming: ElevationUncertainty | None,
) -> ElevationUncertainty | None:
    """Soma incertezas de trechos consecutivos, tratando ausencia como neutra."""
    if current is None:
        return incoming
    if incoming is None:
        return current
    return current.combine(incoming)


def route_arcs(state: SearchState) -> list[str | None]:
    """Reconstroi a sequencia de arcos da origem ate ``state``."""
    arcs: list[str | None] = []
    node: SearchState | None = state
    while node is not None and node.parent is not None:
        arcs.append(node.incoming_arc_id)
        node = node.parent
    arcs.reverse()
    return arcs
