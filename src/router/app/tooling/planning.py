"""Caso de uso: consulta offline completa (ADR-033, E6)."""

from __future__ import annotations

from router.app.planning import RoutePlanResult, plan_offline_routes

__all__ = [
    "RoutePlanResult",
    "plan_offline_routes",
]
