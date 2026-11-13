"""Testes do contrato de circulacao (Camada 03 — guardrails).

Cobrem as decisoes de acesso e de conversao (ADR-026 §2-§3) e o **validador
independente** de legalidade de rota (ADR-026 §7). O contrato e puro: nenhum
destes testes toca a busca, a fisica ou a estrutura Pareto.

Ref.: ``docs/adr/026-contrato-circulacao.md`` §2-§3, §7.
"""

import pytest
from router.core.data.graph.model import Arc, BicycleAccess, Node, RoutingGraph, Segment
from router.core.data.graph.restrictions import AccessRule, TurnRestriction
from router.core.search import (
    CirculationPolicy,
    DecisionReason,
    Verdict,
    arc_decision,
    transition_decision,
    validate_route,
)


# --------------------------------------------------------------------------
# Infra
# --------------------------------------------------------------------------
def make_arc(
    arc_id: str,
    source: int,
    target: int,
    *,
    way_id: int | None = None,
    restrictions: tuple[AccessRule, ...] = (),
    bicycle_access: BicycleAccess = BicycleAccess.YES,
    distance_m: float = 100.0,
) -> Arc:
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=distance_m,
        osm_way_id=way_id,
        restrictions=tuple(restrictions),
        bicycle_access=bicycle_access,
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arcs, node_ids) -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    for arc in arcs:
        graph.add_arc(arc)
    return graph


def rule(scope: str, value: str, condition: str | None = None) -> AccessRule:
    raw = value if condition is None else f"{value} @ {condition}"
    return AccessRule(scope=scope, value=value, condition=condition, raw=raw)


# --------------------------------------------------------------------------
# Decisao de acesso ao arco — §7.1
# --------------------------------------------------------------------------
def test_arc_explicit_forbidden_is_hard_forbid():
    arc = make_arc("a12", 1, 2, restrictions=(rule("bicycle", "no"),))
    decision = arc_decision(arc)
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.EXPLICIT_FORBIDDEN
    assert decision.raw_verdict is Verdict.FORBID
    assert not decision.legality_asserted


def test_arc_explicit_allowed_asserts_legality():
    arc = make_arc("a12", 1, 2, restrictions=(rule("bicycle", "designated"),))
    decision = arc_decision(arc)
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.EXPLICIT_ALLOWED
    assert decision.legality_asserted


def test_arc_absence_of_rule_allows_without_asserting_legality():
    arc = make_arc("a12", 1, 2)
    decision = arc_decision(arc)
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.NO_RULE
    # Ausencia de regra NAO e permissao absoluta (ADR-024).
    assert not decision.legality_asserted


def test_arc_conditional_forbidden_true_forbids():
    arc = make_arc(
        "a12", 1, 2, restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),)
    )
    decision = arc_decision(arc, evaluator=lambda _cond: True)
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.CONDITIONAL_FORBIDDEN
    assert not decision.unresolved


def test_arc_conditional_forbidden_false_is_inactive():
    arc = make_arc(
        "a12", 1, 2, restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),)
    )
    decision = arc_decision(arc, evaluator=lambda _cond: False)
    # A regra nao se aplica -> cai para ausencia de regra.
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.NO_RULE


def test_arc_conditional_without_context_is_unknown_but_does_not_block():
    arc = make_arc(
        "a12", 1, 2, restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),)
    )
    decision = arc_decision(arc)  # sem avaliador
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.reason is DecisionReason.CONDITION_UNRESOLVED
    assert decision.unresolved
    assert not decision.legality_asserted


def test_arc_scope_precedence_falls_through_inactive_specific_rule():
    arc = make_arc(
        "a12",
        1,
        2,
        restrictions=(
            rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),
            rule("access", "no"),
        ),
    )
    # bicycle:conditional falsa -> regra inativa -> vale access=no.
    decision = arc_decision(arc, evaluator=lambda _cond: False)
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.EXPLICIT_FORBIDDEN


def test_arc_unsupported_value_is_unknown_but_does_not_block():
    arc = make_arc("a12", 1, 2, restrictions=(rule("bicycle", "unknown_value"),))
    decision = arc_decision(arc)
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.reason is DecisionReason.UNSUPPORTED


def test_arc_lenient_policy_resolves_unknown_to_allow_but_reports_it():
    arc = make_arc(
        "a12", 1, 2, restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),)
    )
    lenient = CirculationPolicy(unresolved_verdict=Verdict.ALLOW)
    decision = arc_decision(arc, policy=lenient)
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.unresolved
    assert not decision.legality_asserted


def test_arc_conditional_allowed_true_asserts_legality():
    arc = make_arc(
        "a12",
        1,
        2,
        restrictions=(rule("bicycle", "yes", "(Mo-Fr 07:00-10:00)"),),
    )
    decision = arc_decision(arc, evaluator=lambda _cond: True)
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.CONDITIONAL_ALLOWED
    assert decision.legality_asserted
    assert not decision.unresolved


def test_decision_properties_and_serialization():
    forbidden = arc_decision(
        make_arc("a12", 1, 2, restrictions=(rule("bicycle", "no"),))
    )
    assert forbidden.forbids and not forbidden.allows and not forbidden.unresolved
    assert forbidden.to_dict()["reason"] == "explicit_forbidden"

    conservative = arc_decision(
        make_arc(
            "a12",
            1,
            2,
            restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),),
        )
    )
    assert conservative.allows and conservative.unresolved
    assert conservative.to_dict()["raw_verdict"] == "unknown"


def test_policy_rejects_unknown_as_unresolved_verdict():
    with pytest.raises(ValueError):
        CirculationPolicy(unresolved_verdict=Verdict.UNKNOWN)


# --------------------------------------------------------------------------
# Decisao de conversao — §7.1
# --------------------------------------------------------------------------
def make_turn_graph(restrictions=()):
    arcs = [
        make_arc("a12", 1, 2, way_id=10),
        make_arc("a23", 2, 3, way_id=20),
        make_arc("a24", 2, 4, way_id=30),
    ]
    graph = make_graph(arcs, (1, 2, 3, 4))
    for restriction in restrictions:
        graph.add_turn_restriction(restriction)
    return graph


def no_left_turn(restriction_id="r1", **kwargs):
    return TurnRestriction(
        id=restriction_id,
        from_way_id=10,
        to_way_id=20,
        restriction_type="no_left_turn",
        via_node_id=2,
        **kwargs,
    )


def only_straight_on(restriction_id="r2", **kwargs):
    return TurnRestriction(
        id=restriction_id,
        from_way_id=10,
        to_way_id=20,
        restriction_type="only_straight_on",
        via_node_id=2,
        **kwargs,
    )


def test_transition_absent_restriction_allows_without_asserting():
    graph = make_turn_graph()
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a23"), 2
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.NO_RULE
    assert not decision.legality_asserted


def test_transition_no_turn_forbids_matching_target():
    graph = make_turn_graph([no_left_turn()])
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a23"), 2
    )
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.TURN_FORBIDDEN
    assert decision.restriction_id == "r1"


def test_transition_no_turn_ignores_other_target():
    graph = make_turn_graph([no_left_turn()])
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a24"), 2
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.NO_RULE


def test_transition_no_turn_ignores_other_node():
    graph = make_turn_graph([no_left_turn()])
    # Transicao no no 3 nao e o ponto da restricao (via_node_id == 2).
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a23"), 3
    )
    assert decision.verdict is Verdict.ALLOW


def test_transition_only_turn_satisfied_by_target():
    graph = make_turn_graph([only_straight_on()])
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a23"), 2
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.TURN_ONLY_SATISFIED
    assert decision.legality_asserted


def test_transition_only_turn_violated_by_other_target():
    graph = make_turn_graph([only_straight_on()])
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a24"), 2
    )
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.TURN_ONLY_VIOLATED


def test_transition_unresolved_condition_is_conservative_forbid():
    graph = make_turn_graph(
        [
            TurnRestriction(
                id="r3",
                from_way_id=10,
                to_way_id=20,
                restriction_type="no_left_turn",
                via_node_id=2,
                condition="(Mo-Fr 07:00-10:00)",
            )
        ]
    )
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a24"), 2
    )
    # Sem avaliador, a condicao nao e resolvida; a regra de produto nao bloqueia.
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.reason is DecisionReason.CONDITION_UNRESOLVED


def test_transition_unsupported_type_is_unknown_but_does_not_block():
    graph = make_turn_graph(
        [
            TurnRestriction(
                id="r4",
                from_way_id=10,
                to_way_id=20,
                restriction_type="no_entry",
                via_node_id=2,
            )
        ]
    )
    decision = transition_decision(
        graph, graph.get_arc("a12"), graph.get_arc("a24"), 2
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.reason is DecisionReason.UNSUPPORTED


# --- restricao via-way (sequencia ordenada significativa) -----------------
def make_via_way_graph(restriction_type="only_straight_on", **kwargs):
    arcs = [
        make_arc("a01", 1, 2, way_id=10),
        make_arc("a12", 2, 3, way_id=15),
        make_arc("a23", 3, 4, way_id=16),
        make_arc("a34", 4, 5, way_id=20),
        make_arc("a46", 4, 6, way_id=30),
    ]
    graph = make_graph(arcs, (1, 2, 3, 4, 5, 6))
    graph.add_turn_restriction(
        TurnRestriction(
            id="rv",
            from_way_id=10,
            to_way_id=20,
            restriction_type=restriction_type,
            via_way_ids=(15, 16),
            **kwargs,
        )
    )
    return graph


def test_transition_via_way_ordered_satisfied():
    graph = make_via_way_graph()
    decision = transition_decision(
        graph,
        graph.get_arc("a23"),
        graph.get_arc("a34"),
        4,
        history_way_ids=(10, 15, 16),
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.TURN_ONLY_SATISFIED


def test_transition_via_way_ordered_violated():
    graph = make_via_way_graph()
    decision = transition_decision(
        graph,
        graph.get_arc("a23"),
        graph.get_arc("a46"),
        4,
        history_way_ids=(10, 15, 16),
    )
    assert decision.verdict is Verdict.FORBID
    assert decision.reason is DecisionReason.TURN_ONLY_VIOLATED


def test_transition_via_way_order_is_significant():
    graph = make_via_way_graph()
    # Ordem errada: a ultima via da sequencia (16) nao e o arco de entrada (10).
    decision = transition_decision(
        graph,
        graph.get_arc("a01"),
        graph.get_arc("a34"),
        2,
        history_way_ids=(16, 15, 10),
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.reason is DecisionReason.NO_RULE


def test_transition_via_way_without_history_is_unknown_but_does_not_block():
    graph = make_via_way_graph()
    decision = transition_decision(
        graph, graph.get_arc("a23"), graph.get_arc("a34"), 4
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.raw_verdict is Verdict.UNKNOWN
    assert decision.reason is DecisionReason.CONTEXT_INSUFFICIENT


# --------------------------------------------------------------------------
# Validador independente de rota — §7.2
# --------------------------------------------------------------------------
def test_validate_route_without_restrictions_is_compliant_but_not_asserted():
    graph = make_graph(
        [make_arc("a12", 1, 2, way_id=10), make_arc("a23", 2, 3, way_id=20)],
        (1, 2, 3),
    )
    result = validate_route(graph, ("a12", "a23"))
    assert result.compliant
    assert not result.violations
    assert not result.unresolved
    assert {step.reason for step in result.steps} == {DecisionReason.NO_RULE}


def test_validate_route_flags_forbidden_arc():
    graph = make_graph(
        [
            make_arc("a12", 1, 2, way_id=10, restrictions=(rule("bicycle", "no"),)),
            make_arc("a23", 2, 3, way_id=20),
        ],
        (1, 2, 3),
    )
    result = validate_route(graph, ("a12", "a23"))
    assert not result.compliant
    assert len(result.violations) == 1
    assert result.violations[0].reason is DecisionReason.EXPLICIT_FORBIDDEN


def test_validate_route_flags_forbidden_turn():
    graph = make_turn_graph([no_left_turn()])
    result = validate_route(graph, ("a12", "a23"))
    assert not result.compliant
    assert any(
        violation.reason is DecisionReason.TURN_FORBIDDEN
        for violation in result.violations
    )


def test_validate_route_accepts_satisfied_only_turn():
    graph = make_turn_graph([only_straight_on()])
    result = validate_route(graph, ("a12", "a23"))
    assert result.compliant


def test_validate_route_rejects_violated_only_turn():
    graph = make_turn_graph([only_straight_on()])
    result = validate_route(graph, ("a12", "a24"))
    assert not result.compliant
    assert any(
        violation.reason is DecisionReason.TURN_ONLY_VIOLATED
        for violation in result.violations
    )


def test_validate_route_reports_unresolved_without_blocking():
    graph = make_graph(
        [
            make_arc(
                "a12",
                1,
                2,
                way_id=10,
                restrictions=(rule("bicycle", "no", "(Mo-Fr 07:00-10:00)"),),
            ),
            make_arc("a23", 2, 3, way_id=20),
        ],
        (1, 2, 3),
    )
    result = validate_route(graph, ("a12", "a23"))
    assert result.compliant
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason is DecisionReason.CONDITION_UNRESOLVED


def test_validate_route_via_way_satisfied():
    graph = make_via_way_graph()
    result = validate_route(graph, ("a01", "a12", "a23", "a34"))
    assert result.compliant


def test_validate_route_via_way_violated():
    graph = make_via_way_graph()
    result = validate_route(graph, ("a01", "a12", "a23", "a46"))
    assert not result.compliant
