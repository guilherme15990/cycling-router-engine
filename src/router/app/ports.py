"""Portas e adaptadores de orquestracao (ADR-031, secao 5).

As inversoes explicitas que mantem o grafo de dependencias:

* ``ElevationProvider`` - provedores de altitude do nucleo;
* ``EffortPredictor`` - preditor de esforco opcional;
* ``RouteAssessmentView`` - visao minima consumida pela classificacao.

A persistencia do grafo e feita pelas funcoes ``load_graph``/``save_graph``
(``graph.serialize``, formato JSON versionado); o protocolo ``GraphRepository``
e a implementacao ``JsonGraphRepository`` foram removidos por nao terem
consumidor (ADR-033, E2 / invariante 4.1.11).
"""

from __future__ import annotations

from pathlib import Path

from router.core.data.elevation import ElevationProvider
from router.core.data.graph.model import RoutingGraph
from router.core.data.graph.serialize import load_json, save_json
from router.core.evaluation.ports import RouteAssessmentView
from router.core.prediction.ml import EffortPredictor


def load_graph(path: str | Path) -> RoutingGraph:
    """Carrega um grafo serializado (JSON ``FORMAT``/``VERSION`` do grafo)."""
    return load_json(path)


def save_graph(graph: RoutingGraph, path: str | Path) -> Path:
    """Grava o grafo no formato JSON versionado; devolve o caminho escrito."""
    return save_json(graph, path)


__all__ = [
    "ElevationProvider",
    "EffortPredictor",
    "RouteAssessmentView",
    "load_graph",
    "save_graph",
]
