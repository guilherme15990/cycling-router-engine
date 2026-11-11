import json
import pytest
from router.interfaces.cli import main
from router.core.data.elevation import CallableElevation
from router.core.data.graph.model import (
    BicycleAccess,
    RoutingGraph,
    Arc,
    Segment,
    Node,
)
from router.core.data.graph.serialize import graph_from_dict, graph_to_dict, load_json, save_json
from router.core.data.graph.simplify import simplify_graph
from router.core.data.graph.surface import Surface
from router.core.data.graph.validate import validate_graph
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.data.snapping import (
    SnapError,
    SnapOptions,
    SpatialSnapper,
    build_query_graph,
)

CHAIN = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.0},
        {"type": "node", "id": 2, "lat": 0.001, "lon": 0.0},
        {"type": "node", "id": 3, "lat": 0.002, "lon": 0.0},
        {"type": "node", "id": 4, "lat": 0.003, "lon": 0.0},
        {
            "type": "way",
            "id": 201,
            "nodes": [1, 2],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
        {
            "type": "way",
            "id": 202,
            "nodes": [2, 3],
            "tags": {"highway": "residential", "surface": "gravel"},
        },
        {
            "type": "way",
            "id": 203,
            "nodes": [3, 4],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
    ]
}

ONEWAY = {
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


def build_chain(simplify: bool = True, elevation: bool = True) -> RoutingGraph:
    graph, _ = build_graph(OsmData.from_payload(CHAIN), BuildOptions())
    if elevation:
        attach_elevation(
            graph,
            CallableElevation(
                lambda lat, lon: 700.0 + 5000.0 * lat, resolution_m=30.0
            ),
            spacing_m=20.0,
        )
    if simplify:
        graph, _ = simplify_graph(graph)
    return graph


def build_oneway(simplify: bool = True) -> RoutingGraph:
    graph, _ = build_graph(OsmData.from_payload(ONEWAY), BuildOptions())
    if simplify:
        graph, _ = simplify_graph(graph)
    return graph


# --------------------------------------------------------------------------
# Projecao e escolha do ponto
# --------------------------------------------------------------------------
def test_projection_mid_arc_reports_offset_and_fraction():
    graph = build_chain(elevation=False)
    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")

    assert result.virtual
    assert result.candidate.kind == "arc"
    assert result.candidate.arc_id == "e0"
    assert result.candidate.segment_index == 1
    assert result.candidate.segment_id == "w202:0"
    assert result.candidate.offset_m == pytest.approx(11.1, rel=0.02)
    assert result.candidate.fraction == pytest.approx(0.5, abs=0.01)
    assert result.candidate.along_m == pytest.approx(166.8, rel=0.01)
    assert result.node_id == "__origin__"


def test_near_node_prefers_existing_node():
    graph = build_chain(simplify=False, elevation=False)
    result = SpatialSnapper(graph).snap((0.00102, 0.00004))

    assert not result.virtual
    assert result.candidate.kind == "node"
    assert result.candidate.node_id == 2
    assert result.node_id == 2
    assert result.splits == ()


def test_node_preference_yields_to_closer_arc():
    graph = build_chain(simplify=False, elevation=False)
    result = SpatialSnapper(graph).snap((0.0011, 0.00001))

    assert result.virtual
    assert result.candidate.kind == "arc"
    assert result.candidate.arc_id == "w202:0"


def test_near_arc_end_snaps_to_existing_node():
    graph = build_chain(elevation=False)
    result = SpatialSnapper(
        graph, SnapOptions(node_preference_m=0.0)
    ).snap((0.000005, 0.0))

    assert not result.virtual
    assert result.candidate.kind == "node"
    assert result.candidate.node_id == 1


def test_no_candidate_raises():
    graph = build_chain(elevation=False)
    with pytest.raises(SnapError):
        SpatialSnapper(graph, SnapOptions(max_distance_m=5.0)).snap(
            (0.01, 0.01)
        )


# --------------------------------------------------------------------------
# Divisao do arco
# --------------------------------------------------------------------------
def test_split_preserves_distance_elevation_and_surfaces():
    graph = build_chain()
    original = graph.get_arc("e0")
    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")
    query = result.apply(graph)

    head = query.get_arc("e0:h")
    tail = query.get_arc("e0:t")

    assert head.source == 1
    assert head.target == "__origin__"
    assert tail.source == "__origin__"
    assert tail.target == 4
    assert head.segment_count == 2
    assert tail.segment_count == 2

    assert head.distance_m + tail.distance_m == pytest.approx(
        original.distance_m, abs=0.01
    )
    assert head.elevation_gain_m + tail.elevation_gain_m == pytest.approx(
        original.elevation_gain_m, abs=0.01
    )
    assert head.elevation_loss_m + tail.elevation_loss_m == pytest.approx(
        original.elevation_loss_m, abs=0.01
    )

    assert head.surfaces == frozenset({Surface.ASPHALT, Surface.GRAVEL})
    assert tail.surfaces == frozenset({Surface.ASPHALT, Surface.GRAVEL})
    assert head.surface is Surface.ASPHALT
    assert tail.surface is Surface.ASPHALT

    node = query.get_node("__origin__")
    assert node.elevation_m == pytest.approx(707.5, abs=0.2)
    assert head.elevation_profile[-1].elevation_m == pytest.approx(
        node.elevation_m, abs=1e-6
    )
    assert tail.elevation_profile[0].elevation_m == pytest.approx(
        node.elevation_m, abs=1e-6
    )


def test_split_splits_reverse_arc_too():
    graph = build_chain(elevation=False)
    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")
    query = result.apply(graph)

    assert {split.arc_id for split in result.splits} == {"e0", "e1"}

    forward_head = query.get_arc("e0:h")
    reverse_tail = query.get_arc("e1:t")
    assert forward_head.geometry == tuple(reversed(reverse_tail.geometry))

    assert query.get_arc("e1:h").source == 4
    assert query.get_arc("e1:h").target == "__origin__"
    assert query.get_arc("e1:t").source == "__origin__"
    assert query.get_arc("e1:t").target == 1


def test_oneway_arc_has_no_reverse_split():
    graph = build_oneway()
    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")
    query = result.apply(graph)

    assert len(result.splits) == 1
    assert result.splits[0].arc_id == "e0"
    assert "e0:h" in query.arcs
    assert "e0:t" in query.arcs
    assert query.in_arcs(1) == []
    assert query.get_arc("e0:t").target == 3


def test_destination_near_source_of_oneway_splits_instead_of_dead_end_node():
    graph = build_oneway()
    result = SpatialSnapper(graph).snap(
        (0.000005, 0.0), label="destination", role="destination"
    )

    # O no 1 nao tem entrada utilizavel; em vez de conectar nele, o arco e
    # dividida a 1 m da ponta.
    assert result.virtual
    assert result.candidate.kind == "arc"
    assert result.candidate.along_m == pytest.approx(1.0, abs=0.01)
    assert result.candidate.point[0] > 0.0


def test_origin_near_target_of_oneway_splits_instead_of_dead_end_node():
    graph = build_oneway()
    result = SpatialSnapper(graph).snap(
        (0.001995, 0.0), label="origin", role="origin"
    )

    assert result.virtual
    assert result.candidate.kind == "arc"
    assert result.candidate.along_m == pytest.approx(221.4, abs=0.2)
    assert result.candidate.point[0] < 0.002


def test_base_graph_is_not_modified():
    graph = build_chain(elevation=False)
    before_arcs = set(graph.arcs)
    before_nodes = set(graph.nodes)

    SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin").apply(graph)

    assert set(graph.arcs) == before_arcs
    assert set(graph.nodes) == before_nodes
    assert graph.get_arc("e0").source == 1
    assert graph.get_arc("e0").target == 4


def test_split_distance_and_elevation_match_validation():
    graph = build_chain()
    query = build_query_graph(
        graph, (0.0005, 0.0001), (0.0025, 0.0001)
    ).graph
    issues = validate_graph(query)

    errors = [issue for issue in issues if issue.severity == "error"]
    assert errors == []


# --------------------------------------------------------------------------
# Grafo de consulta
# --------------------------------------------------------------------------
def test_build_query_graph_adds_two_virtual_nodes_and_is_connected():
    graph = build_chain(elevation=False)
    query = build_query_graph(
        graph, (0.0005, 0.0001), (0.0025, 0.0001)
    )

    assert query.origin.kind == "arc"
    assert query.destination.kind == "arc"
    assert query.origin_id == "__origin__"
    assert query.destination_id == "__destination__"
    assert query.virtual_nodes == frozenset({"__origin__", "__destination__"})
    assert query.graph.node_count == 4
    assert query.graph.arc_count == 6
    assert query.is_connected()


def test_query_graph_json_roundtrip():
    graph = build_chain(elevation=False)
    query = build_query_graph(
        graph, (0.0005, 0.0001), (0.0025, 0.0001)
    )

    restored = graph_from_dict(graph_to_dict(query.graph))

    assert "__origin__" in restored.nodes
    assert "__destination__" in restored.nodes
    assert restored.get_node("__origin__").tags["virtual"] == "snap"
    assert restored.arc_count == query.graph.arc_count
    assert sum(
        arc.segment_count for arc in restored.arcs.values()
    ) == sum(arc.segment_count for arc in query.graph.arcs.values())


def test_query_graph_origin_equals_destination_is_connected():
    graph = build_chain(elevation=False)
    point = (0.0015, 0.0001)
    query = build_query_graph(graph, point, point)

    assert query.origin_id == query.destination_id
    assert query.is_connected()


# --------------------------------------------------------------------------
# Dismount
# --------------------------------------------------------------------------
def test_no_dismount_skips_dismount_arcs():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    segment = Segment(
        id="s0",
        distance_m=111.19,
        geometry=((0.0, 0.0), (0.0, 0.001)),
        bicycle_access=BicycleAccess.DISMOUNT,
    )
    graph.add_arc(
        Arc(
            id="e0",
            source=1,
            target=2,
            segments=(segment,),
        )
    )

    with pytest.raises(SnapError):
        SpatialSnapper(
            graph, SnapOptions(allow_dismount=False)
        ).snap((0.0, 0.0005))

    result = SpatialSnapper(graph).snap((0.0, 0.0005))
    assert result.virtual
    assert result.candidate.arc_id == "e0"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_snap_json(tmp_path, capsys):
    graph = build_chain(elevation=False)
    path = save_json(graph, tmp_path / "graph.json")

    exit_code = main(
        [
            "snap",
            str(path),
            "--origin",
            "0.0005,0.0001",
            "--destination",
            "0.0025,0.0001",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["origin"]["kind"] == "arc"
    assert output["destination"]["kind"] == "arc"
    assert output["connected"] is True
    assert output["query"]["nodes"] == 4
    assert output["query"]["arcs"] == 6
    assert output["query"]["virtual_nodes"] == ["__destination__", "__origin__"]


def test_cli_snap_writes_query_graph(tmp_path, capsys):
    graph = build_chain(elevation=False)
    path = save_json(graph, tmp_path / "graph.json")
    out = tmp_path / "query.json"

    exit_code = main(
        [
            "snap",
            str(path),
            "--origin",
            "0.0005,0.0001",
            "--destination",
            "0.0025,0.0001",
            "--out-query",
            str(out),
        ]
    )
    capsys.readouterr()

    loaded = load_json(out)
    assert exit_code == 0
    assert loaded.node_count == 4
    assert loaded.arc_count == 6


def test_cli_snap_reports_no_candidate(tmp_path, capsys):
    graph = build_chain(elevation=False)
    path = save_json(graph, tmp_path / "graph.json")

    exit_code = main(
        [
            "snap",
            str(path),
            "--origin",
            "0.01,0.01",
            "--destination",
            "0.0025,0.0001",
            "--max-distance",
            "5",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "erro:" in captured.err
