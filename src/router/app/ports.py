"""Portas e adaptadores de orquestracao (ADR-031, secao 5).

As inversoes explicitas que mantem o grafo de dependencias:

* ``ElevationProvider`` - provedores de altitude do nucleo;
* ``EffortPredictor`` - preditor de esforco opcional;
* ``RouteAssessmentView`` - visao minima consumida pela classificacao;
* ``GraphRepository`` - persistencia do grafo (implementacao JSON versionada).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from router.core.data.elevation import ElevationProvider
from router.core.data.graph.model import RoutingGraph
from router.core.data.graph.serialize import load_json, save_json
from router.core.evaluation.ports import RouteAssessmentView
from router.core.prediction.ml.predictor import EffortPredictor


@runtime_checkable
class GraphRepository(Protocol):
    """Persistencia do grafo ciclavel em formato versionado."""

    def load(self, path: str | Path) -> RoutingGraph:
        """Le um grafo serializado."""

    def save(self, graph: RoutingGraph, path: str | Path) -> Path:
        """Grava o grafo e devolve o caminho escrito."""


class JsonGraphRepository:
    """Implementacao JSON do grafo (``graph.serialize``, formato versionado)."""

    def load(self, path: str | Path) -> RoutingGraph:
        return load_json(path)

    def save(self, graph: RoutingGraph, path: str | Path) -> Path:
        return save_json(graph, path)


def load_graph(path: str | Path) -> RoutingGraph:
    """Carrega um grafo serializado (JSON ``FORMAT``/``VERSION`` do grafo)."""
    return load_json(path)


def save_graph(graph: RoutingGraph, path: str | Path) -> Path:
    """Grava o grafo no formato JSON versionado; devolve o caminho escrito."""
    return save_json(graph, path)


__all__ = [
    "ElevationProvider",
    "EffortPredictor",
    "GraphRepository",
    "JsonGraphRepository",
    "RouteAssessmentView",
    "load_graph",
    "save_graph",
]
