"""Elevacao: provedores, cache, leitura offline de tiles e perfil de arco."""

from .cache import MemoElevation, SqliteElevationCache
from .offline import TerrariumElevation
from .profile import (
    ElevationStats,
    build_elevation_profile,
    fill_missing,
    smooth_elevations,
)
from .providers import (
    DEFAULT_TIMEOUT_S,
    OPENTOPODATA_CHUNK,
    CallableElevation,
    ChainElevation,
    ConstantElevation,
    ElevationError,
    ElevationProvider,
    OpenTopoDataElevation,
    OsmTagElevation,
    RasterDemElevation,
    TryElevations,
)

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "OPENTOPODATA_CHUNK",
    "CallableElevation",
    "ChainElevation",
    "ConstantElevation",
    "ElevationError",
    "ElevationProvider",
    "ElevationStats",
    "MemoElevation",
    "OpenTopoDataElevation",
    "OsmTagElevation",
    "RasterDemElevation",
    "SqliteElevationCache",
    "TerrariumElevation",
    "TryElevations",
    "build_elevation_profile",
    "fill_missing",
    "smooth_elevations",
]
