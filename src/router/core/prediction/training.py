"""Treino reproduzivel e artefato versionado (ADR-027, decisao 4).

O treino e **reproduzivel**: dadas as mesmas observacoes, as mesmas features e o
mesmo metodo, a ``model_version`` resultante e a mesma. Nada depende de estado
oculto nem de aleatoriedade — nao ha semente porque nao ha sorteio.

Baselines e candidatos usam o **mesmo caminho**: montam as linhas de features
pelo contrato existente (``intelligence.features``), exigem o alvo **observado**
e recusam treinar sem dados. A proveniencia do modelo deriva da proveniencia do
conjunto de observacoes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from router.core.physics.effort import EFFORT_FEATURES_VERSION

from .features import (
    ALL_FEATURE_NAMES,
    DEFAULT_FEATURE_NAMES,
    missing_features,
)
from .model_store import ModelArtifact
from .models import (
    ConstantModel,
    LinearModel,
    ModelError,
    fit_constant,
    fit_linear,
)
from .observations import ObservationDataset, Provenance
from .targets import TargetKind


class TrainingError(ValueError):
    """Erro de dados/parametros no treino (nunca mascarado)."""


@dataclass(frozen=True, slots=True)
class TrainingReport:
    """Resumo auditavel do que foi usado/descartado no treino."""

    target: TargetKind
    method: str
    feature_names: tuple[str, ...]
    n_total: int
    n_used: int
    n_dropped_missing_target: int
    n_dropped_missing_features: int

    @property
    def n_dropped(self) -> int:
        return self.n_dropped_missing_target + self.n_dropped_missing_features


@dataclass(frozen=True, slots=True)
class TrainedModel:
    """Artefato + relatorio do treino que o produziu."""

    artifact: ModelArtifact
    report: TrainingReport


def _target_value(target: TargetKind, observation) -> float | None:
    if target is TargetKind.RPE:
        return observation.observed_rpe
    if target is TargetKind.HEART_RATE_POINT:
        return observation.observed_heart_rate_bpm
    # Zonas nao sao um escalar: previsao de zonas e um vetor; nao treinavel como
    # regressao escalar. Explicitamente fora deste caminho.
    raise TrainingError(
        f"alvo {target.value!r} nao e escalar; nao treinavel por regressao "
        "escalar (apenas rpe/heart_rate_point)"
    )


def build_training_matrix(
    dataset: ObservationDataset,
    target: TargetKind,
    feature_names: Sequence[str],
    *,
    feature_schema_version: str = EFFORT_FEATURES_VERSION,
) -> tuple[list[dict[str, float]], list[float], TrainingReport]:
    """Monta ``(linhas, alvos, relatorio)`` a partir das observacoes reais.

    Descarta (e **reporta**) observacoes sem o alvo ou sem todas as features
    exigidas; recusa observacoes com schema de features diferente do treino.
    """
    names = tuple(feature_names)
    unknown = [name for name in names if name not in ALL_FEATURE_NAMES]
    if unknown:
        raise TrainingError(f"features desconhecidas: {unknown!r}")

    rows: list[dict[str, float]] = []
    targets: list[float] = []
    dropped_target = 0
    dropped_features = 0

    for observation in dataset.observations:
        value = _target_value(target, observation)
        if value is None:
            dropped_target += 1
            continue
        if observation.feature_schema_version != feature_schema_version:
            raise TrainingError(
                f"observacao {observation.observation_id!r} usa schema de "
                f"features {observation.feature_schema_version!r}, diferente do "
                f"treino {feature_schema_version!r}"
            )
        row = observation.feature_row
        if row is None:
            dropped_features += 1
            continue
        missing = missing_features(row, names)
        if missing:
            dropped_features += 1
            continue
        rows.append({name: float(row[name]) for name in names})  # type: ignore[arg-type]
        targets.append(float(value))

    report = TrainingReport(
        target=target,
        method="",
        feature_names=names,
        n_total=len(dataset),
        n_used=len(rows),
        n_dropped_missing_target=dropped_target,
        n_dropped_missing_features=dropped_features,
    )
    return rows, targets, report


def _artifact_version(method: str, target: TargetKind, dataset_version: str) -> str:
    return f"{method}:{target.value}:{dataset_version}"


def _make_artifact(
    model: ConstantModel | LinearModel,
    *,
    method: str,
    target: TargetKind,
    dataset: ObservationDataset,
    feature_names: tuple[str, ...],
    feature_schema_version: str,
    is_baseline: bool,
    calibration_method: str | None,
    notes: str | None,
) -> ModelArtifact:
    provenance = Provenance(
        source=f"intelligence.training/{method}",
        version=feature_schema_version,
        notes=f"dataset={dataset.dataset_version}",
    )
    return ModelArtifact(
        model_version=_artifact_version(method, target, dataset.dataset_version),
        dataset_version=dataset.dataset_version,
        effort_schema_version=feature_schema_version,
        target=target,
        method=method,
        provenance=provenance,
        payload=model.to_payload(),
        is_baseline=is_baseline,
        calibration_method=calibration_method,
        notes=notes,
    )


def train_constant_baseline(
    dataset: ObservationDataset,
    *,
    target: TargetKind = TargetKind.RPE,
    feature_schema_version: str = EFFORT_FEATURES_VERSION,
) -> TrainedModel:
    """Baseline constante: media do alvo observado (sem features)."""
    rows, targets, report = build_training_matrix(
        dataset, target, (), feature_schema_version=feature_schema_version
    )
    if not targets:
        raise TrainingError(
            f"sem observacoes do alvo {target.value!r}; nao se treina sem dado"
        )
    model = fit_constant(targets)
    method = "baseline-constant"
    report = report.__class__(
        target=report.target,
        method=method,
        feature_names=report.feature_names,
        n_total=report.n_total,
        n_used=report.n_used,
        n_dropped_missing_target=report.n_dropped_missing_target,
        n_dropped_missing_features=report.n_dropped_missing_features,
    )
    artifact = _make_artifact(
        model,
        method=method,
        target=target,
        dataset=dataset,
        feature_names=(),
        feature_schema_version=feature_schema_version,
        is_baseline=True,
        calibration_method=None,
        notes="baseline constante (media do alvo observado)",
    )
    return TrainedModel(artifact=artifact, report=report)


def train_linear(
    dataset: ObservationDataset,
    *,
    target: TargetKind = TargetKind.RPE,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
    feature_schema_version: str = EFFORT_FEATURES_VERSION,
    is_baseline: bool = False,
    calibration_method: str | None = None,
    notes: str | None = None,
) -> TrainedModel:
    """Treino linear por minimos quadrados (baseline ou candidato)."""
    names = tuple(feature_names)
    if not names:
        raise TrainingError("treino linear exige ao menos uma feature")
    rows, targets, report = build_training_matrix(
        dataset, target, names, feature_schema_version=feature_schema_version
    )
    if not targets:
        raise TrainingError(
            f"sem observacoes utilizaveis do alvo {target.value!r}; "
            "nao se treina sem dado"
        )
    try:
        model = fit_linear(rows, targets, names)
    except ModelError as exc:
        raise TrainingError(f"falha no ajuste linear: {exc}") from exc
    method = "baseline-linear" if is_baseline else "linear-candidate"
    report = report.__class__(
        target=report.target,
        method=method,
        feature_names=report.feature_names,
        n_total=report.n_total,
        n_used=report.n_used,
        n_dropped_missing_target=report.n_dropped_missing_target,
        n_dropped_missing_features=report.n_dropped_missing_features,
    )
    artifact = _make_artifact(
        model,
        method=method,
        target=target,
        dataset=dataset,
        feature_names=names,
        feature_schema_version=feature_schema_version,
        is_baseline=is_baseline,
        calibration_method=calibration_method,
        notes=notes,
    )
    return TrainedModel(artifact=artifact, report=report)


def train_and_save(
    dataset: ObservationDataset,
    store,
    *,
    target: TargetKind = TargetKind.RPE,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
    is_baseline: bool = True,
    feature_schema_version: str = EFFORT_FEATURES_VERSION,
    calibration_method: str | None = None,
    notes: str | None = None,
) -> TrainedModel:
    """Treina (baseline ou candidato linear) e grava o artefato localmente."""
    trained = train_linear(
        dataset,
        target=target,
        feature_names=feature_names,
        feature_schema_version=feature_schema_version,
        is_baseline=is_baseline,
        calibration_method=calibration_method,
        notes=notes,
    )
    store.save(trained.artifact)
    return trained
