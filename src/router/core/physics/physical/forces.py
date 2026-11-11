"""Forcas que se opoem (ou ajudam) o movimento da bicicleta.

Convencao de sinais: forcas positivas se opoem ao avanco.

* gravidade: positiva na subida, negativa na descida;
* rolamento: sempre positiva;
* aerodinamica: positiva com vento de frente, negativa com vento de cauda
  mais rapido que a bicicleta.

A potencia na roda e ``F_total * v``. A potencia no pedivela e a potencia na
roda dividida pela eficiencia da transmissao.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .bicycle import BicycleModel
from .environment import DEFAULT_GRAVITY_M_S2, Environment

MAX_EQUILIBRIUM_SPEED_MPS = 100.0


@dataclass(frozen=True, slots=True)
class ForceBreakdown:
    """Componentes da forca resistiva em um instante."""

    gravity_n: float
    rolling_n: float
    aerodynamic_n: float

    @property
    def total_n(self) -> float:
        return self.gravity_n + self.rolling_n + self.aerodynamic_n

    @property
    def propulsive_n(self) -> float:
        """Forca resultante a favor do movimento (sinal trocado)."""
        return -self.total_n


def gravity_force_n(
    mass_kg: float,
    slope_rad: float,
    gravity_m_s2: float = DEFAULT_GRAVITY_M_S2,
) -> float:
    """Componente da gravidade ao longo da rampa."""
    return mass_kg * gravity_m_s2 * math.sin(slope_rad)


def rolling_force_n(
    mass_kg: float,
    slope_rad: float,
    rolling_resistance: float,
    gravity_m_s2: float = DEFAULT_GRAVITY_M_S2,
) -> float:
    """Resistencia ao rolamento, proporcional a forca normal."""
    return rolling_resistance * mass_kg * gravity_m_s2 * math.cos(slope_rad)


def aerodynamic_force_n(
    speed_mps: float,
    drag_coefficient: float,
    frontal_area_m2: float,
    air_density_kg_m3: float,
    headwind_mps: float = 0.0,
) -> float:
    """Arrasto aerodinamico com vento relativo.

    ``headwind_mps`` positivo significa vento de frente. A forca usa o
    quadrado do vento relativo e preserva o sinal: vento de cauda mais rapido
    que a bicicleta empurra para frente.
    """
    relative = speed_mps + headwind_mps
    return (
        0.5
        * air_density_kg_m3
        * drag_coefficient
        * frontal_area_m2
        * relative
        * abs(relative)
    )


def forces_at(
    slope_rad: float,
    speed_mps: float,
    mass_kg: float,
    frontal_area_m2: float,
    bicycle: BicycleModel,
    environment: Environment,
) -> ForceBreakdown:
    """Forcas resistivas completas para uma inclinacao e velocidade."""
    gravity = gravity_force_n(
        mass_kg, slope_rad, gravity_m_s2=environment.gravity_m_s2
    )
    rolling = rolling_force_n(
        mass_kg,
        slope_rad,
        bicycle.rolling_resistance,
        gravity_m_s2=environment.gravity_m_s2,
    )
    aerodynamic = aerodynamic_force_n(
        speed_mps,
        bicycle.drag_coefficient,
        frontal_area_m2,
        environment.air_density_kg_m3,
        headwind_mps=environment.headwind_mps,
    )
    return ForceBreakdown(gravity, rolling, aerodynamic)


def wheel_power_w(total_force_n: float, speed_mps: float) -> float:
    """Potencia mecanica na roda (pode ser negativa em descida)."""
    return total_force_n * speed_mps


def required_rider_power_w(
    total_force_n: float,
    speed_mps: float,
    drivetrain_efficiency: float,
) -> float:
    """Potencia no pedivela necessaria para manter a velocidade.

    O ciclista nao recupera energia: potencias negativas viram zero.
    """
    if speed_mps <= 0.0:
        return 0.0
    wheel = wheel_power_w(total_force_n, speed_mps)
    return max(0.0, wheel / drivetrain_efficiency)


def equilibrium_speed_mps(
    slope_rad: float,
    mass_kg: float,
    frontal_area_m2: float,
    bicycle: BicycleModel,
    environment: Environment,
    available_power_w: float,
    *,
    drivetrain_efficiency: float | None = None,
    max_speed_mps: float | None = None,
) -> float:
    """Velocidade em que a potencia disponivel iguala a potencia requerida.

    A funcao resolve numericamente ``P(v) = P_disponivel``. O resultado e
    limitado pelo menor entre ``max_speed_mps`` e
    ``environment.safe_max_speed_mps``. Em descida com potencia zero, retorna
    a velocidade terminal (quando existe arrasto).
    """
    efficiency = (
        bicycle.drivetrain_efficiency
        if drivetrain_efficiency is None
        else drivetrain_efficiency
    )

    gravity = gravity_force_n(
        mass_kg, slope_rad, gravity_m_s2=environment.gravity_m_s2
    )
    rolling = rolling_force_n(
        mass_kg,
        slope_rad,
        bicycle.rolling_resistance,
        gravity_m_s2=environment.gravity_m_s2,
    )
    base_force = gravity + rolling
    drag_factor = (
        0.5
        * environment.air_density_kg_m3
        * bicycle.drag_coefficient
        * frontal_area_m2
    )
    headwind = environment.headwind_mps

    limit: float | None = None
    for candidate in (max_speed_mps, environment.safe_max_speed_mps):
        if candidate is not None:
            limit = candidate if limit is None else min(limit, candidate)

    def aerodynamic(speed_mps: float) -> float:
        relative = speed_mps + headwind
        return drag_factor * relative * abs(relative)

    def required_power(speed_mps: float) -> float:
        force = base_force + aerodynamic(speed_mps)
        return max(0.0, force * speed_mps / efficiency)

    if available_power_w <= 0.0:
        if base_force < 0.0 and drag_factor > 0.0:
            terminal = math.sqrt(-base_force / drag_factor) - headwind
        elif base_force < 0.0:
            terminal = MAX_EQUILIBRIUM_SPEED_MPS
        else:
            terminal = 0.0
        return min(max(terminal, 0.0), limit) if limit is not None else max(terminal, 0.0)

    if limit is not None and required_power(limit) <= available_power_w:
        return limit

    high = 1.0 if limit is None else min(1.0, limit)
    upper = limit if limit is not None else MAX_EQUILIBRIUM_SPEED_MPS
    while high < upper and required_power(high) < available_power_w:
        high = min(high * 2.0, upper)

    if required_power(high) < available_power_w:
        return upper

    low = 0.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if required_power(middle) < available_power_w:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0
