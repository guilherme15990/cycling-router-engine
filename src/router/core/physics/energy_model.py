"""Modelo energetico generico: energia mecanica -> energia eletrica.

O modelo de energia e **separado** do perfil de roteamento. Ele responde
"como estimar o consumo?" e nao "o que otimizar?". A camada fisica entrega
energia **mecanica** (``TraversalResult.mechanical_energy_j``); este modulo
estima a energia **eletrica** correspondente:

    E_eletrica ~= E_mec / eta

Como ``E_eletrica`` e ``E_mec`` diferem por um fator constante positivo, a
ordenacao de Pareto e a mesma: a conversao importa para **exibicao** (Wh) e
para a **viabilidade de bateria**. Por isso o modelo nao precisa ser conhecido
para calcular a rota — se nada for configurado, usa-se o generico.

Niveis de estimativa (o perfil nao muda entre eles):

* ``generic``: valores de referencia internos (``GenericEnergyConfig``);
* ``personalized``: parametros do veiculo informados pelo usuario;
* ``calibrated``: ajustado com dados reais de viagens anteriores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ._validation import non_negative, positive

JOULES_PER_WH = 3600.0

# Parametros de referencia do modo generico. Sao internos ao modelo e **nao**
# aparecem na interface: servem apenas para uma estimativa aproximada.
DEFAULT_REFERENCE_MASS_KG = 90.0
DEFAULT_ROLLING_RESISTANCE = 0.008
DEFAULT_DRAG_COEFFICIENT = 0.9
DEFAULT_FRONTAL_AREA_M2 = 0.5
DEFAULT_DRIVETRAIN_EFFICIENCY = 0.75

GENERIC_LEVEL = "generic"
PERSONALIZED_LEVEL = "personalized"
CALIBRATED_LEVEL = "calibrated"


def _efficiency(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise ValueError(
            f"{name} deve estar em (0, 1] (recebido {value!r})"
        )
    return value


def _ratio(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(
            f"{name} deve estar em [0, 1] (recebido {value!r})"
        )
    return value


@dataclass(frozen=True, slots=True)
class GenericEnergyConfig:
    """Parametros de referencia do modelo energetico generico.

    Todos tem valores padrao, de modo que o modelo funciona sem qualquer
    informacao do veiculo. ``drivetrain_efficiency`` (eta) e o fator que
    converte energia mecanica na roda em energia eletrica consumida.
    """

    mass_kg: float = DEFAULT_REFERENCE_MASS_KG
    rolling_resistance: float = DEFAULT_ROLLING_RESISTANCE
    drag_coefficient: float = DEFAULT_DRAG_COEFFICIENT
    frontal_area_m2: float = DEFAULT_FRONTAL_AREA_M2
    drivetrain_efficiency: float = DEFAULT_DRIVETRAIN_EFFICIENCY

    def __post_init__(self) -> None:
        positive("mass_kg", self.mass_kg)
        non_negative("rolling_resistance", self.rolling_resistance)
        non_negative("drag_coefficient", self.drag_coefficient)
        non_negative("frontal_area_m2", self.frontal_area_m2)
        object.__setattr__(
            self,
            "drivetrain_efficiency",
            _efficiency("drivetrain_efficiency", self.drivetrain_efficiency),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "mass_kg": self.mass_kg,
            "rolling_resistance": self.rolling_resistance,
            "drag_coefficient": self.drag_coefficient,
            "frontal_area_m2": self.frontal_area_m2,
            "drivetrain_efficiency": self.drivetrain_efficiency,
        }


@dataclass(frozen=True, slots=True)
class EnergyEstimate:
    """Estimativa de energia de um trecho, rota ou veiculo."""

    mechanical_energy_j: float
    electrical_energy_j: float
    efficiency: float
    level: str = GENERIC_LEVEL

    @property
    def electrical_energy_wh(self) -> float:
        return self.electrical_energy_j / JOULES_PER_WH

    def to_dict(self) -> dict[str, float | str]:
        return {
            "mechanical_energy_j": round(self.mechanical_energy_j, 3),
            "electrical_energy_j": round(self.electrical_energy_j, 3),
            "electrical_energy_wh": round(self.electrical_energy_wh, 4),
            "efficiency": self.efficiency,
            "level": self.level,
        }


@dataclass(frozen=True, slots=True)
class EnergyModel:
    """Converte energia mecanica em energia eletrica estimada.

    O modelo e opcional: sem configuracao, ``EnergyModel.generic()`` usa os
    valores de referencia. A conversao e ``E_eletrica = E_mec / eta`` (e a
    inversa multiplica por ``eta``). Energia mecanica negativa (descida) e
    tratada como ``0`` nesta estimativa — nao modelamos regeneracao.
    """

    config: GenericEnergyConfig = field(default_factory=GenericEnergyConfig)
    level: str = GENERIC_LEVEL

    @classmethod
    def generic(cls) -> "EnergyModel":
        """Modelo de referencia, sem qualquer dado do veiculo."""
        return cls()

    @classmethod
    def configured(
        cls,
        config: GenericEnergyConfig,
        *,
        level: str = PERSONALIZED_LEVEL,
    ) -> "EnergyModel":
        """Modelo com parametros informados (massa, eficiencia, etc.)."""
        return cls(config=config, level=level)

    @property
    def efficiency(self) -> float:
        return self.config.drivetrain_efficiency

    def electrical_energy_j(self, mechanical_energy_j: float) -> float:
        """Energia eletrica estimada para uma energia mecanica na roda."""
        mechanical = max(0.0, float(mechanical_energy_j))
        return mechanical / self.efficiency

    def mechanical_energy_j(self, electrical_energy_j: float) -> float:
        """Energia mecanica na roda a partir de uma energia eletrica."""
        electrical = max(0.0, float(electrical_energy_j))
        return electrical * self.efficiency

    def estimate(self, mechanical_energy_j: float) -> EnergyEstimate:
        """Estimativa completa (mecanica + eletrica + Wh) de um valor."""
        mechanical = max(0.0, float(mechanical_energy_j))
        return EnergyEstimate(
            mechanical_energy_j=mechanical,
            electrical_energy_j=mechanical / self.efficiency,
            efficiency=self.efficiency,
            level=self.level,
        )

    def to_dict(self) -> dict[str, object]:
        return {"level": self.level, "config": self.config.to_dict()}


@dataclass(frozen=True, slots=True)
class BatteryConfig:
    """Bateria opcional: capacidade, carga inicial e reserva desejada.

    Nao e requisito para usar o perfil ``Elétrico``: serve apenas para afirmar
    viabilidade ("da para chegar?") e estimar o percentual na chegada.
    """

    capacity_wh: float
    initial_charge_wh: float | None = None
    reserve_ratio: float = 0.1

    def __post_init__(self) -> None:
        capacity = positive("capacity_wh", self.capacity_wh)
        if self.initial_charge_wh is None:
            charge = capacity
        else:
            charge = non_negative("initial_charge_wh", self.initial_charge_wh)
            if charge > capacity:
                raise ValueError(
                    "initial_charge_wh nao pode exceder capacity_wh "
                    f"({charge!r} > {capacity!r})"
                )
        object.__setattr__(self, "initial_charge_wh", charge)
        object.__setattr__(
            self, "reserve_ratio", _ratio("reserve_ratio", self.reserve_ratio)
        )

    @property
    def reserve_energy_wh(self) -> float:
        return self.capacity_wh * self.reserve_ratio

    @property
    def available_energy_wh(self) -> float:
        return float(self.initial_charge_wh)

    @property
    def usable_energy_wh(self) -> float:
        """Carga disponivel acima da reserva."""
        return max(0.0, self.available_energy_wh - self.reserve_energy_wh)

    def to_dict(self) -> dict[str, float]:
        return {
            "capacity_wh": self.capacity_wh,
            "initial_charge_wh": self.available_energy_wh,
            "reserve_ratio": self.reserve_ratio,
            "reserve_energy_wh": self.reserve_energy_wh,
            "usable_energy_wh": self.usable_energy_wh,
        }


@dataclass(frozen=True, slots=True)
class BatteryFeasibility:
    """Viabilidade de uma rota com a bateria configurada.

    Compara a energia disponivel com o consumo estimado, respeitando a
    reserva. E um estagio **posterior** a selecao: nao participa da fisica nem
    da fronteira Pareto.
    """

    capacity_wh: float
    available_energy_wh: float
    estimated_consumption_wh: float
    reserve_energy_wh: float

    @property
    def remaining_energy_wh(self) -> float:
        return self.available_energy_wh - self.estimated_consumption_wh

    @property
    def remaining_above_reserve_wh(self) -> float:
        return self.remaining_energy_wh - self.reserve_energy_wh

    @property
    def remaining_ratio(self) -> float:
        """Fracao da capacidade restante na chegada (limitada a [0, 1])."""
        if self.capacity_wh <= 0.0:
            return 0.0
        ratio = self.remaining_energy_wh / self.capacity_wh
        return min(1.0, max(0.0, ratio))

    @property
    def feasible(self) -> bool:
        return self.remaining_energy_wh >= self.reserve_energy_wh

    @property
    def shortfall_wh(self) -> float:
        return max(0.0, self.reserve_energy_wh - self.remaining_energy_wh)

    def message(self) -> str:
        if self.feasible:
            return (
                "Alcanca o destino com "
                f"{self.remaining_energy_wh:.0f} Wh restantes "
                f"({self.remaining_ratio * 100:.0f}% da bateria)."
            )
        return (
            "Energia insuficiente: faltam "
            f"{self.shortfall_wh:.0f} Wh para manter a reserva."
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "feasible": self.feasible,
            "capacity_wh": round(self.capacity_wh, 3),
            "available_energy_wh": round(self.available_energy_wh, 3),
            "estimated_consumption_wh": round(
                self.estimated_consumption_wh, 3
            ),
            "reserve_energy_wh": round(self.reserve_energy_wh, 3),
            "remaining_energy_wh": round(self.remaining_energy_wh, 3),
            "remaining_ratio": round(self.remaining_ratio, 4),
            "shortfall_wh": round(self.shortfall_wh, 3),
            "message": self.message(),
        }


def assess_battery(
    config: BatteryConfig, consumption_wh: float
) -> BatteryFeasibility:
    """Avalia se a bateria cobre o consumo estimado respeitando a reserva.

    Estagio **posterior** a selecao: nao participa da fisica nem da fronteira
    Pareto. So faz sentido quando uma ``BatteryConfig`` foi informada.

    Args:
        config: Bateria (capacidade, carga inicial, reserva).
        consumption_wh: Consumo eletrico estimado da rota, em Wh.

    Returns:
        ``BatteryFeasibility`` com sobra, percentual e ``message()`` pronta.

    Raises:
        ValueError: Se ``consumption_wh`` for negativo.

    Example:
        >>> config = BatteryConfig(capacity_wh=400.0)
        >>> feasibility = assess_battery(config, consumption_wh=100.0)
        >>> feasibility.feasible, round(feasibility.remaining_ratio, 2)
        (True, 0.75)
    """
    consumption = non_negative("consumption_wh", consumption_wh)
    return BatteryFeasibility(
        capacity_wh=config.capacity_wh,
        available_energy_wh=config.available_energy_wh,
        estimated_consumption_wh=consumption,
        reserve_energy_wh=config.reserve_energy_wh,
    )
