"""Blocos e envelope do contrato publico de rota v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from router.contracts._validation import (
    _bool,
    _coord,
    _coord_to_dict,
    _float,
    _fractions,
    _fractions_from,
    _int,
    _mapping,
    _round,
    _sequence,
    _str,
)
from router.contracts.errors import RouteContractError
from router.contracts.geo import BBoxV1
from router.contracts.version import CONTRACT_FORMAT, CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class RouteRequestV1:
    """Intencao de roteamento ecoada no resultado."""

    origin: tuple[float, float]
    destination: tuple[float, float]
    profile: str
    stops: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        _coord(self.origin, "request.origin")
        _coord(self.destination, "request.destination")
        _str(self.profile, "request.profile")
        for index, stop in enumerate(self.stops):
            _coord(stop, f"request.stops[{index}]")

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": _coord_to_dict(self.origin),
            "destination": _coord_to_dict(self.destination),
            "profile": self.profile,
            "stops": [_coord_to_dict(stop) for stop in self.stops],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteRequestV1":
        item = _mapping(data, "request")
        raw_stops = _sequence(item.get("stops", []), "request.stops")
        return cls(
            origin=_coord(item.get("origin"), "request.origin"),
            destination=_coord(item.get("destination"), "request.destination"),
            profile=_str(item.get("profile"), "request.profile"),
            stops=tuple(_coord(stop, f"request.stops[{i}]") for i, stop in enumerate(raw_stops)),
        )


@dataclass(frozen=True, slots=True)
class RoutePointV1:
    """Ponto do perfil da rota.

    ``band`` usa a escala de apresentacao de 3 cores (0 verde, 1 laranja,
    2 vermelho) derivada das faixas ``i1``..``i5``; ``None`` quando a
    intensidade nao foi calculada. ``rpe`` e a estimativa **nao calibrada**
    do trecho (Borg), nunca uma medicao.
    """

    lat: float
    lon: float
    ele: float | None
    dist_m: float
    speed_kmh: float
    grade_pct: float
    band: int | None = None
    rpe: float | None = None
    watts: float | None = None

    def __post_init__(self) -> None:
        _coord((self.lat, self.lon), "point")
        _float(self.ele, "point.ele", allow_none=True)
        _float(self.dist_m, "point.dist_m", minimum=0.0)
        _float(self.speed_kmh, "point.speed_kmh", minimum=0.0)
        _float(self.grade_pct, "point.grade_pct")
        if self.band is not None and self.band not in (0, 1, 2):
            raise RouteContractError("point.band deve ser 0, 1, 2 ou None")
        _float(self.rpe, "point.rpe", allow_none=True)
        _float(self.watts, "point.watts", allow_none=True, minimum=0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "lat": round(self.lat, 7),
            "lon": round(self.lon, 7),
            "ele": _round(self.ele, 3),
            "dist_m": round(self.dist_m, 3),
            "speed_kmh": round(self.speed_kmh, 3),
            "grade_pct": round(self.grade_pct, 3),
            "band": self.band,
            "rpe": _round(self.rpe, 2),
            "watts": _round(self.watts, 2),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RoutePointV1":
        item = _mapping(data, "point")
        return cls(
            lat=_float(item.get("lat"), "point.lat"),  # type: ignore[arg-type]
            lon=_float(item.get("lon"), "point.lon"),  # type: ignore[arg-type]
            ele=_float(item.get("ele"), "point.ele", allow_none=True),
            dist_m=_float(item.get("dist_m"), "point.dist_m", minimum=0.0),  # type: ignore[arg-type]
            speed_kmh=_float(item.get("speed_kmh"), "point.speed_kmh", minimum=0.0),  # type: ignore[arg-type]
            grade_pct=_float(item.get("grade_pct"), "point.grade_pct"),  # type: ignore[arg-type]
            band=(
                None
                if item.get("band") is None
                else _int(item.get("band"), "point.band")
            ),
            rpe=_float(item.get("rpe"), "point.rpe", allow_none=True),
            watts=_float(item.get("watts"), "point.watts", allow_none=True, minimum=0.0),
        )


@dataclass(frozen=True, slots=True)
class RouteMetricsV1:
    """Metricas agregadas da rota.

    ``source`` declara a origem: ``"assessment"`` para o simulador completo
    (autoritativo) ou ``"search"`` para a estimativa da busca. Os campos
    opcionais ficam ``None`` quando o motor nao os calcula.

    ``max_grade_pct`` e o maior valor entre os pontos da geometria. A grade de
    um ponto e a **rampa media** do arco (ganho/distancia), nao o pico
    instantaneo (``arcMaxPct``), que a travessia nao expoe. ``meters_band``
    distribui a distancia dos trechos nas 3 cores de apresentacao; ``contra_m``
    e ``walk_m`` so sao preenchidos por quem os calcula (hoje: ``None``).
    """

    source: str
    distance_m: float
    time_s: float
    energy_j: float
    ascent_m: float
    descent_m: float
    max_grade_pct: float | None = None
    meters_band: tuple[float, float, float] = (0.0, 0.0, 0.0)
    contra_m: float | None = None
    walk_m: float | None = None

    def __post_init__(self) -> None:
        if self.source not in ("search", "assessment"):
            raise RouteContractError(
                f"metrics.source deve ser 'search' ou 'assessment' ({self.source!r})"
            )
        for name in ("distance_m", "time_s", "energy_j", "ascent_m", "descent_m"):
            _float(getattr(self, name), f"metrics.{name}", minimum=0.0)
        _float(self.max_grade_pct, "metrics.max_grade_pct", allow_none=True)
        if len(self.meters_band) != 3:
            raise RouteContractError("metrics.meters_band deve ter 3 valores")
        for value in self.meters_band:
            _float(value, "metrics.meters_band[]", minimum=0.0)
        _float(self.contra_m, "metrics.contra_m", allow_none=True, minimum=0.0)
        _float(self.walk_m, "metrics.walk_m", allow_none=True, minimum=0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "distance_m": round(self.distance_m, 3),
            "time_s": round(self.time_s, 3),
            "energy_j": round(self.energy_j, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "max_grade_pct": _round(self.max_grade_pct, 3),
            "meters_band": [round(value, 3) for value in self.meters_band],
            "contra_m": _round(self.contra_m, 3),
            "walk_m": _round(self.walk_m, 3),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteMetricsV1":
        item = _mapping(data, "metrics")
        raw_band = _sequence(item.get("meters_band", [0.0, 0.0, 0.0]), "metrics.meters_band")
        if len(raw_band) != 3:
            raise RouteContractError("metrics.meters_band deve ter 3 valores")
        return cls(
            source=_str(item.get("source"), "metrics.source"),
            distance_m=_float(item.get("distance_m"), "metrics.distance_m", minimum=0.0),  # type: ignore[arg-type]
            time_s=_float(item.get("time_s"), "metrics.time_s", minimum=0.0),  # type: ignore[arg-type]
            energy_j=_float(item.get("energy_j"), "metrics.energy_j", minimum=0.0),  # type: ignore[arg-type]
            ascent_m=_float(item.get("ascent_m"), "metrics.ascent_m", minimum=0.0),  # type: ignore[arg-type]
            descent_m=_float(item.get("descent_m"), "metrics.descent_m", minimum=0.0),  # type: ignore[arg-type]
            max_grade_pct=_float(item.get("max_grade_pct"), "metrics.max_grade_pct", allow_none=True),
            meters_band=(
                _float(raw_band[0], "metrics.meters_band[0]", minimum=0.0),  # type: ignore[arg-type]
                _float(raw_band[1], "metrics.meters_band[1]", minimum=0.0),  # type: ignore[arg-type]
                _float(raw_band[2], "metrics.meters_band[2]", minimum=0.0),  # type: ignore[arg-type]
            ),
            contra_m=_float(item.get("contra_m"), "metrics.contra_m", allow_none=True, minimum=0.0),
            walk_m=_float(item.get("walk_m"), "metrics.walk_m", allow_none=True, minimum=0.0),
        )


@dataclass(frozen=True, slots=True)
class RouteMLV1:
    """Estimativa de intensidade (RPE/faixas) — **nao calibrada**.

    ``calibrated`` e ``is_measurement`` sao explicitos e sempre ``False`` para
    a estimativa computacional atual. ``band_*_fraction`` sao fracoes em
    ``i1``..``i5``.
    """

    mean_rpe: float
    peak_rpe: float
    calibrated: bool
    is_measurement: bool
    band_distance_fraction: dict[str, float] = field(default_factory=dict)
    band_duration_fraction: dict[str, float] | None = None
    method_id: str = ""
    model_version: str = ""
    borg_scale: str = ""

    def __post_init__(self) -> None:
        _float(self.mean_rpe, "ml.mean_rpe")
        _float(self.peak_rpe, "ml.peak_rpe")
        _bool(self.calibrated, "ml.calibrated")
        _bool(self.is_measurement, "ml.is_measurement")
        _fractions(self.band_distance_fraction, "ml.band_distance_fraction")
        if self.band_duration_fraction is not None:
            _fractions(self.band_duration_fraction, "ml.band_duration_fraction")

    def to_dict(self) -> dict[str, object]:
        return {
            "mean_rpe": round(self.mean_rpe, 2),
            "peak_rpe": round(self.peak_rpe, 2),
            "calibrated": self.calibrated,
            "is_measurement": self.is_measurement,
            "method_id": self.method_id,
            "model_version": self.model_version,
            "borg_scale": self.borg_scale,
            "band_distance_fraction": {
                name: round(value, 4)
                for name, value in self.band_distance_fraction.items()
            },
            "band_duration_fraction": (
                None
                if self.band_duration_fraction is None
                else {
                    name: round(value, 4)
                    for name, value in self.band_duration_fraction.items()
                }
            ),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteMLV1":
        item = _mapping(data, "ml")
        raw_duration = item.get("band_duration_fraction")
        return cls(
            mean_rpe=_float(item.get("mean_rpe"), "ml.mean_rpe"),  # type: ignore[arg-type]
            peak_rpe=_float(item.get("peak_rpe"), "ml.peak_rpe"),  # type: ignore[arg-type]
            calibrated=_bool(item.get("calibrated"), "ml.calibrated"),
            is_measurement=_bool(item.get("is_measurement"), "ml.is_measurement"),
            band_distance_fraction=_fractions_from(
                item.get("band_distance_fraction", {}), "ml.band_distance_fraction"
            ),
            band_duration_fraction=(
                None
                if raw_duration is None
                else _fractions_from(raw_duration, "ml.band_duration_fraction")
            ),
            method_id=_str(item.get("method_id", ""), "ml.method_id", allow_empty=True),
            model_version=_str(item.get("model_version", ""), "ml.model_version", allow_empty=True),
            borg_scale=_str(item.get("borg_scale", ""), "ml.borg_scale", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class ReasonV1:
    """Motivo estruturado de seguranca (auditavel)."""

    kind: str
    detail: str
    arc_id: str | None = None

    def __post_init__(self) -> None:
        _str(self.kind, "reason.kind")
        _str(self.detail, "reason.detail", allow_empty=True)
        if self.arc_id is not None:
            _str(self.arc_id, "reason.arc_id")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "detail": self.detail, "arc_id": self.arc_id}

    @classmethod
    def from_dict(cls, data: Any) -> "ReasonV1":
        item = _mapping(data, "reason")
        return cls(
            kind=_str(item.get("kind"), "reason.kind"),
            detail=_str(item.get("detail"), "reason.detail", allow_empty=True),
            arc_id=(
                None
                if item.get("arc_id") is None
                else _str(item.get("arc_id"), "reason.arc_id")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteSafetyV1:
    """Estado de seguranca da rota (ADR-029)."""

    state: str
    eligible: bool
    reasons: tuple[ReasonV1, ...] = ()
    advisories: tuple[ReasonV1, ...] = ()

    def __post_init__(self) -> None:
        _str(self.state, "safety.state")
        _bool(self.eligible, "safety.eligible")

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "eligible": self.eligible,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "advisories": [reason.to_dict() for reason in self.advisories],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteSafetyV1":
        item = _mapping(data, "safety")
        return cls(
            state=_str(item.get("state"), "safety.state"),
            eligible=_bool(item.get("eligible"), "safety.eligible"),
            reasons=tuple(
                ReasonV1.from_dict(reason)
                for reason in _sequence(item.get("reasons", []), "safety.reasons")
            ),
            advisories=tuple(
                ReasonV1.from_dict(reason)
                for reason in _sequence(item.get("advisories", []), "safety.advisories")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteProvenanceV1:
    """Como a rota nasceu (gerador/perfil)."""

    kind: str
    profile: str

    def __post_init__(self) -> None:
        _str(self.kind, "provenance.kind")
        _str(self.profile, "provenance.profile")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "profile": self.profile}

    @classmethod
    def from_dict(cls, data: Any) -> "RouteProvenanceV1":
        item = _mapping(data, "provenance")
        return cls(
            kind=_str(item.get("kind"), "provenance.kind"),
            profile=_str(item.get("profile"), "provenance.profile"),
        )


@dataclass(frozen=True, slots=True)
class RouteV1:
    """Rota no contrato publico."""

    id: str
    profile: str
    recommended: bool
    geometry: tuple[RoutePointV1, ...]
    metrics: RouteMetricsV1
    ml: RouteMLV1
    safety: RouteSafetyV1
    provenance: RouteProvenanceV1

    def __post_init__(self) -> None:
        _str(self.id, "route.id")
        _str(self.profile, "route.profile")
        _bool(self.recommended, "route.recommended")
        previous = -1.0
        for index, point in enumerate(self.geometry):
            if point.dist_m + 1e-9 < previous:
                raise RouteContractError(
                    f"route {self.id!r}: dist_m nao monotono no ponto {index} "
                    f"({point.dist_m} < {previous})"
                )
            previous = point.dist_m

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "profile": self.profile,
            "recommended": self.recommended,
            "geometry": [point.to_dict() for point in self.geometry],
            "metrics": self.metrics.to_dict(),
            "ml": self.ml.to_dict(),
            "safety": self.safety.to_dict(),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteV1":
        item = _mapping(data, "route")
        return cls(
            id=_str(item.get("id"), "route.id"),
            profile=_str(item.get("profile"), "route.profile"),
            recommended=_bool(item.get("recommended"), "route.recommended"),
            geometry=tuple(
                RoutePointV1.from_dict(point)
                for point in _sequence(item.get("geometry", []), "route.geometry")
            ),
            metrics=RouteMetricsV1.from_dict(item.get("metrics")),
            ml=RouteMLV1.from_dict(item.get("ml")),
            safety=RouteSafetyV1.from_dict(item.get("safety")),
            provenance=RouteProvenanceV1.from_dict(item.get("provenance")),
        )


@dataclass(frozen=True, slots=True)
class ZoneV1:
    """Tamanho da zona de busca versus grafo total."""

    nodes: int
    arcs: int
    graph_nodes: int
    graph_arcs: int

    def __post_init__(self) -> None:
        for name in ("nodes", "arcs", "graph_nodes", "graph_arcs"):
            _int(getattr(self, name), f"zone.{name}", minimum=0)

    def to_dict(self) -> dict[str, int]:
        return {
            "nodes": self.nodes,
            "arcs": self.arcs,
            "graph_nodes": self.graph_nodes,
            "graph_arcs": self.graph_arcs,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ZoneV1":
        item = _mapping(data, "zone")
        return cls(
            nodes=_int(item.get("nodes"), "zone.nodes", minimum=0),
            arcs=_int(item.get("arcs"), "zone.arcs", minimum=0),
            graph_nodes=_int(item.get("graph_nodes"), "zone.graph_nodes", minimum=0),
            graph_arcs=_int(item.get("graph_arcs"), "zone.graph_arcs", minimum=0),
        )


@dataclass(frozen=True, slots=True)
class RoutePlanV1:
    """Envelope versionado de uma consulta de rotas.

    ``routes`` contem apenas rotas elegiveis; ``rejected`` preserva as
    rejeitadas para auditoria. ``recommended_route_id`` e sempre uma rota de
    ``routes`` (ou ``None``).
    """

    request: RouteRequestV1
    reached: bool
    search_bbox: BBoxV1
    bbox: BBoxV1
    zone: ZoneV1
    iterations: int
    pareto_route_count: int
    physiological_count: int
    routes: tuple[RouteV1, ...] = ()
    rejected: tuple[RouteV1, ...] = ()
    warnings: tuple[str, ...] = ()
    recommended_route_id: str | None = None
    truncated: bool = False
    frontier_truncated: bool = False
    dropped_labels: int = 0
    format: str = CONTRACT_FORMAT
    version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.format != CONTRACT_FORMAT:
            raise RouteContractError(
                f"format desconhecido: {self.format!r} (esperado {CONTRACT_FORMAT!r})"
            )
        if self.version != CONTRACT_VERSION:
            raise RouteContractError(
                f"versao desconhecida: {self.version!r} "
                f"(esperado {CONTRACT_VERSION!r})"
            )
        _bool(self.reached, "reached")
        _int(self.iterations, "iterations", minimum=0)
        _int(self.pareto_route_count, "pareto_route_count", minimum=0)
        _int(self.physiological_count, "physiological_count", minimum=0)
        _int(self.dropped_labels, "dropped_labels", minimum=0)
        _bool(self.truncated, "truncated")
        _bool(self.frontier_truncated, "frontier_truncated")

        ids = [route.id for route in self.routes]
        if len(ids) != len(set(ids)):
            raise RouteContractError("routes contem ids duplicados")
        rejected_ids = [route.id for route in self.rejected]
        if len(rejected_ids) != len(set(rejected_ids)):
            raise RouteContractError("rejected contem ids duplicados")
        if self.recommended_route_id is not None and self.recommended_route_id not in ids:
            raise RouteContractError(
                "recommended_route_id nao pertence a routes: "
                f"{self.recommended_route_id!r}"
            )
        for route in self.routes + self.rejected:
            if route.profile != self.request.profile:
                raise RouteContractError(
                    f"rota {route.id!r}: perfil {route.profile!r} difere do "
                    f"request {self.request.profile!r}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "version": self.version,
            "request": self.request.to_dict(),
            "reached": self.reached,
            "truncated": self.truncated,
            "frontier_truncated": self.frontier_truncated,
            "dropped_labels": self.dropped_labels,
            "iterations": self.iterations,
            "search_bbox": self.search_bbox.to_dict(),
            "bbox": self.bbox.to_dict(),
            "zone": self.zone.to_dict(),
            "pareto_route_count": self.pareto_route_count,
            "route_count": len(self.routes),
            "rejected_count": len(self.rejected),
            "physiological_count": self.physiological_count,
            "recommended_route_id": self.recommended_route_id,
            "warnings": list(self.warnings),
            "routes": [route.to_dict() for route in self.routes],
            "rejected": [route.to_dict() for route in self.rejected],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RoutePlanV1":
        item = _mapping(data, "route plan")
        fmt = item.get("format")
        version = item.get("version")
        if fmt != CONTRACT_FORMAT:
            raise RouteContractError(
                f"format desconhecido: {fmt!r} (esperado {CONTRACT_FORMAT!r})"
            )
        if version != CONTRACT_VERSION:
            raise RouteContractError(
                f"versao desconhecida: {version!r} (esperado {CONTRACT_VERSION!r})"
            )
        return cls(
            request=RouteRequestV1.from_dict(item.get("request")),
            reached=_bool(item.get("reached"), "reached"),
            search_bbox=BBoxV1.from_dict(item.get("search_bbox")),
            bbox=BBoxV1.from_dict(item.get("bbox")),
            zone=ZoneV1.from_dict(item.get("zone")),
            iterations=_int(item.get("iterations"), "iterations", minimum=0),
            pareto_route_count=_int(
                item.get("pareto_route_count"), "pareto_route_count", minimum=0
            ),
            physiological_count=_int(
                item.get("physiological_count"), "physiological_count", minimum=0
            ),
            routes=tuple(
                RouteV1.from_dict(route)
                for route in _sequence(item.get("routes", []), "routes")
            ),
            rejected=tuple(
                RouteV1.from_dict(route)
                for route in _sequence(item.get("rejected", []), "rejected")
            ),
            warnings=tuple(
                _str(warning, "warnings[]", allow_empty=True)
                for warning in _sequence(item.get("warnings", []), "warnings")
            ),
            recommended_route_id=(
                None
                if item.get("recommended_route_id") is None
                else _str(item.get("recommended_route_id"), "recommended_route_id")
            ),
            truncated=_bool(item.get("truncated", False), "truncated"),
            frontier_truncated=_bool(
                item.get("frontier_truncated", False), "frontier_truncated"
            ),
            dropped_labels=_int(
                item.get("dropped_labels", 0), "dropped_labels", minimum=0
            ),
        )


