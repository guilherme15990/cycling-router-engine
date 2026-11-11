"""Energia mecanica, do ciclista e (futuramente) da bateria.

Os tres conceitos ficam separados de proposito:

* ``wheel_energy_j``: trabalho mecanico aplicado na roda;
* ``rider_energy_j``: trabalho no pedivela (roda / eficiencia);
* ``battery_energy_j``: energia eletrica consumida por uma e-bike.

A primeira versao nao modela assistencia eletrica nem regeneracao. Por isso
``battery_energy_j`` permanece ``None`` para bicicletas convencionais.
"""

from __future__ import annotations

from ._validation import positive


def kinetic_energy_j(mass_kg: float, speed_mps: float) -> float:
    """Energia cinetica de translacao."""
    return 0.5 * mass_kg * speed_mps * speed_mps


def potential_energy_j(
    mass_kg: float,
    height_m: float,
    gravity_m_s2: float = 9.80665,
) -> float:
    """Energia potencial gravitacional relativa."""
    return mass_kg * gravity_m_s2 * height_m


def wheel_energy_from_rider_j(
    rider_energy_j: float,
    drivetrain_efficiency: float,
) -> float:
    """Energia que chega a roda a partir do trabalho no pedivela."""
    positive("drivetrain_efficiency", drivetrain_efficiency)
    if drivetrain_efficiency > 1.0:
        raise ValueError("drivetrain_efficiency deve estar em (0, 1]")
    return max(0.0, rider_energy_j) * drivetrain_efficiency


def rider_energy_from_wheel_j(
    wheel_energy_j: float,
    drivetrain_efficiency: float,
) -> float:
    """Trabalho no pedivela necessario para entregar energia na roda."""
    positive("drivetrain_efficiency", drivetrain_efficiency)
    if drivetrain_efficiency > 1.0:
        raise ValueError("drivetrain_efficiency deve estar em (0, 1]")
    return max(0.0, wheel_energy_j) / drivetrain_efficiency


def battery_energy_from_wheel_j(
    wheel_energy_j: float,
    motor_efficiency: float,
    *,
    regen_efficiency: float = 0.0,
) -> float:
    """Energia eletrica consumida para entregar energia na roda.

    ``regen_efficiency`` so deve ser usado quando a e-bike declarar
    regeneracao. A primeira versao da camada fisica nao chama esta funcao;
    ela existe para a etapa de e-bikes.
    """
    positive("motor_efficiency", motor_efficiency)
    if motor_efficiency > 1.0:
        raise ValueError("motor_efficiency deve estar em (0, 1]")
    if regen_efficiency < 0.0 or regen_efficiency > 1.0:
        raise ValueError("regen_efficiency deve estar em [0, 1]")

    energy = float(wheel_energy_j)
    if energy >= 0.0:
        return energy / motor_efficiency
    # Energia negativa representa regeneracao: apenas parte volta a bateria.
    return energy * motor_efficiency * regen_efficiency
