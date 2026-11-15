import json

import pytest

from router.app.mobility import MobilityEngine, MobilityProfile, MotionEngine, ConstantPower
from router.core.data.access import access_scopes
from router.core.data.graph.model import Arc, Segment
from router.core.data.graph.restrictions import access_rules, turn_restriction_from_relation
from router.core.data.osm.build import allowed_directions, BuildOptions, build_graph
from router.core.data.osm.parse import OsmData
from router.core.data.elevation import CallableElevation
from router.core.search.circulation import arc_decision
from router.core.search.config import SearchConfig


def payload():
    return {"elements": [
        {"type": "node", "id": 1, "lat": 0, "lon": 0},
        {"type": "node", "id": 2, "lat": 0, "lon": .002},
        {"type": "node", "id": 3, "lat": .003, "lon": .001},
        {"type": "way", "id": 10, "nodes": [1, 2],
         "tags": {"highway": "motorway", "oneway": "yes", "bicycle": "no"}},
        {"type": "way", "id": 20, "nodes": [1, 3, 2],
         "tags": {"highway": "cycleway", "motor_vehicle": "no", "bicycle": "designated"}},
    ]}


def engine():
    return MobilityEngine.from_osm(payload(), elevation_provider=CallableElevation(lambda lat, lon: 700))


def test_single_api_routes_different_access_classes_without_reusing_bicycle_graph():
    toolkit = engine()
    bike = toolkit.plan((0, 0), (0, .002), vehicle="bicycle")
    car = toolkit.plan((0, 0), (0, .002), vehicle="motorcar", propulsion_type="electric")
    assert bike.reached and car.reached
    assert bike.routes[0].metrics.distance_m > car.routes[0].metrics.distance_m
    assert bike.diagnostics["access_mode"] == "bicycle"
    assert car.diagnostics["access_mode"] == "motorcar"
    assert car.routes[0].metrics.source_energy_j is None
    assert car.routes[0].metrics.battery_energy_j is None
    assert set(toolkit._networks) == {"bicycle", "motorcar"}
    encoded = json.loads(car.to_json())
    assert encoded["format"] == "roteamento.mobility-route-plan"
    assert encoded["mobility"]["propulsion"] == "electric"
    assert encoded["mobility"]["parameter_sources"]["nominal_power_w"] == "preset_estimate"


def test_motorcar_does_not_inherit_bicycle_contraflow():
    tags = {"oneway": "yes", "oneway:bicycle": "no"}
    assert allowed_directions(tags) == (True, True)
    assert allowed_directions(tags, "motorcar") == (True, False)
    assert allowed_directions(tags, "bicycle") == (True, True)
    tags["oneway:electric_bicycle"] = "yes"
    tags["cycleway"] = "opposite_lane"
    assert allowed_directions(tags, "electric_bicycle") == (True, False)
    assert allowed_directions({"highway": "motorway"}, "motorcar") == (True, False)
    assert allowed_directions({"highway": "motorway", "oneway": "no"}, "motorcar") == (True, True)
    assert allowed_directions({"junction": "roundabout", "oneway": "-1"}, "motorcar") == (False, True)


def test_access_precedence_and_conditional_exception_are_mode_specific():
    tags = {"vehicle": "no", "bicycle": "yes", "motorcar": "no",
            "motorcar:conditional": "yes @ (delivery)"}
    arc = Arc("r", 1, 2, (Segment("s", 100, restrictions=access_rules(tags)),))
    assert arc_decision(arc, access_scopes=access_scopes("bicycle")).allows
    assert arc_decision(arc, access_scopes=access_scopes("motorcar"), evaluator=lambda c: False).forbids
    assert arc_decision(arc, access_scopes=access_scopes("motorcar"), evaluator=lambda c: True).allows


def test_permission_in_one_contracted_segment_cannot_release_another_segment():
    arc = Arc("r", 1, 3, (
        Segment("public", 100, restrictions=access_rules({"bicycle": "yes"})),
        Segment("private", 100, restrictions=access_rules({"bicycle": "no"})),
    ))
    assert arc_decision(arc).forbids


def test_generic_routing_reads_direct_tags_retained_in_old_graphs():
    arc = Arc("r", 1, 2, (Segment("s", 100, tags={"motor_vehicle": "no"}),))
    assert arc_decision(arc, access_scopes=access_scopes("motorcar"), access_from_tags=True).forbids
    assert arc_decision(arc, access_scopes=access_scopes("bicycle"), access_from_tags=True).allows


def test_pbf_factory_requests_motor_road_classes(monkeypatch):
    from router.core.data.osm import pbf
    captured = {}
    def read(path, **kwargs):
        captured.update(kwargs)
        return OsmData.from_payload(payload())
    monkeypatch.setattr(pbf, "read_osm_pbf", read)
    result = MobilityEngine.from_pbf("unused.osm.pbf").plan((0, 0), (0, .002), vehicle="motorcar")
    assert result.reached
    assert {"motorway", "trunk", "cycleway"} <= captured["highways"]


def test_electric_cargo_keeps_cargo_and_electric_access_scopes():
    profile = MobilityProfile.create("cargo_bike", "electric")
    scopes = access_scopes(profile.transport_mode)
    assert scopes[:3] == ("cargo_bike", "electric_bicycle", "bicycle")


def test_turn_relation_exceptions_and_specific_restriction_are_respected():
    relation = dict(id=1, tags={"type": "restriction", "restriction": "no_left_turn", "except": "bicycle"},
                    members=[dict(type="way", ref=10, role="from"), dict(type="node", ref=2, role="via"),
                             dict(type="way", ref=20, role="to")])
    assert turn_restriction_from_relation(relation, travel_mode="electric_bicycle") is None
    assert turn_restriction_from_relation(relation, travel_mode="motorcar") is not None
    relation["tags"] = {"type": "restriction", "restriction": "no_left_turn", "restriction:bicycle": "only_right_turn"}
    assert turn_restriction_from_relation(relation, travel_mode="bicycle").restriction_type == "only_right_turn"
    assert turn_restriction_from_relation(relation, travel_mode="motorcar").restriction_type == "no_left_turn"


def test_filtered_graph_cannot_pretend_to_offer_another_category():
    graph, _ = build_graph(OsmData.from_payload(payload()), BuildOptions(travel_mode="bicycle"))
    with pytest.raises(ValueError, match="rebuild"):
        MobilityEngine(graph).plan((0, 0), (0, .002), vehicle="motorcar")


@pytest.mark.parametrize("profile", ["fastest", "shortest", "flattest", "balanced", "electric"])
def test_every_profile_uses_shared_physics_and_reports_precision(profile):
    plan = engine().plan((0, 0), (0, .002), profile=profile, search_config=SearchConfig(exact_speed=True))
    assert plan.reached
    assert plan.search_complete
    assert plan.state_approximated is False
    assert plan.diagnostics["physics_calls"] > 0
    assert plan.routes[0].metrics.mechanical_energy_j > 0


def test_explicit_search_budget_is_not_reported_complete():
    data = payload()
    data["elements"].extend([
        {"type": "node", "id": 4, "lat": .004, "lon": .002},
        {"type": "way", "id": 30, "nodes": [3, 4], "tags": {"highway": "cycleway"}},
    ])
    plan = MobilityEngine.from_osm(data).plan((0, 0), (0, .002), search_config=SearchConfig(max_expansions=1))
    assert not plan.search_complete
    assert not plan.optimality_proven


def test_required_elevation_never_returns_a_route_with_unavailable_dem():
    toolkit = MobilityEngine.from_osm(payload(), motion=MotionEngine(require_elevation=True))
    plan = toolkit.plan((0, 0), (0, .002))
    assert not plan.reached
    assert plan.diagnostics["rejected_routes"]


def test_public_toolkit_has_no_required_prediction_or_framework_dependency():
    from router.integration import MobilityEngine as PublicEngine, MobilityProfile as PublicProfile
    assert PublicEngine is MobilityEngine
    assert PublicProfile is MobilityProfile


def test_planning_blocks_network_access_in_custom_elevation_provider():
    from router.offline import OfflineViolation, is_network_disabled
    class Remote:
        def elevations(self, coordinates):
            import socket
            assert is_network_disabled()
            socket.create_connection(("example.invalid", 80))
    with pytest.raises(OfflineViolation):
        MobilityEngine.from_osm(payload(), elevation_provider=Remote()).plan((0, 0), (0, .002))


def test_custom_access_filter_is_applied_before_snapping_and_search():
    toolkit = engine()
    denied = toolkit.plan((0, 0), (0, .002), access_filter=lambda arc, mobility: False)
    assert not denied.reached
    assert denied.diagnostics["reason"] == "no_accessible_snap"
    with pytest.raises(TypeError, match="return bool"):
        toolkit.plan((0, 0), (0, .002), access_filter=lambda arc, mobility: "yes")


def test_explicit_preparation_reuses_ingestion_and_preserves_the_input_graph():
    graph, _ = build_graph(OsmData.from_payload(payload()), BuildOptions(travel_mode="bicycle"))
    provider = CallableElevation(lambda lat, lon: 700)
    toolkit = MobilityEngine(graph, elevation_provider=provider)
    prepared = toolkit.prepare()
    assert prepared is not graph
    assert toolkit.prepare() is prepared
    assert all(node.elevation_m == 700 for node in prepared.nodes.values())
    assert all(node.elevation_m is None for node in graph.nodes.values())
    assert toolkit.plan((0, 0), (0, .002)).reached


def test_native_progress_is_validated_incomplete_and_roundtrips_without_changing_final_plan():
    from router.integration import MobilityPlan
    updates = []
    toolkit = engine()
    final = toolkit.plan((0, 0), (0, .002), on_plan=updates.append)
    assert updates and all(plan.routes and not plan.search_complete and not plan.optimality_proven for plan in updates)
    assert final.search_complete
    assert final.routes[0].elevation_profile
    assert final.routes[0].elevation_profile[-1][0] == pytest.approx(final.routes[0].metrics.distance_m)
    assert all(height == pytest.approx(700) for _, height in final.routes[0].elevation_profile)
    assert MobilityPlan.from_dict(json.loads(final.to_json())) == final
    baseline = engine().plan((0, 0), (0, .002))
    assert [(r.arc_ids, r.metrics) for r in final.routes] == [(r.arc_ids, r.metrics) for r in baseline.routes]


def test_native_cancellation_after_first_validated_preview_preserves_usable_plan():
    from router.integration import SearchCancelled
    updates = []
    with pytest.raises(SearchCancelled):
        engine().plan((0, 0), (0, .002), on_plan=updates.append, should_cancel=lambda: bool(updates))
    assert updates[0].routes and not updates[0].search_complete


def test_native_progress_interval_is_validated_before_preparation():
    with pytest.raises(ValueError, match="progress_event_every"):
        engine().plan((0, 0), (0, .002), progress_event_every=0)
