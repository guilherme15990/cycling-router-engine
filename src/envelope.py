"""Zona de busca: recorta o grafo carregado ao box dinamico de uma consulta.

O grafo e a **cidade inteira** (carregado uma vez). A busca offline **nao** roda
sobre o grafo inteiro: ela roda na **zona** definida pelo box da consulta
(``geo.query_bbox``), que tambem limita a altimetria. Nao ha segundo modo — o
aplicativo e integralmente offline, entao o box **sempre** limita a busca.

O recorte e deterministico: um no entra na zona se estiver dentro do box; um
arco entra se **ambas** as pontas estiverem na zona. As margens automaticas do
box (``geo.auto_box_pads``) ja dao folga para desvios junto aos pinos.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from geo import BBox, bbox_contains, bbox_of_routes, query_bbox, union_bbox
from graph.model import RoutingGraph


def graph_for_bbox(graph: RoutingGraph, bbox: BBox) -> RoutingGraph:
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
    for node in graph.nodes.values():
        if south <= node.lat <= north and west <= node.lon <= east:
            zone.add_node(node)
            kept.add(node.id)
    for arc in graph.arcs.values():
        if arc.source in kept and arc.target in kept:
            zone.add_arc(arc)
    return zone


def scale_bbox(bbox: BBox, factor: float) -> BBox:
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
    max_iterations: int = 8,
    pad_m: float = 80.0,
) -> tuple[BBox, RoutingGraph, tuple[Sequence[tuple[float, float]], ...]]:
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
    """
    base = query_bbox(waypoints)
    if base is None:
        raise ValueError("sem pinos para dimensionar a zona")
    box = base
    zone = graph_for_bbox(graph, box)
    routes: Sequence[Sequence[tuple[float, float]]] = ()
    for _ in range(max_iterations):
        zone = graph_for_bbox(graph, box)
        routes = probe(zone)
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

