"""Projecao do resultado interno no contrato publico v1 (ADR-033, E3).

A orquestracao (``router.app.planning``) produz um ``RoutePlanResult`` interno; este
modulo projeta esse resultado no **contrato publico versionado**
(``RoutePlan``). A projecao sai da orquestracao para manter ``planning.py``
focado no pipeline: o contrato e a **unica** representacao de transporte e
inclui a geometria por ponto (``RoutePoint``), as metricas autoritativas da
avaliacao, a estimativa de intensidade e o estado de seguranca.

``planning`` e importado apenas em ``TYPE_CHECKING`` (os DTOs internos nao sao
necessarios em runtime): a dependencia real e de ``router.contracts`` (L0) e dos
tipos do grafo, evitando um ciclo de importacao.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from router.contracts import (
    BBox,
    Reason,
    RouteML,
    RouteMetrics,
    RoutePlan,
    RouteProvenance,
    RouteRequest,
    RouteSafety,
    Route,
    Zone,
)
from router.core.data.geo import GeoBBox

if TYPE_CHECKING:
    from router.app.planning import PlannedRoute, RoutePlanResult

__all__ = ["to_contract"]


def _to_bbox_contract(bbox: GeoBBox) -> BBox:
    south, west, north, east = bbox
    return BBox(south=south, west=west, north=north, east=east)


def _to_route_contract(route: PlannedRoute, *, profile_id: str) -> Route:
    """Converte uma ``PlannedRoute`` no DTO publico ``Route``."""
    assessment = route.assessment
    metrics = assessment.metrics
    intensity = assessment.intensity
    geometry = route.geometry

    meters_band = [0.0, 0.0, 0.0]
    for current, following in zip(geometry, geometry[1:]):
        delta = max(0.0, following.dist_m - current.dist_m)
        if current.band is not None:
            meters_band[current.band] += delta
    max_grade = max((point.grade_pct for point in geometry), default=0.0)

    return Route(
        id=route.route_id,
        profile=profile_id,
        recommended=route.recommended,
        geometry=geometry,
        metrics=RouteMetrics(
            source="assessment",
            distance_m=metrics.distance_m,
            time_s=metrics.time_s,
            energy_j=metrics.energy_j,
            ascent_m=metrics.ascent_m,
            descent_m=metrics.descent_m,
            max_grade_pct=max(0.0, max_grade),
            meters_band=(meters_band[0], meters_band[1], meters_band[2]),
            contra_m=None,
            walk_m=None,
        ),
        ml=RouteML(
            mean_rpe=intensity.mean_rpe,
            peak_rpe=intensity.peak_rpe,
            calibrated=intensity.calibrated,
            is_measurement=intensity.is_measurement,
            band_distance_fraction=dict(intensity.band_distance_fraction),
            band_duration_fraction=(
                None
                if intensity.band_duration_fraction is None
                else dict(intensity.band_duration_fraction)
            ),
            method_id=intensity.method_id,
            model_version=intensity.model_version,
            borg_scale=intensity.borg_scale.value,
        ),
        safety=RouteSafety(
            state=route.safety.state.value,
            eligible=route.safety.eligible,
            reasons=tuple(
                Reason(kind=reason.kind.value, detail=reason.detail, arc_id=reason.arc_id)
                for reason in route.safety.reasons
            ),
            advisories=tuple(
                Reason(kind=reason.kind.value, detail=reason.detail, arc_id=reason.arc_id)
                for reason in route.safety.advisories
            ),
        ),
        provenance=RouteProvenance(kind="search", profile=profile_id),
        demand=route.demand,
        telemetry=route.telemetry,
    )


def partial_contract(
    *, origin, destination, profile_id, search_bbox, bbox,
    zone_nodes, zone_arcs, graph_nodes, graph_arcs, iterations,
    routes, pareto_route_count, physiological_count,
) -> RoutePlan:
    """Projeta resultados validados enquanto a fronteira ainda está aberta."""
    return RoutePlan(
        request=RouteRequest(origin=origin, destination=destination, profile=profile_id),
        search_bbox=_to_bbox_contract(search_bbox), bbox=_to_bbox_contract(bbox), reached=True,
        zone=Zone(nodes=zone_nodes, arcs=zone_arcs, graph_nodes=graph_nodes, graph_arcs=graph_arcs),
        iterations=iterations, routes=routes, pareto_route_count=pareto_route_count,
        physiological_count=physiological_count, recommended_route_id=routes[0].id,
        warnings=("Resultado parcial: a fronteira de busca não foi concluída.",),
    )


def to_contract(plan: RoutePlanResult) -> RoutePlan:
    """Projeta o resultado interno no contrato publico versionado.

    O contrato e a **unica** representacao de transporte. Ele inclui a
    geometria por ponto (``RoutePoint``), as metricas autoritativas da
    avaliacao, a estimativa de intensidade e o estado de seguranca.
    """
    recommended = plan.recommended
    request = RouteRequest(
        origin=plan.origin,
        destination=plan.destination,
        profile=plan.profile_id,
    )
    return RoutePlan(
        request=request,
        reached=plan.reached,
        search_bbox=_to_bbox_contract(plan.search_bbox),
        bbox=_to_bbox_contract(plan.bbox),
        zone=Zone(
            nodes=plan.zone_nodes,
            arcs=plan.zone_arcs,
            graph_nodes=plan.graph_nodes,
            graph_arcs=plan.graph_arcs,
        ),
        iterations=plan.iterations,
        pareto_route_count=plan.pareto_route_count,
        physiological_count=plan.physiological_count,
        routes=tuple(
            _to_route_contract(route, profile_id=plan.profile_id) for route in plan.routes
        ),
        rejected=tuple(
            _to_route_contract(route, profile_id=plan.profile_id) for route in plan.rejected
        ),
        warnings=plan.warnings,
        recommended_route_id=None if recommended is None else recommended.route_id,
        truncated=plan.truncated,
        frontier_truncated=plan.frontier_truncated,
        dropped_labels=plan.dropped_labels,
    )
