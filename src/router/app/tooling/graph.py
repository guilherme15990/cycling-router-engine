"""Caso de uso: grafo salvo, geometria e snapping (ADR-033, E6)."""

from __future__ import annotations

from router.contracts import dumps
from router.core.data.geo import bbox_size_km, parse_bbox, parse_coord
from router.core.data.graph.model import Arc
from router.core.data.graph.serialize import (
    load_json,
    save_geojson,
    save_json,
    summarize,
)
from router.core.data.graph.simplify import simplify_graph
from router.core.data.graph.validate import validate_graph
from router.core.data.snapping import SnapError, SnapOptions, build_query_graph

__all__ = [
    "Arc",
    "SnapError",
    "SnapOptions",
    "bbox_size_km",
    "build_query_graph",
    "dumps",
    "load_json",
    "parse_bbox",
    "parse_coord",
    "save_geojson",
    "save_json",
    "simplify_graph",
    "summarize",
    "validate_graph",
]
