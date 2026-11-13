"""Contrato publico de rota v1 (nivel A da fronteira publica).

O namespace estavel e este pacote: ``router.contracts``. Ele nao importa
nenhum pacote do dominio (apenas a stdlib).
"""

from router.contracts.errors import RouteContractError
from router.contracts.geo import LL, BBox
from router.contracts.route import (
    DemandScenarioRef,
    Reason,
    RouteDemand,
    RouteML,
    RouteMetrics,
    RoutePlan,
    RoutePoint,
    RouteProvenance,
    RouteRequest,
    RouteSafety,
    RouteTelemetry,
    Route,
    Zone,
)
from router.contracts.serialize import dumps, loads, read_plan, write_plan
from router.contracts.version import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    DEMAND_BAND_DEFINITION,
    DEMAND_BAND_EDGES,
    DEMAND_BANDS,
    INTENSITY_BAND_DEFINITION,
    INTENSITY_BAND_EDGES,
    INTENSITY_BANDS,
)

__all__ = [
    "CONTRACT_FORMAT",
    "CONTRACT_VERSION",
    "DEMAND_BAND_DEFINITION",
    "DEMAND_BAND_EDGES",
    "DEMAND_BANDS",
    "INTENSITY_BAND_DEFINITION",
    "INTENSITY_BAND_EDGES",
    "INTENSITY_BANDS",
    "LL",
    "BBox",
    "RouteContractError",
    "RouteRequest",
    "RoutePoint",
    "RouteMetrics",
    "RouteML",
    "DemandScenarioRef",
    "RouteDemand",
    "RouteTelemetry",
    "Reason",
    "RouteSafety",
    "RouteProvenance",
    "Route",
    "Zone",
    "RoutePlan",
    "dumps",
    "loads",
    "write_plan",
    "read_plan",
]
