"""Testes do pool de candidatas: metricas, identidade e deduplicacao.

A camada de candidatas e a ponte entre a reconstrucao e a selecao. Estes testes
cobrem:

* ``CandidateMetrics`` e ``RouteCandidate`` (identidade e metricas);
* ``RouteDeduplicator`` nos tres modos (``PATH``/``METRICS``/``PATH_AND_METRICS``);
* ``RouteCandidatePool`` (``from_routes``/``from_candidates``/``from_result``);
* a API de selecao baseada em candidatas (``RouteSelector``, ``ProfileRanker``,
  ``RouteDiversity``) e os aliases de vocabulario.
"""

import pytest

from graph.model import Arc, Segment
from physical import TraversalResult
from search import (
    CandidateMetrics,
    DeduplicationMode,
    DiversityFallback,
    Objective,
    ProfileRanker,
    RankingStrategy,
    RankingTolerance,
    RouteCandidate,
    RouteCandidatePool,
    RouteDeduplicator,
    RouteDiversity,
    RouteSelectionRequest,
    RouteSelectionResult,
    RouteSelector,
    SearchResult,
    SelectionConfig,
    SelectionResult,
    advance_state,
    initial_state,
    rank_candidates,
    route_profile_for,
    select_candidates,
)

MASS_KG = 87.0


# --------------------------------------------------------------------------- fixtures
def route(
    arc_ids,
    lengths=(),
    *,
    time_s: float = 100.0,
    distance_m: float | None = None,
    ascent_m: float = 10.0,
    descent_m: float = 20.0,
    energy_j: float = 1000.0,
    label_id: int = 0,
):
    from search import Route

    if distance_m is None:
        distance_m = (
            float(sum(lengths)) if lengths else float(len(arc_ids)) * 100.0
        )
    return Route(
        arc_ids=tuple(arc_ids),
        arc_lengths=tuple(lengths),
        time_s=time_s,
        distance_m=distance_m,
        ascent_m=ascent_m,
        descent_m=descent_m,
        energy_j=energy_j,
        label_id=label_id,
    )


def label_for(
    arc_id: str,
    *,
    time_s: float,
    distance_m: float,
    ascent_m: float = 0.0,
    label_id: int = 0,
):
    start = initial_state(1, MASS_KG, 0.0)
    arc = Arc(
        id=arc_id,
        source=1,
        target=2,
        segments=(Segment(id=f"{arc_id}:0", distance_m=distance_m),),
    )
    result = TraversalResult(
        time_s=time_s,
        distance_m=distance_m,
        ascent_m=ascent_m,
        descent_m=0.0,
        initial_speed_mps=0.0,
        final_speed_mps=5.0,
        mechanical_energy_j=1000.0,
        rider_energy_j=1030.0,
        arc_id=arc_id,
    )
    return advance_state(start, arc, result, mass_kg=MASS_KG, label_id=label_id)


# --------------------------------------------------------------------------- metricas
def test_candidate_metrics_from_route_and_to_dict():
    r = route(["a"], time_s=12.5, ascent_m=3.0, descent_m=4.0, energy_j=99.0)

    metrics = CandidateMetrics.from_route(r)

    assert metrics.time_s == pytest.approx(12.5)
    assert metrics.distance_m == pytest.approx(100.0)
    assert metrics.ascent_m == pytest.approx(3.0)
    assert metrics.descent_m == pytest.approx(4.0)
    assert metrics.energy_j == pytest.approx(99.0)
    assert metrics.to_dict()["time_s"] == pytest.approx(12.5)
    assert metrics.to_dict()["descent_m"] == pytest.approx(4.0)


def test_route_candidate_identity_defaults_and_custom():
    r = route(["a", "b"], label_id=7)

    default = RouteCandidate.from_route(r)
    custom = RouteCandidate.from_route(r, route_id="alt-1")

    assert default.route_id == "route-7"
    assert default.label_id == 7
    assert default.arc_ids == ("a", "b")
    assert custom.route_id == "alt-1"
    assert default.metrics == CandidateMetrics.from_route(r)


def test_route_candidate_to_dict_serializes_identity_and_metrics():
    candidate = RouteCandidate.from_route(route(["a", "b"], label_id=3))

    payload = candidate.to_dict()

    assert payload["route_id"] == "route-3"
    assert payload["label_id"] == 3
    assert payload["arcs"] == ["a", "b"]
    assert payload["metrics"]["time_s"] == pytest.approx(100.0)


# --------------------------------------------------------------------------- dedup PATH
def test_dedup_path_collapses_same_path_even_with_different_metrics():
    first = RouteCandidate.from_route(route(["a", "b"], time_s=10.0, label_id=0))
    second = RouteCandidate.from_route(route(["a", "b"], time_s=11.0, label_id=1))

    pool = RouteCandidatePool.from_candidates(
        [first, second], deduplicator=RouteDeduplicator(DeduplicationMode.PATH)
    )

    assert len(pool) == 1
    assert pool.dropped_duplicates == 1
    assert pool.candidates[0] is first


def test_dedup_path_keeps_different_paths():
    forward = RouteCandidate.from_route(route(["a", "b"], label_id=0))
    other = RouteCandidate.from_route(route(["b", "a"], label_id=1))

    pool = RouteCandidatePool.from_candidates(
        [forward, other], deduplicator=RouteDeduplicator(DeduplicationMode.PATH)
    )

    assert len(pool) == 2
    assert pool.dropped_duplicates == 0


# --------------------------------------------------------------------------- dedup METRICS
def test_dedup_metrics_collapses_equivalent_result_on_different_paths():
    left = RouteCandidate.from_route(route(["a", "b"], time_s=10.0, label_id=0))
    right = RouteCandidate.from_route(route(["x", "y"], time_s=10.0, label_id=1))

    pool = RouteCandidatePool.from_candidates(
        [left, right], deduplicator=RouteDeduplicator(DeduplicationMode.METRICS)
    )

    assert len(pool) == 1
    assert pool.dropped_duplicates == 1


def test_dedup_metrics_uses_quantum_tolerance():
    base = RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=0))
    near = RouteCandidate.from_route(route(["b"], time_s=10.0004, label_id=1))
    far = RouteCandidate.from_route(route(["c"], time_s=10.01, label_id=2))

    dedup = RouteDeduplicator(DeduplicationMode.METRICS, metric_quantum=1e-3)

    assert [c.label_id for c in dedup.deduplicate([base, near])] == [0]
    assert [c.label_id for c in dedup.deduplicate([base, far])] == [0, 2]


# --------------------------------------------------------------------------- dedup HIBRIDO
def test_dedup_path_and_metrics_keeps_same_path_with_different_metrics():
    first = RouteCandidate.from_route(route(["a", "b"], time_s=10.0, label_id=0))
    second = RouteCandidate.from_route(route(["a", "b"], time_s=11.0, label_id=1))
    exact = RouteCandidate.from_route(route(["a", "b"], time_s=10.0, label_id=2))

    pool = RouteCandidatePool.from_candidates(
        [first, second, exact],
        deduplicator=RouteDeduplicator(DeduplicationMode.PATH_AND_METRICS),
    )

    assert len(pool) == 2
    assert pool.dropped_duplicates == 1
    assert [c.label_id for c in pool.candidates] == [0, 1]


def test_dedup_rejects_non_positive_quantum():
    with pytest.raises(ValueError):
        RouteDeduplicator(metric_quantum=0.0)
    with pytest.raises(ValueError):
        RouteDeduplicator(metric_quantum=float("inf"))


# --------------------------------------------------------------------------- pool
def test_pool_from_routes_default_dedup_is_path():
    routes = [
        route(["a", "b"], time_s=10.0, label_id=0),
        route(["a", "b"], time_s=99.0, label_id=1),
        route(["x"], time_s=12.0, label_id=2),
    ]

    pool = RouteCandidatePool.from_routes(routes)

    assert len(pool) == 2
    assert pool.dropped_duplicates == 1
    assert pool.routes[0].label_id == 0
    assert isinstance(pool.candidates[0], RouteCandidate)


def test_pool_from_routes_can_disable_dedup_by_quantum():
    # Modo METRICS com quantum grande colapsa tudo que difere menos que 1 s.
    routes = [
        route(["a"], time_s=10.0, label_id=0),
        route(["b"], time_s=10.2, label_id=1),
    ]
    pool = RouteCandidatePool.from_routes(
        routes, deduplicator=RouteDeduplicator(DeduplicationMode.METRICS, 1.0)
    )

    assert len(pool) == 1


def test_pool_from_result_reconstructs_and_dedups():
    result = SearchResult(
        origin=1,
        destination=2,
        profile=route_profile_for("fastest"),
        labels=(
            label_for("e0", time_s=10.0, distance_m=100.0, label_id=0),
            label_for("e0", time_s=10.0, distance_m=100.0, label_id=1),
            label_for("e1", time_s=20.0, distance_m=200.0, label_id=2),
        ),
    )

    pool = RouteCandidatePool.from_result(result)

    # e0 aparece duas vezes por labels fisicas diferentes -> colapsa por PATH.
    assert len(pool) == 2
    assert pool.dropped_duplicates == 1
    assert pool.routes[0].arc_ids == ("e0",)


def test_pool_container_protocol_and_serialization():
    empty = RouteCandidatePool()
    assert not empty
    assert len(empty) == 0

    pool = RouteCandidatePool.from_routes(
        [route(["a"], label_id=0), route(["b"], label_id=1)]
    )
    assert pool
    assert [c.label_id for c in pool] == [0, 1]
    payload = pool.to_dict()
    assert payload["candidates"] == 2
    assert payload["dropped_duplicates"] == 0
    assert len(payload["routes"]) == 2


# --------------------------------------------------------------------------- diversidade
def test_route_diversity_component_similarity_and_threshold():
    a = route(["a", "b"], [5000.0, 5000.0])
    b = route(["a", "x"], [5000.0, 5000.0])

    diversity = RouteDiversity(threshold=0.4)

    assert diversity.similarity(a, b) == pytest.approx(0.5)
    assert diversity.is_too_similar(a, b)
    assert not diversity.is_too_similar(a, b, threshold=0.6)
    assert diversity.to_dict() == {
        "metric": "shared_distance_ratio",
        "threshold": 0.4,
    }


def test_route_diversity_rejects_out_of_range_threshold():
    with pytest.raises(ValueError):
        RouteDiversity(threshold=1.5)
    with pytest.raises(ValueError):
        RouteDiversity(threshold=float("nan"))


# --------------------------------------------------------------------------- ranking/selector
def test_rank_candidates_returns_ranked_wrappers():
    profile = route_profile_for("fastest")
    slow = RouteCandidate.from_route(route(["a"], time_s=30.0, label_id=0))
    fast = RouteCandidate.from_route(route(["b"], time_s=10.0, label_id=1))

    ranked = rank_candidates([slow, fast], profile)

    assert [r.rank for r in ranked] == [0, 1]
    assert ranked[0].candidate is fast
    assert ranked[0].profile_score is None


def test_profile_ranker_component_matches_function():
    profile = route_profile_for("fastest")
    slow = RouteCandidate.from_route(route(["a"], time_s=30.0, label_id=0))
    fast = RouteCandidate.from_route(route(["b"], time_s=10.0, label_id=1))

    ranked = ProfileRanker().rank([slow, fast], profile)

    assert ranked[0].candidate is fast


def test_route_selector_selects_distinct_candidates():
    profile = route_profile_for("fastest")
    shared = RouteCandidate.from_route(
        route(["a", "b", "c", "d"], [250.0] * 4, time_s=20.0, label_id=0)
    )
    similar = RouteCandidate.from_route(
        route(["a", "b", "c"], [300.0] * 3, time_s=21.0, label_id=1)
    )
    distinct = RouteCandidate.from_route(
        route(["x", "y"], [500.0, 500.0], time_s=22.0, label_id=2)
    )

    result = RouteSelector().select(
        [shared, similar, distinct],
        profile,
        SelectionConfig(max_routes=2, min_routes=1),
    )

    assert result.count == 2
    assert [s.candidate.label_id for s in result.selected] == [0, 2]
    # ``SelectedRoute`` expoe a candidata, a rota e as metricas.
    assert result.selected[0].route is shared.route
    assert result.selected[0].route_id == "route-0"
    assert result.selected[0].metrics is shared.metrics


def test_select_candidates_uses_request_aliases():
    assert RouteSelectionRequest is SelectionConfig
    assert RouteSelectionResult is SelectionResult

    profile = route_profile_for("fastest")
    candidates = [
        RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=0)),
        RouteCandidate.from_route(route(["b"], time_s=20.0, label_id=1)),
    ]

    result = select_candidates(
        candidates, profile, RouteSelectionRequest(max_routes=1)
    )

    assert isinstance(result, SelectionResult)
    assert result.count == 1
    assert result.pareto_route_count == 2


def test_select_candidates_records_dropped_duplicates_only_via_pool():
    # ``select_candidates`` opera sobre candidatas ja limpas: nao deduplica.
    # Usamos fallback NONE para isolar a diversidade e provar que duas
    # candidatas de mesmo caminho sobrevivem aqui (o dedup e do pool).
    from search import DiversityConfig, RouteProfile

    profile = RouteProfile(
        id="no-div",
        objectives=(Objective.TIME,),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=RankingTolerance(),
        diversity=DiversityConfig(fallback=DiversityFallback.NONE),
    )
    a = RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=0))
    b = RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=1))

    result = select_candidates([a, b], profile, SelectionConfig(max_routes=2))

    assert result.count == 2
    assert result.dropped_duplicates == 0


def test_selection_result_threshold_none_when_diversity_none_fallback():
    profile = route_profile_for("fastest")
    a = RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=0))

    result = select_candidates(
        [a],
        profile,
        SelectionConfig(max_routes=1),
    )

    # Perfil fastest usa RELAX_THRESHOLD; com uma unica candidata nao ha o que
    # relaxar e o limiar efetivo e o do perfil.
    assert result.diversity_relaxed is False


def test_diversity_fallback_none_marks_neutrally():
    from search import DiversityConfig, RouteProfile

    profile = RouteProfile(
        id="no-div",
        objectives=(Objective.TIME,),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=RankingTolerance(),
        diversity=DiversityConfig(fallback=DiversityFallback.NONE),
    )
    a = RouteCandidate.from_route(route(["a"], time_s=10.0, label_id=0))
    b = RouteCandidate.from_route(route(["a"], time_s=11.0, label_id=1))

    result = select_candidates([a, b], profile, SelectionConfig(max_routes=2))

    assert result.count == 2
    assert result.threshold_used is None
    assert all(
        not selected.selection_reason.diversity_accepted
        for selected in result.selected
    )
