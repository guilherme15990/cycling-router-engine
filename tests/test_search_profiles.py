import pytest

from graph.model import RoutingGraph, Arc, Segment, Node
from physical import TraversalResult
from search import (
    PROFILE_CONFIG,
    PROFILE_IDS,
    BalancedWeights,
    DiversityConfig,
    DiversityFallback,
    DiversityMetric,
    DiversityRelaxation,
    Objective,
    ProfileSearchRequest,
    RankingStrategy,
    RankingTolerance,
    Route,
    RouteExplainer,
    RouteProfile,
    ScoreReference,
    SelectionConfig,
    SelectionReason,
    explain_route,
    profile_for,
    route_profile_for,
    search_profile,
)

MASS_KG = 87.0


def route(
    arc_ids,
    *,
    time_s: float = 100.0,
    distance_m: float = 1000.0,
    ascent_m: float = 10.0,
    label_id: int = 0,
) -> Route:
    return Route(
        arc_ids=tuple(arc_ids),
        arc_lengths=tuple([distance_m / max(1, len(arc_ids))] * len(arc_ids)),
        time_s=time_s,
        distance_m=distance_m,
        ascent_m=ascent_m,
        descent_m=0.0,
        energy_j=1000.0,
        label_id=label_id,
    )


# --------------------------------------------------------------------------
# Configuracao declarativa dos perfis
# --------------------------------------------------------------------------
def test_profile_ids_and_objectives():
    assert PROFILE_IDS == (
        "fastest",
        "shortest",
        "flattest",
        "balanced",
        "electric",
    )
    assert PROFILE_CONFIG["fastest"].objectives == (
        Objective.TIME,
        Objective.ASCENT,
    )
    assert PROFILE_CONFIG["shortest"].objectives == (
        Objective.DISTANCE,
        Objective.TIME,
    )
    assert PROFILE_CONFIG["flattest"].objectives == (
        Objective.ASCENT,
        Objective.DISTANCE,
    )
    assert PROFILE_CONFIG["balanced"].objectives == (
        Objective.TIME,
        Objective.DISTANCE,
        Objective.ASCENT,
    )
    assert PROFILE_CONFIG["electric"].objectives == (
        Objective.ENERGY,
        Objective.TIME,
    )
    assert PROFILE_CONFIG["electric"].title == "Elétrico"


def test_ranking_strategy_per_profile():
    assert (
        PROFILE_CONFIG["fastest"].ranking_strategy
        is RankingStrategy.LEXICOGRAPHIC
    )
    assert (
        PROFILE_CONFIG["shortest"].ranking_strategy
        is RankingStrategy.LEXICOGRAPHIC
    )
    assert (
        PROFILE_CONFIG["flattest"].ranking_strategy
        is RankingStrategy.LEXICOGRAPHIC
    )
    assert (
        PROFILE_CONFIG["balanced"].ranking_strategy
        is RankingStrategy.WEIGHTED_NORMALIZED
    )


def test_balanced_profile_has_weights_and_positive_references():
    balanced = PROFILE_CONFIG["balanced"]
    assert balanced.weights is not None
    assert balanced.references is not None
    weights = balanced.weights
    assert weights.time_s + weights.distance_m + weights.ascent_m == pytest.approx(1.0)
    assert balanced.references.time_s > 0.0
    assert balanced.references.distance_m > 0.0
    assert balanced.references.ascent_m > 0.0


def test_route_profile_for_normalizes_and_validates():
    assert route_profile_for("fastest") is PROFILE_CONFIG["fastest"]
    assert route_profile_for(" FASTEST ") is PROFILE_CONFIG["fastest"]
    with pytest.raises(ValueError):
        route_profile_for("turbo")


def test_to_search_profile_matches_search_view():
    for profile_id in PROFILE_IDS:
        route_profile = PROFILE_CONFIG[profile_id]
        search_profile = route_profile.to_search_profile()
        reference = profile_for(profile_id)
        assert search_profile.id == profile_id
        assert search_profile.names == reference.names
        assert search_profile.objectives == reference.objectives


def test_route_profile_validation():
    with pytest.raises(ValueError):
        RouteProfile(
            id="empty",
            objectives=(),
            ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        )
    with pytest.raises(ValueError):
        RouteProfile(
            id="dup",
            objectives=(Objective.TIME, Objective.TIME),
            ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        )
    with pytest.raises(ValueError):
        RouteProfile(
            id="weighted-without-config",
            objectives=(Objective.TIME,),
            ranking_strategy=RankingStrategy.WEIGHTED_NORMALIZED,
        )


def test_tolerance_weight_and_reference_validation():
    with pytest.raises(ValueError):
        RankingTolerance(time_s=-1.0)
    with pytest.raises(ValueError):
        ScoreReference(time_s=0.0)
    with pytest.raises(ValueError):
        BalancedWeights(time_s=0.5, distance_m=0.5, ascent_m=0.5)
    with pytest.raises(ValueError):
        DiversityConfig(max_similarity=1.5)
    with pytest.raises(ValueError):
        DiversityRelaxation(max_threshold=2.0)
    with pytest.raises(ValueError):
        DiversityRelaxation(step=0.0)


def test_profile_defaults_to_shared_distance_metric():
    config = PROFILE_CONFIG["fastest"].diversity
    assert config.metric is DiversityMetric.SHARED_DISTANCE_RATIO
    assert config.fallback is DiversityFallback.RELAX_THRESHOLD


# --------------------------------------------------------------------------
# Explicacao
# --------------------------------------------------------------------------
def _reason(objective: Objective, value: float, accepted: bool = True):
    return SelectionReason(
        primary_objective=objective,
        primary_value=value,
        diversity_accepted=accepted,
    )


def test_explanation_claims_best_only_when_truly_best():
    profile = route_profile_for("fastest")
    best = route(["a"], time_s=100.0, ascent_m=40.0)
    other = route(["b"], time_s=130.0, ascent_m=5.0)
    candidates = (best, other)

    explainer = RouteExplainer()
    best_text = explainer.explain(
        best, profile, _reason(Objective.TIME, 100.0), candidates=candidates
    )
    other_text = explainer.explain(
        other, profile, _reason(Objective.TIME, 130.0), candidates=candidates
    )

    assert "menor tempo" in best_text.headline.lower()
    assert "acima da alternativa mais rapida" in other_text.headline.lower()


def test_explanation_tradeoffs_compare_with_other_candidates():
    profile = route_profile_for("fastest")
    best = route(["a"], time_s=100.0, ascent_m=40.0)
    flatter = route(["b"], time_s=130.0, ascent_m=5.0)

    text = explain_route(
        best,
        profile,
        _reason(Objective.TIME, 100.0),
        candidates=(best, flatter),
    )

    assert any("subida" in line.lower() for line in text.tradeoffs)


def test_explanation_balanced_mentions_score():
    profile = route_profile_for("balanced")
    only = route(["a"], time_s=900.0, distance_m=5000.0, ascent_m=50.0)

    text = explain_route(
        only, profile, _reason(Objective.TIME, 900.0), candidates=(only,)
    )

    assert "score" in text.headline.lower()


def test_explanation_without_candidates_is_neutral():
    profile = route_profile_for("fastest")
    solo = route(["a"], time_s=250.0)

    text = explain_route(solo, profile, _reason(Objective.TIME, 250.0))

    assert "menor" not in text.headline.lower()
    assert "250" in text.headline or "4.2 min" in text.headline


def test_explanation_marks_near_duplicate():
    profile = route_profile_for("fastest")
    a = route(["a"], time_s=100.0)
    b = route(["b"], time_s=101.0)

    text = explain_route(
        b,
        profile,
        _reason(Objective.TIME, 101.0, accepted=False),
        candidates=(a, b),
    )

    assert any("semelhante" in line.lower() for line in text.tradeoffs)


# --------------------------------------------------------------------------
# Pipeline busca + selecao
# --------------------------------------------------------------------------
def make_arc(
    arc_id: str, source: int, target: int, distance_m: float = 100.0
) -> Arc:
    segment = Segment(id=f"{arc_id}:0", distance_m=distance_m)
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arc_specs, node_ids=(1, 2, 3, 4)) -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.001 * node_id))
    for spec in arc_specs:
        graph.add_arc(make_arc(*spec))
    return graph


class StubTraversal:
    def __init__(self, table, mass_kg: float = MASS_KG) -> None:
        self.table = table
        self._mass_kg = mass_kg

    @property
    def mass_kg(self) -> float:
        return self._mass_kg

    def traverse(self, arc: Arc, initial_speed_mps: float) -> TraversalResult:
        spec = self.table[arc.id]
        return TraversalResult(
            time_s=spec.get("time_s", 10.0),
            distance_m=spec.get("distance_m", arc.distance_m),
            ascent_m=spec.get("ascent_m", 0.0),
            descent_m=spec.get("descent_m", 0.0),
            initial_speed_mps=float(initial_speed_mps),
            final_speed_mps=spec.get("final_speed_mps", 5.0),
            mechanical_energy_j=spec.get("energy_j", 1000.0),
            rider_energy_j=spec.get("energy_j", 1000.0) / 0.97,
            arc_id=arc.id,
        )


def _tradeoff_graph():
    return make_graph(
        [("direct", 1, 3), ("b1", 1, 2), ("b2", 2, 3)]
    )


def _tradeoff_traversal():
    return StubTraversal(
        {
            "direct": {"time_s": 100.0, "distance_m": 1200.0, "ascent_m": 20.0},
            "b1": {"time_s": 50.0, "distance_m": 400.0, "ascent_m": 30.0},
            "b2": {"time_s": 40.0, "distance_m": 500.0, "ascent_m": 30.0},
        }
    )


def test_search_profile_selects_pareto_routes():
    graph = _tradeoff_graph()
    traversal = _tradeoff_traversal()
    request = ProfileSearchRequest(
        origin=1,
        destination=3,
        profile=route_profile_for("fastest"),
        max_routes=2,
    )

    response = search_profile(graph, traversal, request)

    assert response.reached is True
    assert response.profile_id == "fastest"
    assert response.pareto_route_count == 2
    assert response.count == 2
    assert len(response.explanations) == 2
    assert response.selection.selected_count == 2
    assert response.selection.requested_count == 2


def test_search_profile_unreachable_returns_empty():
    graph = make_graph([])
    traversal = StubTraversal({})
    request = ProfileSearchRequest(
        origin=4,
        destination=3,
        profile=route_profile_for("shortest"),
        max_routes=3,
    )

    response = search_profile(graph, traversal, request)

    assert response.reached is False
    assert response.count == 0
    assert response.selection.selected_count == 0


def test_search_profile_honors_selection_config():
    graph = _tradeoff_graph()
    traversal = _tradeoff_traversal()
    request = ProfileSearchRequest(
        origin=1,
        destination=3,
        profile=route_profile_for("shortest"),
        max_routes=2,
    )

    response = search_profile(
        graph,
        traversal,
        request,
        selection_config=SelectionConfig(max_routes=1),
    )

    assert response.count == 1


def test_profile_search_request_validation():
    with pytest.raises(ValueError):
        ProfileSearchRequest(
            origin=1,
            destination=3,
            profile=route_profile_for("fastest"),
            max_routes=0,
        )
    with pytest.raises(ValueError):
        ProfileSearchRequest(
            origin=1,
            destination=3,
            profile=route_profile_for("fastest"),
            initial_speed_mps=-1.0,
        )
