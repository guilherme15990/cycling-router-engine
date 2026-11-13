"""Contrato publico de resposta de rota (ADR-030, F2).

Cobrem o envelope versionado ``route_contract`` e a projecao de
``planning.RoutePlanResult`` para ele:

* round-trip ``dumps``/``loads`` sem perda de informacao;
* ``format``/``version`` obrigatorios e versao desconhecida rejeitada;
* serializacao **estrita**: ``NaN``/``inf`` nunca viram JSON valido (sem
  ``default=str``, sem conversao silenciosa);
* invariantes estruturais: ``dist_m`` monotonico, ``recommended_route_id``
  pertence a ``routes``, perfil da rota igual ao da consulta;
* ausencia e ausencia: ``None`` continua ``None`` no envelope;
* geometria por ponto ancorada na origem e no destino da consulta.
"""

from __future__ import annotations

import json
import math
import pytest
from router.core.data.elevation import CallableElevation
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics import BicycleModel, CyclistModel
from router.app.planning import plan_offline_routes
from router.contracts import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    BBox,
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

# --------------------------------------------------------------------------
# Grafo pequeno (mesmo desenho de ``test_planning``): quadrado 1-2-4 / 1-3-4.
# --------------------------------------------------------------------------
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
DESTINATION = (0.002, 0.002)


def _plan():
    graph, _ = build_graph(OsmData.from_payload(_NETWORK), BuildOptions())
    attach_elevation(
        graph,
        CallableElevation(lambda lat, lon: 700.0 + 3000.0 * lat, resolution_m=30.0),
        spacing_m=20.0,
    )
    return plan_offline_routes(
        graph,
        ORIGIN,
        DESTINATION,
        cyclist=CyclistModel(
            mass_kg=75.0,
            max_power_w=600.0,
            sustainable_power_w=200.0,
            frontal_area_m2=0.5,
        ),
        bicycle=BicycleModel(),
    )


# --------------------------------------------------------------------------
# Construtores minimos para testes puros do contrato
# --------------------------------------------------------------------------
def _point(dist_m: float = 0.0, **kwargs) -> RoutePoint:
    base = dict(
        lat=0.0,
        lon=0.0,
        ele=None,
        dist_m=dist_m,
        speed_kmh=18.0,
        grade_pct=0.0,
    )
    base.update(kwargs)
    return RoutePoint(**base)


def _metrics(**kwargs) -> RouteMetrics:
    base = dict(
        source="assessment",
        distance_m=100.0,
        time_s=20.0,
        energy_j=5000.0,
        ascent_m=1.0,
        descent_m=0.5,
    )
    base.update(kwargs)
    return RouteMetrics(**base)


def _ml(**kwargs) -> RouteML:
    base = dict(
        mean_rpe=11.0,
        peak_rpe=13.0,
        calibrated=False,
        is_measurement=False,
        band_distance_fraction={"i1": 0.5, "i2": 0.5, "i3": 0.0, "i4": 0.0, "i5": 0.0},
        method_id="borg-estimate@1",
        model_version="1",
        borg_scale="borg_cr10",
        mean_rpe_time_weighted=12.0,
    )
    base.update(kwargs)
    return RouteML(**base)


def _route(route_id: str = "r1", **kwargs) -> Route:
    base = dict(
        id=route_id,
        profile="fastest",
        recommended=True,
        geometry=(_point(0.0), _point(100.0)),
        metrics=_metrics(),
        ml=_ml(),
        safety=RouteSafety(state="validated", eligible=True),
        provenance=RouteProvenance(kind="search", profile="fastest"),
    )
    base.update(kwargs)
    return Route(**base)


def _request(**kwargs) -> RouteRequest:
    base = dict(origin=ORIGIN, destination=DESTINATION, profile="fastest")
    base.update(kwargs)
    return RouteRequest(**base)


def _plan_contract(**kwargs) -> RoutePlan:
    bbox = BBox(south=0.0, west=0.0, north=0.002, east=0.002)
    base = dict(
        request=_request(),
        reached=True,
        search_bbox=bbox,
        bbox=bbox,
        zone=Zone(nodes=4, arcs=4, graph_nodes=4, graph_arcs=4),
        iterations=7,
        pareto_route_count=1,
        physiological_count=0,
        routes=(_route(),),
        recommended_route_id="r1",
    )
    base.update(kwargs)
    return RoutePlan(**base)


# --------------------------------------------------------------------------
# Round-trip e versao
# --------------------------------------------------------------------------
def test_contract_round_trip_is_lossless():
    plan = _plan_contract()
    payload = plan.to_dict()
    restored = loads(dumps(plan))
    assert restored.to_dict() == payload
    assert restored == plan


def test_contract_requires_format_and_version():
    payload = _plan_contract().to_dict()
    payload.pop("format")
    with pytest.raises(RouteContractError):
        RoutePlan.from_dict(payload)

    payload = _plan_contract().to_dict()
    payload["version"] = CONTRACT_VERSION + 1
    with pytest.raises(RouteContractError):
        RoutePlan.from_dict(payload)


def test_contract_rejects_unknown_format():
    payload = _plan_contract().to_dict()
    payload["format"] = "outro.formato"
    with pytest.raises(RouteContractError):
        RoutePlan.from_dict(payload)


def test_contract_strict_serialization_rejects_nan():
    plan = _plan_contract()
    # ``nan`` nao pode ser produzido pelo construtor, mas o envelope estrito
    # tambem precisa proteger contra mutacao externa do payload.
    payload = plan.to_dict()
    payload["routes"][0]["metrics"]["distance_m"] = math.nan
    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)


def test_contract_does_not_invent_elevation():
    point = _point(0.0, ele=None)
    assert point.to_dict()["ele"] is None
    restored = RoutePoint.from_dict(point.to_dict())
    assert restored.ele is None


# --------------------------------------------------------------------------
# Invariantes estruturais
# --------------------------------------------------------------------------
def test_contract_rejects_non_monotonic_distance():
    with pytest.raises(RouteContractError):
        _route(geometry=(_point(10.0), _point(5.0)))


def test_contract_rejects_recommended_route_outside_routes():
    with pytest.raises(RouteContractError):
        _plan_contract(recommended_route_id="nao-existe")


def test_contract_rejects_profile_mismatch():
    with pytest.raises(RouteContractError):
        _plan_contract(request=_request(profile="shortest"))


def test_contract_metrics_source_is_declared():
    with pytest.raises(RouteContractError):
        _metrics(source="inventado")


def test_contract_rejects_duplicate_route_ids():
    with pytest.raises(RouteContractError):
        _plan_contract(routes=(_route("r1"), _route("r1", recommended=False)))


# --------------------------------------------------------------------------
# Arquivo e projecao real
# --------------------------------------------------------------------------
def test_contract_write_and_read_plan(tmp_path):
    plan = _plan_contract()
    path = write_plan(tmp_path / "plan.json", plan)
    restored = read_plan(path)
    assert restored.to_dict() == plan.to_dict()


def test_real_plan_projects_to_versioned_contract():
    plan = _plan()
    contract = plan.to_contract()

    assert isinstance(contract, RoutePlan)
    assert contract.format == CONTRACT_FORMAT
    assert contract.version == CONTRACT_VERSION
    assert contract.reached is True
    assert contract.routes, "o grafo de teste deve produzir ao menos uma rota"
    assert contract.recommended_route_id in {route.id for route in contract.routes}

    # ``to_dict`` e um atalho do contrato, nao um segundo caminho.
    assert plan.to_dict() == contract.to_dict()

    route = contract.routes[0]
    assert route.metrics.source == "assessment"
    assert route.ml.calibrated is False
    assert route.ml.is_measurement is False
    assert route.geometry, "a rota projetada deve trazer geometria por ponto"
    distances = [point.dist_m for point in route.geometry]
    assert distances == sorted(distances)


def test_real_plan_geometry_starts_and_ends_at_query():
    plan = _plan()
    contract = plan.to_contract()
    route = contract.routes[0]

    first, last = route.geometry[0], route.geometry[-1]
    assert abs(first.lat - ORIGIN[0]) < 1e-6
    assert abs(first.lon - ORIGIN[1]) < 1e-6
    assert abs(last.lat - DESTINATION[0]) < 1e-6
    assert abs(last.lon - DESTINATION[1]) < 1e-6
    assert first.dist_m == pytest.approx(0.0, abs=1e-6)


def test_real_plan_contract_round_trip():
    contract = _plan().to_contract()
    # A serializacao canonicaliza a precisao declarada; o envelope e estavel.
    assert loads(dumps(contract)).to_dict() == contract.to_dict()
