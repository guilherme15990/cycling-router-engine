import json
from pathlib import Path

import pytest

from cli import main
from elevation import CallableElevation
from graph.serialize import (
    graph_from_dict,
    graph_to_dict,
    load_json,
    save_geojson,
    save_json,
    summarize,
    to_geojson,
)
from graph.simplify import simplify_graph
from graph.surface import SurfaceStatus
from graph.validate import validate_graph
from osm.build import BuildOptions, attach_elevation, build_graph
from osm.parse import OverpassData

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_network.json"

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


def elev():
    return CallableElevation(lambda lat, lon: 700.0 + 5000.0 * lat, resolution_m=30.0)


@pytest.fixture
def graph():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    built, _ = build_graph(OverpassData.from_payload(payload), BuildOptions())
    attach_elevation(built, elev(), spacing_m=20.0)
    return built


@pytest.fixture
def multi_segment_graph():
    built, _ = build_graph(OverpassData.from_payload(CHAIN), BuildOptions())
    attach_elevation(built, elev(), spacing_m=20.0)
    simplified, _ = simplify_graph(built)
    return simplified


def test_json_roundtrip(graph, tmp_path):
    path = save_json(graph, tmp_path / "graph.json")
    loaded = load_json(path)

    assert loaded.node_count == graph.node_count
    assert loaded.arc_count == graph.arc_count

    original = graph.get_arc("w103:0")
    restored = loaded.get_arc("w103:0")
    assert restored.surface is original.surface
    assert restored.surface_status is original.surface_status
    assert restored.distance_m == pytest.approx(original.distance_m)
    assert restored.elevation_gain_m == pytest.approx(original.elevation_gain_m)
    assert len(restored.elevation_profile) == len(original.elevation_profile)
    assert restored.geometry == original.geometry
    assert restored.elevation_source == "callable"
    assert restored.elevation_resolution_m == 30.0

    assert graph_from_dict(graph_to_dict(graph)).arc_count == graph.arc_count


def test_json_roundtrip_preserves_segments(multi_segment_graph, tmp_path):
    path = save_json(multi_segment_graph, tmp_path / "chain.json")
    loaded = load_json(path)

    assert loaded.node_count == 2
    assert loaded.arc_count == 2

    forward = next(arc for arc in loaded.arcs.values() if arc.source == 1)
    assert forward.segment_count == 3
    assert [segment.id for segment in forward.segments] == [
        "w201:0",
        "w202:0",
        "w203:0",
    ]
    assert forward.surfaces == {segment.surface for segment in forward.segments}
    assert forward.elevation_resolution_m == 30.0
    assert {segment.surface_status for segment in forward.segments} == {
        SurfaceStatus.MAPPED
    }


def test_geojson(graph, tmp_path):
    saved = save_geojson(graph, tmp_path / "graph.geojson")
    payload = json.loads(saved.read_text(encoding="utf-8"))

    assert payload["type"] == "FeatureCollection"
    line_features = [
        feature
        for feature in payload["features"]
        if feature["geometry"]["type"] == "LineString"
    ]
    point_features = [
        feature for feature in payload["features"] if feature["geometry"]["type"] == "Point"
    ]
    assert len(line_features) == graph.arc_count
    assert len(point_features) == graph.node_count

    properties = line_features[0]["properties"]
    assert "surface_status" in properties
    assert "segment_count" in properties

    first = line_features[0]["geometry"]["coordinates"][0]
    assert -180 <= first[0] <= 180 and -90 <= first[1] <= 90


def test_summary(graph):
    summary = summarize(graph)

    assert summary["nodes"] == 6
    assert summary["arcs"] == 15
    assert summary["segments"] == 15
    assert summary["total_length_km"] > 0
    assert summary["elevation_coverage"] == 1.0
    assert summary["total_ascent_m"] > 0
    assert summary["elevation_sources"] == {"callable": 15}
    assert summary["length_by_surface_status_m"]["mapped"] > 0
    assert set(summary["length_by_surface_status_m"]) == {"mapped"}
    assert sum(summary["length_by_surface_m"].values()) == pytest.approx(
        summary["total_length_m"], rel=1e-3
    )


def test_to_geojson_is_serializable(graph):
    payload = to_geojson(graph)
    json.dumps(payload)


def test_validate_clean_graph(graph):
    issues = validate_graph(graph)
    errors = [issue for issue in issues if issue.severity == "error"]
    assert errors == []


def test_validate_detects_broken_graph(graph):
    from graph.model import Node

    graph.add_node(Node(id=999, lat=0.0, lon=0.0))
    issues = validate_graph(graph)
    codes = {issue.code for issue in issues}
    assert "isolated-node" in codes


def test_cli_stats(graph, tmp_path, capsys):
    path = save_json(graph, tmp_path / "graph.json")
    exit_code = main(["stats", str(path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "arcos: 15  segmentos: 15" in output
    assert "superficie:" in output
    assert "proveniencia da superficie:" in output
