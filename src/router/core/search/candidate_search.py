"""Orquestracao comum da busca Pareto ate o pool de candidatas.

Esta e a unica implementacao da etapa compartilhada por consumidores de baixo
e alto nivel:

    MultiObjectiveAStar -> SearchResult -> RouteCandidatePool

As camadas acima continuam livres para aplicar selecao, fisica completa, ML,
seguranca, explicacoes e contratos diferentes.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from .astar import MultiObjectiveAStar, SearchResult
from .candidates import RouteCandidatePool, RouteDeduplicator
from .config import SearchConfig
from .heuristic import Heuristic
from .objectives import SearchProfile
from .state import SearchState
from .traversal_model import TraversalModel
from router.core.data.graph.model import RoutingGraph


CandidateStateCallback = Callable[[SearchState, MultiObjectiveAStar], None]


@dataclass(frozen=True, slots=True)
class CandidateSearchResult:
    """Resultado comum da busca e da preparacao de candidatas."""

    astar: MultiObjectiveAStar
    result: SearchResult
    pool: RouteCandidatePool


def search_candidates(
    graph: RoutingGraph,
    traversal: TraversalModel,
    profile: SearchProfile,
    origin: Hashable,
    destination: Hashable,
    *,
    initial_speed_mps: float = 0.0,
    heuristic: Heuristic | None = None,
    search_config: SearchConfig | None = None,
    deduplicator: RouteDeduplicator | None = None,
    on_candidate: Callable[[tuple[str | None, ...], int, int], None] | None = None,
    on_candidate_state: CandidateStateCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    stop_when_candidate: Callable[[], bool] | None = None,
) -> CandidateSearchResult:
    """Executa busca Pareto e prepara o pool sem selecionar a apresentacao."""
    astar = MultiObjectiveAStar(
        graph,
        traversal,
        profile,
        heuristic=heuristic,
        config=search_config,
        initial_speed_mps=initial_speed_mps,
    )

    callback = None
    if on_candidate_state is not None:
        callback = lambda state: on_candidate_state(state, astar)

    result = astar.search(
        origin,
        destination,
        on_candidate=on_candidate,
        on_candidate_state=callback,
        should_cancel=should_cancel,
        stop_when_candidate=stop_when_candidate,
    )
    pool = RouteCandidatePool.from_result(result, deduplicator=deduplicator)
    return CandidateSearchResult(astar=astar, result=result, pool=pool)
