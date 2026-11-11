"""Ranking das rotas candidatas por perfil.

O ranking diz **como ordenar** as candidatas que sobreviveram a dominancia; ele
nao elimina alternativas (isso e papel da diversidade, depois). Duas estrategias:

* ``LEXICOGRAPHIC``: prioridade absoluta ao primeiro objetivo, com **tolerancia**
  por objetivo para evitar desempates instaveis por diferencas minusculas de
  ``float``. Perfis Fastest/Shortest/Flattest.
* ``WEIGHTED_NORMALIZED``: ``S(r) = sum_i w_i * v_i / ref_i`` (menor e melhor),
  com pesos que somam 1 e referencias fixas por perfil. Perfil equilibrado.

Em ambas as estrategias o desempate final e ``(label_id, route_id)`` — a ordem
nunca depende da ordem de entrada. O nucleo opera sobre ``RouteCandidate``; a API
por ``Route`` continua disponivel como conveniencia.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass

from .candidates import RouteCandidate
from .evaluator import (
    ObjectiveEvaluator,
    ObjectiveValues,
    RouteObjectiveEvaluator,
    evaluate,
    objective_value,
)
from .profiles import RankingStrategy, RankingTolerance, RouteProfile
from .route import Route


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Candidata ordenada: posicao, candidata e score (None se lexicografico)."""

    candidate: RouteCandidate
    rank: int
    profile_score: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "profile_score": self.profile_score,
            "candidate": self.candidate.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RankedRoute:
    """Compatibilidade: rota ordenada (sem a identidade da candidata)."""

    route: Route
    rank: int
    profile_score: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "profile_score": self.profile_score,
            "route": self.route.to_dict(),
        }


def profile_score(
    route: Route,
    profile: RouteProfile,
    *,
    evaluator: ObjectiveEvaluator | None = None,
) -> float | None:
    """Score escalar do perfil para uma rota (None se lexicografico).

    No ranking lexicografico nao ha score escalar: a ordem vem da comparacao
    tolerante objetivo a objetivo. No ranking normalizado e
    ``sum_i w_i * v_i / ref_i``.
    """
    return score_from_values(evaluate(route, evaluator), profile)


def score_from_values(
    values: ObjectiveValues, profile: RouteProfile
) -> float | None:
    """Score normalizado de um ``ObjectiveValues`` (None se lexicografico)."""
    if profile.ranking_strategy is not RankingStrategy.WEIGHTED_NORMALIZED:
        return None

    weights = profile.weights
    references = profile.references
    if weights is None or references is None:
        raise ValueError(
            f"perfil {profile.id!r}: ranking normalizado exige pesos e "
            "referencias"
        )

    total = 0.0
    for objective in profile.objectives:
        reference = references.for_objective(objective)
        if reference <= 0.0:
            raise ValueError(
                f"perfil {profile.id!r}: referencia de {objective.value} deve "
                f"ser positiva (recebido {reference!r})"
            )
        weight = weights.for_objective(objective)
        total += weight * (objective_value(values, objective) / reference)
    return total


def rank_candidates(
    candidates: Iterable[RouteCandidate],
    profile: RouteProfile,
    *,
    evaluator: ObjectiveEvaluator | None = None,
) -> list[RankedCandidate]:
    """Ordena candidatas pela preferencia do perfil (rank 0 = melhor)."""
    active = evaluator if evaluator is not None else RouteObjectiveEvaluator()
    items = [(candidate, active.evaluate(candidate.metrics)) for candidate in candidates]

    if profile.ranking_strategy is RankingStrategy.WEIGHTED_NORMALIZED:
        items.sort(key=lambda item: _weighted_key(item, profile))
    else:
        items.sort(
            key=functools.cmp_to_key(
                lambda a, b: _lexicographic_compare(a, b, profile)
            )
        )

    return [
        RankedCandidate(
            candidate=candidate,
            rank=index,
            profile_score=score_from_values(values, profile),
        )
        for index, (candidate, values) in enumerate(items)
    ]


def rank_routes(
    routes: Iterable[Route],
    profile: RouteProfile,
    *,
    evaluator: ObjectiveEvaluator | None = None,
) -> list[RankedRoute]:
    """Ordena rotas pela preferencia do perfil (rank 0 = melhor)."""
    candidates = [RouteCandidate.from_route(route) for route in routes]
    return [
        RankedRoute(
            route=ranked.candidate.route,
            rank=ranked.rank,
            profile_score=ranked.profile_score,
        )
        for ranked in rank_candidates(candidates, profile, evaluator=evaluator)
    ]


def order_routes(
    routes: Iterable[Route],
    profile: RouteProfile,
    *,
    evaluator: ObjectiveEvaluator | None = None,
) -> list[Route]:
    """Atalho: apenas as rotas, na ordem de preferencia do perfil."""
    return [
        ranked.route
        for ranked in rank_routes(routes, profile, evaluator=evaluator)
    ]


@dataclass(frozen=True, slots=True)
class ProfileRanker:
    """Componente de ranking: ordena candidatas segundo o perfil."""

    evaluator: ObjectiveEvaluator | None = None

    def rank(
        self, candidates: Iterable[RouteCandidate], profile: RouteProfile
    ) -> list[RankedCandidate]:
        return rank_candidates(candidates, profile, evaluator=self.evaluator)


# --------------------------------------------------------------------------- internos
def _lexicographic_compare(
    a: tuple[RouteCandidate, ObjectiveValues],
    b: tuple[RouteCandidate, ObjectiveValues],
    profile: RouteProfile,
) -> int:
    tolerance = profile.tolerance or RankingTolerance()
    candidate_a, values_a = a
    candidate_b, values_b = b

    for objective in profile.objectives:
        allowed = tolerance.for_objective(objective)
        value_a = objective_value(values_a, objective)
        value_b = objective_value(values_b, objective)
        if value_a < value_b - allowed:
            return -1
        if value_a > value_b + allowed:
            return 1

    key_a = (candidate_a.route.label_id, candidate_a.route_id)
    key_b = (candidate_b.route.label_id, candidate_b.route_id)
    return (key_a > key_b) - (key_a < key_b)


def _weighted_key(
    item: tuple[RouteCandidate, ObjectiveValues], profile: RouteProfile
) -> tuple:
    candidate, values = item
    score = score_from_values(values, profile)
    if score is None:  # pragma: no cover - garantido pela estrategia
        score = 0.0
    return (
        score,
        *(objective_value(values, objective) for objective in profile.objectives),
        float(candidate.route.label_id),
        candidate.route_id,
    )
