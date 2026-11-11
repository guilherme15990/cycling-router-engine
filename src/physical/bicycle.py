"""Modelo da bicicleta: massa, pneus, aerodinamica e transmissao.

O coeficiente de resistencia ao rolamento (``rolling_resistance``) pertence ao
modelo fisico da bicicleta/pneus. Ele nao e derivado de ``Surface`` nem de
``SURFACE_PRIORITY``: a superficie observada continua sendo uma categoria, e
sua calibracao fisica fica para uma etapa posterior.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._validation import non_negative, positive


@dataclass(frozen=True, slots=True)
class BicycleModel:
    """Caracteristicas fisicas da bicicleta."""

    mass_kg: float = 12.0
    rolling_resistance: float = 0.005
    drag_coefficient: float = 0.9
    drivetrain_efficiency: float = 0.97
    wheel_radius_m: float = 0.35

    def __post_init__(self) -> None:
        positive("mass_kg", self.mass_kg)
        non_negative("rolling_resistance", self.rolling_resistance)
        non_negative("drag_coefficient", self.drag_coefficient)
        positive("drivetrain_efficiency", self.drivetrain_efficiency)
        if self.drivetrain_efficiency > 1.0:
            raise ValueError(
                "drivetrain_efficiency deve estar em (0, 1] "
                f"(recebido {self.drivetrain_efficiency!r})"
            )
        positive("wheel_radius_m", self.wheel_radius_m)
