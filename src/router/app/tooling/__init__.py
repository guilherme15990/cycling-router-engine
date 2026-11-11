"""Casos de uso de operacao consumidos pela CLI (ADR-031; ADR-033, E6).

Reexporta, **agrupados por caso de uso**, os pontos de entrada de baixo nivel
que a CLI usa: ingestao (``ingest``), grafo/geometria/snapping (``graph``),
elevacao (``elevation``), simulacao fisica (``simulation``), predicao
(``prediction``) e planejamento (``planning``). A CLI nunca importa
``router.core.*`` diretamente: importa este pacote.
"""

from __future__ import annotations

from router.app.tooling.elevation import (
    ChainElevation,
    OpenTopoDataElevation,
    OsmTagElevation,
    RasterDemElevation,
    SqliteElevationCache,
    TerrariumElevation,
    TerrariumTileError,
    fetch_terrarium_tiles,
)
from router.app.tooling.graph import (
    Arc,
    SnapError,
    SnapOptions,
    bbox_size_km,
    build_query_graph,
    dumps,
    load_json,
    parse_bbox,
    parse_coord,
    save_geojson,
    save_json,
    simplify_graph,
    summarize,
    validate_graph,
)
from router.app.tooling.ingest import (
    DEFAULT_HIGHWAYS,
    BuildOptions,
    OsmData,
    attach_elevation,
    build_graph,
    read_osm_pbf,
)
from router.app.tooling.planning import RoutePlanResult, plan_offline_routes
from router.app.tooling.prediction import (
    INTENSITY_BAND_NAMES,
    EffortPreference,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    load_predictor,
)
from router.app.tooling.simulation import (
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
    "RoutePlanResult",
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
