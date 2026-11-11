"""Caso de uso: ingestao OSM e montagem do grafo (ADR-033, E6)."""

from __future__ import annotations

from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import DEFAULT_HIGHWAYS, OsmData
from router.core.data.osm.pbf import read_osm_pbf

__all__ = [
    "BuildOptions",
    "DEFAULT_HIGHWAYS",
    "OsmData",
    "attach_elevation",
    "build_graph",
    "read_osm_pbf",
]
