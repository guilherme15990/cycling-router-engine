"""Features de treino = features de servico (ADR-027, decisao 3).

As features de treino e de inferencia sao **as mesmas**: ``EffortFeatures``
(schema fixo) mais caracteristicas explicitas do ciclista (``RiderProfile``) e
contexto (``PredictionContext``). Este modulo **nao** cria feature nova por
decisao de modelo; ele apenas monta a **linha de features** a partir do contrato
existente, de modo que treino e servico usem a **mesma** extracao.

Dados ausentes sao explicitos (``None``); um modelo que exija um campo ausente
devolve ``INSUFFICIENT_DATA`` — nada e imputado. O **alvo** (RPE/FC) nunca entra
aqui: por construcao, a linha de features e funcao apenas do resultado fisico e
do perfil/contexto (sem vazamento).
"""

from __future__ import annotations

from typing import Mapping

from router.core.physics.effort import EffortFeatures
from router.core.physiology import PredictionContext, RiderProfile

#: Versao do contrato de montagem de features.
FEATURES_VERSION = "1"

#: Features fisicas sempre presentes em ``EffortFeatures``.
_PHYSICAL_FEATURES: tuple[str, ...] = (
    "duration_s",
    "distance_m",
    "average_speed_mps",
    "mechanical_work_j",
    "rider_work_j",
    "mean_power_w",
    "climbing_m",
    "descending_m",
    "braking_energy_j",
    "aero_loss_j",
    "rolling_loss_j",
)

#: Features fisicas opcionais (``None`` quando a engine nao as produz).
_OPTIONAL_PHYSICAL_FEATURES: tuple[str, ...] = (
    "peak_power_w",
    "power_variability",
    "battery_energy_j",
    "metabolic_energy_j",
    "elevation_coverage",
    "observed_elevation_coverage",
)

#: Features do ciclista (opcionais por natureza).
_RIDER_FEATURES: tuple[str, ...] = (
    "weight_kg",
    "threshold_power_w",
    "max_heart_rate_bpm",
    "resting_heart_rate_bpm",
)

#: Features de contexto (opcionais por natureza).
_CONTEXT_FEATURES: tuple[str, ...] = ("temperature_c",)

#: Todas as features que esta camada sabe montar (ordem estavel).
ALL_FEATURE_NAMES: tuple[str, ...] = (
    _PHYSICAL_FEATURES
    + _OPTIONAL_PHYSICAL_FEATURES
    + _RIDER_FEATURES
    + _CONTEXT_FEATURES
)

#: Conjunto padrao: apenas features **sempre** presentes na fisica (base
#: auditavel). Features opcionais e de perfil ficam disponiveis para uso
#: explicito, mas nao entram por omissao.
DEFAULT_FEATURE_NAMES: tuple[str, ...] = _PHYSICAL_FEATURES

#: Campos do contrato que **nunca** entram como feature (rotulo/estado/alvo).
EXCLUDED_FROM_FEATURES: tuple[str, ...] = (
    "result_status",
    "used_min_speed_regularization",
    "elevation_uncertainty",
)


def feature_row(
    features: EffortFeatures,
    rider: RiderProfile | None = None,
    context: PredictionContext | None = None,
) -> dict[str, float | None]:
    """Monta a linha de features (sem alvo) a partir do contrato existente.

    Returns:
        Dicionario ``nome -> valor`` com **todas** as ``ALL_FEATURE_NAMES``;
        campos sem dado ficam ``None`` (nunca imputados).
    """
    rider = rider if rider is not None else RiderProfile()
    context = context if context is not None else PredictionContext()

    row: dict[str, float | None] = {}
    for name in _PHYSICAL_FEATURES + _OPTIONAL_PHYSICAL_FEATURES:
        value = getattr(features, name, None)
        row[name] = None if value is None else float(value)
    for name in _RIDER_FEATURES:
        value = getattr(rider, name, None)
        row[name] = None if value is None else float(value)
    for name in _CONTEXT_FEATURES:
        value = getattr(context, name, None)
        row[name] = None if value is None else float(value)
    return row


def select_row(
    row: Mapping[str, float | None], names: tuple[str, ...]
) -> dict[str, float]:
    """Seleciona as features exigidas, garantindo que estao presentes.

    Raises:
        ValueError: se alguma feature exigida estiver ausente/``None`` ou nao
            pertencer a ``ALL_FEATURE_NAMES``.
    """
    unknown = [name for name in names if name not in ALL_FEATURE_NAMES]
    if unknown:
        raise ValueError(f"features desconhecidas: {unknown!r}")
    selected: dict[str, float] = {}
    missing: list[str] = []
    for name in names:
        value = row.get(name)
        if value is None:
            missing.append(name)
        else:
            selected[name] = float(value)
    if missing:
        raise ValueError(f"features ausentes: {missing!r}")
    return selected


def missing_features(
    row: Mapping[str, float | None], names: tuple[str, ...]
) -> tuple[str, ...]:
    """Nomes exigidos que estao ausentes (``None``) na linha."""
    return tuple(name for name in names if row.get(name) is None)
