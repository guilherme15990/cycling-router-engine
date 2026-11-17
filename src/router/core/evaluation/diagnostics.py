"""Diagnosticos opcionais da Camada 05 (ADR-029 D7).

O unico diagnostico aqui e a **consistencia energetica** de uma rota: o residuo
do balanco longitudinal ``dE_k + dE_p = W_wheel - perdas`` ja calculado arco a
arco pelo simulador completo (``TraversalResult.energy_balance``). Este modulo
**nao** introduz equacao nova nem refaz a simulacao: ele apenas **soma** os
balancos ja produzidos e expoe o residuo agregado.

Ausencia do diagnostico **nao** implica rejeicao (D7): ele e informativo.
"""

from __future__ import annotations

from dataclasses import dataclass

from router.core.physics.models import TraversalPath


@dataclass(frozen=True, slots=True)
class EnergyConsistency:
    """Consistencia energetica agregada de uma rota (diagnostico, ADR-029 D7).

    Todos os campos vem do **reuso** de ``TraversalResult.energy_balance``:
    ``wheel_work_j`` e o trabalho na roda; ``residual_j`` e o residuo absoluto do
    balanco; ``relative_residual`` e o residuo relativo ao trabalho na roda
    (``None`` quando nao ha trabalho a roda — nunca um zero falso). Nenhuma
    equacao fisica nova e introduzida.
    """

    wheel_work_j: float
    residual_j: float
    relative_residual: float | None
    mass_kg: float
    kinetic_energy_start_j: float = 0.0
    kinetic_energy_end_j: float = 0.0
    delta_kinetic_j: float = 0.0
    delta_potential_j: float = 0.0
    aero_loss_j: float = 0.0
    rolling_loss_j: float = 0.0
    braking_loss_j: float = 0.0
    dissipative_loss_j: float = 0.0
    energy_loss_detected: bool = False

    def to_dict(self) -> dict[str, float | None]:
        return {
            "wheel_work_j": round(self.wheel_work_j, 4),
            "residual_j": round(self.residual_j, 4),
            "relative_residual": (
                None
                if self.relative_residual is None
                else round(self.relative_residual, 6)
            ),
            "mass_kg": round(self.mass_kg, 4),
            "kinetic_energy_start_j": round(self.kinetic_energy_start_j, 4),
            "kinetic_energy_end_j": round(self.kinetic_energy_end_j, 4),
            "delta_kinetic_j": round(self.delta_kinetic_j, 4),
            "delta_potential_j": round(self.delta_potential_j, 4),
            "aero_loss_j": round(self.aero_loss_j, 4),
            "rolling_loss_j": round(self.rolling_loss_j, 4),
            "braking_loss_j": round(self.braking_loss_j, 4),
            "dissipative_loss_j": round(self.dissipative_loss_j, 4),
            "energy_loss_detected": self.energy_loss_detected,
        }


def energy_consistency(path: TraversalPath, mass_kg: float) -> EnergyConsistency:
    """Agrega o balanco energetico ja calculado de um ``TraversalPath``.

    Reutiliza ``TraversalResult.energy_balance`` (nenhuma re-simulacao); a massa
    total (ciclista + bicicleta) e a mesma usada no percurso.
    """
    wheel_work = 0.0
    residual = 0.0
    kinetic_start = 0.0
    kinetic_end = 0.0
    delta_kinetic = 0.0
    delta_potential = 0.0
    aero_loss = 0.0
    rolling_loss = 0.0
    braking_loss = 0.0
    first = True
    for result in path.results:
        balance = result.energy_balance(mass_kg)
        wheel_work += balance.wheel_work_j
        residual += balance.residual_j
        if first:
            kinetic_start = 0.5 * mass_kg * result.initial_speed_mps**2
            first = False
        kinetic_end = 0.5 * mass_kg * result.final_speed_mps**2
        delta_kinetic += balance.delta_kinetic_j
        delta_potential += balance.delta_potential_j
        aero_loss += balance.aero_loss_j
        rolling_loss += balance.rolling_loss_j
        braking_loss += balance.braking_loss_j
    relative = residual / abs(wheel_work) if wheel_work != 0.0 else None
    # Aerodynamic loss is signed (a tailwind can add energy). Only positive
    # dissipation counts as an actual loss; a kinetic-energy drop alone can be
    # a conversion into potential energy on a climb.
    dissipative = max(0.0, aero_loss) + max(0.0, rolling_loss) + max(0.0, braking_loss)
    return EnergyConsistency(
        wheel_work_j=wheel_work,
        residual_j=residual,
        relative_residual=relative,
        mass_kg=float(mass_kg),
        kinetic_energy_start_j=kinetic_start,
        kinetic_energy_end_j=kinetic_end,
        delta_kinetic_j=delta_kinetic,
        delta_potential_j=delta_potential,
        aero_loss_j=aero_loss,
        rolling_loss_j=rolling_loss,
        braking_loss_j=braking_loss,
        dissipative_loss_j=dissipative,
        energy_loss_detected=dissipative > 1e-6,
    )
