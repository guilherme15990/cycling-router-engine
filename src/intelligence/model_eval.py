"""Avaliacao de qualidade preditiva (ADR-027, decisao 5).

Mede a **qualidade preditiva** de um modelo em **conjunto separado**, com
validacao **temporal** e **por ciclista**, reportando **erro** (MAE/RMSE),
**vies** e generalizacao por subgrupo. Os **criterios minimos** de aceitacao sao
definidos **antes** do treino e aplicados aqui.

Esta avaliacao **nao** se confunde com a "avaliacao de rota" da Camada 05
(``evaluation``): aquela mede a fisica de uma rota; esta mede o modelo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ._validation import non_negative, positive
from .features import missing_features
from .observations import ObservationDataset, ObservedEffort
from .targets import TargetKind


class EvaluationError(ValueError):
    """Erro de dados/parametros na avaliacao (nunca mascarado)."""


@dataclass(frozen=True, slots=True)
class GroupMetrics:
    """Erro/vies de um subgrupo (ex.: por ciclista)."""

    key: str
    n: int
    mae: float
    bias: float


@dataclass(frozen=True, slots=True)
class ModelEvaluation:
    """Resultado auditavel da avaliacao de um modelo."""

    target: TargetKind
    split_scheme: str
    n: int
    mae: float
    rmse: float
    bias: float
    per_group: tuple[GroupMetrics, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.value,
            "split_scheme": self.split_scheme,
            "n": self.n,
            "mae": round(self.mae, 6),
            "rmse": round(self.rmse, 6),
            "bias": round(self.bias, 6),
            "per_group": [
                {"key": g.key, "n": g.n, "mae": round(g.mae, 6),
                 "bias": round(g.bias, 6)}
                for g in self.per_group
            ],
        }


@dataclass(frozen=True, slots=True)
class MinimalCriteria:
    """Criterios minimos definidos **antes** do treino.

    Nao ha valores padrao "escolhidos por conveniencia": quem define os limites e
    explicito. Um modelo so e "aceito" se satisfazer **todos** os criterios.
    """

    max_mae: float
    max_abs_bias: float
    min_n: int = 1

    def __post_init__(self) -> None:
        positive("max_mae", self.max_mae)
        non_negative("max_abs_bias", self.max_abs_bias)
        if self.min_n < 1:
            raise EvaluationError("min_n deve ser >= 1")

    def check(self, evaluation: ModelEvaluation) -> "CriteriaResult":
        reasons: list[str] = []
        if evaluation.n < self.min_n:
            reasons.append(
                f"amostra insuficiente: n={evaluation.n} < min_n={self.min_n}"
            )
        if evaluation.mae > self.max_mae:
            reasons.append(
                f"MAE {evaluation.mae:.6g} > max_mae {self.max_mae:.6g}"
            )
        if abs(evaluation.bias) > self.max_abs_bias:
            reasons.append(
                f"|vies| {abs(evaluation.bias):.6g} > max_abs_bias "
                f"{self.max_abs_bias:.6g}"
            )
        return CriteriaResult(accepted=not reasons, reasons=tuple(reasons))


@dataclass(frozen=True, slots=True)
class CriteriaResult:
    accepted: bool
    reasons: tuple[str, ...] = ()


def _target_value(target: TargetKind, observation: ObservedEffort) -> float | None:
    if target is TargetKind.RPE:
        return observation.observed_rpe
    if target is TargetKind.HEART_RATE_POINT:
        return observation.observed_heart_rate_bpm
    raise EvaluationError(
        f"alvo {target.value!r} nao e escalar; avaliacao escalar indisponivel"
    )


def _pairs(
    model,
    observations: Sequence[ObservedEffort],
    target: TargetKind,
    feature_names: Sequence[str],
) -> list[tuple[ObservedEffort, float, float]]:
    names = tuple(feature_names)
    pairs: list[tuple[ObservedEffort, float, float]] = []
    for observation in observations:
        observed = _target_value(target, observation)
        if observed is None:
            continue
        row = observation.feature_row
        if row is None or missing_features(row, names):
            continue
        predicted = model.predict_row(row)
        if not math.isfinite(predicted):
            raise EvaluationError(
                f"predicao nao finita para {observation.observation_id!r}: "
                f"{predicted!r}"
            )
        pairs.append((observation, float(observed), float(predicted)))
    return pairs


def evaluate_model(
    model,
    observations: Sequence[ObservedEffort],
    *,
    target: TargetKind = TargetKind.RPE,
    feature_names: Sequence[str],
    split_scheme: str = "unspecified",
) -> ModelEvaluation:
    """Avalia MAE/RMSE/vies do modelo nas observacoes fornecidas."""
    pairs = _pairs(model, observations, target, feature_names)
    if not pairs:
        raise EvaluationError("sem observacoes utilizaveis para avaliar")
    errors = [predicted - observed for _, observed, predicted in pairs]
    n = len(errors)
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    bias = sum(errors) / n

    by_cyclist: dict[str, list[float]] = {}
    for observation, observed, predicted in pairs:
        by_cyclist.setdefault(observation.cyclist_id.value, []).append(
            predicted - observed
        )
    per_group = tuple(
        GroupMetrics(
            key=key,
            n=len(errs),
            mae=sum(abs(e) for e in errs) / len(errs),
            bias=sum(errs) / len(errs),
        )
        for key, errs in sorted(by_cyclist.items())
    )

    return ModelEvaluation(
        target=target,
        split_scheme=split_scheme,
        n=n,
        mae=mae,
        rmse=rmse,
        bias=bias,
        per_group=per_group,
    )


def _require_timestamps(observations: Sequence[ObservedEffort]) -> None:
    missing = [o.observation_id for o in observations if o.recorded_at is None]
    if missing:
        raise EvaluationError(
            "validacao temporal exige recorded_at em todas as observacoes; "
            f"faltando em: {missing[:5]!r}"
        )


def temporal_split(
    dataset: ObservationDataset, *, test_fraction: float = 0.2
) -> tuple[ObservationDataset, ObservationDataset]:
    """Separa treino/teste no tempo (teste no **futuro** do treino)."""
    _require_timestamps(dataset.observations)
    if not 0.0 < test_fraction < 1.0:
        raise EvaluationError("test_fraction deve estar em (0, 1)")
    ordered = sorted(
        dataset.observations, key=lambda o: (o.recorded_at, o.observation_id)
    )
    cut = max(1, int(round(len(ordered) * (1.0 - test_fraction))))
    cut = min(cut, len(ordered) - 1)
    if cut < 1:
        raise EvaluationError("observacoes insuficientes para divisao temporal")
    train = _subset(dataset, ordered[:cut], "train-temporal")
    test = _subset(dataset, ordered[cut:], "test-temporal")
    return train, test


def cyclist_split(
    dataset: ObservationDataset, *, test_fraction: float = 0.2
) -> tuple[ObservationDataset, ObservationDataset]:
    """Separa por ciclista: ciclistas de teste **nao** aparecem no treino."""
    if not 0.0 < test_fraction < 1.0:
        raise EvaluationError("test_fraction deve estar em (0, 1)")
    cyclists = sorted({o.cyclist_id.value for o in dataset.observations})
    if len(cyclists) < 2:
        raise EvaluationError(
            "divisao por ciclista exige ao menos 2 ciclistas distintos"
        )
    n_test = max(1, int(round(len(cyclists) * test_fraction)))
    n_test = min(n_test, len(cyclists) - 1)
    test_ids = set(cyclists[-n_test:])
    train = [o for o in dataset.observations if o.cyclist_id.value not in test_ids]
    test = [o for o in dataset.observations if o.cyclist_id.value in test_ids]
    if not train or not test:
        raise EvaluationError("divisao por ciclista produziu conjunto vazio")
    return (
        _subset(dataset, train, "train-cyclist"),
        _subset(dataset, test, "test-cyclist"),
    )


def _subset(
    dataset: ObservationDataset,
    observations: Sequence[ObservedEffort],
    suffix: str,
) -> ObservationDataset:
    return ObservationDataset(
        dataset_version=f"{dataset.dataset_version}.{suffix}",
        observations=tuple(observations),
        provenance=dataset.provenance,
        notes=dataset.notes,
    )
