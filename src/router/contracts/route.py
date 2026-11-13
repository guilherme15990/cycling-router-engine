"""Blocos e envelope do contrato publico de rota v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from router.contracts._validation import (
    _bool,
    _coord,
    _coord_to_dict,
    _demand_fractions,
    _demand_fractions_from,
    _float,
    _fractions,
    _fractions_from,
    _int,
    _mapping,
    _round,
    _sequence,
    _str,
    _unit_distribution,
)
from router.contracts.errors import RouteContractError
from router.contracts.geo import BBox
from router.contracts.version import (
    CONTRACT_FORMAT,
    CONTRACT_VERSION,
    DEMAND_BAND_DEFINITION,
    DEMAND_BANDS,
    INTENSITY_BANDS,
)


@dataclass(frozen=True, slots=True)
class RouteRequest:
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
    def from_dict(cls, data: Any) -> "RouteRequest":
        item = _mapping(data, "request")
        raw_stops = _sequence(item.get("stops", []), "request.stops")
        return cls(
            origin=_coord(item.get("origin"), "request.origin"),
            destination=_coord(item.get("destination"), "request.destination"),
            profile=_str(item.get("profile"), "request.profile"),
            stops=tuple(_coord(stop, f"request.stops[{i}]") for i, stop in enumerate(raw_stops)),
        )


@dataclass(frozen=True, slots=True)
class RoutePoint:
    """Ponto do perfil da rota.

    ``band`` usa a escala de apresentacao de 3 cores (0 verde, 1 laranja,
    2 vermelho) derivada das faixas ``i1``..``i5``; ``None`` quando a
    intensidade nao foi calculada. ``rpe`` e a estimativa **nao calibrada**
    do trecho (Borg), nunca uma medicao.

    ``arc_demand_w``/``arc_demand_ratio``/``demand_band`` descrevem o **custo
    do terreno** no arco que termina neste ponto (potencia necessaria para
    mante-lo no ritmo de referencia). Nao dependem do esforco do ciclista.
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
    arc_demand_w: float | None = None
    arc_demand_ratio: float | None = None
    demand_band: str | None = None

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
        _float(self.arc_demand_w, "point.arc_demand_w", allow_none=True, minimum=0.0)
        _float(self.arc_demand_ratio, "point.arc_demand_ratio", allow_none=True, minimum=0.0)
        if self.demand_band is not None and self.demand_band not in DEMAND_BANDS:
            raise RouteContractError(
                f"point.demand_band desconhecida: {self.demand_band!r}"
            )

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
            "arc_demand_w": _round(self.arc_demand_w, 2),
            "arc_demand_ratio": _round(self.arc_demand_ratio, 4),
            "demand_band": self.demand_band,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RoutePoint":
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
            arc_demand_w=_float(
                item.get("arc_demand_w"), "point.arc_demand_w",
                allow_none=True, minimum=0.0,
            ),
            arc_demand_ratio=_float(
                item.get("arc_demand_ratio"), "point.arc_demand_ratio",
                allow_none=True, minimum=0.0,
            ),
            demand_band=(
                None
                if item.get("demand_band") is None
                else _str(item.get("demand_band"), "point.demand_band")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteMetrics:
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
    def from_dict(cls, data: Any) -> "RouteMetrics":
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
class RouteML:
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
    def from_dict(cls, data: Any) -> "RouteML":
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
class DemandScenarioRef:
    """Parametros declarados em que a demanda do terreno foi medida.

    ``reference_speed_kmh`` e o ritmo planejado declarado; **nao** e derivado
    de ``reference_power_w``. ``critical_power_source``/``w_prime_source`` sao
    ``measured``, ``profile_estimated`` ou ``provisional_default`` — o mesmo
    vocabulario da fisiologia, repetido aqui para o contrato nao importar o
    dominio.
    """

    reference_speed_kmh: float
    reference_power_w: float
    critical_power_w: float
    w_prime_j: float
    policy: str = "hold-reference-speed@2"
    demand_model: str = "arc-demand@1"
    critical_power_source: str = "provisional_default"
    w_prime_source: str = "provisional_default"

    def __post_init__(self) -> None:
        for name in (
            "reference_speed_kmh",
            "reference_power_w",
            "critical_power_w",
            "w_prime_j",
        ):
            _float(getattr(self, name), f"demand.scenario.{name}", minimum=0.0)
            if getattr(self, name) <= 0.0:
                raise RouteContractError(
                    f"demand.scenario.{name} deve ser positivo"
                )
        for name in (
            "policy",
            "demand_model",
            "critical_power_source",
            "w_prime_source",
        ):
            _str(getattr(self, name), f"demand.scenario.{name}")
        for name in ("critical_power_source", "w_prime_source"):
            value = getattr(self, name)
            if value not in (
                "measured",
                "profile_estimated",
                "calibrated_model_estimated",
                "provisional_default",
            ):
                raise RouteContractError(
                    f"demand.scenario.{name} desconhecida: {value!r}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_speed_kmh": round(self.reference_speed_kmh, 3),
            "reference_power_w": round(self.reference_power_w, 3),
            "critical_power_w": round(self.critical_power_w, 3),
            "w_prime_j": round(self.w_prime_j, 3),
            "policy": self.policy,
            "demand_model": self.demand_model,
            "critical_power_source": self.critical_power_source,
            "w_prime_source": self.w_prime_source,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DemandScenarioRef":
        item = _mapping(data, "demand.scenario")
        return cls(
            reference_speed_kmh=_float(
                item.get("reference_speed_kmh"),
                "demand.scenario.reference_speed_kmh",
                minimum=0.0,
            ),  # type: ignore[arg-type]
            reference_power_w=_float(
                item.get("reference_power_w"),
                "demand.scenario.reference_power_w",
                minimum=0.0,
            ),  # type: ignore[arg-type]
            critical_power_w=_float(
                item.get("critical_power_w"),
                "demand.scenario.critical_power_w",
                minimum=0.0,
            ),  # type: ignore[arg-type]
            w_prime_j=_float(
                item.get("w_prime_j"), "demand.scenario.w_prime_j", minimum=0.0
            ),  # type: ignore[arg-type]
            policy=_str(
                item.get("policy", "hold-reference-speed@2"),
                "demand.scenario.policy",
            ),
            demand_model=_str(
                item.get("demand_model", "arc-demand@1"),
                "demand.scenario.demand_model",
            ),
            critical_power_source=_str(
                item.get("critical_power_source", "provisional_default"),
                "demand.scenario.critical_power_source",
            ),
            w_prime_source=_str(
                item.get("w_prime_source", "provisional_default"),
                "demand.scenario.w_prime_source",
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteDemand:
    """Custo do terreno ao longo da rota, no ritmo de referencia.

    ``distance_fraction_by_band`` e ``distance_by_band_m`` usam ``d1``..``d5``;
    ``distance_by_grade_bin_m`` usa os tres baldes ``<=4%``, ``4-8%``, ``>8%``
    (descidas contam no primeiro). Nada aqui e uma medida de esforco.
    """

    scenario: DemandScenarioRef
    mean_demand_power_w: float
    distance_fraction_by_band: dict[str, float]
    distance_by_band_m: dict[str, float]
    distance_by_grade_bin_m: tuple[float, float, float]
    band_definition: str = DEMAND_BAND_DEFINITION

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, DemandScenarioRef):
            raise RouteContractError("demand.scenario deve ser um DemandScenarioRef")
        _float(
            self.mean_demand_power_w,
            "demand.mean_demand_power_w",
            minimum=0.0,
        )
        _demand_fractions(
            self.distance_fraction_by_band, "demand.distance_fraction_by_band"
        )
        _demand_fractions(self.distance_by_band_m, "demand.distance_by_band_m")
        if len(self.distance_by_grade_bin_m) != 3:
            raise RouteContractError(
                "demand.distance_by_grade_bin_m deve ter 3 valores"
            )
        for value in self.distance_by_grade_bin_m:
            _float(
                value, "demand.distance_by_grade_bin_m[]", minimum=0.0
            )
        _str(self.band_definition, "demand.band_definition")

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario.to_dict(),
            "mean_demand_power_w": round(self.mean_demand_power_w, 3),
            "distance_fraction_by_band": {
                name: round(value, 4)
                for name, value in self.distance_fraction_by_band.items()
            },
            "distance_by_band_m": {
                name: round(value, 3)
                for name, value in self.distance_by_band_m.items()
            },
            "distance_by_grade_bin_m": [
                round(value, 3) for value in self.distance_by_grade_bin_m
            ],
            "band_definition": self.band_definition,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteDemand":
        item = _mapping(data, "demand")
        raw_grade = _sequence(
            item.get("distance_by_grade_bin_m", [0.0, 0.0, 0.0]),
            "demand.distance_by_grade_bin_m",
        )
        if len(raw_grade) != 3:
            raise RouteContractError(
                "demand.distance_by_grade_bin_m deve ter 3 valores"
            )
        return cls(
            scenario=DemandScenarioRef.from_dict(item.get("scenario")),
            mean_demand_power_w=_float(
                item.get("mean_demand_power_w"),
                "demand.mean_demand_power_w",
                minimum=0.0,
            ),  # type: ignore[arg-type]
            distance_fraction_by_band=_demand_fractions_from(
                item.get("distance_fraction_by_band", {}),
                "demand.distance_fraction_by_band",
            ),
            distance_by_band_m=_demand_fractions_from(
                item.get("distance_by_band_m", {}),
                "demand.distance_by_band_m",
            ),
            distance_by_grade_bin_m=(
                _float(
                    raw_grade[0], "demand.distance_by_grade_bin_m[0]", minimum=0.0
                ),  # type: ignore[arg-type]
                _float(
                    raw_grade[1], "demand.distance_by_grade_bin_m[1]", minimum=0.0
                ),  # type: ignore[arg-type]
                _float(
                    raw_grade[2], "demand.distance_by_grade_bin_m[2]", minimum=0.0
                ),  # type: ignore[arg-type]
            ),
            band_definition=_str(
                item.get("band_definition", DEMAND_BAND_DEFINITION),
                "demand.band_definition",
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteTelemetry:
    """Observaveis do esforco e do traçado, para o cartao de rota.

    ``rpe_band`` usa ``i1``..``i5``; ``rpe_label`` e o rotulo verbal do Borg.
    ``inertia_fraction`` e a fracao da distancia em que a velocidade de saida
    do arco nao caiu mais que 0.2 m/s. ``arterial_entries`` conta **entradas**
    em vias arteriais (trechos consecutivos contam uma vez).
    ``heart_rate_zones`` fica ``None`` quando nao existe metodo fisiologico
    real — o campo existe para nao inventar zonas cardiacas.
    """

    policy: str
    mean_rpe: float
    peak_rpe: float
    rpe_band: str
    rpe_label: str
    inertia_fraction: float
    arterial_entries: int
    w_prime_start_j: float | None = None
    w_prime_end_j: float | None = None
    w_prime_is_provisional: bool = True
    heart_rate_zones: dict[str, float] | None = None
    heart_rate_zone_definition: str | None = None

    def __post_init__(self) -> None:
        _str(self.policy, "telemetry.policy")
        for name in ("mean_rpe", "peak_rpe"):
            value = _float(getattr(self, name), f"telemetry.{name}", minimum=0.0)
            assert value is not None
            if value > 20.0:
                raise RouteContractError(
                    f"telemetry.{name} deve estar em [0, 20] (Borg)"
                )
        if self.rpe_band not in INTENSITY_BANDS:
            raise RouteContractError(
                f"telemetry.rpe_band desconhecida: {self.rpe_band!r}"
            )
        _str(self.rpe_label, "telemetry.rpe_label")
        inertia = _float(
            self.inertia_fraction, "telemetry.inertia_fraction", minimum=0.0
        )
        assert inertia is not None
        if inertia > 1.0:
            raise RouteContractError("telemetry.inertia_fraction deve estar em [0, 1]")
        _int(self.arterial_entries, "telemetry.arterial_entries", minimum=0)
        _float(self.w_prime_start_j, "telemetry.w_prime_start_j", allow_none=True, minimum=0.0)
        _float(self.w_prime_end_j, "telemetry.w_prime_end_j", allow_none=True, minimum=0.0)
        _bool(self.w_prime_is_provisional, "telemetry.w_prime_is_provisional")
        if self.heart_rate_zones is not None:
            _unit_distribution(
                self.heart_rate_zones, "telemetry.heart_rate_zones"
            )
            _str(
                self.heart_rate_zone_definition,
                "telemetry.heart_rate_zone_definition",
            )
        elif self.heart_rate_zone_definition is not None:
            raise RouteContractError(
                "telemetry.heart_rate_zone_definition exige heart_rate_zones"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "mean_rpe": round(self.mean_rpe, 2),
            "peak_rpe": round(self.peak_rpe, 2),
            "rpe_band": self.rpe_band,
            "rpe_label": self.rpe_label,
            "inertia_fraction": round(self.inertia_fraction, 4),
            "arterial_entries": self.arterial_entries,
            "w_prime_start_j": _round(self.w_prime_start_j, 3),
            "w_prime_end_j": _round(self.w_prime_end_j, 3),
            "w_prime_is_provisional": self.w_prime_is_provisional,
            "heart_rate_zones": (
                None
                if self.heart_rate_zones is None
                else {
                    name: round(value, 4)
                    for name, value in self.heart_rate_zones.items()
                }
            ),
            "heart_rate_zone_definition": self.heart_rate_zone_definition,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RouteTelemetry":
        item = _mapping(data, "telemetry")
        raw_zones = item.get("heart_rate_zones")
        return cls(
            policy=_str(item.get("policy"), "telemetry.policy"),
            mean_rpe=_float(item.get("mean_rpe"), "telemetry.mean_rpe", minimum=0.0),  # type: ignore[arg-type]
            peak_rpe=_float(item.get("peak_rpe"), "telemetry.peak_rpe", minimum=0.0),  # type: ignore[arg-type]
            rpe_band=_str(item.get("rpe_band"), "telemetry.rpe_band"),
            rpe_label=_str(item.get("rpe_label"), "telemetry.rpe_label"),
            inertia_fraction=_float(
                item.get("inertia_fraction"),
                "telemetry.inertia_fraction",
                minimum=0.0,
            ),  # type: ignore[arg-type]
            arterial_entries=_int(
                item.get("arterial_entries"),
                "telemetry.arterial_entries",
                minimum=0,
            ),  # type: ignore[arg-type]
            w_prime_start_j=_float(
                item.get("w_prime_start_j"),
                "telemetry.w_prime_start_j",
                allow_none=True,
                minimum=0.0,
            ),
            w_prime_end_j=_float(
                item.get("w_prime_end_j"),
                "telemetry.w_prime_end_j",
                allow_none=True,
                minimum=0.0,
            ),
            w_prime_is_provisional=_bool(
                item.get("w_prime_is_provisional", True),
                "telemetry.w_prime_is_provisional",
            ),
            heart_rate_zones=(
                None
                if raw_zones is None
                else _unit_distribution(raw_zones, "telemetry.heart_rate_zones")
            ),
            heart_rate_zone_definition=(
                None
                if item.get("heart_rate_zone_definition") is None
                else _str(
                    item.get("heart_rate_zone_definition"),
                    "telemetry.heart_rate_zone_definition",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class Reason:
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
    def from_dict(cls, data: Any) -> "Reason":
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
class RouteSafety:
    """Estado de seguranca da rota (ADR-029)."""

    state: str
    eligible: bool
    reasons: tuple[Reason, ...] = ()
    advisories: tuple[Reason, ...] = ()

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
    def from_dict(cls, data: Any) -> "RouteSafety":
        item = _mapping(data, "safety")
        return cls(
            state=_str(item.get("state"), "safety.state"),
            eligible=_bool(item.get("eligible"), "safety.eligible"),
            reasons=tuple(
                Reason.from_dict(reason)
                for reason in _sequence(item.get("reasons", []), "safety.reasons")
            ),
            advisories=tuple(
                Reason.from_dict(reason)
                for reason in _sequence(item.get("advisories", []), "safety.advisories")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteProvenance:
    """Como a rota nasceu (gerador/perfil)."""

    kind: str
    profile: str

    def __post_init__(self) -> None:
        _str(self.kind, "provenance.kind")
        _str(self.profile, "provenance.profile")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "profile": self.profile}

    @classmethod
    def from_dict(cls, data: Any) -> "RouteProvenance":
        item = _mapping(data, "provenance")
        return cls(
            kind=_str(item.get("kind"), "provenance.kind"),
            profile=_str(item.get("profile"), "provenance.profile"),
        )


@dataclass(frozen=True, slots=True)
class Route:
    """Rota no contrato publico."""

    id: str
    profile: str
    recommended: bool
    geometry: tuple[RoutePoint, ...]
    metrics: RouteMetrics
    ml: RouteML
    safety: RouteSafety
    provenance: RouteProvenance
    demand: "RouteDemand | None" = None
    telemetry: "RouteTelemetry | None" = None

    def __post_init__(self) -> None:
        _str(self.id, "route.id")
        _str(self.profile, "route.profile")
        _bool(self.recommended, "route.recommended")
        if self.demand is not None and not isinstance(self.demand, RouteDemand):
            raise RouteContractError("route.demand deve ser um RouteDemand ou None")
        if self.telemetry is not None and not isinstance(
            self.telemetry, RouteTelemetry
        ):
            raise RouteContractError(
                "route.telemetry deve ser um RouteTelemetry ou None"
            )
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
            "demand": None if self.demand is None else self.demand.to_dict(),
            "telemetry": (
                None if self.telemetry is None else self.telemetry.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Route":
        item = _mapping(data, "route")
        raw_demand = item.get("demand")
        raw_telemetry = item.get("telemetry")
        return cls(
            id=_str(item.get("id"), "route.id"),
            profile=_str(item.get("profile"), "route.profile"),
            recommended=_bool(item.get("recommended"), "route.recommended"),
            geometry=tuple(
                RoutePoint.from_dict(point)
                for point in _sequence(item.get("geometry", []), "route.geometry")
            ),
            metrics=RouteMetrics.from_dict(item.get("metrics")),
            ml=RouteML.from_dict(item.get("ml")),
            safety=RouteSafety.from_dict(item.get("safety")),
            provenance=RouteProvenance.from_dict(item.get("provenance")),
            demand=(
                None if raw_demand is None else RouteDemand.from_dict(raw_demand)
            ),
            telemetry=(
                None
                if raw_telemetry is None
                else RouteTelemetry.from_dict(raw_telemetry)
            ),
        )


@dataclass(frozen=True, slots=True)
class Zone:
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
    def from_dict(cls, data: Any) -> "Zone":
        item = _mapping(data, "zone")
        return cls(
            nodes=_int(item.get("nodes"), "zone.nodes", minimum=0),
            arcs=_int(item.get("arcs"), "zone.arcs", minimum=0),
            graph_nodes=_int(item.get("graph_nodes"), "zone.graph_nodes", minimum=0),
            graph_arcs=_int(item.get("graph_arcs"), "zone.graph_arcs", minimum=0),
        )


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Envelope versionado de uma consulta de rotas.

    ``routes`` contem apenas rotas elegiveis; ``rejected`` preserva as
    rejeitadas para auditoria. ``recommended_route_id`` e sempre uma rota de
    ``routes`` (ou ``None``).
    """

    request: RouteRequest
    reached: bool
    search_bbox: BBox
    bbox: BBox
    zone: Zone
    iterations: int
    pareto_route_count: int
    physiological_count: int
    routes: tuple[Route, ...] = ()
    rejected: tuple[Route, ...] = ()
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
    def from_dict(cls, data: Any) -> "RoutePlan":
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
            request=RouteRequest.from_dict(item.get("request")),
            reached=_bool(item.get("reached"), "reached"),
            search_bbox=BBox.from_dict(item.get("search_bbox")),
            bbox=BBox.from_dict(item.get("bbox")),
            zone=Zone.from_dict(item.get("zone")),
            iterations=_int(item.get("iterations"), "iterations", minimum=0),
            pareto_route_count=_int(
                item.get("pareto_route_count"), "pareto_route_count", minimum=0
            ),
            physiological_count=_int(
                item.get("physiological_count"), "physiological_count", minimum=0
            ),
            routes=tuple(
                Route.from_dict(route)
                for route in _sequence(item.get("routes", []), "routes")
            ),
            rejected=tuple(
                Route.from_dict(route)
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


