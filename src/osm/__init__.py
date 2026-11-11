"""Ingestao de dados do OpenStreetMap a partir de arquivos **locais** (``.osm.pbf``).

O leitor PBF (``osm.pbf``) opera **offline** — e a fonte geografica local
exigida pelo ADR-027 §0 (lacuna O1). Nao ha ingestao online: o motor consome
arquivos ``.osm.pbf`` obtidos previamente (fora do caminho de execucao).
"""

from .build import (
    BuildOptions,
    BuildStats,
    attach_elevation,
    build_graph,
    allowed_directions,
    bicycle_access,
)
from .parse import (
    DEFAULT_HIGHWAYS,
    OsmData,
    RawNode,
    RawRelation,
    RawWay,
)
from .pbf import PbfError, PbfHeader, PbfStats, read_header, read_osm_pbf

__all__ = [
    "BuildOptions",
    "BuildStats",
    "DEFAULT_HIGHWAYS",
    "OsmData",
    "PbfError",
    "PbfHeader",
    "PbfStats",
    "RawNode",
    "RawRelation",
    "RawWay",
    "allowed_directions",
    "attach_elevation",
    "bicycle_access",
    "build_graph",
    "read_header",
    "read_osm_pbf",
]
