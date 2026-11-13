"""Busca A* multiobjetivo (MOA*) por perfil.

A busca **nao** calcula fisica nem decide preferencia de superficie. Ela:

1. chama o modelo fisico (``TraversalModel``) para cada transicao;
2. acumula as metricas na label (``advance_state``);
3. mantem alternativas nao dominadas por ``(no, bucket de velocidade)``
   (``FrontierManager``);
4. explora na ordem dada por uma fila lexicografica (``LexicographicQueue``),
   com o objetivo principal do perfil primeiro.

A fila define apenas a **ordem de exploracao** — a fronteira Pareto nao e a
fila. O criterio de parada e conservador: a busca termina quando a fila de
labels ativas esvazia. Selecionar as N rotas finais e uma etapa **posterior**,
fora desta classe.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass

from router.core.data.graph.model import Arc, RoutingGraph
from router.core.physics.models import TraversalResult
from .circulation import (
    CirculationPolicy,
    ConditionEvaluator,
    DEFAULT_POLICY,
    arc_decision,
    constrained_nodes,
    transition_decision,
)
from .config import SearchConfig
from .dominance import Dominance, SpeedBucketDominance
from .frontier import FrontierManager
from .heuristic import Heuristic
from .objectives import SearchProfile, profile_for
from .queue import LexicographicQueue
from .state import SearchState, advance_state, initial_state, route_arcs
from .traversal_model import (
    UNKNOWN_SPEED,
    TraversalModel,
    _UnknownSpeedSentinel,
)


class SearchCancelled(Exception):
    """Cancelamento solicitado pelo consumidor da busca progressiva."""


def is_feasible(result: TraversalResult) -> bool:
    """True se o resultado fisico pode ser usado numa label.

    Uma travessia com metrica nao finita (ou velocidade final negativa) nao
    pode virar estado: descartamos em vez de propagar ``inf``/``nan`` na fila.
    """
    return (
        math.isfinite(result.time_s)
        and math.isfinite(result.distance_m)
        and math.isfinite(result.final_speed_mps)
        and math.isfinite(result.mechanical_energy_j)
        and result.final_speed_mps >= 0.0
    )


def model_max_speed(
    traversal: TraversalModel,
) -> float | None | _UnknownSpeedSentinel:
    """Teto de velocidade **declarado** pelo modelo, se houver.

    Distingue os tres estados:

    * ``float`` — teto real conhecido (m/s);
    * ``None`` — o modelo declara-se ilimitado;
    * ``UNKNOWN_SPEED`` — o modelo nao expoe ``max_speed_mps`` (desconhecido).

    A distincao importa: um teto desconhecido nao pode ser tratado como
    "ilimitado" (nao poderiamos punir heuristicas) nem como finito (nao
    poderiamos confiar num valor).
    """
    return getattr(traversal, "max_speed_mps", UNKNOWN_SPEED)


def _default_heuristic_speed(traversal: TraversalModel) -> float | None:
    """Teto para a heuristica padrao: teto declarado, ou ``None`` se desconhecido.

    Um teto desconhecido (``UNKNOWN_SPEED``) vira ``None`` -> limite de tempo
    ``0`` (admissivel porem mais fraco). Um teto declaradamente ilimitado
    (``None``) permanece ``None``.
    """
    ceiling = model_max_speed(traversal)
    if ceiling is UNKNOWN_SPEED:
        return None
    return ceiling


def _check_heuristic_admissible(
    heuristic: Heuristic, traversal: TraversalModel
) -> None:
    """Recusa heuristicas de tempo **inadmissiveis**.

    A heuristica usa ``tempo >= distancia / max_speed_mps``. Ela so e um limite
    inferior valido se ``max_speed_mps`` for um teto real. Rejeitamos quando:

    * o modelo declara **ilimitado** (``None``) e a heuristica tem teto finito;
    * o modelo anuncia um teto finito e a heuristica e **mais restritiva** que
      ele.

    Teto **desconhecido** (``UNKNOWN_SPEED``) ou heuristica ``None`` (limite de
    tempo desligado) passam: nos dois casos o limite de tempo e ``0`` ou nao ha
    como superestimar de forma comprovavel.
    """
    ceiling = model_max_speed(traversal)
    heuristic_speed = heuristic.max_speed_mps
    if ceiling is UNKNOWN_SPEED or heuristic_speed is None:
        return
    if ceiling is None:
        raise ValueError(
            "heuristica inadmissivel: o modelo declara velocidade ilimitada "
            f"(max_speed_mps=None), mas a heuristica impoe teto finito "
            f"({heuristic_speed}). Use max_speed_mps=None (desliga o limite de "
            "tempo) ou defina um teto real em Environment.safe_max_speed_mps."
        )
    if heuristic_speed < ceiling - 1e-9:
        raise ValueError(
            "heuristica inadmissivel: max_speed_mps "
            f"({heuristic_speed}) e menor que o teto do modelo "
            f"({ceiling}); o limite inferior de tempo superestimaria. Use "
            "max_speed_mps >= teto do modelo, ou None para desligar o limite "
            "de tempo da heuristica."
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Fronteira Pareto de destino devolvida por ``MultiObjectiveAStar``.

    ``labels`` contem as labels nao dominadas que chegaram ao destino, em ordem
    deterministica (valores objetivo do perfil, depois ``label_id``). A
    reconstrucao das rotas usa ``parent``/``incoming_arc_id`` via
    ``arc_sequences()``.

    Sinais de completude (independentes):

    * ``truncated`` — a busca foi **interrompida** por ``max_expansions`` (a
      fronteira de destino pode estar incompleta);
    * ``frontier_truncated`` / ``dropped_labels`` — ``max_labels_per_key``
      removeu labels **nao dominadas** dentro de uma chave, sem interromper a
      busca. Nao confundir com ``truncated`` nem com a deduplicacao posterior
      de candidatas (``dropped_duplicates``, outra camada).
    """

    origin: Hashable
    destination: Hashable
    profile: SearchProfile
    labels: tuple[SearchState, ...]
    expanded: int = 0
    generated: int = 0
    truncated: bool = False
    frontier_truncated: bool = False
    dropped_labels: int = 0

    @property
    def reached(self) -> bool:
        """True se ao menos uma rota nao dominada chegou ao destino."""
        return bool(self.labels)

    @property
    def count(self) -> int:
        return len(self.labels)

    def arc_sequences(self) -> tuple[tuple[str | None, ...], ...]:
        """Sequencia de arcos de cada rota da fronteira de destino."""
        return tuple(tuple(route_arcs(label)) for label in self.labels)

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "profile": self.profile.id,
            "objectives": list(self.profile.names),
            "reached": self.reached,
            "routes": self.count,
            "expanded": self.expanded,
            "generated": self.generated,
            "truncated": self.truncated,
            "frontier_truncated": self.frontier_truncated,
            "dropped_labels": self.dropped_labels,
            "labels": [label.to_dict() for label in self.labels],
        }


class MultiObjectiveAStar:
    """Busca Pareto por perfil sobre um grafo finito.

    Parametros:

    * ``graph``: grafo ciclavel (fornece nos e arcos de saida);
    * ``traversal``: ``TraversalModel`` — a unica fonte de tempo/energia;
    * ``profile``: ``SearchProfile`` ou nome de perfil (``profile_for``);
    * ``heuristic``: limites inferiores; por padrao ``Heuristic(graph)`` com o
      teto de velocidade **anunciado pelo modelo** (``max_speed_mps``), o que
      mantem a heuristica de tempo admissivel. Se o modelo nao anunciar teto,
      o limite de tempo da heuristica e ``0``;
    * ``dominance``: regra de dominancia; por padrao
      ``SpeedBucketDominance(profile.objective_set, speed_step_mps=config.
      speed_step_mps)``;
    * ``config``: controles de desempenho (``SearchConfig``);
    * ``initial_speed_mps``: estado fisico inicial na origem;
    * ``condition_evaluator``: avaliador de condicoes de restricao (``None`` =
      toda condicao e tratada conservadoramente como nao avaliavel);
    * ``circulation_policy``: politica para vereditos ``UNKNOWN`` (conservadora
      por padrao).

    Os controles de ``config`` (``max_expansions``, ``max_labels_per_node``)
    sao de **desempenho**: podem interromper a busca ou remover labels nao
    dominadas. Os dois efeitos sao **sinalizados de forma independente** no
    resultado:

    * ``max_expansions`` interrompe a busca -> ``truncated=True``;
    * ``max_labels_per_node`` remove labels nao dominadas por chave ->
      ``frontier_truncated=True`` e ``dropped_labels`` > 0.

    Nenhum dos dois se confunde com a deduplicacao posterior de candidatas.

    Os guardrails de circulacao (ADR-026) filtram a expansao apenas quando ha
    **restricao registrada** (acesso ou conversao); sem nenhuma, a busca e
    **identica** ao comportamento anterior.
    """

    def __init__(
        self,
        graph: RoutingGraph,
        traversal: TraversalModel,
        profile: SearchProfile | str,
        *,
        heuristic: Heuristic | None = None,
        dominance: Dominance | None = None,
        config: SearchConfig | None = None,
        initial_speed_mps: float = 0.0,
        condition_evaluator: ConditionEvaluator | None = None,
        circulation_policy: CirculationPolicy = DEFAULT_POLICY,
    ) -> None:
        if not math.isfinite(initial_speed_mps) or initial_speed_mps < 0.0:
            raise ValueError(
                "initial_speed_mps deve ser nao negativo e finito "
                f"(recebido {initial_speed_mps!r})"
            )

        self.graph = graph
        self.traversal = traversal
        self.profile = (
            profile_for(profile) if isinstance(profile, str) else profile
        )
        self.config = config or SearchConfig()
        self.initial_speed_mps = float(initial_speed_mps)
        self.condition_evaluator = condition_evaluator
        self.circulation_policy = circulation_policy

        # Guardrails de circulacao: ativados pelos **dados**, nao por config.
        # Sem regras de acesso nem restricoes de conversao, sao inertes.
        self._has_access_rules = any(
            bool(arc.restrictions) for arc in graph.arcs.values()
        )
        self._has_turn_restrictions = bool(graph.turn_restrictions)
        self._guardrails_active = (
            self._has_access_rules or self._has_turn_restrictions
        )

        self.heuristic = (
            heuristic
            if heuristic is not None
            else Heuristic(
                graph, max_speed_mps=_default_heuristic_speed(traversal)
            )
        )
        if heuristic is not None:
            _check_heuristic_admissible(heuristic, traversal)
        self.dominance = (
            dominance
            if dominance is not None
            else SpeedBucketDominance(
                self.profile.objective_set,
                speed_step_mps=self.config.speed_step_mps,
                arrival_class_nodes=constrained_nodes(graph),
            )
        )

        # Ultima fronteira explorada (introspeccao/depuracao).
        self.frontier: FrontierManager | None = None

    # ---------------------------------------------------------------- busca
    def search(
        self, origin: Hashable, destination: Hashable, *,
        on_candidate: Callable[[tuple[str | None, ...], int, int], None] | None = None,
        on_candidate_state: Callable[[SearchState], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        stop_when_candidate: Callable[[], bool] | None = None,
    ) -> SearchResult:
        """Explora a fronteira completa de destino entre ``origin`` e ``destination``.

        Nao para ao encontrar N rotas: uma rota candidata pode ser dominada por
        outra ainda nao explorada. A parada segura e a fila ativa vazia.

        ``on_candidate`` recebe os IDs dos arcos e os contadores de expansão e
        geração quando uma label ativa chega ao destino. É uma candidata bruta:
        ainda precisa de avaliação física completa e guardrails antes de ser
        apresentada; pode ser dominada posteriormente. O callback é síncrono
        e suas exceções são propagadas ao chamador.

        ``on_candidate_state`` recebe a label interna completa para avaliação
        síncrona. O consumidor não deve modificá-la nem reter a referência: a
        busca pode invalidá-la ao descobrir uma alternativa dominante.
        """
        self.graph.get_node(origin)
        self.graph.get_node(destination)

        # A chave do FrontierManager e (no, bucket); o config fala em
        # "labels por no", mas o limite e aplicado por chave para conter a
        # explosao de estados fisicos.
        manager = FrontierManager(
            self.dominance,
            max_labels_per_key=self.config.max_labels_per_node,
        )
        # A fronteira corrente também fica disponível ao callback síncrono de
        # progresso; ela continua pertencendo à busca e não deve ser alterada.
        self.frontier = manager
        queue = LexicographicQueue()

        counter = itertools.count(1)
        start = initial_state(
            origin,
            self.traversal.mass_kg,
            self.initial_speed_mps,
            label_id=0,
        )
        manager.insert(start)
        queue.push(start, self._priority(start, destination))

        expanded = 0
        generated = 0
        truncated = False

        while not queue.is_empty:
            if should_cancel is not None and should_cancel():
                raise SearchCancelled("Busca cancelada pelo usuário")
            label, _ = queue.pop()

            # Labels removidas da fronteira por dominancia sao invalidadas aqui.
            if not label.active:
                continue

            # O destino nao e expandido: ele e a fronteira de destino.
            if label.node_id == destination:
                if on_candidate is not None:
                    on_candidate(tuple(route_arcs(label)), expanded, generated)
                if on_candidate_state is not None:
                    on_candidate_state(label)
                if stop_when_candidate is not None and stop_when_candidate():
                    break
                continue

            if (
                self.config.max_expansions is not None
                and expanded >= self.config.max_expansions
            ):
                truncated = True
                break

            expanded += 1
            history_way_ids = (
                self._history_way_ids(label)
                if self._guardrails_active and self._has_turn_restrictions
                else ()
            )
            for arc in self.graph.out_arcs(label.node_id):
                # [G1] acesso do arco / [G2] conversao — antes de gastar fisica.
                if self._guardrails_active and not self._allows(
                    label, arc, history_way_ids
                ):
                    continue
                result = self.traversal.traverse(
                    arc, label.physical_state.speed_mps
                )
                if not is_feasible(result):
                    continue

                next_label = advance_state(
                    label,
                    arc,
                    result,
                    mass_kg=self.traversal.mass_kg,
                    label_id=next(counter),
                )
                generated += 1
                if manager.insert(next_label):
                    queue.push(next_label, self._priority(next_label, destination))

        self.frontier = manager
        labels = order_labels(self.profile, manager.labels_at(destination))

        return SearchResult(
            origin=origin,
            destination=destination,
            profile=self.profile,
            labels=labels,
            expanded=expanded,
            generated=generated,
            truncated=truncated,
            frontier_truncated=manager.frontier_truncated,
            dropped_labels=manager.dropped_labels,
        )

    # -------------------------------------------------------------- interno
    def _allows(
        self,
        label: SearchState,
        arc: Arc,
        history_way_ids: Sequence[int | None],
    ) -> bool:
        """Guardrail de circulacao: ``False`` se acesso ou conversao proibidos.

        Apenas consulta o contrato (``arc_decision``/``transition_decision``);
        nao recalcula fisica nem cria custo. A política conservadora pode
        resolver ``UNKNOWN`` como ``FORBID``.
        """
        if self._has_access_rules:
            access = arc_decision(
                arc,
                evaluator=self.condition_evaluator,
                policy=self.circulation_policy,
            )
            if access.forbids:
                return False
        if self._has_turn_restrictions and label.incoming_arc_id is not None:
            from_arc = self.graph.get_arc(label.incoming_arc_id)
            transition = transition_decision(
                self.graph,
                from_arc,
                arc,
                label.node_id,
                history_way_ids=history_way_ids,
                evaluator=self.condition_evaluator,
                policy=self.circulation_policy,
            )
            if transition.forbids:
                return False
        return True

    def _history_way_ids(
        self, label: SearchState
    ) -> tuple[int | None, ...]:
        """``way_id`` dos arcos da origem ate ``label`` (mais recente por ultimo).

        Segue a cadeia ``parent`` (ADR-026 §4.2, opcao a): nenhum estado novo, no
        maximo ``O(profundidade)`` por expansao e apenas quando ha restricao de
        conversao.
        """
        ways: list[int | None] = []
        node: SearchState | None = label
        while node is not None and node.incoming_arc_id is not None:
            ways.append(self.graph.get_arc(node.incoming_arc_id).osm_way_id)
            node = node.parent
        ways.reverse()
        return tuple(ways)

    def _priority(
        self, label: SearchState, destination: Hashable
    ) -> tuple[float, ...]:
        """Vetor ``f = g + h`` na ordem do perfil, com ``label_id`` no fim.

        Ordena a exploracao pelo objetivo principal do perfil primeiro. Nao
        significa que o resultado final e lexicografico: a fronteira Pareto
        continua preservando as alternativas nao dominadas.
        """
        g = self.profile.values(label)
        h = self.heuristic.lower_bounds(
            label.node_id, destination, self.profile.names
        )
        return tuple(gv + hv for gv, hv in zip(g, h)) + (float(label.label_id),)


def order_labels(
    profile: SearchProfile, labels: Iterable[SearchState]
) -> tuple[SearchState, ...]:
    """Ordena labels pelos valores do perfil e desempata por ``label_id``."""
    return tuple(
        sorted(
            labels,
            key=lambda label: (*profile.values(label), float(label.label_id)),
        )
    )


def arc_ids_of(state: SearchState) -> Sequence[str | None]:
    """Atalho para a sequencia de arcos de uma unica label."""
    return tuple(route_arcs(state))
