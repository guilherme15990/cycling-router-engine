"""Planejamento com paradas intermediarias (ADR-030, Fase 3).

``plan_routes`` encadeia ``planning.plan_offline_routes`` por perna e funde os
resultados no contrato publico ``RoutePlanV1``. A fusao acontece **no nucleo**,
nao no consumidor: a geometria e continua, ``dist_m`` e acumulado e as metricas
sao somadas (ou ponderadas, quando a grandeza e uma fracao/media).

Regras de honestidade:

* so existem rotas fundidas quando **todas** as pernas tem rota elegivel; uma
  perna sem rota nao vira um trajeto parcial silencioso;
* um valor opcional desconhecido em qualquer perna permanece ``None`` (nunca
  zero inventado);
* ``rejected`` de cada perna nao e fundido: a rejeicao e avaliada por perna e o
  envelope multi-perna contem apenas trajetos completos. Os planos por perna
  continuam disponiveis para auditoria;
* o estado fisico **nao** e encadeado entre pernas: cada perna parte do estado
  inicial informado (por padrao, repouso). Isso mantem as alternativas
  comparaveis e esta declarado como limitacao da v1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from router.core.data.graph.model import RoutingGraph
from router.app.planning import plan_offline_routes
from router.core.physics.physical.bicycle import BicycleModel
from router.core.physics.physical.cyclist import CyclistModel
from router.contracts import (
    INTENSITY_BANDS,
    BBoxV1,
    ReasonV1,
    RouteMLV1,
    RouteMetricsV1,
    RoutePlanV1,
    RoutePointV1,
    RouteProvenanceV1,
    RouteRequestV1,
    RouteSafetyV1,
    RouteV1,
    ZoneV1,
)

Coord = tuple[float, float]

#: Ordem de gravidade dos estados de seguranca (ADR-029 D2).
_SAFETY_RANK: dict[str, int] = {"validated": 0, "degraded": 1, "rejected": 2}

#: Tolerancia para considerar dois pontos como o mesmo (juncao entre pernas).
_EPS_COORD = 1e-9


def _same_coord(a: Coord | RoutePointV1, b: Coord | RoutePointV1) -> bool:
    ax, ay = (a.lat, a.lon) if isinstance(a, RoutePointV1) else a
    bx, by = (b.lat, b.lon) if isinstance(b, RoutePointV1) else b
    return abs(ax - bx) < _EPS_COORD and abs(ay - by) < _EPS_COORD


def _union_bbox(boxes: Sequence[BBoxV1]) -> BBoxV1:
    if not boxes:
        raise ValueError("nao ha bbox para fundir")
    return BBoxV1(
        south=min(box.south for box in boxes),
        west=min(box.west for box in boxes),
        north=max(box.north for box in boxes),
        east=max(box.east for box in boxes),
    )


def _sum_optional(values: Sequence[float | None]) -> float | None:
    """Soma valores; se **qualquer** um for desconhecido, o total e desconhecido."""
    if not values or any(value is None for value in values):
        return None
    return float(sum(value for value in values if value is not None))


def _weighted_mean(values: Sequence[float], weights: Sequence[float], fallback: float = 0.0) -> float:
    total = sum(weights)
    if total <= 0.0:
        return sum(values) / len(values) if values else fallback
    return sum(value * weight for value, weight in zip(values, weights)) / total


def _merge_geometry(legs: Sequence[RouteV1]) -> tuple[RoutePointV1, ...]:
    """Concatena a geometria das pernas, deslocando ``dist_m`` e sem duplicar a juncao."""
    points: list[RoutePointV1] = []
    offset = 0.0
    for leg in legs:
        for point in leg.geometry:
            if points and _same_coord(points[-1], point):
                continue
            points.append(replace(point, dist_m=offset + point.dist_m))
        offset += leg.metrics.distance_m
    return tuple(points)


def _merge_metrics(legs: Sequence[RouteV1]) -> RouteMetricsV1:
    max_grades = [
        leg.metrics.max_grade_pct
        for leg in legs
        if leg.metrics.max_grade_pct is not None
    ]
    band = [
        sum(leg.metrics.meters_band[index] for leg in legs)
        for index in range(3)
    ]
    return RouteMetricsV1(
        source="assessment" if all(leg.metrics.source == "assessment" for leg in legs) else "search",
        distance_m=sum(leg.metrics.distance_m for leg in legs),
        time_s=sum(leg.metrics.time_s for leg in legs),
        energy_j=sum(leg.metrics.energy_j for leg in legs),
        ascent_m=sum(leg.metrics.ascent_m for leg in legs),
        descent_m=sum(leg.metrics.descent_m for leg in legs),
        max_grade_pct=max(max_grades) if max_grades else None,
        meters_band=(band[0], band[1], band[2]),
        contra_m=_sum_optional([leg.metrics.contra_m for leg in legs]),
        walk_m=_sum_optional([leg.metrics.walk_m for leg in legs]),
    )


def _merge_ml(legs: Sequence[RouteV1]) -> RouteMLV1:
    """Funde a estimativa de intensidade mantendo-a explicitamente **nao calibrada**."""
    weights = [max(leg.metrics.distance_m, 0.0) for leg in legs]
    band_distance = {
        band: _weighted_mean(
            [leg.ml.band_distance_fraction.get(band, 0.0) for leg in legs],
            weights,
        )
        for band in INTENSITY_BANDS
    }

    durations = [max(leg.metrics.time_s, 0.0) for leg in legs]
    if all(leg.ml.band_duration_fraction is not None for leg in legs):
        band_duration: dict[str, float] | None = {
            band: _weighted_mean(
                [
                    (leg.ml.band_duration_fraction or {}).get(band, 0.0)
                    for leg in legs
                ],
                durations,
            )
            for band in INTENSITY_BANDS
        }
    else:
        band_duration = None

    metadata = {(leg.ml.method_id, leg.ml.model_version, leg.ml.borg_scale) for leg in legs}
    if len(metadata) == 1:
        method_id, model_version, borg_scale = next(iter(metadata))
    else:
        # Pernas com metodos/escalas diferentes: nao escolher um deles como se
        # representasse o todo. A ausencia de um identificador unico e explicita.
        method_id, model_version, borg_scale = "multi_leg", "", ""

    return RouteMLV1(
        mean_rpe=_weighted_mean([leg.ml.mean_rpe for leg in legs], weights),
        peak_rpe=max(leg.ml.peak_rpe for leg in legs),
        calibrated=all(leg.ml.calibrated for leg in legs),
        is_measurement=all(leg.ml.is_measurement for leg in legs),
        band_distance_fraction=band_distance,
        band_duration_fraction=band_duration,
        method_id=method_id,
        model_version=model_version,
        borg_scale=borg_scale,
    )


def _merge_reasons(legs: Sequence[RouteV1]) -> tuple[ReasonV1, ...]:
    merged: list[ReasonV1] = []
    seen: set[tuple[str, str, str | None]] = set()
    for leg in legs:
        for reason in leg.safety.reasons:
            key = (reason.kind, reason.detail, reason.arc_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(reason)
    return tuple(merged)


def _merge_advisories(legs: Sequence[RouteV1]) -> tuple[ReasonV1, ...]:
    merged: list[ReasonV1] = []
    seen: set[tuple[str, str, str | None]] = set()
    for leg in legs:
        for advisory in leg.safety.advisories:
            key = (advisory.kind, advisory.detail, advisory.arc_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(advisory)
    return tuple(merged)


def _merge_safety(legs: Sequence[RouteV1]) -> RouteSafetyV1:
    state = max(
        (leg.safety.state for leg in legs),
        key=lambda value: _SAFETY_RANK.get(value, _SAFETY_RANK["rejected"]),
    )
    return RouteSafetyV1(
        state=state,
        eligible=all(leg.safety.eligible for leg in legs),
        reasons=_merge_reasons(legs),
        advisories=_merge_advisories(legs),
    )


def _merge_route(legs: Sequence[RouteV1], *, rank: int, profile: str) -> RouteV1:
    return RouteV1(
        id=f"m{rank}",
        profile=profile,
        recommended=rank == 0,
        geometry=_merge_geometry(legs),
        metrics=_merge_metrics(legs),
        ml=_merge_ml(legs),
        safety=_merge_safety(legs),
        provenance=RouteProvenanceV1(kind="multi_leg", profile=profile),
    )


def _empty_plan(
    request: RouteRequestV1,
    legs: Sequence[RoutePlanV1],
) -> RoutePlanV1:
    graph = legs[0]
    return RoutePlanV1(
        request=request,
        reached=False,
        search_bbox=graph.search_bbox,
        bbox=graph.bbox,
        zone=ZoneV1(
            nodes=sum(leg.zone.nodes for leg in legs),
            arcs=sum(leg.zone.arcs for leg in legs),
            graph_nodes=graph.zone.graph_nodes,
            graph_arcs=graph.zone.graph_arcs,
        ),
        iterations=sum(leg.iterations for leg in legs),
        pareto_route_count=sum(leg.pareto_route_count for leg in legs),
        physiological_count=0,
        routes=(),
        rejected=(),
        warnings=tuple(dict.fromkeys(
            warning for leg in legs for warning in leg.warnings
        )),
        recommended_route_id=None,
        truncated=any(leg.truncated for leg in legs),
        frontier_truncated=any(leg.frontier_truncated for leg in legs),
        dropped_labels=sum(leg.dropped_labels for leg in legs),
    )


def _merge_leg_plans(
    legs: Sequence[RoutePlanV1],
    *,
    request: RouteRequestV1,
) -> RoutePlanV1:
    if any(not leg.reached for leg in legs):
        return _empty_plan(request, legs)

    # So trajetos completos: uma rota por *rank* presente em todas as pernas.
    # O rank 0 e a recomendada de cada perna; os demais sao as alternativas na
    # mesma ordem de selecao. E o equivalente nuclear de `mergeLegRoutes`.
    route_count = min(len(leg.routes) for leg in legs)
    if route_count == 0:
        return _empty_plan(request, legs)

    merged = [
        _merge_route(
            [leg.routes[rank] for leg in legs],
            rank=rank,
            profile=request.profile,
        )
        for rank in range(route_count)
    ]

    eligible = [route for route in merged if route.safety.eligible]
    rejected = [
        replace(route, recommended=False)
        for route in merged
        if not route.safety.eligible
    ]
    # Reafirma a marcacao: a recomendada e a primeira elegivel, nunca uma
    # rejeitada (ADR-029 D4).
    eligible = [replace(route, recommended=False) for route in eligible]
    if eligible:
        eligible[0] = replace(eligible[0], recommended=True)

    return RoutePlanV1(
        request=request,
        reached=all(leg.reached for leg in legs),
        search_bbox=_union_bbox([leg.search_bbox for leg in legs]),
        bbox=_union_bbox([leg.bbox for leg in legs]),
        zone=ZoneV1(
            nodes=sum(leg.zone.nodes for leg in legs),
            arcs=sum(leg.zone.arcs for leg in legs),
            graph_nodes=legs[0].zone.graph_nodes,
            graph_arcs=legs[0].zone.graph_arcs,
        ),
        iterations=sum(leg.iterations for leg in legs),
        pareto_route_count=sum(leg.pareto_route_count for leg in legs),
        physiological_count=sum(
            1 for route in eligible if route.ml.calibrated
        ),
        routes=tuple(eligible),
        rejected=tuple(rejected),
        warnings=tuple(dict.fromkeys(
            warning for leg in legs for warning in leg.warnings
        )),
        recommended_route_id=eligible[0].id if eligible else None,
        truncated=any(leg.truncated for leg in legs),
        frontier_truncated=any(leg.frontier_truncated for leg in legs),
        dropped_labels=sum(leg.dropped_labels for leg in legs),
    )


def plan_routes(
    graph: RoutingGraph,
    origin: Coord,
    destination: Coord,
    *,
    stops: Sequence[Coord] = (),
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    profile: str = "fastest",
    **options: Any,
) -> RoutePlanV1:
    """Planeja uma rota passando por ``stops``, na ordem informada.

    Sem paradas, delega a ``plan_offline_routes`` e devolve o contrato v1 — o
    comportamento e identico ao de uma perna unica. Com paradas, executa uma
    busca por perna e funde os resultados.

    Args:
        graph: Grafo carregado; nao e alterado.
        origin: Origem da rota.
        destination: Destino final.
        stops: Paradas intermediarias, na ordem de passagem.
        cyclist: Modelo do ciclista (obrigatorio, como na busca).
        bicycle: Modelo da bicicleta (obrigatorio).
        profile: Perfil de roteamento (``fastest``, ``shortest``, ...).
        **options: Repassadas a ``plan_offline_routes`` (``snap_options``,
            ``traversal``, ``environment``, ``predictor``, ``max_routes``, ...).
            Cada perna parte do estado inicial informado; a fisiologia/fadiga
            **nao** e encadeada entre pernas na v1.

    Returns:
        ``RoutePlanV1`` com ``request.stops`` ecoado e geometria continua.

    Raises:
        ValueError: paradas consecutivas iguais (perna degenerada).
    """
    stop_points: tuple[Coord, ...] = tuple(
        (float(lat), float(lon)) for lat, lon in stops
    )
    waypoints: tuple[Coord, ...] = (origin, *stop_points, destination)
    for index, (start, end) in enumerate(zip(waypoints, waypoints[1:])):
        if _same_coord(start, end):
            raise ValueError(
                f"perna {index}: origem e destino iguais "
                f"({start[0]!r}, {start[1]!r})"
            )

    leg_plans = [
        plan_offline_routes(
            graph,
            start,
            end,
            cyclist=cyclist,
            bicycle=bicycle,
            profile=profile,
            **options,
        )
        for start, end in zip(waypoints, waypoints[1:])
    ]

    if not stop_points:
        # Uma perna: o contrato daquela perna ja e a resposta completa.
        return leg_plans[0].to_contract()

    contracts = [leg.to_contract() for leg in leg_plans]
    profile_id = leg_plans[0].profile_id
    request = RouteRequestV1(
        origin=origin,
        destination=destination,
        profile=profile_id,
        stops=stop_points,
    )
    return _merge_leg_plans(contracts, request=request)


__all__ = ["plan_routes"]
