"""Planejamento multi-perna (ADR-030, Fase 3).

Cobrem ``multi_leg.plan_routes``: delegacao sem paradas, continuidade da
geometria, acumulacao de ``dist_m``/metricas, contrato versionado e recusa de
pernas degeneradas.
"""

from __future__ import annotations

import pytest
from router.core.data.elevation import CallableElevation
from router.app.multi_leg import plan_routes
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics import BicycleModel, CyclistModel
from router.app.planning import plan_offline_routes
from router.contracts import RoutePlan, dumps, loads

_NETWORK = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.000},
        {"type": "node", "id": 2, "lat": 0.002, "lon": 0.000},
        {"type": "node", "id": 3, "lat": 0.000, "lon": 0.002},
        {"type": "node", "id": 4, "lat": 0.002, "lon": 0.002},
        {
            "type": "way",
            "id": 101,
            "nodes": [1, 2],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
        {
            "type": "way",
            "id": 102,
            "nodes": [2, 4],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
        {
            "type": "way",
            "id": 103,
            "nodes": [1, 3],
            "tags": {"highway": "residential", "surface": "gravel"},
        },
        {
            "type": "way",
            "id": 104,
            "nodes": [3, 4],
            "tags": {"highway": "residential", "surface": "gravel"},
        },
    ]
}

ORIGIN = (0.000, 0.000)
STOP = (0.002, 0.000)
DESTINATION = (0.002, 0.002)


def _graph():
    graph, _ = build_graph(OsmData.from_payload(_NETWORK), BuildOptions())
    attach_elevation(
        graph,
        CallableElevation(lambda lat, lon: 700.0 + 3000.0 * lat, resolution_m=30.0),
        spacing_m=20.0,
    )
    return graph


def _cyclist() -> CyclistModel:
    return CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )


def _plan(stops=(), **kwargs) -> RoutePlan:
    return plan_routes(
        _graph(),
        ORIGIN,
        DESTINATION,
        stops=stops,
        cyclist=_cyclist(),
        bicycle=BicycleModel(),
        **kwargs,
    )


def test_no_stops_matches_single_leg_contract():
    expected = plan_offline_routes(
        _graph(),
        ORIGIN,
        DESTINATION,
        cyclist=_cyclist(),
        bicycle=BicycleModel(),
    ).to_contract()
    assert _plan().to_dict() == expected.to_dict()


def test_two_legs_have_continuous_geometry_and_accumulated_distance():
    plan = _plan(stops=(STOP,))

    assert isinstance(plan, RoutePlan)
    assert plan.reached is True
    assert plan.request.stops == (STOP,)
    assert plan.routes, "o trajeto completo deve existir"
    assert plan.recommended_route_id == plan.routes[0].id
    assert plan.routes[0].id == "m0"

    route = plan.routes[0]
    distances = [point.dist_m for point in route.geometry]
    assert distances == sorted(distances)
    assert distances[0] == pytest.approx(0.0, abs=1e-6)
    assert distances[-1] == pytest.approx(route.metrics.distance_m, abs=1e-3)

    # A geometria e continua e visita a parada; a juncao nao e duplicada.
    first, last = route.geometry[0], route.geometry[-1]
    assert (first.lat, first.lon) == pytest.approx(ORIGIN)
    assert (last.lat, last.lon) == pytest.approx(DESTINATION)
    at_stop = [
        point
        for point in route.geometry
        if abs(point.lat - STOP[0]) < 1e-9 and abs(point.lon - STOP[1]) < 1e-9
    ]
    assert len(at_stop) == 1
    assert at_stop[0].dist_m == pytest.approx(route.metrics.distance_m / 2, rel=0.05)


def test_merged_metrics_are_the_sum_of_legs():
    plan = _plan(stops=(STOP,))
    route = plan.routes[0]

    first = plan_offline_routes(
        _graph(),
        ORIGIN,
        STOP,
        cyclist=_cyclist(),
        bicycle=BicycleModel(),
    ).to_contract()
    second = plan_offline_routes(
        _graph(),
        STOP,
        DESTINATION,
        cyclist=_cyclist(),
        bicycle=BicycleModel(),
    ).to_contract()

    leg_distance = first.routes[0].metrics.distance_m + second.routes[0].metrics.distance_m
    leg_time = first.routes[0].metrics.time_s + second.routes[0].metrics.time_s
    assert route.metrics.distance_m == pytest.approx(leg_distance, abs=1e-3)
    assert route.metrics.time_s == pytest.approx(leg_time, abs=1e-3)
    assert route.metrics.energy_j == pytest.approx(
        first.routes[0].metrics.energy_j + second.routes[0].metrics.energy_j,
        abs=1e-3,
    )
    assert route.metrics.meters_band == pytest.approx(
        (
            first.routes[0].metrics.meters_band[0] + second.routes[0].metrics.meters_band[0],
            first.routes[0].metrics.meters_band[1] + second.routes[0].metrics.meters_band[1],
            first.routes[0].metrics.meters_band[2] + second.routes[0].metrics.meters_band[2],
        ),
        abs=1e-3,
    )
    assert route.provenance.kind == "multi_leg"
    assert route.provenance.profile == plan.request.profile


def test_merged_contract_round_trips():
    plan = _plan(stops=(STOP,))
    assert loads(dumps(plan)).to_dict() == plan.to_dict()


def test_max_routes_limits_merged_routes():
    plan = _plan(stops=(STOP,), max_routes=1)
    assert len(plan.routes) == 1
    assert plan.recommended_route_id == "m0"


def test_degenerate_leg_is_rejected():
    with pytest.raises(ValueError, match="perna 0"):
        _plan(stops=(ORIGIN,))
    with pytest.raises(ValueError, match="perna 1"):
        _plan(stops=(STOP, STOP))


def test_two_stops_keep_all_waypoints():
    extra = (0.000, 0.002)
    plan = _plan(stops=(STOP, extra))
    assert plan.request.stops == (STOP, extra)
    route = plan.routes[0]
    for waypoint in (STOP, extra, DESTINATION):
        assert any(
            abs(point.lat - waypoint[0]) < 1e-9
            and abs(point.lon - waypoint[1]) < 1e-9
            for point in route.geometry
        )
    distances = [point.dist_m for point in route.geometry]
    assert distances == sorted(distances)
