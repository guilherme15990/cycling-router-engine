"""Caso de uso: fontes de elevacao e tiles Terrarium (ADR-033, E6)."""

from __future__ import annotations

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

__all__ = [
    "ChainElevation",
    "OpenTopoDataElevation",
    "OsmTagElevation",
    "RasterDemElevation",
    "SqliteElevationCache",
    "TerrariumElevation",
    "TerrariumTileError",
    "fetch_terrarium_tiles",
]
