"""Spatial helpers for explicit extracts and map envelopes.

Primary route planning uses the full supplied graph. These optional helpers
serve callers requesting a particular extract; they do not limit that planner.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from router.core.data.geo import GeoBBox, bbox_contains, bbox_of_routes, query_bbox, union_bbox
from router.core.data.graph.model import RoutingGraph


def graph_for_bbox(graph: RoutingGraph, bbox: GeoBBox) -> RoutingGraph:
    """Subgrafo da zona: nos dentro do box + arcos com ambas as pontas dentro.

    Args:
        graph: Grafo carregado (cidade inteira). Nao e alterado.
        bbox: Caixa ``(sul, oeste, norte, leste)`` em graus decimais.

    Returns:
        Novo ``RoutingGraph`` com os nos e arcos da zona de busca.
    """
    south, west, north, east = bbox
    zone = RoutingGraph()
    kept: set[object] = set()
    for node_id in graph.query_node_ids(bbox):
        node = graph.get_node(node_id)
        zone.add_node(node)
        kept.add(node.id)
    for arc_id in graph.query_arc_ids(bbox):
        arc = graph.get_arc(arc_id)
        if arc.source in kept and arc.target in kept:
            zone.add_arc(arc)
    for restriction in graph.turn_restrictions.values():
        zone.add_turn_restriction(restriction)
    zone.metadata = dict(graph.metadata)
    return zone


def scale_bbox(bbox: GeoBBox, factor: float) -> GeoBBox:
    """Escala o box sobre o proprio centro por ``factor`` (``>1`` cresce)."""
    if factor <= 0:
        raise ValueError("factor deve ser positivo")
    south, west, north, east = bbox
    center_lat = (south + north) / 2.0
    center_lon = (west + east) / 2.0
    half_lat = (north - south) / 2.0 * factor
    half_lon = (east - west) / 2.0 * factor
    return (
        center_lat - half_lat,
        center_lon - half_lon,
        center_lat + half_lat,
        center_lon + half_lon,
    )


def adaptive_zone(
    graph: RoutingGraph,
    waypoints: Sequence[tuple[float, float]],
    probe: Callable[[RoutingGraph], Sequence[Sequence[tuple[float, float]]]],
    *,
    growth: float = 1.6,
    max_iterations: int | None = 8,
    pad_m: float = 80.0,
    explore_full_coverage: bool = False,
    use_full_graph: bool = False,
    on_zone: Callable[[GeoBBox, RoutingGraph, int], None] | None = None,
) -> tuple[GeoBBox, RoutingGraph, tuple[Sequence[tuple[float, float]], ...]]:
    """Zona de busca **adaptativa**, dimensionada pelas rotas.

    Comeca no box dos pinos (``geo.query_bbox``) e, a cada passo, recorta a zona
    e chama ``probe(zone)`` — que roda a busca e devolve o **tracado real** de
    cada rota (sequencias de coordenadas). O box:

    * cresce para a **uniao** do box dos pinos com o envelope de **todas** as
      rotas (``bbox_with_routes``);
    * se ``probe`` nao devolver rota (zona desconectada), cresce por ``growth``
      sobre o centro, para reencontrar a rede.

    Para quando **todas** as rotas ficam dentro do box (nenhum trecho percorrido
    sai da zona) ou ao atingir ``max_iterations``. Nao ha parametro de fisica:
    ``growth`` so controla a busca da conectividade numa malha viaria esparsa.

    Returns:
        ``(box_final, zona_final, rotas)`` onde ``rotas`` e o retorno de
        ``probe`` na iteracao final.

    .. note::
        Este utilitario **nao** e mais usado por ``plan_offline_routes``: uma
        consulta agora define o box do corredor **uma vez** e roda **uma** busca
        (ver ``planning._search_zone``). Mantido como utilitario publico
        (cobertura de grafo / testes); ``max_iterations=1`` reproduz a busca
        unica.
    """
    base = query_bbox(waypoints)
    if base is None:
        raise ValueError("sem pinos para dimensionar a zona")
    if explore_full_coverage and (not math.isfinite(growth) or growth <= 1.0):
        raise ValueError("growth deve ser maior que 1")
    if max_iterations is None and not explore_full_coverage:
        raise ValueError("max_iterations=None exige explore_full_coverage")
    box = base
    if use_full_graph and graph.node_count:
        all_nodes = tuple(graph.nodes.values())
        south, west = min(n.lat for n in all_nodes), min(n.lon for n in all_nodes)
        north, east = max(n.lat for n in all_nodes), max(n.lon for n in all_nodes)
        margin_lat = pad_m / 111_320.0
        margin_lon = pad_m / max(1.0, 111_320.0 * math.cos(math.radians((south + north) / 2)))
        box = (south - margin_lat, west - margin_lon, north + margin_lat, east + margin_lon)
        explore_full_coverage = False
        max_iterations = 1
    zone = graph_for_bbox(graph, box)
    routes: Sequence[Sequence[tuple[float, float]]] = ()
    nodes = graph.nodes.values()
    coverage = None
    if explore_full_coverage and graph.node_count:
        coverage = (min(n.lat for n in nodes), min(n.lon for n in nodes),
                    max(n.lat for n in nodes), max(n.lon for n in nodes))
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        zone = graph_for_bbox(graph, box)
        if on_zone is not None:
            on_zone(box, zone, iteration)
        routes = probe(zone)
        if explore_full_coverage:
            route_box = bbox_of_routes(routes, pad_m) if routes else None
            coverage_searched = coverage is None or bbox_contains(box, coverage)
            if route_box is not None:
                box = union_bbox(box, route_box)
            if coverage_searched:
                return box, zone, tuple(routes)
            box = scale_bbox(box, growth)
            continue
        if not routes:
            box = scale_bbox(box, growth)
            continue
        route_box = bbox_of_routes(routes, pad_m)
        if route_box is not None and bbox_contains(box, route_box):
            return box, zone, tuple(routes)
        box = union_bbox(box, route_box) if route_box is not None else box
        if route_box is None:
            box = scale_bbox(box, growth)
    return box, zone, tuple(routes)
