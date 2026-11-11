"""Estimativa de energia eletrica por rota (perfil ``Elétrico``).

Ponte entre a selecao (``Route`` reconstruida) e o modelo energetico da camada
fisica (``physical.energy_model``). A energia acumulada na rota e **mecanica**;
aqui ela e convertida em **eletrica** (``E_eletrica ~= E_mec / eta``) para
exibicao em Wh e, se houver bateria configurada, para avaliar viabilidade.

A estimativa **nao** altera a fronteira Pareto nem a ordem das rotas: a
conversao e um fator constante. Ela e um estagio posterior a selecao.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from physical.energy_model import (
    BatteryConfig,
    BatteryFeasibility,
    EnergyEstimate,
    EnergyModel,
    assess_battery,
)

from .route import Route


@dataclass(frozen=True, slots=True)
class RouteEnergyEstimate:
    """Energia estimada de uma rota + viabilidade de bateria (opcional)."""

    route_id: str
    estimate: EnergyEstimate
    battery: BatteryFeasibility | None = None

    @property
    def mechanical_energy_j(self) -> float:
        return self.estimate.mechanical_energy_j

    @property
    def electrical_energy_j(self) -> float:
        return self.estimate.electrical_energy_j

    @property
    def electrical_energy_wh(self) -> float:
        return self.estimate.electrical_energy_wh

    @property
    def level(self) -> str:
        return self.estimate.level

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "route_id": self.route_id,
            **self.estimate.to_dict(),
        }
        payload["battery"] = (
            None if self.battery is None else self.battery.to_dict()
        )
        return payload


def estimate_route_energy(
    route: Route,
    model: EnergyModel | None = None,
    *,
    battery: BatteryConfig | None = None,
    route_id: str = "",
) -> RouteEnergyEstimate:
    """Converte a energia mecanica de ``route`` em estimativa eletrica."""
    active = model if model is not None else EnergyModel.generic()
    estimate = active.estimate(route.energy_j)
    feasibility = (
        assess_battery(battery, estimate.electrical_energy_wh)
        if battery is not None
        else None
    )
    return RouteEnergyEstimate(
        route_id=route_id, estimate=estimate, battery=feasibility
    )


def estimate_routes_energy(
    routes: Iterable[Route],
    model: EnergyModel | None = None,
    *,
    battery: BatteryConfig | None = None,
    route_ids: Iterable[str] | None = None,
) -> tuple[RouteEnergyEstimate, ...]:
    """Estima a energia de varias rotas, alinhando ``route_ids`` por indice."""
    identifiers = tuple(route_ids) if route_ids is not None else ()
    estimates: list[RouteEnergyEstimate] = []
    for index, route in enumerate(routes):
        identifier = identifiers[index] if index < len(identifiers) else ""
        estimates.append(
            estimate_route_energy(
                route, model, battery=battery, route_id=identifier
            )
        )
    return tuple(estimates)
