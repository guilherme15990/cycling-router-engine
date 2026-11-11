"""Testes do leitor PBF (``osm.pbf``) com um codificador minimo proprio.

O codificador PBF minimo (varints, campos length-delimited,
``BlobHeader``/``Blob`` com zlib, ``StringTable``, ``DenseNodes`` e ``Way``)
vive em ``tests/_pbf_fixtures.py`` para ser reutilizado pela linha de comando
offline. Os testes aqui sao totalmente hermeticos: sem depender do arquivo real
nem da rede.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from _pbf_fixtures import _PbfBuilder, _deg, _fbytes, _fvarint
from router.core.data.osm.build import build_graph
from router.core.data.osm.parse import DEFAULT_HIGHWAYS, OsmData
from router.core.data.osm.pbf import PbfError, PbfStats, read_header, read_osm_pbf


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def test_read_header_bbox_and_program(tmp_path: Path) -> None:
    bbox = (-18.346, -51.73, -17.094, -50.359)
    path = (
        _PbfBuilder(tmp_path)
        .header(bbox=bbox, writing_program="test-suite", source="unit-test")
        .data()
        .write()
    )

    header = read_header(path)
    assert header.bbox == pytest.approx(bbox)
    assert header.writing_program == "test-suite"
    assert header.source == "unit-test"
    assert header.bbox is not None
    assert header.bbox[0] < header.bbox[2]  # sul < norte
    assert header.bbox[1] < header.bbox[3]  # oeste < leste


def test_read_header_without_bbox_returns_none(tmp_path: Path) -> None:
    path = _PbfBuilder(tmp_path).header(writing_program="x").data().write()
    header = read_header(path)
    assert header.bbox is None
    assert header.writing_program == "x"
    assert header.source is None


def test_read_header_declares_features(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header(required=("OsmSchema-V0.6",), optional=("DenseNodes",))
        .data()
        .write()
    )
    header = read_header(path)
    assert header.required_features == ("OsmSchema-V0.6",)
    assert header.optional_features == ("DenseNodes",)


def test_read_header_without_header_blob_raises(tmp_path: Path) -> None:
    path = _PbfBuilder(tmp_path).data(dense=[(1, 0, 0, {})]).write()
    with pytest.raises(PbfError):
        read_header(path)


# ---------------------------------------------------------------------------
# Nos densos (round-trip)
# ---------------------------------------------------------------------------
def test_roundtrip_dense_nodes_coords_and_tags(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header(bbox=(0.0, 0.0, 1.0, 1.0), writing_program="enc")
        .data(
            dense=[
                (1, 10000, 20000, {"ele": "850"}),
                (2, 35, 40, {}),
                (3, -120, 55, {"name": "Pico"}),
            ],
            ways=[(100, (1, 2, 3), {"highway": "residential", "name": "Rua A"})],
        )
        .write()
    )

    data = read_osm_pbf(path)

    assert isinstance(data, OsmData)
    assert set(data.nodes) == {1, 2, 3}
    assert data.nodes[1].lat == pytest.approx(_deg(10000))
    assert data.nodes[1].lon == pytest.approx(_deg(20000))
    assert data.nodes[1].tags == {"ele": "850"}
    assert data.nodes[2].tags == {}
    assert data.nodes[3].lat == pytest.approx(_deg(-120))
    assert data.nodes[3].tags == {"name": "Pico"}


def test_way_preserves_id_refs_and_order(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {}), (4, 0, 0, {})],
            ways=[(42, (3, 1, 4, 2), {"highway": "track", "surface": "gravel"})],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert len(data.ways) == 1
    way = data.ways[0]
    assert way.id == 42
    assert way.nodes == (3, 1, 4, 2)
    assert way.tags == {"highway": "track", "surface": "gravel"}


# ---------------------------------------------------------------------------
# Relacoes de restricao de conversao (``type=restriction``)
# ---------------------------------------------------------------------------
def test_reads_restriction_relation_members_and_order(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (2, 3), {"highway": "residential"}),
            ],
            relations=[
                (
                    500,
                    [(1, 10, "from"), (0, 2, "via"), (1, 11, "to")],
                    {"type": "restriction", "restriction": "no_left_turn"},
                )
            ],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert len(data.relations) == 1
    relation = data.relations[0]
    assert relation.id == 500
    assert relation.tags == {"type": "restriction", "restriction": "no_left_turn"}
    assert relation.members == (
        {"type": "way", "ref": 10, "role": "from"},
        {"type": "node", "ref": 2, "role": "via"},
        {"type": "way", "ref": 11, "role": "to"},
    )


def test_non_restriction_relation_is_ignored(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {})],
            ways=[(10, (1, 2), {"highway": "residential"})],
            relations=[
                (600, [(1, 10, "")], {"type": "route", "route": "bicycle"})
            ],
        )
        .write()
    )

    data, stats = read_osm_pbf(path, with_stats=True)
    assert data.relations == []
    assert stats.relations_seen == 1


def test_build_graph_attaches_turn_restriction_from_relation(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 19000, 0, {}), (2, 18900, 10, {}), (3, 18800, 20, {})],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (2, 3), {"highway": "residential"}),
            ],
            relations=[
                (
                    500,
                    [(1, 10, "from"), (0, 2, "via"), (1, 11, "to")],
                    {"type": "restriction", "restriction": "no_left_turn"},
                )
            ],
        )
        .write()
    )

    graph, stats = build_graph(read_osm_pbf(path))

    assert stats.turn_restrictions == 1
    assert list(graph.turn_restrictions) == ["r500"]
    restriction = graph.get_turn_restriction("r500")
    assert restriction.from_way_id == 10
    assert restriction.to_way_id == 11
    assert restriction.via_node_id == 2
    assert restriction.restriction_type == "no_left_turn"


def test_coordinates_respect_granularity_and_offset(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(7, 10, 20, {})],
            ways=[(1, (7, 7), {"highway": "path"})],
            granularity=1000,
            lat_offset=1_000_000,
            lon_offset=-2_000_000,
        )
        .write()
    )
    data = read_osm_pbf(path)
    assert data.nodes[7].lat == pytest.approx(_deg(10, granularity=1000, offset=1_000_000))
    assert data.nodes[7].lon == pytest.approx(_deg(20, granularity=1000, offset=-2_000_000))


# ---------------------------------------------------------------------------
# Filtragem por highway / area e nos referenciados
# ---------------------------------------------------------------------------
def test_default_highways_filter(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (2, 3), {"highway": "motorway"}),  # fora do padrao
                (12, (1, 3), {}),  # sem highway
            ],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert [way.id for way in data.ways] == [10]
    assert "residential" in DEFAULT_HIGHWAYS
    assert set(data.nodes) == {1, 2}


def test_custom_highways_filter(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (2, 3), {"highway": "track"}),
            ],
        )
        .write()
    )

    data = read_osm_pbf(path, highways=("track",))
    assert [way.id for way in data.ways] == [11]
    assert set(data.nodes) == {2, 3}


def test_only_referenced_nodes_are_kept(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[
                (1, 0, 0, {}),
                (2, 0, 0, {}),
                (3, 0, 0, {}),  # pertence apenas a via descartada
                (4, 0, 0, {}),  # orfao, nunca referenciado
            ],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (3, 3), {"highway": "motorway"}),
            ],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert set(data.nodes) == {1, 2}


def test_area_yes_way_is_excluded(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[
                (10, (1, 2), {"highway": "pedestrian", "area": "yes"}),
                (11, (1, 3), {"highway": "pedestrian"}),
            ],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert [way.id for way in data.ways] == [11]
    assert set(data.nodes) == {1, 3}


def test_empty_selection_returns_empty_data(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(dense=[(1, 0, 0, {})], ways=[(10, (1, 1), {"highway": "motorway"})])
        .write()
    )
    data = read_osm_pbf(path)
    assert data.ways == []
    assert data.nodes == {}


# ---------------------------------------------------------------------------
# Nos nao densos (formato ``Node``)
# ---------------------------------------------------------------------------
def test_plain_nodes_are_decoded(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            plain=[
                (1, 10000, 20000, {"ele": "900"}),
                (2, 300, 400, {}),
            ],
            ways=[(10, (1, 2), {"highway": "unclassified"})],
        )
        .write()
    )

    data = read_osm_pbf(path)
    assert set(data.nodes) == {1, 2}
    assert data.nodes[1].lat == pytest.approx(_deg(10000))
    assert data.nodes[1].lon == pytest.approx(_deg(20000))
    assert data.nodes[1].tags == {"ele": "900"}
    assert data.nodes[2].tags == {}


def test_unpacked_way_refs_are_decoded(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[(10, (1, 3, 2), {"highway": "service"}, False)],
        )
        .write()
    )
    data = read_osm_pbf(path)
    assert data.ways[0].nodes == (1, 3, 2)


# ---------------------------------------------------------------------------
# Blobs, grupos e estatisticas
# ---------------------------------------------------------------------------
def test_uncompressed_blob_is_read(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 500, 500, {"ele": "10"})],
            ways=[(10, (1, 1), {"highway": "path"})],
            compress=False,
        )
        .write()
    )
    data = read_osm_pbf(path)
    assert data.nodes[1].lat == pytest.approx(_deg(500))
    assert data.ways[0].id == 10


def test_multiple_blocks_and_groups_are_merged(tmp_path: Path) -> None:
    builder = _PbfBuilder(tmp_path).header()
    builder.data(
        dense=[(1, 0, 0, {})],
        ways=[(10, (1, 2), {"highway": "residential"})],
        separate_groups=True,
    )
    builder.data(
        dense=[(2, 100, 100, {"ele": "5"})],
        ways=[(11, (2, 1), {"highway": "track"})],
        separate_groups=True,
    )
    path = builder.write()

    data = read_osm_pbf(path)
    assert {way.id for way in data.ways} == {10, 11}
    # a via 10 referencia o no 2, que so aparece no segundo bloco
    assert set(data.nodes) == {1, 2}
    assert data.nodes[2].tags == {"ele": "5"}


def test_with_stats_reports_counts(tmp_path: Path) -> None:
    path = (
        _PbfBuilder(tmp_path)
        .header()
        .data(
            dense=[(1, 0, 0, {}), (2, 0, 0, {}), (3, 0, 0, {})],
            ways=[
                (10, (1, 2), {"highway": "residential"}),
                (11, (2, 3), {"highway": "motorway"}),
            ],
        )
        .write()
    )

    data, stats = read_osm_pbf(path, with_stats=True)
    assert isinstance(stats, PbfStats)
    assert stats.ways_seen == 2
    assert stats.ways_selected == 1
    assert stats.nodes_seen == 3
    assert stats.nodes_selected == 2
    assert stats.blobs == 1


# ---------------------------------------------------------------------------
# Erros de formato
# ---------------------------------------------------------------------------
def test_truncated_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "truncated.osm.pbf"
    path.write_bytes(b"\x00\x00")
    with pytest.raises(PbfError):
        read_header(path)


def test_blob_without_payload_raises(tmp_path: Path) -> None:
    # Blob vazio (sem raw/zlib/zstd): deve falhar com PbfError.
    blob = b""
    header = _fbytes(1, b"OSMHeader") + _fvarint(3, len(blob))
    path = tmp_path / "bad.osm.pbf"
    path.write_bytes(struct.pack(">I", len(header)) + header + blob)
    with pytest.raises(PbfError):
        read_header(path)
