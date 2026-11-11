"""Ferramentas de operacao consumidas pela CLI (ADR-031).

Reexporta os pontos de entrada de baixo nivel que a CLI usa (ingestao,
serializacao, fisica e elevacao). A CLI nunca importa ``router.core.*``
diretamente.
"""

from __future__ import annotations

from router.app.planning import RoutePlan, plan_offline_routes
from router.contracts import dumps
from router.core.data.elevation import (
    ChainElevation,
    OpenTopoDataElevation,
    OsmTagElevation,
    RasterDemElevation,
    SqliteElevationCache,
    TerrariumElevation,
)
from router.core.data.elevation.tiles import (
    TerrariumTileError,
    fetch_terrarium_tiles,
)
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
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import DEFAULT_HIGHWAYS, OsmData
from router.core.data.osm.pbf import read_osm_pbf
from router.core.data.snapping import SnapError, SnapOptions, build_query_graph
from router.core.evaluation import EffortPreference
from router.core.physics.physical import (
    MIN_SPEED_MPS,
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    SimulationMode,
    SimulationState,
    SimulationStatus,
    TraversalResult,
)
from router.core.prediction.intelligence import (
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    load_predictor,
)
from router.core.prediction.intelligence.intensity import INTENSITY_BAND_NAMES

__all__ = [
    "Arc",
    "BicycleModel",
    "BuildOptions",
    "ChainElevation",
    "CyclistModel",
    "DEFAULT_HIGHWAYS",
    "EffortPreference",
    "Environment",
    "INTENSITY_BAND_NAMES",
    "MIN_SPEED_MPS",
    "ModelIncompatibleError",
    "ModelNotFoundError",
    "ModelStore",
    "OpenTopoDataElevation",
    "OsmData",
    "OsmTagElevation",
    "PhysicalTraversal",
    "RasterDemElevation",
    "RoutePlan",
    "SimulationMode",
    "SimulationState",
    "SimulationStatus",
    "SnapError",
    "SnapOptions",
    "SqliteElevationCache",
    "TerrariumElevation",
    "TerrariumTileError",
    "TraversalResult",
    "attach_elevation",
    "bbox_size_km",
    "build_graph",
    "build_query_graph",
    "dumps",
    "fetch_terrarium_tiles",
    "load_json",
    "load_predictor",
    "parse_bbox",
    "parse_coord",
    "plan_offline_routes",
    "read_osm_pbf",
    "save_geojson",
    "save_json",
    "simplify_graph",
    "summarize",
    "validate_graph",
]
