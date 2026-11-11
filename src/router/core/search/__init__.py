"""Camada de busca multiobjetivo (MOA*) e selecao por perfil.

A busca A* multiobjetivo integra estado, dominancia, fronteira e fila:

* ``ObjectiveSet`` / ``Objective`` / ``SearchProfile`` e objetivos por perfil;
* dominancia de Pareto com discretizacao de velocidade (``SpeedBucketDominance``);
* ``PhysicalState`` / ``SearchState`` (metricas acumuladas + estado fisico);
* ``ParetoFrontier``, ``FrontierMap`` e ``FrontierManager``;
* ``Heuristic`` (limites inferiores admissiveis) e ``SearchConfig``;
* ``LexicographicQueue`` (fila de prioridade por perfil);
* ``TraversalModel`` / ``PhysicalTraversalModel`` (adaptador do modelo fisico);
* ``MultiObjectiveAStar`` / ``SearchResult`` (fronteira completa de destino).

A **selecao por perfil** e uma camada independente, posterior a busca:

* ``RouteProfile`` e configuracao declarativa (``profiles.py``);
* ``SearchRoute`` / reconstrucao (``route.py``) e ``ObjectiveEvaluator``
  (``evaluator.py``);
* diversidade por ``arc_jaccard`` / ``shared_distance_ratio`` (``diversity.py``);
* ranking lexicografico com tolerancia e score normalizado (``ranking.py``);
* selecao das N rotas com fallback (``selection.py``);
* explicacao comparando com as candidatas reais (``explanation.py``);
* contrato busca<->selecao (``pipeline.py``).

O modelo fisico continua sendo a unica fonte de tempo e energia: esta camada
apenas acumula metricas e mantem alternativas nao dominadas. O perfil **nao**
altera a fisica.
"""

from .astar import (
    MultiObjectiveAStar,
    SearchResult,
    arc_ids_of,
    is_feasible,
    model_max_speed,
    order_labels,
)
from .approximate import (
    ArcEstimateError,
    ComparisonReport,
    RankingComparison,
    SearchRankingComparison,
    SteadyStateTraversalModel,
    compare_rankings,
    compare_search_rankings,
    compare_traversals,
    estimate_arc,
)
from .candidates import (
    CandidateMetrics,
    DeduplicationMode,
    RouteCandidate,
    RouteCandidatePool,
    RouteDeduplicator,
)
from .circulation import (
    ACCESS_SCOPES,
    ALLOWED_ACCESS_VALUES,
    DEFAULT_POLICY,
    FORBIDDEN_ACCESS_VALUES,
    NO_TURN_TYPES,
    ONLY_TURN_TYPES,
    CirculationPolicy,
    ConditionEvaluator,
    Decision,
    DecisionReason,
    RouteValidation,
    Verdict,
    arc_decision,
    constrained_nodes,
    transition_decision,
    validate_route,
)
from .config import SearchConfig
from .diversity import (
    RouteDiversity,
    arc_jaccard,
    is_too_similar,
    route_diversity,
    route_similarity,
    shared_distance_ratio,
)
from .dominance import (
    DEFAULT_SPEED_STEP_MPS,
    Dominance,
    SpeedBucketDominance,
    speed_bucket,
)
from .energy import (
    RouteEnergyEstimate,
    estimate_route_energy,
    estimate_routes_energy,
)
from .evaluator import (
    MetricSource,
    ObjectiveEvaluator,
    ObjectiveValues,
    RouteObjectiveEvaluator,
    evaluate,
    evaluate_metrics,
    evaluate_route,
    objective_value,
)
from .explanation import RouteExplanation, RouteExplainer, explain_route
from .frontier import FrontierManager, FrontierMap, ParetoFrontier
from .heuristic import (
    DEFAULT_MAX_SPEED_MPS,
    SUPPORTED_OBJECTIVES,
    Heuristic,
    HeuristicBounds,
)
from .objectives import (
    PROFILE_NAMES,
    PROFILE_OBJECTIVES,
    PROFILES,
    Objective,
    ObjectiveSet,
    SearchProfile,
    dominates,
    epsilon_dominates,
    objectives_for,
    profile_for,
)
from .pipeline import (
    ProfileSearchRequest,
    ProfileSearchResponse,
    SelectionMetadata,
    search_profile,
)
from .profiles import (
    PROFILE_CONFIG,
    PROFILE_IDS,
    BalancedWeights,
    DiversityConfig,
    DiversityFallback,
    DiversityMetric,
    DiversityRelaxation,
    RankingStrategy,
    RankingTolerance,
    RouteObjective,
    RouteProfile,
    ScoreReference,
    route_profile_for,
)
from .queue import LexicographicQueue
from .ranking import (
    ProfileRanker,
    RankedCandidate,
    RankedRoute,
    order_routes,
    profile_score,
    rank_candidates,
    rank_routes,
)
from .route import (
    SearchRoute,
    route_arc_lengths,
    route_from_state,
    routes_from_result,
)
from .selection import (
    RouteSelectionRequest,
    RouteSelectionResult,
    RouteSelector,
    SelectedRoute,
    SelectionConfig,
    SelectionReason,
    SelectionResult,
    select_candidates,
    select_from_result,
    select_routes,
)
from .state import (
    PhysicalState,
    SearchState,
    advance_state,
    initial_state,
    route_arcs,
)
from .traversal_model import (
    UNKNOWN_SPEED,
    PhysicalTraversalModel,
    TraversalModel,
)

__all__ = [
    "ACCESS_SCOPES",
    "ALLOWED_ACCESS_VALUES",
    "ArcEstimateError",
    "BalancedWeights",
    "CandidateMetrics",
    "CirculationPolicy",
    "ComparisonReport",
    "ConditionEvaluator",
    "DEFAULT_MAX_SPEED_MPS",
    "DEFAULT_POLICY",
    "DEFAULT_SPEED_STEP_MPS",
    "Decision",
    "DecisionReason",
    "DeduplicationMode",
    "Dominance",
    "DiversityConfig",
    "DiversityFallback",
    "DiversityMetric",
    "DiversityRelaxation",
    "FORBIDDEN_ACCESS_VALUES",
    "FrontierManager",
    "FrontierMap",
    "Heuristic",
    "HeuristicBounds",
    "LexicographicQueue",
    "MetricSource",
    "MultiObjectiveAStar",
    "NO_TURN_TYPES",
    "ONLY_TURN_TYPES",
    "Objective",
    "ObjectiveEvaluator",
    "ObjectiveSet",
    "ObjectiveValues",
    "PROFILE_CONFIG",
    "PROFILE_IDS",
    "PROFILE_NAMES",
    "PROFILE_OBJECTIVES",
    "PROFILES",
    "ParetoFrontier",
    "PhysicalState",
    "PhysicalTraversalModel",
    "ProfileRanker",
    "ProfileSearchRequest",
    "ProfileSearchResponse",
    "RankedCandidate",
    "RankedRoute",
    "RankingComparison",
    "RankingStrategy",
    "RankingTolerance",
    "SearchRoute",
    "RouteCandidate",
    "RouteCandidatePool",
    "RouteDeduplicator",
    "RouteDiversity",
    "RouteEnergyEstimate",
    "RouteExplanation",
    "RouteExplainer",
    "RouteObjective",
    "RouteObjectiveEvaluator",
    "RouteProfile",
    "RouteSelectionRequest",
    "RouteSelectionResult",
    "RouteSelector",
    "RouteValidation",
    "SUPPORTED_OBJECTIVES",
    "ScoreReference",
    "SearchConfig",
    "SearchProfile",
    "SearchRankingComparison",
    "SearchResult",
    "SearchState",
    "SelectedRoute",
    "SelectionConfig",
    "SelectionMetadata",
    "SelectionReason",
    "SelectionResult",
    "SpeedBucketDominance",
    "SteadyStateTraversalModel",
    "TraversalModel",
    "UNKNOWN_SPEED",
    "Verdict",
    "advance_state",
    "compare_rankings",
    "compare_search_rankings",
    "compare_traversals",
    "constrained_nodes",
    "dominates",
    "arc_decision",
    "arc_ids_of",
    "arc_jaccard",
    "epsilon_dominates",
    "estimate_arc",
    "estimate_route_energy",
    "estimate_routes_energy",
    "evaluate",
    "evaluate_metrics",
    "evaluate_route",
    "explain_route",
    "initial_state",
    "is_feasible",
    "is_too_similar",
    "model_max_speed",
    "objective_value",
    "objectives_for",
    "order_labels",
    "order_routes",
    "profile_for",
    "profile_score",
    "rank_candidates",
    "rank_routes",
    "route_diversity",
    "route_arc_lengths",
    "route_arcs",
    "route_from_state",
    "route_profile_for",
    "route_similarity",
    "routes_from_result",
    "search_profile",
    "select_candidates",
    "select_from_result",
    "select_routes",
    "shared_distance_ratio",
    "speed_bucket",
    "transition_decision",
    "validate_route",
]
