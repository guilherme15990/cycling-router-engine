"""Avaliacao central dos objetivos de um perfil.

Uma unica implementacao extrai os valores objetivo de uma fonte de metricas
(``Route``, ``CandidateMetrics`` ou ``RouteCandidate``), para que ranking, selecao e
explicacao leiam as metricas do mesmo jeito. O perfil nao reinterpreta a fisica:
apenas escolhe quais metricas usar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .profiles import RouteObjective


@runtime_checkable
class MetricSource(Protocol):
    """Qualquer objeto com as metricas lidas pelos objetivos."""

    time_s: float
    distance_m: float
    ascent_m: float
    energy_j: float


@dataclass(frozen=True, slots=True)
class ObjectiveValues:
    """Metricas finais de uma rota, na forma usada pelos objetivos."""

    time_s: float
    distance_m: float
    ascent_m: float
    energy_j: float

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
            "energy_j": self.energy_j,
        }


def objective_value(
    values: ObjectiveValues, objective: RouteObjective
) -> float:
    """Valor escalar de um objetivo dentro de ``ObjectiveValues``."""
    return float(getattr(values, objective.value))


@runtime_checkable
class ObjectiveEvaluator(Protocol):
    """Contrato minimo de extracao de valores objetivo."""

    def evaluate(self, source: MetricSource) -> ObjectiveValues:
        """Metricas finais de ``source`` no formato dos objetivos."""


@dataclass(frozen=True, slots=True)
class RouteObjectiveEvaluator:
    """Implementacao central: le as metricas ja acumuladas na rota."""

    def evaluate(self, source: MetricSource) -> ObjectiveValues:
        return ObjectiveValues(
            time_s=float(source.time_s),
            distance_m=float(source.distance_m),
            ascent_m=float(source.ascent_m),
            energy_j=float(source.energy_j),
        )


def evaluate(
    source: MetricSource, evaluator: ObjectiveEvaluator | None = None
) -> ObjectiveValues:
    """Atalho: avalia ``source`` com o avaliador dado (ou o central)."""
    active = evaluator if evaluator is not None else RouteObjectiveEvaluator()
    return active.evaluate(source)


def evaluate_route(
    route: MetricSource, evaluator: ObjectiveEvaluator | None = None
) -> ObjectiveValues:
    """Compatibilidade: avalia uma rota (mesmo que ``evaluate``)."""
    return evaluate(route, evaluator)


def evaluate_metrics(
    metrics: MetricSource, evaluator: ObjectiveEvaluator | None = None
) -> ObjectiveValues:
    """Compatibilidade: avalia um conjunto de metricas."""
    return evaluate(metrics, evaluator)
