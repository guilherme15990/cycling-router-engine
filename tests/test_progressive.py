from router.app.progressive import plan_routes_progressive
from router.core.physics import BicycleModel
from test_planning import _graph, _cyclist, ORIGIN, DESTINATION
from router.core.data.graph.model import RoutingGraph, Node
from router.core.data.envelope import adaptive_zone


def test_progress_then_complete_with_coverage():
    events = []
    result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=False, on_event=events.append)
    found = [e for e in events if e.status == 'route_found']
    assert found and found[0].plan.routes and found[0].plan.routes[0].safety.eligible
    assert not found[0].plan.truncated
    assert 'parcial' in found[0].plan.warnings[0].lower()
    assert events.index(found[0]) < len(events)-1
    assert result.status == 'completed' and result.plan.routes
    assert result.details['coverage'] == 'provided_graph'
    assert result.details['optimality_proven'] is False
    searches = [e for e in events if e.status == 'searching']
    assert max(e.details['iteration'] for e in searches) >= 1
    assert result.plan.zone.nodes <= result.plan.zone.graph_nodes


def test_progressive_without_subscriber_skips_preview(monkeypatch):
    import router.app.planning as planning
    from router.core.search import arc_jaccard

    calls = []
    original = planning.select_distinct_routes

    def counted(routes, *, max_routes=None, similarity=None):
        calls.append(len(routes))
        return original(
            routes,
            max_routes=max_routes,
            similarity=similarity or arc_jaccard,
        )

    monkeypatch.setattr(planning, "select_distinct_routes", counted)
    result = plan_routes_progressive(
        _graph(), ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(),
    )

    assert result.status == "completed"
    assert len(calls) == 1


def test_progressive_preview_applies_structural_collapse(monkeypatch):
    """O preview incremental usa a MESMA selecao estrutural do resultado final.

    Antes, ``publish_candidate`` mostrava todos os parciais (quase-duplicatas) e
    so encolhia no fim; aqui a selecao e espionada para provar que o preview
    tambem passa por ela (nao apenas o resultado final).
    """
    import router.app.planning as planning
    from router.core.search.route_set import METHOD_FARTHEST_FIRST, RouteSetSelection

    def only_first(routes, *, max_routes=None, similarity=None):
        return RouteSetSelection(
            routes=(routes[0],), indices=(0,), method=METHOD_FARTHEST_FIRST
        )

    monkeypatch.setattr(planning, "select_distinct_routes", only_first)
    events = []
    result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=False, on_event=events.append)
    found = [e for e in events if e.status == 'route_found']
    assert found  # houve preview incremental
    for event in found:
        assert len(event.plan.routes) == 1  # preview colapsado, nao a fronteira bruta
    assert len(result.plan.routes) == 1  # e o resultado final tambem


def test_cancel_preserves_first_validated_plan():
    events = []
    cancelled = [False]
    def on_event(event):
        events.append(event)
        if event.status == 'route_found': cancelled[0] = True
    result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=False, on_event=on_event,
        should_cancel=lambda: cancelled[0])
    assert result.status == 'interrupted' and result.plan.routes
    assert events[-1].status == 'interrupted'


def test_stops_publish_only_after_all_legs_have_validated_routes():
    events = []
    middle = (0.002, 0.0)
    result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION, stops=(middle,),
        cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=False, on_event=events.append)
    found = [e for e in events if e.status == 'route_found']
    assert found and found[0].plan.request.stops == (middle,)
    assert found[0].plan.routes[0].geometry[0].dist_m == 0
    assert result.status == 'completed' and result.plan.routes


def test_disconnected_graph_exhausts_coverage_without_claiming_global_no_route():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.002, lon=0.002))
    events = []
    result = plan_routes_progressive(graph, ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(), on_event=events.append)
    assert result.status == 'no_route' and not result.plan.routes
    assert result.details['coverage'] == 'provided_graph'
    assert not any(e.status == 'route_found' for e in events)


def test_cancel_before_first_route_does_not_claim_no_route():
    result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
        cyclist=_cyclist(), bicycle=BicycleModel(), should_cancel=lambda: True)
    assert result.status == 'interrupted' and result.plan is None


def test_instrumented_progress_preserves_result_and_restores_monitoring():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'debug'))
    from layer_trace import LayerTrace
    for _ in range(2):
        events = []
        with LayerTrace(samples=1) as trace:
            result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
                cyclist=_cyclist(), bicycle=BicycleModel(), on_event=events.append)
        layers = {row['layer'] for row in trace.report()['functions']}
        assert {'01', '02', '03', '04', '05', '06'} <= layers
        assert result.status == 'completed' and result.plan.routes
        assert sys.gettrace() is None


def test_instrumentation_observes_multiple_legs():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'debug'))
    from layer_trace import LayerTrace
    with LayerTrace(samples=2) as trace:
        result = plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
            stops=((0.002, 0.0),), cyclist=_cyclist(), bicycle=BicycleModel())
    layers = {row['layer'] for row in trace.report()['functions']}
    assert {'01', '02', '03', '04', '05'} <= layers
    assert result.status == 'completed'


def test_route_envelope_cannot_falsely_prove_that_new_area_was_searched():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=1.0, lon=1.0))
    searched = []
    def probe(zone):
        searched.append(zone.node_count)
        return (((-1.0, -1.0), (2.0, 2.0)),)
    adaptive_zone(graph, ((0.0, 0.0), (0.001, 0.001)), probe,
                  max_iterations=None, explore_full_coverage=True, pad_m=0)
    assert len(searched) >= 2 and searched[-1] == 2


def test_corridor_search_does_not_expand_beyond_initial_box():
    """Uma **unica** busca no corredor: sem crescimento para achar um desvio.

    Origem e destino so se ligam por um no **fora** do corredor (pins + margens
    automaticas). Como a busca ocorre uma so vez dentro do box, o resultado
    honesto e ``no_route`` — nao havia segunda busca nem ampliacao de zona.
    """
    from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
    from router.core.data.osm.parse import OsmData
    from router.core.data.elevation import CallableElevation
    data = {'elements': [
        {'type': 'node', 'id': 1, 'lat': 0.0, 'lon': 0.0},
        {'type': 'node', 'id': 2, 'lat': 0.0, 'lon': 0.002},
        {'type': 'node', 'id': 3, 'lat': 0.01, 'lon': 0.001},
        {'type': 'way', 'id': 10, 'nodes': [1, 3],
         'tags': {'highway': 'residential'}},
        {'type': 'way', 'id': 11, 'nodes': [3, 2],
         'tags': {'highway': 'residential'}},
    ]}
    graph, _ = build_graph(OsmData.from_payload(data), BuildOptions())
    attach_elevation(graph, CallableElevation(lambda lat, lon: 700.0), spacing_m=25)
    events = []
    result = plan_routes_progressive(graph, (0, 0), (0, .002),
        cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=False, on_event=events.append)
    # Nenhuma expansao: exatamente uma busca, na iteracao 1.
    searches = [e for e in events if e.status == 'searching']
    assert [e.details['iteration'] for e in searches] == [1]
    assert not any(e.status == 'route_found' for e in events)
    assert result.status == 'no_route' and not result.plan.routes


def test_progressive_rejects_old_fixed_iteration_limit():
    import pytest
    with pytest.raises(ValueError, match='max_iterations'):
        plan_routes_progressive(_graph(), ORIGIN, DESTINATION,
            cyclist=_cyclist(), bicycle=BicycleModel(), use_full_graph=True, max_iterations=1)
