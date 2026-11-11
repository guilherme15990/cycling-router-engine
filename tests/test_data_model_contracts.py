"""Contratos da Camada 01 (Data Model) — ADR-024.

Cobre os tres fechamentos: separacao Segment/Link/Arc com geometria derivada;
contratos de restricao (AccessRule/TurnRestriction/RestrictionSource); e
qualidade/proveniencia de elevacao.
"""

import pytest
from router.core.data.elevation import CallableElevation, OsmTagElevation
from router.core.data.graph.model import (
    Arc,
    ElevationQuality,
    ElevationSample,
    GraphError,
    Link,
    Node,
    RoutingGraph,
    Segment,
    concat_geometry,
)
from router.core.data.graph.restrictions import (
    AccessRule,
    RestrictionSource,
    TurnRestriction,
    turn_restriction_from_relation,
)
from router.core.data.graph.serialize import graph_from_dict, graph_to_dict
from router.core.data.graph.simplify import simplify_graph
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics.physical.arc_profile import ArcProfile
from router.core.data.snapping import SpatialSnapper

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _segment(segment_id="s0", distance_m=100.0, geometry=((0.0, 0.0), (0.0, 0.001))):
    return Segment(id=segment_id, distance_m=distance_m, geometry=geometry)


def _graph():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    graph.add_node(Node(id=3, lat=0.0, lon=0.002))
    graph.add_arc(Arc(id="e0", source=1, target=2, segments=(_segment("s0"),)))
    graph.add_arc(Arc(id="e1", source=2, target=3, segments=(_segment("s1"),)))
    return graph


# --------------------------------------------------------------------------
# Decisao 3 — qualidade e proveniencia da elevacao
# --------------------------------------------------------------------------
def test_elevation_quality_default_inference():
    sample = ElevationSample(distance_m=0.0, elevation_m=12.5)
    assert sample.quality is ElevationQuality.DEM_SAMPLED

    missing = ElevationSample(distance_m=0.0, elevation_m=None)
    assert missing.quality is ElevationQuality.MISSING
    assert missing.is_missing


def test_elevation_sample_source_id_is_preserved():
    sample = ElevationSample(distance_m=0.0, elevation_m=1.0, source_id="srtm")
    assert sample.source_id == "srtm"
    assert ElevationSample(distance_m=0.0, elevation_m=None).source_id is None


def test_missing_requires_none_elevation():
    with pytest.raises(GraphError):
        ElevationSample(
            distance_m=0.0, elevation_m=0.0, quality=ElevationQuality.MISSING
        )


def test_non_missing_requires_numeric_elevation():
    with pytest.raises(GraphError):
        ElevationSample(
            distance_m=0.0, elevation_m=None, quality=ElevationQuality.DEM_SAMPLED
        )


def test_invalid_distance_and_resolution_rejected():
    with pytest.raises(GraphError):
        ElevationSample(distance_m=-1.0, elevation_m=1.0)
    with pytest.raises(GraphError):
        ElevationSample(distance_m=0.0, elevation_m=1.0, resolution_m=0.0)


def test_explicit_quality_is_preserved():
    sample = ElevationSample(
        distance_m=0.0, elevation_m=3.0, quality=ElevationQuality.INTERPOLATED
    )
    assert sample.quality is ElevationQuality.INTERPOLATED


def test_arc_profile_coverage():
    with_elevation = Segment(
        id="s0",
        distance_m=100.0,
        elevation_gain_m=10.0,
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=0.0),
            ElevationSample(distance_m=100.0, elevation_m=10.0),
        ),
    )
    plain = Segment(id="s1", distance_m=100.0)
    arc = Arc(id="e0", source=1, target=2, segments=(with_elevation, plain))
    profile = ArcProfile.from_arc(arc)
    assert profile.unknown_elevation_m == pytest.approx(100.0)
    assert profile.coverage_available == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Decisao 2 — restricoes
# --------------------------------------------------------------------------
def test_access_rule_requires_scope_and_value():
    rule = AccessRule(scope="bicycle", value="no", condition=None, raw="no")
    assert rule.scope == "bicycle"
    assert rule.value == "no"
    assert not rule.is_conditional


def test_turn_restriction_requires_via():
    with pytest.raises(ValueError):
        TurnRestriction(id="r1", from_way_id=1, to_way_id=2, restriction_type="no_left_turn")


def test_turn_restriction_requires_type():
    with pytest.raises(ValueError):
        TurnRestriction(id="r1", from_way_id=1, to_way_id=2, restriction_type="", via_node_id=5)


def test_turn_restriction_from_relation():
    relation = {
        "type": "relation",
        "id": 987,
        "tags": {"type": "restriction", "restriction": "no_left_turn"},
        "members": [
            {"type": "way", "ref": 10, "role": "from"},
            {"type": "node", "ref": 55, "role": "via"},
            {"type": "way", "ref": 20, "role": "to"},
        ],
    }
    restriction = turn_restriction_from_relation(relation, version="2026-11")
    assert restriction is not None
    assert restriction.id == "r987"
    assert restriction.from_way_id == 10
    assert restriction.to_way_id == 20
    assert restriction.via_node_id == 55
    assert restriction.restriction_type == "no_left_turn"
    assert restriction.source is not None
    assert restriction.source.relation_id == 987
    assert restriction.source.version == "2026-11"


def test_turn_restriction_from_relation_ignores_non_restriction():
    assert turn_restriction_from_relation({"tags": {"type": "route"}}) is None


def test_graph_turn_restriction_registry():
    graph = _graph()
    restriction = TurnRestriction(
        id="r1",
        from_way_id=10,
        to_way_id=20,
        restriction_type="no_u_turn",
        via_node_id=2,
    )
    graph.add_turn_restriction(restriction)
    assert graph.turn_restrictions["r1"] is restriction
    assert graph.get_turn_restriction("r1") is restriction
    assert graph.turn_restrictions_at(2) == [restriction]

    with pytest.raises(GraphError):
        graph.add_turn_restriction(restriction)

    clone = graph.copy()
    assert clone.turn_restrictions["r1"] is restriction


# --------------------------------------------------------------------------
# Decisao 1 — geometria derivada (Segment e a fonte de verdade)
# --------------------------------------------------------------------------
def test_arc_geometry_is_derived_from_segments():
    first = _segment("s0", geometry=((0.0, 0.0), (0.0, 0.001)))
    second = _segment("s1", geometry=((0.0, 0.001), (0.0, 0.002)))
    arc = Arc(id="e", source=1, target=2, segments=(first, second))
    assert arc.geometry == ((0.0, 0.0), (0.0, 0.001), (0.0, 0.002))
    assert arc.geometry == concat_geometry(
        segment.geometry for segment in arc.segments
    )


def test_arc_geometry_not_persisted():
    graph = _graph()
    payload = graph_to_dict(graph)
    for raw in payload["arcs"]:
        assert "geometry" not in raw


def test_graph_arc_geometry_cache_invalidates_on_replace():
    graph = _graph()
    first = graph.arc_geometry("e0")
    assert first == graph.get_arc("e0").geometry

    new_segment = _segment("s0", geometry=((0.0, 0.0), (0.0, 0.002)))
    graph.replace_arc(
        Arc(id="e0", source=1, target=2, segments=(new_segment,))
    )
    assert graph.arc_geometry("e0") == ((0.0, 0.0), (0.0, 0.002))


def test_link_geometry_derived():
    link = Link(
        id="l0",
        source=1,
        target=2,
        segments=(
            _segment("s0", geometry=((0.0, 0.0), (0.0, 0.001))),
            _segment("s1", geometry=((0.0, 0.001), (0.0, 0.002))),
        ),
    )
    assert len(link.geometry) == 3


# --------------------------------------------------------------------------
# Serializacao (v4) + compatibilidade v3
# --------------------------------------------------------------------------
def test_serialization_roundtrips_quality_and_source():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    sample_missing = ElevationSample(distance_m=0.0, elevation_m=None)
    sample_dem = ElevationSample(
        distance_m=50.0,
        elevation_m=10.0,
        source_id="dem-x",
        resolution_m=30.0,
        quality=ElevationQuality.DEM_SAMPLED,
    )
    segment = Segment(
        id="s0",
        distance_m=100.0,
        geometry=((0.0, 0.0), (0.0, 0.001)),
        elevation_profile=(sample_missing, sample_dem),
    )
    graph.add_arc(Arc(id="e0", source=1, target=2, segments=(segment,)))

    loaded = graph_from_dict(graph_to_dict(graph))
    restored = loaded.get_arc("e0").segments[0].elevation_profile
    assert restored[0].quality is ElevationQuality.MISSING
    assert restored[0].elevation_m is None
    assert restored[1].quality is ElevationQuality.DEM_SAMPLED
    assert restored[1].source_id == "dem-x"
    assert restored[1].resolution_m == 30.0


def test_serialization_roundtrips_turn_restriction():
    graph = _graph()
    graph.add_turn_restriction(
        TurnRestriction(
            id="r1",
            from_way_id=10,
            to_way_id=20,
            restriction_type="only_straight_on",
            via_way_ids=(30, 31),
            condition="(Mo-Fr)",
            source=RestrictionSource(relation_id=987, tags={"type": "restriction"}),
        )
    )
    loaded = graph_from_dict(graph_to_dict(graph))
    restored = loaded.get_turn_restriction("r1")
    assert restored.via_way_ids == (30, 31)
    assert restored.condition == "(Mo-Fr)"
    assert restored.source is not None
    assert restored.source.relation_id == 987


def test_rejects_legacy_v3_payload():
    graph = _graph()
    payload = graph_to_dict(graph)
    payload["version"] = 3
    with pytest.raises(ValueError):
        graph_from_dict(payload)


def test_rejects_unknown_version():
    payload = graph_to_dict(_graph())
    payload["version"] = 99
    with pytest.raises(ValueError):
        graph_from_dict(payload)


# ==========================================================================
# Rodada de endurecimento (auditoria ADR-024): cache, orientacao, proveniencia
# ==========================================================================

_CHAIN = {
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


def _chain_graph(simplify=False, elevation=False):
    graph, _ = build_graph(OsmData.from_payload(_CHAIN), BuildOptions())
    if elevation:
        attach_elevation(
            graph,
            CallableElevation(lambda lat, lon: 700.0 + 5000.0 * lat, resolution_m=30.0),
            spacing_m=20.0,
        )
    if simplify:
        graph, _ = simplify_graph(graph)
    return graph


def _graph_with_elevation():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    segment = Segment(
        id="s0",
        distance_m=100.0,
        geometry=((0.0, 0.0), (0.0, 0.001)),
        elevation_gain_m=12.0,
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=None),
            ElevationSample(
                distance_m=50.0,
                elevation_m=10.0,
                source_id="dem-x",
                resolution_m=30.0,
                quality=ElevationQuality.DEM_SAMPLED,
            ),
            ElevationSample(
                distance_m=100.0,
                elevation_m=12.0,
                quality=ElevationQuality.INTERPOLATED,
            ),
        ),
        restrictions=(
            AccessRule(scope="bicycle", value="no", condition=None, raw="no"),
        ),
    )
    graph.add_arc(Arc(id="e0", source=1, target=2, segments=(segment,)))
    graph.add_turn_restriction(
        TurnRestriction(
            id="r1",
            from_way_id=1,
            to_way_id=2,
            restriction_type="no_left_turn",
            via_node_id=2,
            source=RestrictionSource(relation_id=5, version="v1", tags={"type": "restriction"}),
        )
    )
    return graph


def _reverse_pairs_checked(graph):
    pairs = {}
    for arc in graph.arcs.values():
        pairs.setdefault((arc.source, arc.target), []).append(arc)
    checked = 0
    for (source, target), arcs in pairs.items():
        if not isinstance(source, int) or not isinstance(target, int) or source >= target:
            continue
        back = pairs.get((target, source))
        if not back:
            continue
        assert back[0].geometry == tuple(reversed(arcs[0].geometry))
        checked += 1
    return checked


# --- Decisao 1: desduplicacao so de pontos consecutivos -------------------
def test_concat_geometry_dedupes_only_consecutive_points():
    loop = ((0.0, 0.0), (0.0, 0.001), (0.0, 0.0))
    following = ((0.0, 0.0), (0.0, -0.001))
    merged = concat_geometry([loop, following])
    # O retorno nao-consecutivo ao mesmo ponto e preservado (laco).
    assert merged == (
        (0.0, 0.0),
        (0.0, 0.001),
        (0.0, 0.0),
        (0.0, -0.001),
    )


# --- Decisao 1: orientacao da geometria reversa ---------------------------
def test_reversed_arc_geometry_is_reversed():
    arc = Arc(
        id="e",
        source=1,
        target=2,
        segments=(
            _segment("s0", geometry=((0.0, 0.0), (0.0, 0.001))),
            _segment("s1", geometry=((0.0, 0.001), (0.0, 0.002))),
        ),
    )
    reverse = arc.reversed_arc()
    assert reverse.geometry == tuple(reversed(arc.geometry))
    assert reverse.geometry == ((0.0, 0.002), (0.0, 0.001), (0.0, 0.0))


def test_reversed_geometry_after_build_and_simplify():
    graph = _chain_graph(elevation=True, simplify=True)
    assert _reverse_pairs_checked(graph) >= 1


def test_reversed_geometry_after_snapping():
    graph = _chain_graph(elevation=True)
    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")
    query = result.apply(graph)
    assert _reverse_pairs_checked(query) >= 1


# --- Decisao 1: cache so invalida quando a dependencia muda ---------------
def test_cache_refreshes_even_without_explicit_invalidation():
    graph = _graph()
    assert graph.arc_geometry("e0") == ((0.0, 0.0), (0.0, 0.001))

    # Mutacao direta (bypassa as operacoes controladas): a identidade dos
    # segmentos muda, entao o cache precisa renovar sozinho.
    new_segment = _segment("s0", geometry=((0.0, 0.0), (0.0, 0.005)))
    graph._arcs["e0"] = Arc(id="e0", source=1, target=2, segments=(new_segment,))
    assert graph.arc_geometry("e0") == ((0.0, 0.0), (0.0, 0.005))


def test_cache_updates_on_remove_and_reinsert():
    graph = _graph()
    graph.arc_geometry("e1")
    graph.remove_arc("e1")
    with pytest.raises(GraphError):
        graph.arc_geometry("e1")


# --- Decisao 1: copia e snapping nao mexem no original --------------------
def test_copy_preserves_restrictions_segments_and_geometry():
    graph = _graph_with_elevation()
    clone = graph.copy()
    assert set(clone.turn_restrictions) == set(graph.turn_restrictions)
    for arc_id in graph.arcs:
        assert clone.get_arc(arc_id).segments == graph.get_arc(arc_id).segments
        assert clone.arc_geometry(arc_id) == graph.arc_geometry(arc_id)


def test_snapping_does_not_mutate_original_graph():
    graph = _chain_graph(elevation=True)
    before_arcs = dict(graph.arcs)
    before_geometry = {arc_id: graph.arc_geometry(arc_id) for arc_id in before_arcs}

    result = SpatialSnapper(graph).snap((0.0015, 0.0001), label="origin")
    query = result.apply(graph)

    assert set(graph.arcs) == set(before_arcs)
    for arc_id, arc in before_arcs.items():
        assert graph.get_arc(arc_id) is arc
        assert graph.arc_geometry(arc_id) == before_geometry[arc_id]
    assert set(query.arcs) != set(before_arcs)


# --- Decisao 1: simplificacao preserva restricoes -------------------------
def test_simplify_preserves_turn_restrictions():
    graph = _chain_graph()
    graph.add_turn_restriction(
        TurnRestriction(
            id="r1",
            from_way_id=201,
            to_way_id=202,
            restriction_type="no_left_turn",
            via_node_id=2,
        )
    )
    simplified, _ = simplify_graph(graph)
    assert simplified.get_turn_restriction("r1") is not None


# --- Decisao 2: ordem das vias intermediarias -----------------------------
def test_via_way_order_is_preserved():
    relation = {
        "type": "relation",
        "id": 1,
        "tags": {"type": "restriction", "restriction": "only_straight_on"},
        "members": [
            {"type": "way", "ref": 100, "role": "from"},
            {"type": "way", "ref": 310, "role": "via"},
            {"type": "way", "ref": 305, "role": "via"},
            {"type": "way", "ref": 200, "role": "to"},
        ],
    }
    restriction = turn_restriction_from_relation(relation)
    assert restriction is not None
    assert restriction.via_way_ids == (310, 305)

    loaded = graph_from_dict(graph_to_dict(_with_restriction(restriction)))
    assert loaded.get_turn_restriction(restriction.id).via_way_ids == (310, 305)


def _with_restriction(restriction):
    graph = _graph()
    graph.add_turn_restriction(restriction)
    return graph


# --- Decisao 3: ele=* nao e MEASURED --------------------------------------
def test_osm_ele_quality_is_source_reported():
    assert OsmTagElevation.quality is ElevationQuality.SOURCE_REPORTED
    assert OsmTagElevation.quality is not ElevationQuality.MEASURED


def test_is_observed_membership():
    assert ElevationSample(
        distance_m=0.0, elevation_m=1.0, quality=ElevationQuality.DEM_SAMPLED
    ).is_observed
    assert ElevationSample(
        distance_m=0.0, elevation_m=1.0, quality=ElevationQuality.SOURCE_REPORTED
    ).is_observed
    assert not ElevationSample(
        distance_m=0.0, elevation_m=1.0, quality=ElevationQuality.INTERPOLATED
    ).is_observed
    assert not ElevationSample(
        distance_m=0.0, elevation_m=1.0, quality=ElevationQuality.ESTIMATED
    ).is_observed


# --- Decisao 3: cobertura disponivel x observada --------------------------
def test_coverage_available_vs_observed():
    observed = Segment(
        id="s0",
        distance_m=100.0,
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=0.0, quality=ElevationQuality.DEM_SAMPLED),
            ElevationSample(distance_m=100.0, elevation_m=5.0, quality=ElevationQuality.DEM_SAMPLED),
        ),
    )
    estimated = Segment(
        id="s1",
        distance_m=100.0,
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=0.0, quality=ElevationQuality.ESTIMATED),
            ElevationSample(distance_m=100.0, elevation_m=5.0, quality=ElevationQuality.ESTIMATED),
        ),
    )
    profile = ArcProfile.from_arc(
        Arc(id="e0", source=1, target=2, segments=(observed, estimated))
    )
    assert profile.coverage_available == pytest.approx(1.0)
    assert profile.coverage_observed == pytest.approx(0.5)
    assert profile.to_dict()["coverage_observed"] == pytest.approx(0.5)


def test_interpolated_only_profile_has_zero_observed_coverage():
    segment = Segment(
        id="s0",
        distance_m=100.0,
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=0.0, quality=ElevationQuality.INTERPOLATED),
            ElevationSample(distance_m=100.0, elevation_m=5.0, quality=ElevationQuality.INTERPOLATED),
        ),
    )
    profile = ArcProfile.from_arc(Arc(id="e0", source=1, target=2, segments=(segment,)))
    assert profile.coverage_available == pytest.approx(1.0)
    assert profile.coverage_observed == 0.0


# --- Serializacao: v4 -> obj -> v4 e migracao v3 --------------------------
def test_v4_roundtrip_is_semantically_stable():
    graph = _graph_with_elevation()
    payload = graph_to_dict(graph)
    reloaded = graph_from_dict(payload)
    assert graph_to_dict(reloaded) == payload


def test_missing_elevation_stays_missing_across_roundtrip():
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    segment = Segment(
        id="s0",
        distance_m=100.0,
        geometry=((0.0, 0.0), (0.0, 0.001)),
        elevation_profile=(
            ElevationSample(distance_m=0.0, elevation_m=None),
            ElevationSample(distance_m=100.0, elevation_m=None),
        ),
    )
    graph.add_arc(Arc(id="e0", source=1, target=2, segments=(segment,)))

    loaded = graph_from_dict(graph_to_dict(graph))
    restored = loaded.get_arc("e0").segments[0]
    assert all(sample.elevation_m is None for sample in restored.elevation_profile)
    assert all(
        sample.quality is ElevationQuality.MISSING
        for sample in restored.elevation_profile
    )
    assert not restored.has_elevation

