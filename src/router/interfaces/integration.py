"""Superficie de integracao com o aplicativo (ADR-030, Fase 3 / ADR-031).

A fachada ``api`` continua ampla por compatibilidade (F6). Este modulo e a
superficie **curada** que um consumidor externo (o ``toporide``) deve importar:
poucos nomes, um caminho por operacao e apenas o contrato publico ``v1``.

Regras (ADR-031, secao 6):

* nada aqui e uma segunda implementacao: todos os nomes reexportam o ponto de
  entrada eleito de ``router.app`` ou ``router.contracts``;
* esta camada (``interfaces``) **nao** importa ``router.core.*``;
* ``load_graph``/``save_graph`` sao os adaptadores de persistencia de
  ``router.app.ports`` (JSON versionado do grafo), nao um formato novo;
* o plano devolvido por ``plan_routes`` ja e o envelope ``RoutePlanV1``
  serializavel por ``dumps``/``loads``. O futuro porte em TypeScript consome
  esse envelope e traduz os nomes (``snake_case`` -> ``camelCase``).
"""

from __future__ import annotations

from router.app import (
    PROFILE_IDS,
    BicycleModel,
    CyclistModel,
    RoutingGraph,
    load_graph,
    plan_routes,
    save_graph,
)
from router.contracts import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    BBoxV1,
    ReasonV1,
    RouteContractError,
    RouteMLV1,
    RouteMetricsV1,
    RoutePlanV1,
    RoutePointV1,
    RouteProvenanceV1,
    RouteRequestV1,
    RouteSafetyV1,
    RouteV1,
    ZoneV1,
    dumps,
    loads,
    read_plan,
    write_plan,
)
from router.offline import OfflineViolation, is_network_disabled, offline

__all__ = [
    # Planejamento
    "plan_routes",
    "PROFILE_IDS",
    # Entradas fisicas (o app informa o ciclista e a bicicleta)
    "CyclistModel",
    "BicycleModel",
    # Grafo
    "RoutingGraph",
    "load_graph",
    "save_graph",
    # Contrato publico de rota v1
    "CONTRACT_FORMAT",
    "CONTRACT_VERSION",
    "RouteContractError",
    "BBoxV1",
    "ZoneV1",
    "ReasonV1",
    "RouteRequestV1",
    "RoutePointV1",
    "RouteMetricsV1",
    "RouteMLV1",
    "RouteSafetyV1",
    "RouteProvenanceV1",
    "RouteV1",
    "RoutePlanV1",
    # Serializacao estrita
    "dumps",
    "loads",
    "write_plan",
    "read_plan",
    # Guard de rede
    "offline",
    "is_network_disabled",
    "OfflineViolation",
]
