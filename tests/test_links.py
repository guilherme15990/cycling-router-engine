"""Testes da entidade Link (ligacao fisica nao-direcionada entre dois nos)."""

import json
from pathlib import Path

import pytest

from graph.model import Link, RoutingGraph
from graph.serialize import graph_from_dict, graph_to_dict
from osm.build import BuildOptions, build_graph
from osm.parse import OverpassData

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_network.json"


@pytest.fixture
def graph():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    built, _ = build_graph(OverpassData.from_payload(payload), BuildOptions())
    return built


def test_every_arc_belongs_to_a_link(graph):
    for arc in graph.arcs.values():
        link = graph.link_of(arc.id)
        assert isinstance(link, Link)
        assert {link.source, link.target} == {arc.source, arc.target}
        assert arc.id in link.arc_ids


def test_link_count_and_arc_partition(graph):
    # 15 arcos: 1 via de mao unica + 7 vias de mao dupla -> 8 links fisicos.
    assert graph.link_count == 8
    assert sum(len(link.arc_ids) for link in graph.links.values()) == graph.arc_count


def test_bidirectional_link_groups_both_arcs(graph):
    link = graph.link_of("w103:0")

    assert link is not None
    assert link.is_bidirectional
    assert set(link.arc_ids) == {"w103:0", "w103:0:rev"}
    assert graph.link_of("w103:0:rev") is link
    assert link.segments == graph.get_arc("w103:0").segments

    reverse_source = graph.get_arc("w103:0:rev").source
    assert {link.source, link.target} == {graph.get_arc("w103:0").source, reverse_source}


def test_oneway_link_has_single_arc(graph):
    link = graph.link_of("w101:0")

    assert link is not None
    assert link.arc_ids == ("w101:0",)
    assert not link.is_bidirectional


def test_incident_links(graph):
    links = graph.incident_links(1)

    assert links
    assert all(link.source == 1 or link.target == 1 for link in links)
    assert graph.link_of("w101:0") in links


def test_remove_arc_updates_link(graph):
    graph.remove_arc("w103:0:rev")

    link = graph.link_of("w103:0")
    assert link is not None
    assert link.arc_ids == ("w103:0",)
    assert not link.is_bidirectional
    assert graph.link_of("w103:0:rev") is None


def test_copy_preserves_links(graph):
    clone = graph.copy()

    assert clone.link_count == graph.link_count
    assert clone.link_of("w103:0") is not None
    assert clone.link_of("w103:0:rev") is clone.link_of("w103:0")


def test_serialization_preserves_links(graph):
    loaded = graph_from_dict(graph_to_dict(graph))

    assert loaded.link_count == graph.link_count
    assert set(loaded.links) == set(graph.links)
    link = loaded.link_of("w103:0")
    assert link is not None
    assert set(link.arc_ids) == {"w103:0", "w103:0:rev"}


def test_link_section_absent_is_rebuilt(graph):
    payload = graph_to_dict(graph)
    payload.pop("links")

    loaded = graph_from_dict(payload)

    assert loaded.link_count == graph.link_count
    assert set(loaded.link_of("w103:0").arc_ids) == {"w103:0", "w103:0:rev"}


def test_add_bidirectional_returns_arcs_and_registers_link():
    graph = RoutingGraph()
    from graph.model import Arc, Node, Segment

    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.001, lon=0.0))
    forward, reverse = graph.add_bidirectional(
        Arc(
            id="a0",
            source=1,
            target=2,
            segments=(Segment(id="a0:s0", distance_m=100.0),),
        )
    )

    link = graph.get_link("a0")
    assert link.arc_ids == (forward.id, reverse.id)
    assert link.is_bidirectional
