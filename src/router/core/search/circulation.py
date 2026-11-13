"""Contrato de circulacao (Camada 03): acesso de arco e conversao entre arcos.

Este modulo formaliza **quando** um arco pode ser usado e **quando** uma
conversao entre arcos e permitida, reaproveitando as entidades ja preservadas
pelo ADR-024 (``AccessRule``, ``TurnRestriction``, ``BicycleAccess``). Ele
**nao** altera a busca, a fisica nem a estrutura Pareto: e um contrato puro,
deterministico, que a expansao (``astar.py``) e a validacao independente de
rotas poderao consultar.

Politica (conservadora, ADR-026 §3):

* **proibicao explicita** -> ``FORBID`` (dura, nunca custo ajustavel);
* **permissao explicita** -> ``ALLOW`` (legalidade declarada);
* **ausencia de regra** -> ``ALLOW`` para rotear, mas a legalidade **nao** e
  afirmada (``legality_asserted=False``);
* **condicao/contexto nao avaliavel** -> ``UNKNOWN``, reportado sem bloquear a
  rota quando a politica padrao o permite;
Contexto real do grafo (build OSM): ``bicycle=no``/``access=no`` sao resolvidos
na **construcao** (a via nao entra no grafo); por isso, nos arcos, as regras de
acesso representadas sao **condicionais** (``*:conditional``). O contrato cobre
tambem regras incondicionais, para grafos montados a mao e para a validacao de
rotas geradas pelo ML.

Ref.: ADR-026 e ``docs/adr/026-contrato-circulacao.md`` §2-§3.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from enum import Enum

from router.core.data.graph.model import Arc, RoutingGraph
from router.core.data.graph.restrictions import AccessRule, TurnRestriction

#: Avaliador de condicao: ``True``/``False`` se avaliavel; ``None`` se nao.
#: Sem avaliador (``None``), toda condicao e tratada como nao avaliavel.
ConditionEvaluator = Callable[[str], bool | None]

#: Escopos de acesso relevantes a bicicleta, do mais especifico ao mais geral
#: (OSM: ``bicycle`` tem precedencia sobre ``vehicle``, que tem sobre
#: ``access``). Apenas esses escopos sao considerados na decisao de acesso.
ACCESS_SCOPES: tuple[str, ...] = ("bicycle", "vehicle", "access")

#: Valores de acesso que **proibem** a circulacao ciclavel (vocabulario OSM).
FORBIDDEN_ACCESS_VALUES: frozenset[str] = frozenset(
    {"no", "private", "use_sidepath", "military", "restricted"}
)

#: Valores de acesso que **permitem** explicitamente a circulacao ciclavel.
#: Espelha ``osm.build.ALLOWED_BICYCLE_VALUES`` (importar ``osm`` criaria
#: dependencia indevida da Camada 03 sobre o pipeline de construcao).
ALLOWED_ACCESS_VALUES: frozenset[str] = frozenset(
    {"yes", "designated", "permissive", "official"}
)

#: Tipos de restricao de conversao **interpretaveis** (ADR-024 §2.2). Qualquer
#: outro tipo e tratado conservadoramente como nao interpretavel.
NO_TURN_TYPES: frozenset[str] = frozenset(
    {"no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn"}
)
ONLY_TURN_TYPES: frozenset[str] = frozenset(
    {"only_left_turn", "only_right_turn", "only_straight_on", "only_u_turn"}
)


class Verdict(str, Enum):
    """Veredito tri-estado de uma decisao de circulacao."""

    ALLOW = "allow"
    FORBID = "forbid"
    UNKNOWN = "unknown"


class DecisionReason(str, Enum):
    """Motivo de uma decisao (nunca silencioso; sempre auditavel)."""

    NO_RULE = "no_rule"
    EXPLICIT_FORBIDDEN = "explicit_forbidden"
    EXPLICIT_ALLOWED = "explicit_allowed"
    CONDITIONAL_FORBIDDEN = "conditional_forbidden"
    CONDITIONAL_ALLOWED = "conditional_allowed"
    CONDITION_UNRESOLVED = "condition_unresolved"
    CONTEXT_INSUFFICIENT = "context_insufficient"
    TURN_FORBIDDEN = "turn_forbidden"
    TURN_ONLY_SATISFIED = "turn_only_satisfied"
    TURN_ONLY_VIOLATED = "turn_only_violated"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Decision:
    """Resultado de uma consulta de circulacao.

    ``verdict`` ja e o veredito **efetivo** (apos a politica); ``raw_verdict``
    e o veredito **bruto** antes da politica, onde ``UNKNOWN`` distingue uma
    decisao que permanece incerta mesmo quando a regra de produto permite
    continuar.

    ``legality_asserted`` e ``True`` **apenas** quando uma regra explicita
    permite a circulacao; permanece ``False`` na ausencia de regra (roteavel sem
    legalidade afirmada) e quando a decisao veio de resolucao conservadora.
    """

    verdict: Verdict
    reason: DecisionReason
    raw_verdict: Verdict = Verdict.ALLOW
    legality_asserted: bool = False
    restriction_id: str | None = None

    @property
    def allows(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def forbids(self) -> bool:
        return self.verdict is Verdict.FORBID

    @property
    def unresolved(self) -> bool:
        """True se o veredito bruto era ``UNKNOWN`` (resolvido pela politica)."""
        return self.raw_verdict is Verdict.UNKNOWN

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "raw_verdict": self.raw_verdict.value,
            "reason": self.reason.value,
            "legality_asserted": self.legality_asserted,
            "restriction_id": self.restriction_id,
        }


@dataclass(frozen=True, slots=True)
class CirculationPolicy:
    """Politica **declarativa** para vereditos ``UNKNOWN`` (brutos).

    Por padrao, ``UNKNOWN`` vira ``ALLOW``, mas continua reportado em
    ``raw_verdict``/``unresolved``. Isso permite rotear sem afirmar que a
    legalidade foi comprovada.
    """

    unresolved_verdict: Verdict = Verdict.ALLOW

    def __post_init__(self) -> None:
        if self.unresolved_verdict is Verdict.UNKNOWN:
            raise ValueError(
                "unresolved_verdict nao pode ser UNKNOWN: a politica precisa "
                "resolver o veredito bruto"
            )

    def resolve(self, verdict: Verdict) -> Verdict:
        """Aplica a politica a um veredito bruto."""
        return self.unresolved_verdict if verdict is Verdict.UNKNOWN else verdict


DEFAULT_POLICY = CirculationPolicy()


def _decision(
    raw: Verdict,
    reason: DecisionReason,
    policy: CirculationPolicy,
    *,
    legality_asserted: bool = False,
    restriction_id: str | None = None,
) -> Decision:
    """Monta uma ``Decision`` resolvendo o veredito bruto pela politica."""
    return Decision(
        verdict=policy.resolve(raw),
        reason=reason,
        raw_verdict=raw,
        legality_asserted=legality_asserted,
        restriction_id=restriction_id,
    )


# --------------------------------------------------------------------------
# Decisao de acesso ao arco
# --------------------------------------------------------------------------
def _rule_decision(
    rule: AccessRule,
    evaluator: ConditionEvaluator | None,
    policy: CirculationPolicy,
) -> Decision | None:
    """Decisao de uma ``AccessRule``; ``None`` se a regra esta inativa.

    A regra so deixa de valer (``None``) quando e **condicional** e a condicao
    foi avaliada como **falsa** — nesse caso a avaliacao prossegue para regras
    mais gerais. Valor nao interpretavel e sempre conservador (``UNKNOWN``).
    """
    forbidden = rule.value in FORBIDDEN_ACCESS_VALUES
    allowed = rule.value in ALLOWED_ACCESS_VALUES
    if not forbidden and not allowed:
        return _decision(Verdict.UNKNOWN, DecisionReason.UNSUPPORTED, policy)

    if rule.condition is None:
        if forbidden:
            return _decision(
                Verdict.FORBID, DecisionReason.EXPLICIT_FORBIDDEN, policy
            )
        return _decision(
            Verdict.ALLOW,
            DecisionReason.EXPLICIT_ALLOWED,
            policy,
            legality_asserted=True,
        )

    outcome = evaluator(rule.condition) if evaluator is not None else None
    if outcome is None:
        return _decision(
            Verdict.UNKNOWN, DecisionReason.CONDITION_UNRESOLVED, policy
        )
    if not outcome:
        return None
    if forbidden:
        return _decision(
            Verdict.FORBID, DecisionReason.CONDITIONAL_FORBIDDEN, policy
        )
    return _decision(
        Verdict.ALLOW,
        DecisionReason.CONDITIONAL_ALLOWED,
        policy,
        legality_asserted=True,
    )


def arc_decision(
    arc: Arc,
    *,
    evaluator: ConditionEvaluator | None = None,
    policy: CirculationPolicy = DEFAULT_POLICY,
) -> Decision:
    """Decide o acesso de ``arc``.

    Ordem: regras de acesso (escopo mais especifico primeiro) -> ausencia de
    regra. Nao consulta a fisica nem cria custo.
    """
    for scope in ACCESS_SCOPES:
        for rule in arc.restrictions:
            if rule.scope != scope:
                continue
            decision = _rule_decision(rule, evaluator, policy)
            if decision is not None:
                return decision

    return _decision(Verdict.ALLOW, DecisionReason.NO_RULE, policy)


# --------------------------------------------------------------------------
# Decisao de conversao entre arcos
# --------------------------------------------------------------------------
def _way_tail(
    from_arc: Arc, history_way_ids: Sequence[int | None]
) -> tuple[int | None, ...]:
    """Sequencia de ``way_id`` que antecede a conversao (mais recente por ultimo).

    Sem historico, recai para o ``way_id`` do proprio arco de entrada.
    """
    tail = tuple(history_way_ids)
    return tail if tail else (from_arc.osm_way_id,)


def _restriction_matches(
    restriction: TurnRestriction,
    from_arc: Arc,
    node: Hashable,
    tail: tuple[int | None, ...],
) -> bool | None:
    """A restricao se aplica a esta transicao? ``None`` = contexto insuficiente.

    A sequencia exigida antes do arco de saida e ``(from_way_id, *via_way_ids)``
    (ADR-024: caminho intermediario ordenado). O arco de entrada precisa ser a
    **ultima** via dessa sequencia.
    """
    if restriction.via_node_id is not None and restriction.via_node_id != node:
        return False
    required = (restriction.from_way_id, *restriction.via_way_ids)
    if not required:
        return False
    if from_arc.osm_way_id != required[-1]:
        return False
    if len(tail) >= len(required):
        return tuple(tail[-len(required) :]) == required
    # Sem historico completo nao se confirma a sequencia via-way. A proibicao e
    # avaliada conservadoramente no ponto de aplicacao.
    return None


def _turn_decision(
    restriction: TurnRestriction,
    to_arc: Arc,
    evaluator: ConditionEvaluator | None,
    policy: CirculationPolicy,
) -> Decision | None:
    """Aplica uma restricao **que ja casa** com a transicao.

    ``None`` quando a condicao (se houver) foi avaliada como falsa.
    """
    if restriction.condition is not None:
        outcome = evaluator(restriction.condition) if evaluator is not None else None
        if outcome is None:
            return _decision(
                Verdict.UNKNOWN,
                DecisionReason.CONDITION_UNRESOLVED,
                policy,
                restriction_id=restriction.id,
            )
        if not outcome:
            return None

    restriction_type = restriction.restriction_type
    same_to = (
        to_arc.osm_way_id is not None
        and to_arc.osm_way_id == restriction.to_way_id
    )

    if restriction_type in NO_TURN_TYPES:
        if same_to:
            return _decision(
                Verdict.FORBID,
                DecisionReason.TURN_FORBIDDEN,
                policy,
                restriction_id=restriction.id,
            )
        return None
    if restriction_type in ONLY_TURN_TYPES:
        if same_to:
            return _decision(
                Verdict.ALLOW,
                DecisionReason.TURN_ONLY_SATISFIED,
                policy,
                legality_asserted=True,
                restriction_id=restriction.id,
            )
        return _decision(
            Verdict.FORBID,
            DecisionReason.TURN_ONLY_VIOLATED,
            policy,
            restriction_id=restriction.id,
        )
    return _decision(
        Verdict.UNKNOWN,
        DecisionReason.UNSUPPORTED,
        policy,
        restriction_id=restriction.id,
    )


def transition_decision(
    graph: RoutingGraph,
    from_arc: Arc,
    to_arc: Arc,
    node_id: Hashable | None = None,
    *,
    history_way_ids: Sequence[int | None] = (),
    evaluator: ConditionEvaluator | None = None,
    policy: CirculationPolicy = DEFAULT_POLICY,
) -> Decision:
    """Decide a conversao ``from_arc -> to_arc`` no no de transicao.

    ``history_way_ids`` sao os ``way_id`` dos arcos ja percorridos, mais recente
    por ultimo, **incluindo** ``from_arc``; e necessario para restricoes
    via-way. Sem restricao aplicavel, devolve ``ALLOW``/``NO_RULE`` (a
    legalidade da conversao nao e afirmada).
    """
    node = from_arc.target if node_id is None else node_id
    tail = _way_tail(from_arc, history_way_ids)

    decisions: list[Decision] = []
    for restriction in graph.turn_restrictions.values():
        matches = _restriction_matches(restriction, from_arc, node, tail)
        if matches is None:
            decisions.append(
                _decision(
                    Verdict.UNKNOWN,
                    DecisionReason.CONTEXT_INSUFFICIENT,
                    policy,
                    restriction_id=restriction.id,
                )
            )
            continue
        if not matches:
            continue
        decision = _turn_decision(restriction, to_arc, evaluator, policy)
        if decision is not None:
            decisions.append(decision)

    if not decisions:
        return _decision(Verdict.ALLOW, DecisionReason.NO_RULE, policy)

    forbidden = [decision for decision in decisions if decision.forbids]
    if forbidden:
        return forbidden[0]
    unresolved = [decision for decision in decisions if decision.unresolved]
    if unresolved:
        return unresolved[0]
    return decisions[0]


# --------------------------------------------------------------------------
# Validador independente de legalidade de rota
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RouteValidation:
    """Resultado da validacao de uma rota contra o contrato de circulacao.

    ``compliant`` significa "nenhum passo **proibido** pelas restricoes
    aplicaveis e registradas" — **nao** que a rota seja legal (a ausencia de
    regra nao e permissao absoluta). ``unresolved`` lista os passos bloqueados
    por resolucao conservadora (nao por prova de proibicao).
    """

    compliant: bool
    steps: tuple[Decision, ...]
    violations: tuple[Decision, ...]
    unresolved: tuple[Decision, ...]
    def to_dict(self) -> dict[str, object]:
        return {
            "compliant": self.compliant,
            "steps": len(self.steps),
            "violations": [decision.to_dict() for decision in self.violations],
            "unresolved": [decision.to_dict() for decision in self.unresolved],
        }


def validate_route(
    graph: RoutingGraph,
    arc_ids: Sequence[str],
    *,
    evaluator: ConditionEvaluator | None = None,
    policy: CirculationPolicy = DEFAULT_POLICY,
) -> RouteValidation:
    """Valida uma sequencia de arcos direcionados contra o contrato.

    Funcao **independente do fluxo**: usa exatamente o mesmo guardrail que a
    expansao usara, de modo que rotas tradicionais e rotas geradas pelo ML
    compartilhem um unico contrato. Verifica acesso de cada arco e a conversao
    entre arcos consecutivos.
    """
    arcs = [graph.get_arc(arc_id) for arc_id in arc_ids]

    steps: list[Decision] = []
    ways: list[int | None] = []
    for index, arc in enumerate(arcs):
        access = arc_decision(arc, evaluator=evaluator, policy=policy)
        steps.append(access)

        if index > 0:
            transition = transition_decision(
                graph,
                arcs[index - 1],
                arc,
                arcs[index - 1].target,
                history_way_ids=tuple(ways),
                evaluator=evaluator,
                policy=policy,
            )
            steps.append(transition)

        ways.append(arc.osm_way_id)

    frozen = tuple(steps)
    violations = tuple(decision for decision in frozen if decision.forbids)
    unresolved = tuple(decision for decision in frozen if decision.unresolved)
    return RouteValidation(
        compliant=not violations,
        steps=frozen,
        violations=violations,
        unresolved=unresolved,
    )


def constrained_nodes(graph: RoutingGraph) -> frozenset[Hashable]:
    """Nos em que a legalidade da proxima conversao depende do arco de chegada.

    ADR-026 §5: a chave de dominancia so precisa incluir a classe de chegada nos
    nos com **restricao de conversao aplicavel**. Restricoes por ``via_node_id``
    sao ancoradas num no explicito; restricoes via-way sao ancoradas no
    **destino** de qualquer arco cuja via seja a ultima via da sequencia exigida.
    Sem restricoes de conversao, o conjunto e vazio (dominancia inalterada).
    """
    nodes: set[Hashable] = set()
    via_way_lasts: set[int] = set()
    for restriction in graph.turn_restrictions.values():
        if restriction.via_node_id is not None:
            nodes.add(restriction.via_node_id)
        if restriction.via_way_ids:
            via_way_lasts.add(restriction.via_way_ids[-1])
    if via_way_lasts:
        for arc in graph.arcs.values():
            if arc.osm_way_id in via_way_lasts:
                nodes.add(arc.target)
    return frozenset(nodes)
