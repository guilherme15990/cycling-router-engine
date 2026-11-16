"""Busca independente de custo aprendido; o modelo é uma função fornecida pelo app."""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import itertools
import math

from .astar import is_feasible
from .circulation import DEFAULT_POLICY, arc_decision, transition_decision
from .config import SearchConfig
from .exceptions import SearchCancelled
from .state import initial_state, advance_state
from router.core.physics.models import SimulationStatus


@dataclass(frozen=True)
class LearnedSearchResult:
    state: object | None
    cost: tuple[float, ...] | None
    expanded: int
    status: str
    state_approximated: bool


def search_learned(graph, traversal, origin, destination, cost_of, *, initial_speed_mps=0.0,
                   config=None, should_cancel=None, condition_evaluator=None, circulation_policy=DEFAULT_POLICY):
    """Dijkstra vetorial, sem poda pela frente Pareto física e sem heurística suposta.

    A chave mantém arco de chegada, contexto via-way e velocidade. Buckets
    aproximam o estado como na busca física e são declarados no resultado.
    O custo não depende de fadiga oculta; futuros modelos temporais exigirão
    acrescentar suas dependências ao estado e à chave.
    """
    config = config or SearchConfig()
    counter = itertools.count()
    context_size = max((len(r.via_way_ids) + 1 for r in graph.turn_restrictions.values() if r.via_way_ids), default=0)

    def key(state):
        speed = state.speed_mps if config.exact_speed else round(state.speed_mps / config.speed_step_mps)
        return state.node_id, state.incoming_arc_id, state.turn_context, speed

    start = initial_state(origin, traversal.mass_kg, initial_speed_mps)
    # A dimensão é descoberta na primeira transição; zero escalar compara
    # corretamente com os custos vetoriais não negativos no início.
    queue = [((), next(counter), start)]
    best = {key(start): ()}
    expanded = 0
    while queue:
        if should_cancel and should_cancel():
            raise SearchCancelled("Busca ML cancelada")
        cost, _, current = heapq.heappop(queue)
        if best.get(key(current)) != cost:
            continue
        if current.node_id == destination:
            return LearnedSearchResult(current, cost, expanded, "found", not config.exact_speed)
        if config.max_expansions is not None and expanded >= config.max_expansions:
            return LearnedSearchResult(None, None, expanded, "expansion_limit", not config.exact_speed)
        expanded += 1
        for arc in graph.iter_out_arcs(current.node_id):
            if should_cancel and should_cancel():
                raise SearchCancelled("Busca ML cancelada")
            if not traversal.allows_arc(arc) or arc_decision(arc, evaluator=condition_evaluator,
                    policy=circulation_policy, access_scopes=traversal.access_scopes, access_from_tags=True).forbids:
                continue
            if current.incoming_arc_id is not None and transition_decision(graph,
                    graph.get_arc(current.incoming_arc_id), arc, current.node_id,
                    history_way_ids=current.turn_context, evaluator=condition_evaluator, policy=circulation_policy).forbids:
                continue
            part = traversal.traverse(arc, current.speed_mps)
            if not is_feasible(part) or part.stalled or part.result_status is SimulationStatus.UNAVAILABLE:
                continue
            step_cost = tuple(cost_of(part))
            if not step_cost or any(not math.isfinite(value) or value < 0 for value in step_cost) or (part.distance_m > 0 and not any(step_cost)):
                raise ValueError("custo ML deve ser finito, não negativo e não nulo")
            updated = tuple(a + b for a, b in zip(cost or (0.0,) * len(step_cost), step_cost))
            if cost and len(cost) != len(step_cost):
                raise ValueError("dimensão variável do custo ML")
            state = advance_state(current, arc, part, mass_kg=traversal.mass_kg, label_id=next(counter))
            if context_size:
                state.turn_context = (*current.turn_context, arc.osm_way_id)[-context_size:]
            state_key = key(state)
            if state_key not in best or updated < best[state_key]:
                best[state_key] = updated
                heapq.heappush(queue, (updated, next(counter), state))
    return LearnedSearchResult(None, None, expanded, "no_route", not config.exact_speed)
