"""Estimativa e propagacao de velocidade ao longo de um passo.

O modelo usa discretizacao espacial e conservacao de energia cinetica:

    Delta E_k = (F_motor - F_resistencia) * ds

A velocidade final de um passo e a velocidade inicial do proximo. Isso captura
o efeito de uma descida em um trecho plano seguinte, por exemplo.

Limites de velocidade sao aplicados ao final do passo. A energia cinetica
excedente e contabilizada como frenagem, nunca como energia recuperada pelo
ciclista.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._validation import finite, non_negative, positive
from .bicycle import BicycleModel
from .energy import wheel_energy_from_rider_j
from .environment import Environment
from .forces import (
    aerodynamic_force_n,
    equilibrium_speed_mps,
    gravity_force_n,
    rolling_force_n,
)

# Piso numerico para evitar divisao por zero em baixa velocidade.
MIN_SPEED_MPS = 0.5


@dataclass(frozen=True, slots=True)
class SpeedStep:
    """Resultado fisico de um passo espacial.

    Alem dos trabalhos de propulsao, expoe as **perdas assinadas** do passo
    (``aero_loss_j``/``rolling_loss_j``) para que o balanco energetico
    ``dE_k + dE_p = W_drive - perdas`` seja verificavel (ADR-025, decisao 2).
    ``wheel_energy_j`` e o trabalho **positivo** entregue a roda (nunca
    negativo); nao ha fonte regenerativa modelada.

    ``regularized`` sinaliza que o passo usou o piso ``MIN_SPEED_MPS`` na
    avaliacao das forcas (regime de baixa velocidade), de modo que a precisao
    nao seja superestimada (ADR-025, decisao 6, item 2).
    """

    initial_speed_mps: float
    final_speed_mps: float
    time_s: float
    wheel_energy_j: float
    rider_energy_j: float
    braking_energy_j: float
    pedaling: bool
    stalled: bool = False
    limited_by_max_speed: bool = False
    aero_loss_j: float = 0.0
    rolling_loss_j: float = 0.0
    regularized: bool = False


def advance_speed(
    distance_m: float,
    slope_rad: float,
    initial_speed_mps: float,
    available_power_w: float,
    mass_kg: float,
    frontal_area_m2: float,
    bicycle: BicycleModel,
    environment: Environment,
    *,
    max_speed_mps: float | None = None,
    acceleration_limit_mps2: float = 2.0,
    drivetrain_efficiency: float | None = None,
) -> SpeedStep:
    """Avanca a velocidade por ``distance_m`` metros.

    ``available_power_w`` e a potencia no pedivela. ``drivetrain_efficiency``
    permite sobrescrever a eficiencia da bicicleta (util em testes).
    """
    finite("distance_m", distance_m)
    finite("slope_rad", slope_rad)
    finite("initial_speed_mps", initial_speed_mps)
    non_negative("available_power_w", available_power_w)
    positive("mass_kg", mass_kg)
    positive("frontal_area_m2", frontal_area_m2)

    if distance_m <= 0.0:
        speed = max(0.0, initial_speed_mps)
        return SpeedStep(
            initial_speed_mps=speed,
            final_speed_mps=speed,
            time_s=0.0,
            wheel_energy_j=0.0,
            rider_energy_j=0.0,
            braking_energy_j=0.0,
            pedaling=False,
        )

    efficiency = (
        bicycle.drivetrain_efficiency
        if drivetrain_efficiency is None
        else drivetrain_efficiency
    )
    positive("drivetrain_efficiency", efficiency)
    if efficiency > 1.0:
        raise ValueError("drivetrain_efficiency deve estar em (0, 1]")

    power = max(0.0, float(available_power_w))
    reported_initial = max(0.0, float(initial_speed_mps))
    v0 = reported_initial

    limit = _speed_limit(environment, max_speed_mps)
    braking = 0.0

    # Se a velocidade inicial ja excede o limite, freia antes de simular.
    if limit is not None and v0 > limit:
        braking += 0.5 * mass_kg * (v0 * v0 - limit * limit)
        v0 = limit

    pedaling = power > 0.0 and (limit is None or v0 < limit - 1e-9)

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

    v_effective = max(v0, MIN_SPEED_MPS)
    regularized_low_speed = v0 < MIN_SPEED_MPS
    aero = aerodynamic_force_n(
        v_effective,
        bicycle.drag_coefficient,
        frontal_area_m2,
        environment.air_density_kg_m3,
        headwind_mps=environment.headwind_mps,
    )
    resistance = base_force + aero

    if pedaling:
        drive_force = power * efficiency / v_effective
        delta_energy = (drive_force - resistance) * distance_m
    else:
        delta_energy = -resistance * distance_m

    final_squared = v0 * v0 + 2.0 * delta_energy / mass_kg
    v1 = math.sqrt(max(0.0, final_squared))

    # O limite de velocidade e aplicado antes dos demais tetos para que a
    # energia cinetica excedente seja contabilizada como frenagem.
    limited_by_max_speed = False
    if limit is not None and v1 > limit:
        braking += 0.5 * mass_kg * (v1 * v1 - limit * limit)
        v1 = limit
        limited_by_max_speed = True

    # Nao permite ultrapassar a velocidade de equilibrio do modelo.
    if pedaling:
        equilibrium = equilibrium_speed_mps(
            slope_rad,
            mass_kg,
            frontal_area_m2,
            bicycle,
            environment,
            power,
            drivetrain_efficiency=efficiency,
            max_speed_mps=limit,
        )
        v1 = min(v1, max(v0, equilibrium))
    else:
        terminal = equilibrium_speed_mps(
            slope_rad,
            mass_kg,
            frontal_area_m2,
            bicycle,
            environment,
            0.0,
            drivetrain_efficiency=efficiency,
            max_speed_mps=limit,
        )
        if terminal > 0.0:
            v1 = min(v1, max(v0, terminal))

    # Limite cinematico de aceleracao ao longo do passo.
    if acceleration_limit_mps2 > 0.0:
        acceleration_cap = math.sqrt(
            max(0.0, v0 * v0 + 2.0 * acceleration_limit_mps2 * distance_m)
        )
        v1 = min(v1, acceleration_cap)

    if limit is not None and v1 > limit:
        braking += 0.5 * mass_kg * (v1 * v1 - limit * limit)
        v1 = limit
        limited_by_max_speed = True

    average_speed = (v0 + v1) / 2.0
    stalled = average_speed < MIN_SPEED_MPS
    if stalled:
        average_speed = MIN_SPEED_MPS
    time_s = distance_m / average_speed

    if pedaling:
        rider_energy = power * time_s
        wheel_energy = wheel_energy_from_rider_j(rider_energy, efficiency)
    else:
        rider_energy = 0.0
        wheel_energy = 0.0

    return SpeedStep(
        initial_speed_mps=reported_initial,
        final_speed_mps=v1,
        time_s=time_s,
        wheel_energy_j=wheel_energy,
        rider_energy_j=rider_energy,
        braking_energy_j=braking,
        pedaling=pedaling,
        stalled=stalled,
        limited_by_max_speed=limited_by_max_speed,
        aero_loss_j=aero * distance_m,
        rolling_loss_j=rolling * distance_m,
        regularized=regularized_low_speed or stalled,
    )


def _speed_limit(
    environment: Environment,
    max_speed_mps: float | None,
) -> float | None:
    limit: float | None = None
    for candidate in (max_speed_mps, environment.safe_max_speed_mps):
        if candidate is None:
            continue
        value = float(candidate)
        if not math.isfinite(value) or value <= 0.0:
            continue
        limit = value if limit is None else min(limit, value)
    return limit
