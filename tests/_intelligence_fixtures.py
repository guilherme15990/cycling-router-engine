"""Fixtures compartilhadas dos testes da Camada 04 (``intelligence``).

Apenas dados **explicitos e deterministicos** para testes. Nenhuma fixture
representa observacao fisiologica real; os alvos sinteticos existem **somente**
para exercitar o fluxo de treino/avaliacao e **nunca** sao apresentados como
verdade fisiologica (ADR-027, decisao 1).
"""

from __future__ import annotations

from router.core.physics.effort import EffortFeatures
from router.core.physics.models import SimulationStatus

#: Features fisicas "sempre presentes" usadas como base dos testes.
BASE_FEATURES: dict[str, float] = {
    "duration_s": 600.0,
    "distance_m": 3000.0,
    "average_speed_mps": 5.0,
    "mechanical_work_j": 90_000.0,
    "rider_work_j": 100_000.0,
    "mean_power_w": 166.6667,
    "climbing_m": 100.0,
    "descending_m": 50.0,
    "braking_energy_j": 0.0,
    "aero_loss_j": 1000.0,
    "rolling_loss_j": 2000.0,
}


def features(**overrides) -> EffortFeatures:
    """``EffortFeatures`` deterministico e explicito para testes unitarios."""
    base = dict(
        duration_s=BASE_FEATURES["duration_s"],
        distance_m=BASE_FEATURES["distance_m"],
        average_speed_mps=BASE_FEATURES["average_speed_mps"],
        mechanical_work_j=BASE_FEATURES["mechanical_work_j"],
        rider_work_j=BASE_FEATURES["rider_work_j"],
        mean_power_w=BASE_FEATURES["mean_power_w"],
        peak_power_w=None,
        power_variability=None,
        climbing_m=BASE_FEATURES["climbing_m"],
        descending_m=BASE_FEATURES["descending_m"],
        elevation_coverage=1.0,
        observed_elevation_coverage=1.0,
        braking_energy_j=BASE_FEATURES["braking_energy_j"],
        aero_loss_j=BASE_FEATURES["aero_loss_j"],
        rolling_loss_j=BASE_FEATURES["rolling_loss_j"],
        battery_energy_j=None,
        metabolic_energy_j=None,
        result_status=SimulationStatus.VALID,
        used_min_speed_regularization=False,
        elevation_uncertainty=None,
    )
    base.update(overrides)
    return EffortFeatures(**base)
