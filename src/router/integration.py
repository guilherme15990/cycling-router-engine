"""Superficie publica curada nivel B (ADR-031 secao 6, ADR-032 D4, ADR-033 4.1.10).

Atalho oficial de importacao: o consumidor externo importa **um** modulo -
``router.integration`` - e, no maximo, os tipos de ``router.contracts``. Este e
a **unica** implementacao da superficie curada: todos os nomes reexportam o
ponto de entrada eleito de ``router.app``, ``router.contracts`` ou
``router.offline``; nada aqui e uma segunda implementacao.

Regras:

* nada aqui reimplementa fisica, busca ou contrato: os nomes sao reexportados;
* ``load_graph``/``save_graph`` sao os adaptadores de persistencia de
  ``router.app.ports`` (JSON versionado do grafo), nao um formato novo;
* o plano devolvido por ``plan_routes`` ja e o envelope ``RoutePlan``
  serializavel por ``dumps``/``loads``. O futuro porte em TypeScript consome
  esse envelope e traduz os nomes (``snake_case`` -> ``camelCase``).

Custo de importacao (ADR-033, E5): apenas ``contracts`` e ``offline`` - folhas,
sem dependencia do nucleo - sao carregados no import deste modulo. Os nomes de
``router.app`` (que arrasta o pipeline inteiro) sao resolvidos de forma
**preguicosa** por ``__getattr__``: ``import router.integration`` nao carrega o
nucleo. O primeiro acesso a um desses nomes - ou um ``from router.integration
import *`` - carrega ``router.app`` uma unica vez (a importacao fica em cache no
``sys.modules``). Nenhum ``importlib``/``__import__`` e usado: cada import e uma
instrucao estatica e auditavel.
"""

from __future__ import annotations

from typing import Any

from router.contracts import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    BBox,
    Reason,
    RouteContractError,
    RouteML,
    RouteMetrics,
    RoutePlan,
    RoutePoint,
    RouteProvenance,
    RouteRequest,
    RouteSafety,
    Route,
    Zone,
    dumps,
    loads,
    read_plan,
    write_plan,
)
from router.offline import OfflineViolation, is_network_disabled, offline

__all__ = [
    "plan_routes_progressive",
    "RouteProgress",
    "altimetry_edges",
    "prepare_dem_tiles",
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
    "BBox",
    "Zone",
    "Reason",
    "RouteRequest",
    "RoutePoint",
    "RouteMetrics",
    "RouteML",
    "RouteSafety",
    "RouteProvenance",
    "Route",
    "RoutePlan",
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

#: Nomes servidos por ``router.app`` (carregam o nucleo; resolvidos sob demanda).
_APP_EXPORTS = frozenset(
    {
        "plan_routes",
        "PROFILE_IDS",
        "CyclistModel",
        "BicycleModel",
        "RoutingGraph",
        "load_graph",
        "save_graph",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve sob demanda os nomes de ``router.app`` (import tardio e explicito).

    Mantem o topo do modulo livre do nucleo: ``import router.integration`` carrega
    apenas ``contracts``/``offline``. Cada ramo e um import estatico; nomes
    desconhecidos levantam ``AttributeError`` (inclusive os DTOs internos).
    """
    if name in {"plan_routes_progressive", "RouteProgress"}:
        from router.app.progressive import plan_routes_progressive, RouteProgress
        return {"plan_routes_progressive": plan_routes_progressive, "RouteProgress": RouteProgress}[name]
    if name in {"altimetry_edges", "prepare_dem_tiles"}:
        from router.app.dem_integration import altimetry_edges, prepare_dem_tiles
        return {"altimetry_edges": altimetry_edges, "prepare_dem_tiles": prepare_dem_tiles}[name]
    if name == "plan_routes":
        from router.app import plan_routes

        return plan_routes
    if name == "PROFILE_IDS":
        from router.app import PROFILE_IDS

        return PROFILE_IDS
    if name == "CyclistModel":
        from router.app import CyclistModel

        return CyclistModel
    if name == "BicycleModel":
        from router.app import BicycleModel

        return BicycleModel
    if name == "RoutingGraph":
        from router.app import RoutingGraph

        return RoutingGraph
    if name == "load_graph":
        from router.app import load_graph

        return load_graph
    if name == "save_graph":
        from router.app import save_graph

        return save_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
