"""Contrato publico de rota v1 (nivel A da fronteira publica).

O namespace estavel e este pacote: ``router.contracts``. Ele nao importa
nenhum pacote do dominio (apenas a stdlib).
"""

from router.contracts.errors import RouteContractError
from router.contracts.geo import LL, BBox
from router.contracts.route import (
    Reason,
    RouteML,
    RouteMetrics,
    RoutePlan,
    RoutePoint,
    RouteProvenance,
    RouteRequest,
    RouteSafety,
    Route,
    Zone,
)
from router.contracts.serialize import dumps, loads, read_plan, write_plan
from router.contracts.version import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    INTENSITY_BANDS,
)

__all__ = [
    "CONTRACT_FORMAT",
    "CONTRACT_VERSION",
    "INTENSITY_BANDS",
    "LL",
    "BBox",
    "RouteContractError",
    "RouteRequest",
    "RoutePoint",
    "RouteMetrics",
    "RouteML",
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
