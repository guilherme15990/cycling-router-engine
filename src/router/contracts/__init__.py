"""Contrato publico de rota v1 (nivel A da fronteira publica).

O namespace estavel e este pacote: ``router.contracts``. Ele nao importa
nenhum pacote do dominio (apenas a stdlib).
"""

from router.contracts.errors import RouteContractError
from router.contracts.geo import LL, BBoxV1
from router.contracts.route import (
    ReasonV1,
    RouteMLV1,
    RouteMetricsV1,
    RoutePlanV1,
    RoutePointV1,
    RouteProvenanceV1,
    RouteRequestV1,
    RouteSafetyV1,
    RouteV1,
    ZoneV1,
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
    "BBoxV1",
    "RouteContractError",
    "RouteRequestV1",
    "RoutePointV1",
    "RouteMetricsV1",
    "RouteMLV1",
    "ReasonV1",
    "RouteSafetyV1",
    "RouteProvenanceV1",
    "RouteV1",
    "ZoneV1",
    "RoutePlanV1",
    "dumps",
    "loads",
    "write_plan",
    "read_plan",
]
