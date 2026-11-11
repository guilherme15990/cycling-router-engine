"""L6 - orquestracao: API estavel do motor (ADR-031).

Resolve ``stops`` sobre o nucleo e devolve ``router.contracts.RoutePlan``.
Nao implementa fisica nem busca: apenas elege pontos de entrada.
"""

from __future__ import annotations

from router.app.multi_leg import plan_routes
from router.app.planning import PlannedRoute, RoutePlanResult, plan_offline_routes
from router.app.ports import (
    ElevationProvider,
    EffortPredictor,
    RouteAssessmentView,
    load_graph,
    save_graph,
)
from router.core.data.graph.model import RoutingGraph
from router.core.physics import BicycleModel, CyclistModel
from router.core.search.profiles import PROFILE_IDS

__all__ = [
    "BicycleModel",
    "CyclistModel",
    "EffortPredictor",
    "ElevationProvider",
    "PROFILE_IDS",
    "PlannedRoute",
    "RouteAssessmentView",
    "RoutePlanResult",
    "RoutingGraph",
    "load_graph",
    "plan_offline_routes",
    "plan_routes",
    "save_graph",
]
