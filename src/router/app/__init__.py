"""L6 - orquestracao: API estavel do motor (ADR-031).

Resolve ``stops`` sobre o nucleo e devolve ``router.contracts.RoutePlanV1``.
Nao implementa fisica nem busca: apenas elege pontos de entrada.
"""

from __future__ import annotations

from router.app.multi_leg import plan_routes
from router.app.planning import PlannedRoute, RoutePlan, plan_offline_routes
from router.app.ports import (
    ElevationProvider,
    EffortPredictor,
    GraphRepository,
    JsonGraphRepository,
    RouteAssessmentView,
    load_graph,
    save_graph,
)
from router.core.data.graph.model import RoutingGraph
from router.core.physics.physical import BicycleModel, CyclistModel
from router.core.search.profiles import PROFILE_IDS

__all__ = [
    "BicycleModel",
    "CyclistModel",
    "EffortPredictor",
    "ElevationProvider",
    "GraphRepository",
    "JsonGraphRepository",
    "PROFILE_IDS",
    "PlannedRoute",
    "RouteAssessmentView",
    "RoutePlan",
    "RoutingGraph",
    "load_graph",
    "plan_offline_routes",
    "plan_routes",
    "save_graph",
]
