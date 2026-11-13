"""Demanda fisica de um arco em um ritmo de referencia.

A demanda responde **quanto custa** manter um arco na velocidade de referencia:
e uma propriedade do terreno e do conjunto ciclista+bicicleta, calculada com as
mesmas equacoes de ``forces``. Ela nao depende da potencia de referencia do
ciclista — descreve o custo, nao o orcamento.

Como a velocidade de referencia e declarada (ritmo planejado), a demanda e
estavel entre corredores: o mesmo arco tem a mesma demanda para um recreacional
e para um treinado. A diferenca entre eles aparece no ritmo que conseguem
sustentar (``pacing.py``) e no RPE, nao no custo do terreno.

A potencia por passo e calculada com ``required_rider_power_w``. Descidas com
potencia negativa viram zero: o ciclista nao recupera energia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from router.core.data.graph.model import Arc

from .arc_profile import ProfileStep
from .bicycle import BicycleModel
from .environment import Environment
from .forces import forces_at, required_rider_power_w
from .traversal import PhysicalTraversal

#: Identificador estavel do modelo de demanda.
DEMAND_MODEL_VERSION = "arc-demand@1"

#: Cortes superiores (percentual de rampa) dos tres baldes de declividade.
GRADE_BIN_EDGES_PCT: tuple[float, float] = (4.0, 8.0)

#: Rotulos dos baldes, na ordem de ``GRADE_BIN_EDGES_PCT``.
GRADE_BIN_LABELS: tuple[str, str, str] = ("<=4%", "4-8%", ">8%")


def grade_bin_index(grade_pct: float) -> int:
    """Indice do balde de declividade (0..2) para uma rampa assinada.

    Descidas entram no primeiro balde (``<=4%``): o balde descreve terreno, e
    uma descida nao e uma rampa mais forte. O sinal continua disponivel em
    ``mean_grade_pct``.
    """
    positive = max(0.0, float(grade_pct))
    for index, edge in enumerate(GRADE_BIN_EDGES_PCT):
        if positive <= edge:
            return index
    return len(GRADE_BIN_EDGES_PCT)


@dataclass(frozen=True, slots=True)
class DemandScenario:
    """Condicoes em que a demanda e medida.

    Todos os campos sao imutaveis (``BicycleModel`` e ``Environment`` sao
    ``frozen``), portanto a instancia e utilizavel como chave de cache. A
    velocidade de referencia e uma **preferencia declarada** de ritmo, nunca
    derivada de ``P_ref``.
    """

    reference_speed_mps: float
    mass_kg: float
    frontal_area_m2: float
    bicycle: BicycleModel
    environment: Environment

    def __post_init__(self) -> None:
        if not math.isfinite(self.reference_speed_mps) or self.reference_speed_mps <= 0.0:
            raise ValueError(
                "reference_speed_mps deve ser positivo e finito "
                f"(recebido {self.reference_speed_mps!r})"
            )
        if not math.isfinite(self.mass_kg) or self.mass_kg <= 0.0:
            raise ValueError(f"mass_kg deve ser positivo (recebido {self.mass_kg!r})")
        if not math.isfinite(self.frontal_area_m2) or self.frontal_area_m2 <= 0.0:
            raise ValueError(
                f"frontal_area_m2 deve ser positivo (recebido {self.frontal_area_m2!r})"
            )
        if not isinstance(self.bicycle, BicycleModel):
            raise ValueError("bicycle deve ser um BicycleModel")
        if not isinstance(self.environment, Environment):
            raise ValueError("environment deve ser um Environment")

    @classmethod
    def from_models(
        cls,
        cyclist_mass_kg: float,
        frontal_area_m2: float,
        bicycle: BicycleModel,
        environment: Environment,
        reference_speed_mps: float,
    ) -> "DemandScenario":
        """Monta o cenario com a massa total do sistema (ciclista + bicicleta)."""
        return cls(
            reference_speed_mps=reference_speed_mps,
            mass_kg=float(cyclist_mass_kg) + float(bicycle.mass_kg),
            frontal_area_m2=frontal_area_m2,
            bicycle=bicycle,
            environment=environment,
        )


@dataclass(frozen=True, slots=True)
class ArcDemand:
    """Custo energetico de um arco no ritmo de referencia.

    ``mean_power_w`` e a potencia media no pedivela ponderada por distancia;
    ``peak_power_w`` e o maior passo. ``distance_by_grade_bin_m`` distribui a
    distancia nos tres baldes de ``GRADE_BIN_EDGES_PCT``.
    """

    arc_id: str
    distance_m: float
    mean_power_w: float
    peak_power_w: float
    mean_grade_pct: float
    distance_by_grade_bin_m: tuple[float, float, float]

    def __post_init__(self) -> None:
        for name in ("distance_m", "mean_power_w", "peak_power_w"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} deve ser finito e nao negativo")
        if not math.isfinite(self.mean_grade_pct):
            raise ValueError("mean_grade_pct deve ser finito")
        if len(self.distance_by_grade_bin_m) != 3:
            raise ValueError("distance_by_grade_bin_m deve ter 3 valores")
        for value in self.distance_by_grade_bin_m:
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "distance_by_grade_bin_m deve conter valores finitos e nao negativos"
                )

    def ratio_to(self, reference_power_w: float) -> float:
        """Demanda relativa declarada (``P_req / P_ref``); ``0`` sem referencia."""
        if reference_power_w <= 0.0:
            return 0.0
        return self.mean_power_w / reference_power_w

    def to_dict(self) -> dict[str, object]:
        return {
            "arc_id": self.arc_id,
            "distance_m": round(self.distance_m, 3),
            "mean_power_w": round(self.mean_power_w, 3),
            "peak_power_w": round(self.peak_power_w, 3),
            "mean_grade_pct": round(self.mean_grade_pct, 3),
            "distance_by_grade_bin_m": [
                round(value, 3) for value in self.distance_by_grade_bin_m
            ],
        }


def arc_demand(
    steps: Sequence[ProfileStep],
    scenario: DemandScenario,
    *,
    arc_id: str = "",
) -> ArcDemand:
    """Calcula a demanda de um arco a partir dos passos do perfil.

    Cada passo contribui com a potencia necessaria para vencer as forcas
    resistivas na velocidade de referencia. A media e ponderada por distancia,
    de modo que um trecho curto e ingreme nao domine a demanda do arco inteiro;
    o pico continua exposto em ``peak_power_w``.
    """
    if not isinstance(scenario, DemandScenario):
        raise ValueError("scenario deve ser um DemandScenario")

    speed = scenario.reference_speed_mps
    efficiency = scenario.bicycle.drivetrain_efficiency
    total_distance = 0.0
    power_distance = 0.0
    grade_distance = 0.0
    peak = 0.0
    bins = [0.0, 0.0, 0.0]

    for step in steps:
        distance = float(step.distance_m)
        if distance <= 0.0:
            continue
        grade_pct = math.tan(step.slope_rad) * 100.0
        forces = forces_at(
            step.slope_rad,
            speed,
            scenario.mass_kg,
            scenario.frontal_area_m2,
            scenario.bicycle,
            scenario.environment,
        )
        power = required_rider_power_w(forces.total_n, speed, efficiency)
        total_distance += distance
        power_distance += power * distance
        grade_distance += grade_pct * distance
        peak = max(peak, power)
        bins[grade_bin_index(grade_pct)] += distance

    return ArcDemand(
        arc_id=arc_id,
        distance_m=total_distance,
        mean_power_w=(power_distance / total_distance) if total_distance > 0.0 else 0.0,
        peak_power_w=peak,
        mean_grade_pct=(grade_distance / total_distance) if total_distance > 0.0 else 0.0,
        distance_by_grade_bin_m=(bins[0], bins[1], bins[2]),
    )


class ArcDemandField:
    """Cache de demanda por arco e cenario.

    O cache e indexado por ``(arc.id, scenario)`` e guarda a identidade do
    objeto ``Arc`` — o mesmo padrao de ``PhysicalTraversal``. Como a demanda
    nao depende de ``P_ref``, trocar a potencia de referencia reutiliza os
    valores ja calculados: apenas o ritmo muda.
    """

    def __init__(self, traversal: PhysicalTraversal | None = None) -> None:
        self._traversal = traversal if traversal is not None else PhysicalTraversal()
        self._cache: dict[tuple[str, DemandScenario], tuple[Arc, ArcDemand]] = {}

    @property
    def traversal(self) -> PhysicalTraversal:
        return self._traversal

    def demand(self, arc: Arc, scenario: DemandScenario) -> ArcDemand:
        key = (arc.id, scenario)
        cached = self._cache.get(key)
        if cached is not None and cached[0] is arc:
            return cached[1]
        value = arc_demand(self._traversal.steps(arc), scenario, arc_id=arc.id)
        self._cache[key] = (arc, value)
        return value


__all__ = [
    "DEMAND_MODEL_VERSION",
    "GRADE_BIN_EDGES_PCT",
    "GRADE_BIN_LABELS",
    "grade_bin_index",
    "DemandScenario",
    "ArcDemand",
    "arc_demand",
    "ArcDemandField",
]
