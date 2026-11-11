"""Ingestao de dados do OpenStreetMap via Overpass **ou** OSM.PBF local.

O leitor PBF (``osm.pbf``) opera **offline** — e a fonte geografica local
exigida pelo ADR-027 §0 (lacuna O1). A ingestao via ``OverpassClient`` continua
sendo **preparacao** (rede), fora do caminho de execucao.
"""

from .build import (
    BuildOptions,
    BuildStats,
    attach_elevation,
    build_graph,
    allowed_directions,
    bicycle_access,
)
from .overpass import OverpassClient, OverpassError, build_query
from .parse import OverpassData, RawNode, RawWay
from .pbf import PbfError, PbfHeader, PbfStats, read_header, read_osm_pbf

__all__ = [
    "BuildOptions",
    "BuildStats",
    "OverpassClient",
    "OverpassData",
    "OverpassError",
    "PbfError",
    "PbfHeader",
    "PbfStats",
    "RawNode",
    "RawWay",
    "allowed_directions",
    "attach_elevation",
    "bicycle_access",
    "build_graph",
    "build_query",
    "read_header",
    "read_osm_pbf",
]
