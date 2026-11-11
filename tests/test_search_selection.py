import pytest

from graph.model import Arc, Segment
from physical import TraversalResult
from search import (
    DiversityConfig,
    DiversityFallback,
    DiversityMetric,
    Objective,
    RankingStrategy,
    RankingTolerance,
    Route,
    RouteProfile,
    SearchResult,
    SelectionConfig,
    advance_state,
    arc_jaccard,
    initial_state,
    is_too_similar,
    order_routes,
    profile_for,
    profile_score,
    rank_routes,
    route_from_state,
    route_profile_for,
    route_similarity,
    routes_from_result,
    select_from_result,
    select_routes,
    shared_distance_ratio,
)

MASS_KG = 87.0


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
) -> Route:
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


def small_profile(*, fallback=None, max_similarity: float = 0.7) -> RouteProfile:
    diversity = DiversityConfig(max_similarity=max_similarity)
    if fallback is not None:
        from search import DiversityRelaxation

        diversity = DiversityConfig(
            max_similarity=max_similarity,
            fallback=fallback,
            relaxation=DiversityRelaxation(max_threshold=1.0, step=0.1),
        )
    return RouteProfile(
        id="custom",
        objectives=(Objective.TIME, Objective.ASCENT),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=RankingTolerance(),
        diversity=diversity,
    )


# --------------------------------------------------------------------------
# Reconstrucao
# --------------------------------------------------------------------------
def test_route_from_state_reconstructs_arcs_lengths_and_metrics():
    label = label_for("e0", time_s=20.0, distance_m=200.0, ascent_m=5.0)

    rebuilt = route_from_state(label)

    assert rebuilt.arc_ids == ("e0",)
    assert rebuilt.arc_lengths == (pytest.approx(200.0),)
    assert rebuilt.arc_set == frozenset({"e0"})
    assert rebuilt.arc_count == 1
    assert rebuilt.time_s == pytest.approx(20.0)
    assert rebuilt.distance_m == pytest.approx(200.0)
    assert rebuilt.ascent_m == pytest.approx(5.0)
    assert rebuilt.energy_j == pytest.approx(1000.0)
    assert rebuilt.to_dict()["arcs"] == ["e0"]


def test_route_from_state_arc_lengths_follow_parent_chain():
    first = label_for("e0", time_s=10.0, distance_m=150.0, label_id=0)
    arc = Arc(
        id="e1",
        source=2,
        target=3,
        segments=(Segment(id="e1:0", distance_m=50.0),),
    )
    result = TraversalResult(
        time_s=5.0,
        distance_m=50.0,
        ascent_m=0.0,
        descent_m=0.0,
        initial_speed_mps=5.0,
        final_speed_mps=5.0,
        mechanical_energy_j=100.0,
        rider_energy_j=103.0,
        arc_id="e1",
    )
    second = advance_state(first, arc, result, mass_kg=MASS_KG, label_id=1)

    rebuilt = route_from_state(second)

    assert rebuilt.arc_ids == ("e0", "e1")
    assert rebuilt.arc_lengths == (pytest.approx(150.0), pytest.approx(50.0))
    assert rebuilt.arc_length_map == {"e0": 150.0, "e1": 50.0}


def test_routes_from_result():
    result = SearchResult(
        origin=1,
        destination=2,
        profile=profile_for("fastest"),
        labels=(
            label_for("e0", time_s=10.0, distance_m=100.0, label_id=0),
            label_for("e1", time_s=20.0, distance_m=200.0, label_id=1),
        ),
    )

    routes = routes_from_result(result)

    assert [r.arc_ids for r in routes] == [("e0",), ("e1",)]


# --------------------------------------------------------------------------
# Diversidade
# --------------------------------------------------------------------------
def test_arc_jaccard_and_alias():
    a = route(["x", "y", "z"])
    b = route(["x", "y", "z"])
    c = route(["y", "z", "w"])
    d = route(["p", "q"])

    assert arc_jaccard(a, b) == 1.0
    assert arc_jaccard(a, c) == pytest.approx(2 / 4)
    assert arc_jaccard(a, d) == 0.0
    assert arc_jaccard(route([]), route([])) == 1.0
    assert route_similarity(a, c) == pytest.approx(2 / 4)


def test_arc_jaccard_preserves_direction():
    forward = route(["e1"])
    reverse = route(["e1:rev"])

    assert arc_jaccard(forward, reverse) == 0.0


def test_shared_distance_ratio_uses_lengths():
    a = route(["a", "b"], [5000.0, 5000.0])
    b = route(["a", "x"], [5000.0, 5000.0])
    same = route(["a", "b"], [5000.0, 5000.0])
    disjoint = route(["p", "q"], [5000.0, 5000.0])

    assert shared_distance_ratio(a, same) == pytest.approx(1.0)
    assert shared_distance_ratio(a, b) == pytest.approx(0.5)
    assert shared_distance_ratio(a, disjoint) == 0.0


def test_shared_distance_ratio_prefers_shorter_route_scale():
    short = route(["a", "b"], [1000.0], distance_m=1000.0)
    long = route(["a", "b", "c", "d"], [1000.0, 1000.0, 1000.0, 1000.0])

    # 1000 m compartilhados sobre a rota mais curta (1000 m) -> 1.0.
    assert shared_distance_ratio(short, long) == pytest.approx(1.0)


def test_shared_distance_ratio_falls_back_to_counts():
    a = route(["a", "b", "c"], distance_m=300.0)
    b = route(["a", "b", "d"], distance_m=300.0)

    # Sem comprimentos por arco -> razao de arcos (2/3).
    assert shared_distance_ratio(a, b) == pytest.approx(2 / 3)


def test_is_too_similar_uses_threshold():
    a = route(["a", "b"], [5000.0, 5000.0])
    b = route(["a", "x"], [5000.0, 5000.0])

    assert is_too_similar(a, b, DiversityMetric.SHARED_DISTANCE_RATIO, 0.4)
    assert not is_too_similar(a, b, DiversityMetric.SHARED_DISTANCE_RATIO, 0.6)


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------
def test_rank_routes_lexicographic_by_primary_objective():
    r1 = route(["a"], time_s=30.0, distance_m=100.0, ascent_m=50.0)
    r2 = route(["b"], time_s=10.0, distance_m=500.0, ascent_m=5.0)
    r3 = route(["c"], time_s=20.0, distance_m=200.0, ascent_m=80.0)
    candidates = [r1, r2, r3]

    fastest = order_routes(candidates, route_profile_for("fastest"))
    shortest = order_routes(candidates, route_profile_for("shortest"))
    flattest = order_routes(candidates, route_profile_for("flattest"))

    assert [r.time_s for r in fastest] == [10.0, 20.0, 30.0]
    assert [r.distance_m for r in shortest] == [100.0, 200.0, 500.0]
    assert [r.ascent_m for r in flattest] == [5.0, 50.0, 80.0]


def test_rank_routes_is_deterministic_regardless_of_input_order():
    r1 = route(["a"], time_s=10.0, ascent_m=5.0, label_id=1)
    r2 = route(["b"], time_s=10.0, ascent_m=5.0, label_id=0)

    ordered_a = order_routes([r1, r2], route_profile_for("fastest"))
    ordered_b = order_routes([r2, r1], route_profile_for("fastest"))

    assert [r.label_id for r in ordered_a] == [0, 1]
    assert ordered_a == ordered_b


def test_lexicographic_tolerance_allows_secondary_to_break_tie():
    # Perfil fastest: (tempo, subida) com tolerancia de 5 s no tempo.
    profile = route_profile_for("fastest")
    fast_hilly = route(["a"], time_s=100.0, ascent_m=10.0, label_id=0)
    slightly_slower_flat = route(["b"], time_s=103.0, ascent_m=5.0, label_id=1)

    ranked = rank_routes([fast_hilly, slightly_slower_flat], profile)

    # 3 s de diferenca ficam dentro da tolerancia -> desempata pela subida.
    assert ranked[0].route is slightly_slower_flat
    assert ranked[0].rank == 0
    assert ranked[0].profile_score is None


def test_lexicographic_time_outside_tolerance_wins():
    profile = route_profile_for("fastest")
    slower_flat = route(["a"], time_s=110.0, ascent_m=1.0, label_id=0)
    faster_hilly = route(["b"], time_s=103.0, ascent_m=9.0, label_id=1)

    ranked = rank_routes([slower_flat, faster_hilly], profile)

    assert ranked[0].route is faster_hilly


def test_balanced_profile_score_is_normalized():
    profile = route_profile_for("balanced")
    small = route(["a"], time_s=900.0, distance_m=5000.0, ascent_m=50.0)
    large = route(["b"], time_s=1800.0, distance_m=10000.0, ascent_m=100.0)

    assert profile_score(small, profile) == pytest.approx(0.5)
    assert profile_score(large, profile) == pytest.approx(1.0)


def test_balanced_rank_orders_by_score():
    small = route(["a"], time_s=900.0, distance_m=5000.0, ascent_m=50.0)
    large = route(["b"], time_s=1800.0, distance_m=10000.0, ascent_m=100.0)

    ranked = rank_routes([large, small], route_profile_for("balanced"))

    assert ranked[0].route is small
    assert ranked[0].profile_score == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Selecao + fallback
# --------------------------------------------------------------------------
def _candidates():
    # ``similar`` compartilha a maior parte do caminho com ``shared`` (0.83 de
    # distancia compartilhada, acima do limiar 0.7), mas NAO e identica: o
    # deduplicador de caminho nao a colapsa; quem a rejeita e a diversidade.
    shared = route(["a", "b", "c", "d"], [250.0] * 4, time_s=20.0, label_id=0)
    similar = route(["a", "b", "c"], [300.0] * 3, time_s=21.0, label_id=1)
    distinct = route(["x", "y"], [500.0, 500.0], time_s=22.0, label_id=2)
    return shared, similar, distinct


def test_select_routes_prefers_distinct():
    shared, similar, distinct = _candidates()
    profile = small_profile()

    selection = select_routes(
        [shared, similar, distinct],
        profile,
        SelectionConfig(max_routes=2, min_routes=1),
    )

    assert selection.count == 2
    assert selection.routes == (shared, distinct)
    assert selection.diversity_relaxed is False
    assert selection.rejected_as_similar_count == 1
    assert all(s.selection_reason.diversity_accepted for s in selection.selected)


def test_select_routes_return_fewer():
    shared, similar, distinct = _candidates()
    profile = small_profile(fallback=DiversityFallback.RETURN_FEWER)

    selection = select_routes(
        [shared, similar, distinct],
        profile,
        SelectionConfig(max_routes=3, min_routes=3),
    )

    assert selection.count == 2
    assert selection.diversity_relaxed is False
    assert selection.rejected_as_similar_count == 1


def test_select_routes_relax_threshold_fills():
    shared, similar, distinct = _candidates()
    profile = small_profile(fallback=DiversityFallback.RELAX_THRESHOLD)

    selection = select_routes(
        [shared, similar, distinct],
        profile,
        SelectionConfig(max_routes=3, min_routes=3),
    )

    assert selection.count == 3
    assert selection.diversity_relaxed is True
    # shared_distance_ratio(shared, similar) = 750/900 = 0.83: com passos de 0.1
    # o primeiro limiar que admite ``similar`` e 0.9.
    assert selection.threshold_used == pytest.approx(0.9)
    # A rota preenchida pelo relaxamento nao foi aceita na diversidade.
    assert selection.selected[1].route is similar
    assert selection.selected[1].selection_reason.diversity_accepted is False


def test_select_routes_none_ignores_diversity():
    shared, similar, distinct = _candidates()
    profile = small_profile(fallback=DiversityFallback.NONE)

    selection = select_routes(
        [shared, similar, distinct],
        profile,
        SelectionConfig(max_routes=2, min_routes=2),
    )

    assert selection.count == 2
    assert selection.routes == (shared, similar)
    assert selection.diversity_relaxed is False
    assert selection.threshold_used is None


def test_select_routes_respects_max_routes():
    shared, similar, distinct = _candidates()

    selection = select_routes(
        [shared, similar, distinct],
        small_profile(),
        SelectionConfig(max_routes=1),
    )

    assert selection.count == 1
    assert selection.routes == (shared,)


def test_selection_config_validation():
    with pytest.raises(ValueError):
        SelectionConfig(max_routes=0)
    with pytest.raises(ValueError):
        SelectionConfig(max_routes=True)
    with pytest.raises(ValueError):
        SelectionConfig(max_routes=2, min_routes=0)
    with pytest.raises(ValueError):
        SelectionConfig(max_routes=2, min_routes=3)


def test_select_from_result_derives_profile():
    result = SearchResult(
        origin=1,
        destination=2,
        profile=profile_for("fastest"),
        labels=(
            label_for("e0", time_s=10.0, distance_m=100.0, label_id=0),
            label_for("e1", time_s=20.0, distance_m=200.0, label_id=1),
        ),
    )

    selection = select_from_result(
        result, None, SelectionConfig(max_routes=2, min_routes=1)
    )

    assert selection.count == 2
    assert selection.pareto_route_count == 2
    assert selection.selected[0].route.arc_ids == ("e0",)
    assert selection.selected[0].selection_reason.primary_objective is Objective.TIME
