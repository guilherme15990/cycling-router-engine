"""Ponte entre o modelo físico do ciclista e o modelo fisiológico.

A física só conhece massa e potência. Este módulo monta um ``RiderModel`` com
proveniência explícita, sem deixar a fisiologia importar ``core.physics``
(respeitando as fronteiras do ADR-031).
"""

from __future__ import annotations

from router.core.physics.cyclist import CyclistModel
from router.core.physiology import (
    HeartRateMaxSource,
    HeartRateParameters,
    ParameterSource,
    RiderModel,
    RiderProfile,
    estimate_reference_power,
    resolve_capacity_profile,
    tanaka_max_heart_rate,
)


def _heart_rate_parameters(profile: RiderProfile) -> HeartRateParameters:
    max_bpm: float | None = profile.max_heart_rate_bpm
    if max_bpm is not None:
        max_source = HeartRateMaxSource.MEASURED
    elif profile.age_years is not None:
        max_bpm = tanaka_max_heart_rate(profile.age_years)
        max_source = HeartRateMaxSource.TANAKA_POPULATION
    else:
        max_source = HeartRateMaxSource.UNAVAILABLE

    return HeartRateParameters(
        max_heart_rate_bpm=max_bpm,
        resting_heart_rate_bpm=profile.resting_heart_rate_bpm,
        max_source=max_source,
        resting_source=(
            ParameterSource.MEASURED
            if profile.resting_heart_rate_bpm is not None
            else None
        ),
    )


def rider_model_from_cyclist(
    cyclist: CyclistModel,
    rider: RiderProfile | None = None,
    *,
    calibrated_reference_power_w: float | None = None,
) -> RiderModel:
    """Constrói ``RiderModel`` a partir do ciclista físico e do perfil.

    A massa do ciclista vem de ``RiderProfile.weight_kg`` quando informada;
    caso contrário, de ``CyclistModel.mass_kg``. FTP medida tem prioridade
    sobre presets. CP medida é mantida como conceito distinto e só alimenta o
    modelo de fadiga quando W' também está disponível.
    """
    profile = rider if rider is not None else RiderProfile()
    rider_mass_kg = (
        profile.weight_kg if profile.weight_kg is not None else cyclist.mass_kg
    )
    capacity_profile = resolve_capacity_profile(
        profile.capacity_profile, profile.activity_level
    )
    reference = estimate_reference_power(
        rider_mass_kg,
        ftp_w=profile.ftp_w,
        critical_power_w=profile.critical_power_w,
        calibrated_reference_power_w=calibrated_reference_power_w,
        capacity_profile=profile.capacity_profile,
        activity_level=profile.activity_level,
        provisional_power_w=cyclist.sustainable_power_w,
    )
    return RiderModel(
        rider_mass_kg=rider_mass_kg,
        reference=reference,
        capacity_profile=capacity_profile,
        activity_level=profile.activity_level,
        age_years=profile.age_years,
        heart_rate=_heart_rate_parameters(profile),
        critical_power_w=profile.critical_power_w,
        w_prime_j=profile.w_prime_j,
    )


__all__ = ["rider_model_from_cyclist"]
