import json
import math
from pathlib import Path

import pytest

from elevation import (
    CallableElevation,
    build_elevation_profile,
    fill_missing,
)
from geo import (
    cumulative_distances,
    haversine_m,
    parse_bbox,
    sample_along,
)
from graph.model import RoutingGraph, Arc, Segment, Surface
from graph.restrictions import parse_conditional_restrictions
from graph.simplify import SimplifyOptions, simplify_graph
from graph.surface import (
    SURFACE_PRIORITY,
    SurfaceStatus,
    surface_from_tags,
    surface_observation,
)
from osm.build import BuildOptions, attach_elevation, build_graph
from osm.parse import OsmData

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_network.json"

CHAIN_FIXTURE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.0},
        {"type": "node", "id": 2, "lat": 0.001, "lon": 0.0},
        {"type": "node", "id": 3, "lat": 0.002, "lon": 0.0},
        {"type": "node", "id": 4, "lat": 0.003, "lon": 0.0},
        {
            "type": "way",
            "id": 201,
            "nodes": [1, 2],
            "tags": {"highway": "residential", "surface": "asphalt", "name": "Rua X"},
        },
        {
            "type": "way",
            "id": 202,
            "nodes": [2, 3],
            "tags": {"highway": "residential", "surface": "gravel", "name": "Rua X"},
        },
        {
            "type": "way",
            "id": 203,
            "nodes": [3, 4],
            "tags": {"highway": "residential", "surface": "asphalt", "name": "Rua X"},
        },
    ]
}

ONEWAY_FIXTURE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.0},
        {"type": "node", "id": 2, "lat": 0.001, "lon": 0.0},
        {"type": "node", "id": 3, "lat": 0.002, "lon": 0.0},
        {
            "type": "way",
            "id": 301,
            "nodes": [1, 2],
            "tags": {"highway": "cycleway", "oneway": "yes"},
        },
        {
            "type": "way",
            "id": 302,
            "nodes": [2, 3],
            "tags": {"highway": "cycleway", "oneway": "yes"},
        },
    ]
}


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build_from_fixture(payload, **options):
    data = OsmData.from_payload(payload)
    return build_graph(data, BuildOptions(**options))


# --------------------------------------------------------------------------
# geo
# --------------------------------------------------------------------------
def test_haversine_one_degree_latitude():
    assert haversine_m((0.0, 0.0), (1.0, 0.0)) == pytest.approx(111_195, rel=0.001)


def test_cumulative_and_sampling():
    coords = [(0.0, 0.0), (0.001, 0.0)]
    cumulative = cumulative_distances(coords)
    assert cumulative[0] == 0.0
    assert cumulative[-1] == pytest.approx(111.19, rel=0.01)

    samples = sample_along(coords, spacing_m=25.0)
    assert samples[0] == (0.0, (0.0, 0.0))
    assert samples[-1][0] == pytest.approx(cumulative[-1])
    assert samples[-1][1] == coords[-1]
    distances = [distance for distance, _ in samples]
    assert distances == sorted(distances)
    steps = [b - a for a, b in zip(distances, distances[1:])]
    assert all(math.isclose(step, 25.0) for step in steps[:-1])
    assert 0 < steps[-1] <= 25.0


def test_parse_bbox():
    south, west, north, east = parse_bbox("-23.56,-46.66,-23.54,-46.62")
    assert (south, west, north, east) == (-23.56, -46.66, -23.54, -46.62)
    with pytest.raises(ValueError):
        parse_bbox("1,2,3")
    with pytest.raises(ValueError):
        parse_bbox("-20,10,-25,11")


# --------------------------------------------------------------------------
# superficie e proveniencia
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"surface": "asphalt"}, Surface.ASPHALT),
        ({"surface": "concrete:plates"}, Surface.CONCRETE),
        ({"surface": "paving_stones"}, Surface.PAVED),
        ({"surface": "cobblestone"}, Surface.PAVED),
        ({"surface": "compacted"}, Surface.COMPACTED_GRAVEL),
        ({"surface": "gravel"}, Surface.GRAVEL),
        ({"surface": "ground"}, Surface.DIRT),
        ({"surface": "sand"}, Surface.SAND),
        ({"tracktype": "grade1"}, Surface.COMPACTED_GRAVEL),
        ({"tracktype": "grade5"}, Surface.SAND),
        ({"highway": "residential"}, Surface.UNKNOWN),
    ],
)
def test_surface_mapping(tags, expected):
    assert surface_from_tags(tags) is expected


def test_surface_provenance():
    assert surface_observation({"surface": "asphalt"}) == (
        Surface.ASPHALT,
        SurfaceStatus.MAPPED,
    )
    assert surface_observation({"tracktype": "grade2"}) == (
        Surface.GRAVEL,
        SurfaceStatus.MAPPED,
    )
    assert surface_observation({"highway": "residential"}) == (
        Surface.UNKNOWN,
        SurfaceStatus.UNKNOWN,
    )
    # valor existe, mas fora da taxonomia: nao e ausencia de dado
    assert surface_observation({"surface": "lava"}) == (
        Surface.UNKNOWN,
        SurfaceStatus.UNMAPPED,
    )


def test_surface_priority_is_ordinal():
    assert SURFACE_PRIORITY[Surface.ASPHALT] == 1
    assert SURFACE_PRIORITY[Surface.PAVED] == 2
    assert SURFACE_PRIORITY[Surface.SAND] == 5
    assert SURFACE_PRIORITY[Surface.UNKNOWN] == 6


def test_conditional_restrictions_preserved():
    restrictions = parse_conditional_restrictions(
        {
            "bicycle:conditional": "no @ (Mo-Fr 07:00-10:00)",
            "access:conditional": "yes @ (Sa,Su)",
            "surface": "asphalt",
        }
    )
    assert len(restrictions) == 2
    bicycle = next(item for item in restrictions if item.scope == "bicycle")
    assert bicycle.value == "no"
    assert bicycle.condition == "(Mo-Fr 07:00-10:00)"
    assert bicycle.is_conditional
    assert bicycle.raw == "no @ (Mo-Fr 07:00-10:00)"


# --------------------------------------------------------------------------
# construcao do grafo
# --------------------------------------------------------------------------
def test_graph_structure(payload):
    graph, stats = build_from_fixture(payload)

    assert isinstance(graph, RoutingGraph)
    assert graph.node_count == 6
    assert graph.arc_count == 15

    segment_count = sum(arc.segment_count for arc in graph.arcs.values())
    assert segment_count == 15
    assert stats.segments == 8  # 101..107 + 113
    assert stats.skipped_short == 0


def test_oneway_and_bicycle_exceptions(payload):
    from osm.build import allowed_directions

    graph, _ = build_from_fixture(payload)

    assert "w101:0" in graph.arcs
    assert "w101:0:rev" not in graph.arcs
    assert "w104:0" in graph.arcs
    assert "w104:0:rev" in graph.arcs

    assert allowed_directions({"oneway": "-1"}) == (False, True)
    assert allowed_directions({"junction": "roundabout"}) == (True, False)
    assert allowed_directions({"oneway": "yes", "cycleway": "opposite_lane"}) == (True, True)


def test_excluded_highways_and_access(payload):
    graph, _ = build_from_fixture(payload)

    highways = {arc.highway for arc in graph.arcs.values()}
    assert "steps" not in highways
    assert "footway" not in highways
    assert all(arc.osm_way_id != 109 for arc in graph.arcs.values())


def test_allow_footway_option(payload):
    graph, _ = build_from_fixture(payload, allow_footway=True)
    footway_arcs = [arc for arc in graph.arcs.values() if arc.highway == "footway"]
    assert len(footway_arcs) == 2
    assert all(arc.surface is Surface.ASPHALT for arc in footway_arcs)


def test_surface_and_attributes(payload):
    graph, _ = build_from_fixture(payload)

    assert graph.get_arc("w102:0").surface is Surface.PAVED
    assert graph.get_arc("w102:0").surface_priority == 2
    assert graph.get_arc("w102:0").surface_status is SurfaceStatus.MAPPED
    assert graph.get_arc("w105:0").surface is Surface.GRAVEL
    assert graph.get_arc("w106:0").surface is Surface.DIRT
    assert graph.get_arc("w101:0").name == "Rua Um"
    assert graph.get_node(1).elevation_m == 700.0

    unknown = graph.get_arc("w113:0")
    assert unknown.surface is Surface.ASPHALT  # w113 tem surface=asphalt


def test_geometry_preserved_and_length_is_path_length(payload):
    graph, _ = build_from_fixture(payload)
    arc = graph.get_arc("w113:0")

    assert len(arc.geometry) == 3
    straight = haversine_m(arc.geometry[0], arc.geometry[-1])
    assert arc.distance_m > straight
    assert arc.distance_m == pytest.approx(183.4, rel=0.01)


def test_reverse_arc_swaps_elevation(payload):
    graph, _ = build_from_fixture(payload)
    forward = graph.get_arc("w103:0")
    reverse = graph.get_arc("w103:0:rev")

    assert reverse.source == forward.target
    assert reverse.target == forward.source
    assert reverse.elevation_gain_m == forward.elevation_loss_m
    assert reverse.elevation_loss_m == forward.elevation_gain_m
    assert reverse.geometry == tuple(reversed(forward.geometry))
    assert [s.id for s in reverse.segments] == [
        f"{s.id}:rev" for s in reversed(forward.segments)
    ]


def test_segment_requires_positive_distance():
    with pytest.raises(ValueError):
        Segment(id="x", distance_m=0.0)

    with pytest.raises(ValueError):
        Arc(id="e", source=1, target=1, segments=(Segment(id="s", distance_m=1.0),))


# --------------------------------------------------------------------------
# simplificacao
# --------------------------------------------------------------------------
def test_simplify_contracts_degree_two_chain_preserving_segments():
    graph, _ = build_from_fixture(CHAIN_FIXTURE)
    assert graph.node_count == 4
    assert graph.arc_count == 6

    simplified, stats = simplify_graph(graph)

    assert stats.contracted_nodes == 2
    assert stats.multi_segment_arcs == 2
    assert simplified.node_count == 2
    assert simplified.arc_count == 2
    assert set(simplified.nodes) == {1, 4}

    forward = next(arc for arc in simplified.arcs.values() if arc.source == 1)
    assert forward.target == 4
    assert forward.segment_count == 3
    assert [segment.id for segment in forward.segments] == ["w201:0", "w202:0", "w203:0"]
    assert forward.distance_m == pytest.approx(3 * 111.19, rel=0.01)
    assert forward.surfaces == frozenset({Surface.ASPHALT, Surface.GRAVEL})
    assert forward.surface is Surface.ASPHALT  # dominante por comprimento
    assert forward.name == "Rua X"
    assert len(forward.geometry) == 4

    reverse = next(arc for arc in simplified.arcs.values() if arc.source == 4)
    assert reverse.target == 1
    assert [segment.id for segment in reverse.segments] == [
        "w203:0:rev",
        "w202:0:rev",
        "w201:0:rev",
    ]


def test_simplify_keeps_elevation_profiles():
    graph, _ = build_from_fixture(CHAIN_FIXTURE)
    attach_elevation(
        graph,
        CallableElevation(lambda lat, lon: 700.0 + 5000.0 * lat, resolution_m=30.0),
        spacing_m=20.0,
    )

    simplified, _ = simplify_graph(graph)
    forward = next(arc for arc in simplified.arcs.values() if arc.source == 1)

    assert forward.elevation_gain_m == pytest.approx(15.0, abs=0.2)
    assert forward.elevation_loss_m == pytest.approx(0.0, abs=0.1)
    assert forward.elevation_delta_m == pytest.approx(15.0, abs=0.2)
    assert forward.elevation_source == "callable"
    assert forward.elevation_resolution_m == 30.0
    assert sum(segment.elevation_gain_m for segment in forward.segments) == pytest.approx(
        forward.elevation_gain_m, abs=1e-6
    )

    reverse = next(arc for arc in simplified.arcs.values() if arc.source == 4)
    assert reverse.elevation_gain_m == pytest.approx(forward.elevation_loss_m, abs=1e-6)
    assert reverse.elevation_loss_m == pytest.approx(forward.elevation_gain_m, abs=1e-6)


def test_simplify_oneway_chain():
    graph, _ = build_from_fixture(ONEWAY_FIXTURE)
    assert graph.arc_count == 2

    simplified, stats = simplify_graph(graph)

    assert stats.contracted_nodes == 1
    assert simplified.node_count == 2
    assert simplified.arc_count == 1
    arc = next(iter(simplified.arcs.values()))
    assert (arc.source, arc.target) == (1, 3)
    assert arc.segment_count == 2


def test_simplify_can_keep_nodes():
    graph, _ = build_from_fixture(CHAIN_FIXTURE)
    simplified, stats = simplify_graph(graph, SimplifyOptions(keep_nodes=frozenset({2})))

    assert stats.contracted_nodes == 1
    assert 2 in simplified.nodes
    assert simplified.node_count == 3


def test_simplify_network_with_junctions(payload):
    graph, _ = build_from_fixture(payload)
    simplified, stats = simplify_graph(graph)

    # Nos 4, 5 e 6 sao apenas passagens; 1, 2 e 3 sao cruzamentos.
    assert stats.contracted_nodes == 3
    assert simplified.node_count == 3
    assert set(simplified.nodes) == {1, 2, 3}
    assert simplified.arc_count == 9

    merged = next(
        arc
        for arc in simplified.arcs.values()
        if arc.source == 3 and arc.target == 2 and arc.segment_count == 3
    )
    assert merged.surfaces == frozenset({Surface.ASPHALT, Surface.DIRT, Surface.GRAVEL})
    assert merged.distance_m == pytest.approx(3 * 111.19, rel=0.01)


# --------------------------------------------------------------------------
# elevacao
# --------------------------------------------------------------------------
def test_fill_missing_interpolates_gaps():
    assert fill_missing([None, None]) == [None, None]
    assert fill_missing([None, 10.0, None]) == [10.0, 10.0, 10.0]
    assert fill_missing([0.0, None, 20.0]) == [0.0, 10.0, 20.0]


def test_smoothing_reduces_dem_noise_and_keeps_endpoints():
    from elevation import smooth_elevations

    noisy = [700.0, 700.5, 699.5, 700.4, 699.6, 700.0]
    smoothed = smooth_elevations(noisy, window=3)

    assert smoothed[0] == noisy[0]
    assert smoothed[-1] == noisy[-1]
    assert len(smoothed) == len(noisy)

    raw_gain = sum(max(0.0, b - a) for a, b in zip(noisy, noisy[1:]))
    smooth_gain = sum(max(0.0, b - a) for a, b in zip(smoothed, smoothed[1:]))
    assert smooth_gain < raw_gain
    assert smooth_elevations(noisy, window=1) == noisy


def test_elevation_profile_accumulates_ascent_and_descent():
    def bump(lat: float, lon: float) -> float:
        return 700.0 + 20.0 * math.exp(-(((lon - 0.0005) / 0.0002) ** 2))

    geometry = [(0.0, 0.0), (0.0, 0.001)]
    stats = build_elevation_profile(geometry, CallableElevation(bump), spacing_m=10.0)

    assert stats.gain_m > 15.0
    assert stats.loss_m > 15.0
    assert abs(stats.gain_m - stats.loss_m) < 1.0
    assert stats.delta_m == pytest.approx(0.0, abs=1e-6)
    assert stats.profile[0].source_id == "callable"
    assert stats.profile[0].resolution_m is None


def test_elevation_profile_linear_ramp():
    def ramp(lat: float, lon: float) -> float:
        return 700.0 + 10_000.0 * lat

    geometry = [(0.0, 0.0), (0.001, 0.0)]
    stats = build_elevation_profile(geometry, CallableElevation(ramp), spacing_m=25.0)

    assert stats.gain_m == pytest.approx(10.0, abs=0.05)
    assert stats.loss_m == pytest.approx(0.0, abs=1e-9)
    assert stats.delta_m == pytest.approx(10.0, abs=0.05)


def test_attach_elevation_fills_nodes_and_segments(payload):
    graph, _ = build_from_fixture(payload)

    attach_elevation(
        graph,
        CallableElevation(
            lambda lat, lon: 700.0 + 5000.0 * lat, resolution_m=30.0
        ),
        spacing_m=20.0,
    )

    assert graph.get_node(1).elevation_m == pytest.approx(700.0, abs=0.1)
    assert graph.get_node(2).elevation_m == pytest.approx(705.0, abs=0.1)

    arc = graph.get_arc("w101:0")
    assert arc.elevation_gain_m == pytest.approx(5.0, abs=0.1)
    assert arc.elevation_loss_m == pytest.approx(0.0, abs=0.05)
    assert arc.has_elevation
    assert arc.surface_priority == 1

    segment = arc.segments[0]
    assert segment.elevation_source == "callable"
    assert segment.elevation_resolution_m == 30.0
    assert segment.has_elevation
    assert arc.elevation_source == "callable"
