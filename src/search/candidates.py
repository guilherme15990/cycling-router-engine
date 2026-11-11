"""Pool de rotas candidatas: metricas, identidade e deduplicacao.

Etapa entre a reconstrucao e o ranking. A busca devolve a fronteira Pareto; a
reconstrucao vira uma lista de ``RouteCandidate`` (rota + metricas + identidade).
Antes de rankear, **deduplicamos**: labels fisicas diferentes ou caminhos
equivalentes podem representar a mesma alternativa.

``CandidateMetrics`` e o conjunto completo de metricas finais (inclui descida,
que nao e objetivo). Os objetivos sao um subconjunto avaliado por
``evaluator.py``. O nome evita colisao com o acumulador ``RouteMetrics`` da
camada fisica.

O pool **nao** decide preferencia nem selecao: ele apenas prepara candidatas
limpas e nao dominadas para as etapas seguintes.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum

from .route import Route, routes_from_result


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    """Metricas finais de uma candidata (objetivos + descida).

    Diferente de ``physical.RouteMetrics`` (acumulador fisico com
    energia do ciclista e bateria), este e o subconjunto lido pela selecao.
    """

    time_s: float
    distance_m: float
    ascent_m: float
    descent_m: float
    energy_j: float

    @classmethod
    def from_route(cls, route: Route) -> "CandidateMetrics":
        return cls(
            time_s=float(route.time_s),
            distance_m=float(route.distance_m),
            ascent_m=float(route.ascent_m),
            descent_m=float(route.descent_m),
            energy_j=float(route.energy_j),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": round(self.time_s, 3),
            "distance_m": round(self.distance_m, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "energy_j": round(self.energy_j, 3),
        }


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """Alternativa candidata: identidade + rota reconstruida + metricas."""

    route_id: str
    route: Route
    metrics: CandidateMetrics

    @classmethod
    def from_route(
        cls, route: Route, route_id: str | None = None
    ) -> "RouteCandidate":
        identifier = (
            route_id if route_id is not None else f"route-{route.label_id}"
        )
        return cls(
            route_id=identifier,
            route=route,
            metrics=CandidateMetrics.from_route(route),
        )

    @property
    def arc_ids(self) -> tuple[str | None, ...]:
        return self.route.arc_ids

    @property
    def label_id(self) -> int:
        return self.route.label_id

    @property
    def result_status(self):
        return self.route.result_status

    @property
    def elevation_uncertainty(self):
        return self.route.elevation_uncertainty

    @property
    def used_min_speed_regularization(self) -> bool:
        return self.route.used_min_speed_regularization

    @property
    def stalled(self) -> bool:
        return self.route.stalled

    @property
    def dismount_used(self) -> bool:
        return self.route.dismount_used

    def to_dict(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "label_id": self.route.label_id,
            "metrics": self.metrics.to_dict(),
            "arcs": list(self.route.arc_ids),
            "result_status": self.route.result_status.value,
            "used_min_speed_regularization": self.route.used_min_speed_regularization,
            "stalled": self.route.stalled,
            "dismount_used": self.route.dismount_used,
        }


class DeduplicationMode(str, Enum):
    """Como decidir que duas candidatas sao a mesma alternativa."""

    PATH = "path"
    METRICS = "metrics"
    PATH_AND_METRICS = "path_and_metrics"


@dataclass(frozen=True, slots=True)
class RouteDeduplicator:
    """Remove candidatas repetidas mantendo a primeira ocorrencia.

    * ``PATH``: colapsa rotas com a mesma sequencia de arcos direcionados
      (labels fisicas diferentes, mesmo caminho);
    * ``METRICS``: colapsa rotas com o mesmo resultado fisico
      (caminhos equivalentes), quantizado por ``metric_quantum``;
    * ``PATH_AND_METRICS``: so colapsa quando caminho e metricas coincidem.
    """

    mode: DeduplicationMode = DeduplicationMode.PATH
    metric_quantum: float = 1e-3

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.metric_quantum)
            or self.metric_quantum <= 0.0
        ):
            raise ValueError(
                "metric_quantum deve ser positivo e finito "
                f"(recebido {self.metric_quantum!r})"
            )

    def key(self, candidate: RouteCandidate) -> tuple:
        path_key = tuple(candidate.route.arc_ids)
        metrics_key = (
            round(candidate.metrics.time_s / self.metric_quantum),
            round(candidate.metrics.distance_m / self.metric_quantum),
            round(candidate.metrics.ascent_m / self.metric_quantum),
            round(candidate.metrics.descent_m / self.metric_quantum),
            round(candidate.metrics.energy_j / self.metric_quantum),
        )
        if self.mode is DeduplicationMode.PATH:
            return (path_key,)
        if self.mode is DeduplicationMode.METRICS:
            return (metrics_key,)
        return (path_key, metrics_key)

    def deduplicate(
        self, candidates: Iterable[RouteCandidate]
    ) -> tuple[RouteCandidate, ...]:
        seen: set[tuple] = set()
        kept: list[RouteCandidate] = []
        for candidate in candidates:
            key = self.key(candidate)
            if key in seen:
                continue
            seen.add(key)
            kept.append(candidate)
        return tuple(kept)


@dataclass(frozen=True, slots=True)
class RouteCandidatePool:
    """Conjunto de candidatas nao dominadas, ja deduplicado."""

    candidates: tuple[RouteCandidate, ...] = ()
    dropped_duplicates: int = 0

    @classmethod
    def from_routes(
        cls,
        routes: Iterable[Route],
        *,
        deduplicator: RouteDeduplicator | None = None,
    ) -> "RouteCandidatePool":
        return cls.from_candidates(
            (RouteCandidate.from_route(route) for route in routes),
            deduplicator=deduplicator,
        )

    @classmethod
    def from_candidates(
        cls,
        candidates: Iterable[RouteCandidate],
        *,
        deduplicator: RouteDeduplicator | None = None,
    ) -> "RouteCandidatePool":
        items = tuple(candidates)
        active = deduplicator if deduplicator is not None else RouteDeduplicator()
        kept = active.deduplicate(items)
        return cls(
            candidates=kept,
            dropped_duplicates=len(items) - len(kept),
        )

    @classmethod
    def from_result(
        cls,
        result,
        *,
        deduplicator: RouteDeduplicator | None = None,
    ) -> "RouteCandidatePool":
        return cls.from_routes(
            routes_from_result(result), deduplicator=deduplicator
        )

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(candidate.route for candidate in self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    def __bool__(self) -> bool:
        return bool(self.candidates)

    def __iter__(self) -> Iterator[RouteCandidate]:
        return iter(self.candidates)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidates": len(self.candidates),
            "dropped_duplicates": self.dropped_duplicates,
            "routes": [candidate.to_dict() for candidate in self.candidates],
        }
