"""Modelos base (constante e linear) e ajuste por minimos quadrados.

Estes modelos sao **genericos** e auditaveis. Um *baseline* (ADR-027, decisao 4)
e, tipicamente, um modelo destes com proveniencia declarada; um modelo candidato
futuro pode ser mais rico, desde que implemente ``ml.EffortPredictor``.

O ajuste **nao esconde** falha numerica: uma matriz singular levanta
``ModelError`` em vez de devolver coeficientes arbitrarios.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ._validation import finite

#: Versao do contrato dos modelos base.
MODELS_VERSION = "1"


class ModelError(ValueError):
    """Erro de dados/numerico no ajuste ou uso de um modelo."""


@dataclass(frozen=True, slots=True)
class ConstantModel:
    """Modelo constante: preve sempre o mesmo valor (baseline trivial)."""

    value: float

    def __post_init__(self) -> None:
        finite("value", self.value)

    def predict_row(self, row: Mapping[str, float | None]) -> float:
        return float(self.value)

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "constant", "value": self.value}


@dataclass(frozen=True, slots=True)
class LinearModel:
    """Modelo linear ``y = intercept + sum(coef_i * feature_i)``."""

    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float

    def __post_init__(self) -> None:
        if not self.feature_names:
            raise ModelError("LinearModel exige ao menos uma feature")
        if len(self.feature_names) != len(self.coefficients):
            raise ModelError(
                "feature_names e coefficients precisam ter o mesmo tamanho"
            )
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ModelError(f"feature_names duplicadas: {self.feature_names!r}")
        finite("intercept", self.intercept)
        for name, coef in zip(self.feature_names, self.coefficients):
            finite(f"coeficiente[{name!r}]", coef)

    def predict_row(self, row: Mapping[str, float | None]) -> float:
        total = float(self.intercept)
        missing: list[str] = []
        for name, coef in zip(self.feature_names, self.coefficients):
            value = row.get(name)
            if value is None:
                missing.append(name)
                continue
            total += coef * float(value)
        if missing:
            raise ModelError(f"features ausentes para predicao: {missing!r}")
        return total

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": "linear",
            "feature_names": list(self.feature_names),
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
        }


def model_from_payload(payload: Mapping[str, Any]) -> ConstantModel | LinearModel:
    """Reconstroi um modelo a partir do ``payload`` persistido."""
    kind = payload.get("kind")
    if kind == "constant":
        return ConstantModel(value=float(payload["value"]))
    if kind == "linear":
        return LinearModel(
            feature_names=tuple(str(name) for name in payload["feature_names"]),
            coefficients=tuple(float(c) for c in payload["coefficients"]),
            intercept=float(payload["intercept"]),
        )
    raise ModelError(f"tipo de modelo desconhecido: {kind!r}")


def fit_constant(targets: Sequence[float]) -> ConstantModel:
    """Baseline constante: media dos alvos observados."""
    values = [float(v) for v in targets]
    if not values:
        raise ModelError("fit_constant exige ao menos um alvo observado")
    return ConstantModel(value=sum(values) / len(values))


def fit_linear(
    rows: Sequence[Mapping[str, float]],
    targets: Sequence[float],
    feature_names: Sequence[str],
) -> LinearModel:
    """Ajuste linear por minimos quadrados (equacoes normais + eliminacao).

    Resolve ``(X^T X) beta = X^T y`` com ``X`` incluindo a coluna de intercepto.
    Levanta ``ModelError`` se o sistema for singular ou os dados malformados.
    """
    names = tuple(feature_names)
    if not names:
        raise ModelError("fit_linear exige ao menos uma feature")
    if len(rows) != len(targets):
        raise ModelError("rows e targets precisam ter o mesmo tamanho")
    if len(rows) < len(names) + 1:
        raise ModelError(
            f"observacoes insuficientes para ajuste: {len(rows)} linhas para "
            f"{len(names)} features (+intercepto)"
        )

    columns = len(names) + 1
    # Matriz normal A = X^T X  e vetor b = X^T y.
    a = [[0.0] * columns for _ in range(columns)]
    b = [0.0] * columns
    for row, target in zip(rows, targets):
        design = [1.0] + [float(row[name]) for name in names]
        for i in range(columns):
            b[i] += design[i] * float(target)
            for j in range(columns):
                a[i][j] += design[i] * design[j]

    solution = _solve(a, b)
    intercept = solution[0]
    coefficients = tuple(solution[1:])
    return LinearModel(
        feature_names=names, coefficients=coefficients, intercept=intercept
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Eliminacao gaussiana com pivoteamento parcial (sistema denso pequeno)."""
    size = len(a)
    matrix = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            raise ModelError("matriz singular: features colineares/insuficientes")
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        pivot_value = matrix[col][col]
        for r in range(col + 1, size):
            factor = matrix[r][col] / pivot_value
            if factor == 0.0:
                continue
            for c in range(col, size + 1):
                matrix[r][c] -= factor * matrix[col][c]
    solution = [0.0] * size
    for col in range(size - 1, -1, -1):
        total = matrix[col][size]
        for c in range(col + 1, size):
            total -= matrix[col][c] * solution[c]
        solution[col] = total / matrix[col][col]
    return solution
