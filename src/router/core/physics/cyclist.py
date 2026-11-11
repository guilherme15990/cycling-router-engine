"""Modelo do ciclista: massa, potencia e area frontal.

A camada fisica nao conhece perfis de rota (Fastest, Flattest etc.). Ela
apenas descreve quem pedala. A potencia disponivel e um parametro de entrada;
o modelo nao decide se o ciclista deve poupar energia ou atacar.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._validation import non_negative, positive


@dataclass(frozen=True, slots=True)
class CyclistModel:
    """Caracteristicas fisicas do ciclista.

    ``max_power_w`` e o pico que o ciclista consegue produzir por poucos
    segundos. ``sustainable_power_w`` e a potencia que ele consegue manter ao
    longo de uma travessia. A primeira versao usa a potencia sustentavel por
    padrao, mas permite outro valor em ``PhysicalTraversal.traverse``.
    """

    mass_kg: float
    max_power_w: float
    sustainable_power_w: float
    frontal_area_m2: float

    def __post_init__(self) -> None:
        positive("mass_kg", self.mass_kg)
        non_negative("max_power_w", self.max_power_w)
        non_negative("sustainable_power_w", self.sustainable_power_w)
        positive("frontal_area_m2", self.frontal_area_m2)
        if self.sustainable_power_w > self.max_power_w:
            raise ValueError(
                "sustainable_power_w nao pode exceder max_power_w "
                f"({self.sustainable_power_w} > {self.max_power_w})"
            )

    @property
    def default_power_w(self) -> float:
        """Potencia usada quando nenhuma potencia explicita e informada."""
        return self.sustainable_power_w
