"""Selecao das N rotas: ranking por perfil, diversidade e fallback.

Modulo **proprio** e independente da busca: selecionar N rotas e diferente de
encontrar a fronteira Pareto. Recebe candidatas reconstruidas com metricas
finais (``RouteCandidate``), nao conhece a fila A* nem recalcula fisica. O
pipeline e separado em etapas testaveis:

1. ``rank_candidates`` — ordena pela preferencia do perfil (``ranking.py``);
2. diversidade greedy — aceita uma candidata quando nao e demasiado semelhante
   (``diversity.py``);
3. fallback — quando a diversidade impede preencher o minimo, a politica do
   perfil decide o que fazer.

``N`` e um limite de **apresentacao**, nao de exploracao: o A* sempre busca a
fronteira completa. O score do perfil serve para **ordenar**; ele nao substitui a
fronteira Pareto.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .candidates import RouteCandidate, RouteCandidatePool, RouteDeduplicator
from .diversity import is_too_similar
from .evaluator import ObjectiveEvaluator, evaluate, objective_value
from .profiles import (
    DiversityConfig,
    DiversityFallback,
    RouteObjective,
    RouteProfile,
    route_profile_for,
)
from .ranking import RankedCandidate, rank_candidates
from .route import Route

if TYPE_CHECKING:
    from .astar import SearchResult


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    """Como escolher as N rotas (limite de apresentacao).

    ``diversity`` sobrescreve a configuracao de diversidade do perfil quando
    informado; caso contrario usa ``RouteProfile.diversity``. Orquestradores de
    alto nivel usam ``min_routes = max_routes`` para realmente preencher N.
    """

    max_routes: int
    min_routes: int = 1
    diversity: DiversityConfig | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_routes, bool)
            or not isinstance(self.max_routes, int)
            or self.max_routes <= 0
        ):
            raise ValueError(
                "max_routes deve ser um inteiro positivo "
                f"(recebido {self.max_routes!r})"
            )
        if (
            isinstance(self.min_routes, bool)
            or not isinstance(self.min_routes, int)
            or self.min_routes <= 0
        ):
            raise ValueError(
                "min_routes deve ser um inteiro positivo "
                f"(recebido {self.min_routes!r})"
            )
        if self.min_routes > self.max_routes:
            raise ValueError(
                "min_routes nao pode exceder max_routes "
                f"({self.min_routes} > {self.max_routes})"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_routes": self.max_routes,
            "min_routes": self.min_routes,
            "diversity": (
                None if self.diversity is None else self.diversity.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class SelectionReason:
    """Por que a rota entrou: objetivo primario e aceite de diversidade."""

    primary_objective: RouteObjective
    primary_value: float
    diversity_accepted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_objective": self.primary_objective.value,
            "primary_value": self.primary_value,
            "diversity_accepted": self.diversity_accepted,
        }


@dataclass(frozen=True, slots=True)
class SelectedRoute:
    """Candidata escolhida: posicao, score do perfil e razao da escolha."""

    candidate: RouteCandidate
    rank: int
    profile_score: float | None
    selection_reason: SelectionReason

    @property
    def route(self) -> Route:
        return self.candidate.route

    @property
    def route_id(self) -> str:
        return self.candidate.route_id

    @property
    def metrics(self):
        return self.candidate.metrics

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "profile_score": self.profile_score,
            "reason": self.selection_reason.to_dict(),
            "route": self.route.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Resultado da selecao.

    * ``selected``: candidatas escolhidas, em ordem de ``rank``;
    * ``pareto_route_count``: tamanho da fronteira de entrada (pos-dedup);
    * ``rejected_as_similar_count``: candidatas descartadas por semelhanca no
      limiar inicial;
    * ``diversity_relaxed``: True se o limiar foi relaxado para preencher;
    * ``threshold_used``: limiar efetivamente aplicado (apos relaxamento);
    * ``dropped_duplicates``: candidatas colapsadas na deduplicacao.
    """

    selected: tuple[SelectedRoute, ...]
    pareto_route_count: int
    rejected_as_similar_count: int
    diversity_relaxed: bool
    threshold_used: float | None = None
    dropped_duplicates: int = 0

    @property
    def count(self) -> int:
        return len(self.selected)

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(selected.route for selected in self.selected)

    @property
    def candidates(self) -> tuple[RouteCandidate, ...]:
        return tuple(selected.candidate for selected in self.selected)

    def __len__(self) -> int:
        return len(self.selected)

    def __iter__(self):
        return iter(self.selected)

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": self.count,
            "pareto_route_count": self.pareto_route_count,
            "rejected_as_similar_count": self.rejected_as_similar_count,
            "diversity_relaxed": self.diversity_relaxed,
            "threshold_used": self.threshold_used,
            "dropped_duplicates": self.dropped_duplicates,
            "routes": [selected.to_dict() for selected in self.selected],
        }


def select_candidates(
    candidates: Iterable[RouteCandidate],
    profile: RouteProfile,
    config: SelectionConfig | None = None,
    *,
    evaluator: ObjectiveEvaluator | None = None,
) -> SelectionResult:
    """Seleciona ate ``config.max_routes`` candidatas, em ordem de preferencia.

    Diversidade greedy, com fallback configuravel pelo perfil:

    * ``NONE``: ignora diversidade e devolve as primeiras ``max_routes``;
    * ``RETURN_FEWER``: estrito — pode devolver menos de ``min_routes``;
    * ``RELAX_THRESHOLD``: relaxa o limiar por passos ate
      ``relaxation.max_threshold``, preenchendo com as rejeitadas em ordem de
      ``rank`` (resultado informa ``diversity_relaxed=True``).
    """
    if config is None:
        config = SelectionConfig(max_routes=3)

    diversity = config.diversity or profile.diversity
    ranked = rank_candidates(candidates, profile, evaluator=evaluator)
    pareto_count = len(ranked)

    # ------------------------------------------------------- politica NONE
    if diversity.fallback is DiversityFallback.NONE:
        selected = tuple(
            _selected_from(ranked_candidate, profile, evaluator, accepted=False)
            for ranked_candidate in ranked[: config.max_routes]
        )
        return SelectionResult(
            selected=selected,
            pareto_route_count=pareto_count,
            rejected_as_similar_count=0,
            diversity_relaxed=False,
            threshold_used=None,
        )

    # ------------------------------------------------- diversidade greedy
    # ``accepted`` guarda (candidata_ordenada, aceita_na_diversidade). Na fase
    # greedy a diversidade e honrada (True); no relaxamento o limiar e
    # afrouxado, entao a candidata entra como alternativa proxima (False).
    accepted: list[tuple[RankedCandidate, bool]] = []
    chosen_routes: list[Route] = []
    rejected: list[RankedCandidate] = []

    for ranked_candidate in ranked:
        if len(accepted) >= config.max_routes:
            break
        route = ranked_candidate.candidate.route
        if _is_distinct(route, chosen_routes, diversity):
            accepted.append((ranked_candidate, True))
            chosen_routes.append(route)
        else:
            rejected.append(ranked_candidate)

    threshold_used = diversity.max_similarity
    relaxed = False

    # ------------------------------------------------------- fallback
    if (
        len(accepted) < config.min_routes
        and diversity.fallback is DiversityFallback.RELAX_THRESHOLD
    ):
        threshold = diversity.max_similarity
        max_threshold = diversity.relaxation.max_threshold
        step = diversity.relaxation.step

        while len(accepted) < config.max_routes and threshold < max_threshold:
            threshold = min(max_threshold, threshold + step)
            for ranked_candidate in rejected:
                if len(accepted) >= config.max_routes:
                    break
                if any(ranked_candidate is item[0] for item in accepted):
                    continue
                route = ranked_candidate.candidate.route
                if _is_distinct(route, chosen_routes, diversity, threshold):
                    accepted.append((ranked_candidate, False))
                    chosen_routes.append(route)
                    # Relaxamento so conta quando de fato admite uma rota.
                    relaxed = True
                    threshold_used = threshold
            if threshold >= max_threshold:
                break

    accepted.sort(key=lambda item: item[0].rank)

    selected = tuple(
        _selected_from(ranked_candidate, profile, evaluator, accepted=flag)
        for ranked_candidate, flag in accepted
    )

    return SelectionResult(
        selected=selected,
        pareto_route_count=pareto_count,
        rejected_as_similar_count=len(rejected),
        diversity_relaxed=relaxed,
        threshold_used=threshold_used,
    )


def select_routes(
    routes: Iterable[Route],
    profile: RouteProfile,
    config: SelectionConfig | None = None,
    *,
    evaluator: ObjectiveEvaluator | None = None,
    deduplicator: RouteDeduplicator | None = None,
) -> SelectionResult:
    """Atalho: forma o pool de candidatas (com dedup) e seleciona."""
    pool = RouteCandidatePool.from_routes(routes, deduplicator=deduplicator)
    selection = select_candidates(
        pool.candidates, profile, config, evaluator=evaluator
    )
    if pool.dropped_duplicates:
        selection = replace(
            selection, dropped_duplicates=pool.dropped_duplicates
        )
    return selection


def select_from_result(
    result: SearchResult,
    profile: RouteProfile | str | None = None,
    config: SelectionConfig | None = None,
    *,
    evaluator: ObjectiveEvaluator | None = None,
    deduplicator: RouteDeduplicator | None = None,
) -> SelectionResult:
    """Atalho: forma o pool a partir de um ``SearchResult`` e seleciona.

    Sem ``profile``, deriva o perfil de selecao do id do perfil de busca
    (``result.profile.id``), mantendo as duas visoes consistentes.
    """
    resolved = _resolve_profile(profile, result)
    pool = RouteCandidatePool.from_result(result, deduplicator=deduplicator)
    selection = select_candidates(
        pool.candidates, resolved, config, evaluator=evaluator
    )
    if pool.dropped_duplicates:
        selection = replace(
            selection, dropped_duplicates=pool.dropped_duplicates
        )
    return selection


@dataclass(frozen=True, slots=True)
class RouteSelector:
    """Componente de selecao: escolhe ate N candidatas diversas."""

    evaluator: ObjectiveEvaluator | None = None

    def select(
        self,
        candidates: Iterable[RouteCandidate],
        profile: RouteProfile,
        request: SelectionConfig | None = None,
    ) -> SelectionResult:
        return select_candidates(
            candidates, profile, request, evaluator=self.evaluator
        )


# Vocabulario da arquitetura de N rotas.
RouteSelectionRequest = SelectionConfig
RouteSelectionResult = SelectionResult


# --------------------------------------------------------------------------- internos
def _resolve_profile(
    profile: RouteProfile | str | None, result: SearchResult
) -> RouteProfile:
    if isinstance(profile, RouteProfile):
        return profile
    if profile is None:
        return route_profile_for(result.profile.id)
    return route_profile_for(profile)


def _is_distinct(
    candidate: Route,
    chosen: list[Route],
    diversity: DiversityConfig,
    threshold: float | None = None,
) -> bool:
    limit = diversity.max_similarity if threshold is None else threshold
    return all(
        not is_too_similar(candidate, other, diversity.metric, limit)
        for other in chosen
    )


def _selected_from(
    ranked_candidate: RankedCandidate,
    profile: RouteProfile,
    evaluator: ObjectiveEvaluator | None,
    *,
    accepted: bool,
) -> SelectedRoute:
    values = evaluate(ranked_candidate.candidate.metrics, evaluator)
    return SelectedRoute(
        candidate=ranked_candidate.candidate,
        rank=ranked_candidate.rank,
        profile_score=ranked_candidate.profile_score,
        selection_reason=SelectionReason(
            primary_objective=profile.primary,
            primary_value=objective_value(values, profile.primary),
            diversity_accepted=accepted,
        ),
    )
