"""OSM access-class hierarchy, separate from the physical propulsion source."""
from __future__ import annotations



_SCOPES = {
    "foot": ("foot", "access"),
    "bicycle": ("bicycle", "vehicle", "access"),
    "electric_bicycle": ("electric_bicycle", "bicycle", "vehicle", "access"),
    "cargo_bike": ("cargo_bike", "bicycle", "vehicle", "access"),
    "electric_cargo_bike": ("cargo_bike", "electric_bicycle", "bicycle", "vehicle", "access"),
    "kick_scooter": ("kick_scooter", "vehicle", "access"),
    "small_electric_vehicle": ("small_electric_vehicle", "motor_vehicle", "vehicle", "access"),
    "motorcycle": ("motorcycle", "motor_vehicle", "vehicle", "access"),
    "moped": ("moped", "motor_vehicle", "vehicle", "access"),
    "mofa": ("mofa", "motor_vehicle", "vehicle", "access"),
    "speed_pedelec": ("speed_pedelec", "moped", "motor_vehicle", "vehicle", "access"),
    "motorcar": ("motorcar", "motor_vehicle", "vehicle", "access"),
    "goods": ("goods", "motor_vehicle", "vehicle", "access"),
    "hgv": ("hgv", "motor_vehicle", "vehicle", "access"),
    "bus": ("bus", "psv", "motor_vehicle", "vehicle", "access"),
    "taxi": ("taxi", "psv", "motor_vehicle", "vehicle", "access"),
}


def access_scopes(mode: str) -> tuple[str, ...]:
    try:
        return _SCOPES[mode]
    except KeyError:
        raise ValueError(f"unsupported OSM transport mode: {mode}") from None


_ROADS = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road", "track",
})
_CYCLING = (_ROADS - {"motorway", "motorway_link", "trunk", "trunk_link"}) | {"cycleway", "cycleway_link", "path"}
_WALKING = (_CYCLING - {"cycleway", "cycleway_link"}) | {"footway", "pedestrian", "steps"}
ALL_ROAD_HIGHWAYS = frozenset(_ROADS | _CYCLING | _WALKING | {"bridleway"})


def candidate_highways(mode: str) -> frozenset[str]:
    """Network inclusion defaults, not a jurisdiction-specific legal verdict.

    Explicit permission can include other road classes. Actual access rules
    are retained on arcs and evaluated at query time.
    """
    scopes = access_scopes(mode)
    if "bicycle" in scopes:
        return frozenset(_CYCLING)
    if mode in ("foot", "kick_scooter"):
        return frozenset(_WALKING)
    return _ROADS


def include_way(tags: dict[str, str], mode: str) -> bool:
    highway = tags.get("highway", "").strip().lower()
    if not highway or highway in {"construction", "proposed", "abandoned"}:
        return False
    scopes = access_scopes(mode)
    explicitly_possible = any(
        tags.get(scope, "").strip().lower() in {"yes", "designated", "permissive", "official", "dismount"}
        or f"{scope}:conditional" in tags for scope in scopes if scope != "access"
    )
    return highway in candidate_highways(mode) or explicitly_possible


def supported_access_keys() -> frozenset[str]:
    return frozenset(scope for scopes in _SCOPES.values() for scope in scopes)
