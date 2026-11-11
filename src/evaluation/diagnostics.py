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

from physical.models import TraversalPath


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
        }


def energy_consistency(path: TraversalPath, mass_kg: float) -> EnergyConsistency:
    """Agrega o balanco energetico ja calculado de um ``TraversalPath``.

    Reutiliza ``TraversalResult.energy_balance`` (nenhuma re-simulacao); a massa
    total (ciclista + bicicleta) e a mesma usada no percurso.
    """
    wheel_work = 0.0
    residual = 0.0
    for result in path.results:
        balance = result.energy_balance(mass_kg)
        wheel_work += balance.wheel_work_j
        residual += balance.residual_j
    relative = residual / abs(wheel_work) if wheel_work != 0.0 else None
    return EnergyConsistency(
        wheel_work_j=wheel_work,
        residual_j=residual,
        relative_residual=relative,
        mass_kg=float(mass_kg),
    )
