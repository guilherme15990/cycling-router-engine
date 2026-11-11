"""Envelope dinamico de consulta (box) e recorte da zona de busca.

Portado de ``toporide`` (``src/domain/geo.ts``). Como o aplicativo e
integralmente offline, o box limita a **busca** e a **altimetria** (nao ha um
segundo modo em que o grafo inteiro seja percorrido). Cobrem:

* margens automaticas por eixo (faixa, crescimento com a diagonal, anisotropia);
* box do corredor (pinos) e crescimento para cobrir **todas** as rotas;
* ``query_bbox`` (pinos + rotas) e utilitarios (uniao, contencao, span minimo);
* ``graph_for_bbox`` (recorte deterministico do grafo carregado).
"""

from __future__ import annotations

import pytest
from router.core.data.envelope import adaptive_zone, graph_for_bbox, scale_bbox
from router.core.data.geo import (
    auto_box_pads,
    bbox_contains,
    bbox_intersects,
    bbox_of_routes,
    bbox_size_km,
    bbox_with_routes,
    corridor_bbox_of,
    ensure_min_span,
    expand_bbox,
    m_per_deg_lon,
    query_bbox,
    union_bbox,
)
from router.core.data.graph.model import Arc, Node, RoutingGraph, Segment


# --------------------------------------------------------------------------
# Infra
# --------------------------------------------------------------------------
def _node(node_id, lat, lon):
    return Node(id=node_id, lat=lat, lon=lon)


def _arc(arc_id, source, target, distance_m=100.0):
    return Arc(
        id=arc_id,
        source=source,
        target=target,
        segments=(Segment(id=f"{arc_id}:0", distance_m=distance_m),),
    )


# --------------------------------------------------------------------------
# Margens e expansao
# --------------------------------------------------------------------------
def test_expand_bbox_uses_per_axis_scale():
    box = expand_bbox((0.0, 0.0, 0.0, 0.0), 1000.0, 0.0)
    south, west, north, east = box
    assert north == pytest.approx(1000.0 / 110_574.0)
    assert east == pytest.approx(1000.0 / m_per_deg_lon(0.0))
    assert south == pytest.approx(-north)


def test_auto_box_pads_stay_within_limits():
    for span in (0.0, 0.001, 0.05, 0.5, 5.0):
        pads = auto_box_pads([(-span, -span), (span, span)])
        assert 0.1 <= pads[0] <= 0.4
        assert 0.1 <= pads[1] <= 0.4


def test_auto_box_pads_grow_with_diagonal():
    short = auto_box_pads([(0.0, 0.0), (0.01, 0.01)])
    long = auto_box_pads([(0.0, 0.0), (0.2, 0.2)])
    assert long[0] >= short[0]
    assert long[1] >= short[1]
    assert long != short


def test_auto_box_pads_are_anisotropic():
    # Rota norte-sul: eixo Y longo recebe MENOS folga; eixo X curto recebe MAIS.
    pads = auto_box_pads([(0.0, 0.0), (0.1, 0.0)])
    assert pads[0] > pads[1]


def test_auto_box_pads_trivial_cases():
    assert auto_box_pads([]) == (0.15, 0.15)
    assert auto_box_pads([(0.0, 0.0)]) == (0.2, 0.2)


# --------------------------------------------------------------------------
# Corredor e rotas
# --------------------------------------------------------------------------
def test_corridor_bbox_contains_all_points():
    points = [(-17.80, -50.92), (-17.78, -50.90), (-17.79, -50.95)]
    box = corridor_bbox_of(points, 0.2, 0.3)
    assert box[0] < min(p[0] for p in points)
    assert box[1] < min(p[1] for p in points)
    assert box[2] > max(p[0] for p in points)
    assert box[3] > max(p[1] for p in points)


def test_bbox_of_routes_none_when_empty():
    assert bbox_of_routes([]) is None


def test_bbox_with_routes_grows_to_cover_detour():
    base = corridor_bbox_of([(0.0, 0.0), (0.01, 0.01)], 0.1, 0.1)
    route = [(0.0, 0.0), (0.05, 0.02)]  # sai bem ao norte do box dos pinos
    final = bbox_with_routes(base, [route])
    assert final is not None
    assert bbox_contains(final, base)
    assert final[2] >= 0.05  # cobre o trecho que saiu do box inicial


def test_bbox_with_routes_keeps_base_when_no_routes():
    base = (0.0, 0.0, 0.01, 0.01)
    assert bbox_with_routes(base, []) == base
    assert bbox_with_routes(None, []) is None


def test_union_bbox_is_minimal_covering_box():
    assert union_bbox((0.0, 0.0, 1.0, 1.0), (0.5, -1.0, 2.0, 0.5)) == (
        0.0,
        -1.0,
        2.0,
        1.0,
    )


# --------------------------------------------------------------------------
# query_bbox
# --------------------------------------------------------------------------
def test_query_bbox_pins_only_contains_pins():
    waypoints = [(-17.80, -50.92), (-17.78, -50.90)]
    box = query_bbox(waypoints)
    assert box is not None
    assert bbox_contains(box, corridor_bbox_of(waypoints, 0.0))
    assert box[0] < waypoints[0][0] and box[2] > waypoints[1][0]


def test_query_bbox_equals_bbox_with_routes_of_pins_base():
    waypoints = [(-17.80, -50.92), (-17.78, -50.90)]
    routes = [[(-17.80, -50.92), (-17.79, -50.86)]]
    pad_x, pad_y = auto_box_pads(waypoints)
    expected = bbox_with_routes(
        corridor_bbox_of(waypoints, pad_x, pad_y), routes
    )
    assert query_bbox(waypoints, routes) == expected


def test_query_bbox_none_without_pins_and_routes():
    assert query_bbox([]) is None


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------
def test_ensure_min_span():
    box = ensure_min_span((0.0, 0.0, 0.001, 0.001), 0.01)
    assert box[2] - box[0] == pytest.approx(0.01)
    assert box[3] - box[1] == pytest.approx(0.01)
    assert (box[0] + box[2]) / 2 == pytest.approx(0.0005)


def test_bbox_size_km():
    width_km, height_km = bbox_size_km((0.0, 0.0, 0.001, 0.001))
    assert height_km == pytest.approx(0.001 * 110_574.0 / 1000.0)
    assert width_km == pytest.approx(0.001 * m_per_deg_lon(0.0) / 1000.0)


def test_bbox_contains_and_intersects():
    outer = (0.0, 0.0, 1.0, 1.0)
    assert bbox_contains(outer, (0.2, 0.2, 0.8, 0.8))
    assert not bbox_contains(outer, (0.2, 0.2, 1.5, 0.8))
    assert bbox_intersects(outer, (0.5, 0.5, 2.0, 2.0))
    assert not bbox_intersects(outer, (2.0, 2.0, 3.0, 3.0))


# --------------------------------------------------------------------------
# Recorte do grafo (zona de busca)
# --------------------------------------------------------------------------
def _line_graph():
    graph = RoutingGraph()
    graph.add_node(_node("a", 0.0, 0.0))
    graph.add_node(_node("b", 0.0, 0.001))
    graph.add_node(_node("c", 0.0, 0.002))
    graph.add_node(_node("far", 5.0, 5.0))
    graph.add_arc(_arc("ab", "a", "b"))
    graph.add_arc(_arc("bc", "b", "c"))
    graph.add_arc(_arc("c_far", "c", "far"))
    return graph


def test_graph_for_bbox_keeps_only_inside_nodes_and_arcs():
    graph = _line_graph()
    zone = graph_for_bbox(graph, (-0.01, -0.01, 0.01, 0.01))
    assert set(zone.node_ids()) == {"a", "b", "c"}
    assert zone.arc_count == 2
    assert "c_far" not in zone.arcs  # arco com ponta fora do box e descartado


def test_graph_for_bbox_does_not_mutate_source():
    graph = _line_graph()
    graph_for_bbox(graph, (-0.01, -0.01, 0.01, 0.01))
    assert graph.node_count == 4
    assert graph.arc_count == 3


def test_graph_for_bbox_covers_everything_when_box_is_huge():
    graph = _line_graph()
    zone = graph_for_bbox(graph, (-90.0, -180.0, 90.0, 180.0))
    assert zone.node_count == graph.node_count
    assert zone.arc_count == graph.arc_count


# --------------------------------------------------------------------------
# Zona adaptativa (box cresce ate envolver as rotas)
# --------------------------------------------------------------------------
def test_scale_bbox_grows_about_center():
    assert scale_bbox((0.0, 0.0, 1.0, 2.0), 2.0) == (-0.5, -1.0, 1.5, 3.0)


def test_scale_bbox_rejects_non_positive_factor():
    with pytest.raises(ValueError):
        scale_bbox((0.0, 0.0, 1.0, 1.0), 0.0)


def test_adaptive_zone_keeps_base_when_routes_fit():
    empty = RoutingGraph()
    waypoints = [(0.0, 0.0), (0.0, 0.004)]
    base = query_bbox(waypoints)
    route = [(0.0, 0.0), (0.0, 0.003)]

    box, _zone, routes = adaptive_zone(empty, waypoints, lambda _z: [route])
    assert box == base
    assert routes == (route,)


def test_adaptive_zone_scales_when_probe_returns_nothing():
    empty = RoutingGraph()
    waypoints = [(0.0, 0.0), (0.0, 0.004)]
    base = query_bbox(waypoints)
    calls = {"n": 0}

    def probe(_zone):
        calls["n"] += 1
        return [] if calls["n"] == 1 else [[(0.0, 0.0), (0.0, 0.003)]]

    box, _zone, routes = adaptive_zone(empty, waypoints, probe, growth=2.0)
    assert box != base  # precisou crescer para "religar" a rede
    assert routes  # na 2a chamada veio rota


def test_adaptive_zone_grows_to_cover_detour():
    empty = RoutingGraph()
    waypoints = [(0.0, 0.0), (0.0, 0.004)]
    route = [(0.0, 0.0), (0.05, 0.01)]  # sai bastante do box dos pinos

    box, _zone, routes = adaptive_zone(empty, waypoints, lambda _z: [route])
    assert bbox_contains(box, bbox_of_routes(routes, 80.0))


def test_adaptive_zone_requires_waypoints():
    with pytest.raises(ValueError):
        adaptive_zone(RoutingGraph(), [], lambda _z: [])
