"""Grafo ciclavel: modelo, superficies, restricoes, simplificacao e validacao."""

from .model import (
    BicycleAccess,
    RoutingGraph,
    Arc,
    Link,
    Segment,
    ElevationQuality,
    ElevationSample,
    Node,
    OBSERVED_ELEVATION_QUALITIES,
)
from .restrictions import (
    AccessRule,
    RestrictionSource,
    TurnRestriction,
    parse_conditional_restrictions,
    turn_restriction_from_relation,
)
from .simplify import SimplifyOptions, SimplifyStats, simplify_graph
from .surface import (
    SURFACE_PRIORITY,
    Surface,
    SurfaceStatus,
    surface_from_tags,
    surface_observation,
)

__all__ = [
    "AccessRule",
    "BicycleAccess",
    "RoutingGraph",
    "Arc",
    "Link",
    "Segment",
    "ElevationQuality",
    "ElevationSample",
    "Node",
    "OBSERVED_ELEVATION_QUALITIES",
    "RestrictionSource",
    "SURFACE_PRIORITY",
    "SimplifyOptions",
    "SimplifyStats",
    "Surface",
    "SurfaceStatus",
    "TurnRestriction",
    "parse_conditional_restrictions",
    "simplify_graph",
    "surface_from_tags",
    "surface_observation",
    "turn_restriction_from_relation",
]
