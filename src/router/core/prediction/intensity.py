"""Intensidade de rota: mecânica, resposta provisória e faixas de Borg.

O módulo orquestra a separação do ADR-034:

1. **física da rota** — ``RoutePhysics`` (distância, duração, trabalho);
2. **intensidade mecânica** — ``P / P_ref``, sem massa de referência
   artificial, sem penalidade dupla de inclinação e sem tráfego;
3. **resposta provisória** — RPE, FC e zonas, rotulados como provisórios até
   existir calibração individual.

A estimativa continua **não calibrada** (``calibrated = False``) e **nunca** é
uma medição. Ela também não preenche ``PhysiologicalPrediction``: essa camada
permanece reservada a modelos calibrados.

A inclinação não é somada ao RPE porque a gravidade já está no trabalho de
pedivela. O tráfego é um descritor separado (``traffic.py``) e não altera a
intensidade mecânica nem o RPE.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from router.core.physics.models import TraversalPath
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    BorgScale,
    RiderModel,
)

from .mechanical import (
    DEFAULT_PROPULSION,
    MechanicalIntensity,
    PropulsionModel,
    RouteSegment,
    estimate_mechanical_intensity,
)
from .response import (
    DEFAULT_RESPONSE_CONFIG,
    ResponseConfig,
    RiderResponseEstimate,
    estimate_rider_response,
)
from .traffic import hw_stress

#: Identificador estável do novo método.
INTENSITY_MODEL_ID = "mechanical-response"

#: Versão do método de estimativa de intensidade.
INTENSITY_MODEL_VERSION = "2"

#: Definição versionada das faixas de intensidade (derivadas do Borg 6–20).
#: ``@2`` corrige o primeiro corte (``9``, não ``10``) e passa a ser a **única**
#: definição de faixa do sistema (o app deriva as cores daqui). É uma
#: divergência **deliberada** do corte auditado do `toporide` (ver
#: ``docs/adr/028-auditoria-rpe-toporide.md``).
INTENSITY_BAND_DEFINITION = "borg-intensity-bands@2"

#: Nomes das cinco faixas de intensidade (não são zonas cardíacas).
INTENSITY_BAND_NAMES: tuple[str, ...] = ("i1", "i2", "i3", "i4", "i5")

#: Cortes superiores (Borg) de cada faixa de intensidade. O corte é inclusivo:
#: ``rpe <= corte`` cai na faixa. É a **única** definição de faixa do sistema —
#: a escala de cores do app deriva daqui, sem cortes duplicados.
INTENSITY_BAND_EDGES: tuple[float, ...] = (9.0, 12.0, 14.0, 16.0)

#: Rótulos de apresentação das faixas de intensidade (do mais leve ao máximo).
INTENSITY_BAND_LABELS: dict[str, str] = {
    "i1": "muito leve",
    "i2": "leve",
    "i3": "moderado",
    "i4": "intenso",
    "i5": "muito intenso",
}

#: Definição versionada das faixas de **demanda** (custo do terreno relativo à
#: potência de referência). Não são zonas de esforço: descrevem o que o
#: terreno pede, não o que o ciclista gastou.
DEMAND_BAND_DEFINITION = "demand-ratio-bands@1"

#: Nomes das cinco faixas de demanda.
DEMAND_BAND_NAMES: tuple[str, ...] = ("d1", "d2", "d3", "d4", "d5")

#: Cortes superiores (razão ``P_req / P_ref``) de cada faixa de demanda.
#: Geométricos: cada faixa dobra o custo relativo da anterior na primeira
#: metade e cresce mais devagar na segunda.
DEMAND_BAND_EDGES: tuple[float, ...] = (0.50, 0.80, 1.25, 2.00)

#: Rótulos de apresentação das faixas de demanda.
DEMAND_BAND_LABELS: dict[str, str] = {
    "d1": "leve",
    "d2": "moderada",
    "d3": "forte",
    "d4": "muito forte",
    "d5": "severa",
}

#: Rótulos verbais do Borg (6–20) usados no cartão de esforço.
RPE_LABELS: tuple[tuple[float, str], ...] = (
    (7.0, "muito leve"),
    (9.0, "leve"),
    (11.0, "relativamente leve"),
    (13.0, "um pouco forte"),
    (15.0, "forte"),
    (17.0, "muito forte"),
    (19.0, "extremamente forte"),
    (20.0, "máximo"),
)

#: Nome histórico do tipo de trecho.
IntensitySegment = RouteSegment

#: Nomes históricos para a configuração da resposta provisória.
IntensityCoefficients = ResponseConfig
DEFAULT_INTENSITY_COEFFICIENTS = DEFAULT_RESPONSE_CONFIG


def band_for_rpe(rpe: float) -> str:
    """Nome da faixa de intensidade para um RPE (Borg)."""
    for name, edge in zip(INTENSITY_BAND_NAMES, INTENSITY_BAND_EDGES):
        if rpe <= edge:
            return name
    return INTENSITY_BAND_NAMES[-1]


def demand_band_for_ratio(ratio: float) -> str:
    """Nome da faixa de demanda para ``P_req / P_ref``.

    Uma razão de ``1.0`` significa "o terreno pede exatamente a potência de
    referência declarada". Não é uma medida de esforço: o mesmo arco tem a
    mesma demanda para ciclistas diferentes.
    """
    if not math.isfinite(ratio) or ratio < 0.0:
        raise ValueError(f"ratio deve ser finito e nao negativo (recebido {ratio!r})")
    for name, edge in zip(DEMAND_BAND_NAMES, DEMAND_BAND_EDGES):
        if ratio <= edge:
            return name
    return DEMAND_BAND_NAMES[-1]


def rpe_label(rpe: float) -> str:
    """Rótulo verbal do Borg para um RPE (o primeiro corte que o contém)."""
    for edge, label in RPE_LABELS:
        if rpe <= edge:
            return label
    return RPE_LABELS[-1][1]


@dataclass(frozen=True, slots=True)
class IntensityEstimate:
    """Estimativa de intensidade de uma rota — computacional e não calibrada.

    ``mean_rpe`` é a média **ponderada por tempo** (RPE é um estado temporal).
    A média ponderada por distância é exposta separadamente para auditoria e
    para consumidores que precisem do descritor antigo. ``peak_rpe`` é o pico
    de uma janela móvel fixa, robusto à segmentação arbitrária da malha.
    """

    borg_scale: BorgScale
    mechanical: MechanicalIntensity
    rider_response: RiderResponseEstimate
    band_distance_fraction: dict[str, float]
    band_duration_fraction: dict[str, float] | None
    segment_bands: tuple[str, ...]
    #: Identidade do metodo e versao das faixas: vao para ``RouteML`` e
    #: permitem ao ``multi_leg`` detectar pernas com metodos diferentes.
    method_id: str = INTENSITY_MODEL_ID
    model_version: str = INTENSITY_MODEL_VERSION
    band_definition: str = INTENSITY_BAND_DEFINITION
    traffic_stress_mean: float | None = None

    #: Marcação explícita: **não** calibrada fisiologicamente. O contrato
    #: ``RouteML`` exige o valor; a estimativa nunca é uma medição.
    calibrated: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if not isinstance(self.borg_scale, BorgScale):
            raise ValueError("borg_scale deve ser um BorgScale")
        if not isinstance(self.mechanical, MechanicalIntensity):
            raise ValueError("mechanical deve ser um MechanicalIntensity")
        if not isinstance(self.rider_response, RiderResponseEstimate):
            raise ValueError("rider_response deve ser um RiderResponseEstimate")
        _check_fraction_dict("band_distance_fraction", self.band_distance_fraction)
        if self.band_duration_fraction is not None:
            _check_fraction_dict(
                "band_duration_fraction", self.band_duration_fraction
            )
        if len(self.segment_bands) != len(self.rider_response.segment_rpes):
            raise ValueError(
                "segment_bands deve ter o mesmo tamanho de segment_rpes "
                f"({len(self.segment_bands)} != "
                f"{len(self.rider_response.segment_rpes)})"
            )
        for band in self.segment_bands:
            if band not in INTENSITY_BAND_NAMES:
                raise ValueError(f"faixa de intensidade desconhecida: {band!r}")
        if self.traffic_stress_mean is not None:
            if (
                not math.isfinite(self.traffic_stress_mean)
                or self.traffic_stress_mean < 0.0
            ):
                raise ValueError(
                    "traffic_stress_mean deve ser finito e nao negativo"
                )

    @property
    def is_measurement(self) -> bool:
        """Sempre ``False``: a estimativa não é uma medição."""
        return False

    @property
    def mean_rpe(self) -> float:
        """RPE médio ponderado por tempo."""
        return self.rider_response.mean_rpe_time_weighted

    @property
    def mean_rpe_time_weighted(self) -> float:
        return self.rider_response.mean_rpe_time_weighted

    @property
    def mean_rpe_distance_weighted(self) -> float:
        return self.rider_response.mean_rpe_distance_weighted

    @property
    def peak_rpe(self) -> float:
        return self.rider_response.peak_rpe

    @property
    def segment_rpes(self) -> tuple[float, ...]:
        return self.rider_response.segment_rpes

    @property
    def distance_m(self) -> float:
        return self.mechanical.physics.distance_m

    @property
    def duration_s(self) -> float:
        return self.mechanical.physics.duration_s

    @property
    def relative_intensity(self) -> float:
        return self.mechanical.relative_intensity

    @property
    def estimated_mean_heart_rate_bpm(self) -> float | None:
        return self.rider_response.mean_heart_rate_bpm

    @property
    def estimated_peak_heart_rate_bpm(self) -> float | None:
        return self.rider_response.peak_heart_rate_bpm

    @property
    def heart_rate_zone_distribution(self) -> dict[str, float] | None:
        return self.rider_response.heart_rate_zone_distribution

    @property
    def green_fraction(self) -> float:
        """Fração da distância em faixas baixas (``i1``+``i2``, RPE <= 12)."""
        return self.band_distance_fraction.get("i1", 0.0) + (
            self.band_distance_fraction.get("i2", 0.0)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "calibrated": self.calibrated,
            "is_measurement": self.is_measurement,
            "method_id": self.method_id,
            "model_version": self.model_version,
            "band_definition": self.band_definition,
            "borg_scale": self.borg_scale.value,
            "mean_rpe": round(self.mean_rpe, 2),
            "mean_rpe_time_weighted": round(self.mean_rpe_time_weighted, 2),
            "mean_rpe_distance_weighted": round(
                self.mean_rpe_distance_weighted, 2
            ),
            "peak_rpe": round(self.peak_rpe, 2),
            "distance_m": round(self.distance_m, 3),
            "duration_s": round(self.duration_s, 3),
            "green_fraction": round(self.green_fraction, 4),
            "traffic_stress_mean": (
                None
                if self.traffic_stress_mean is None
                else round(self.traffic_stress_mean, 4)
            ),
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
            "segment_rpes": [round(value, 2) for value in self.segment_rpes],
            "segment_bands": list(self.segment_bands),
            "mechanical": self.mechanical.to_dict(),
            "rider_response": self.rider_response.to_dict(),
        }

    def to_feature_row(self) -> dict[str, float | None]:
        """Features estáveis para ML, derivadas do artefato principal."""
        row: dict[str, float | None] = {
            "mechanical_relative_intensity": self.mechanical.relative_intensity,
            "mechanical_rms_relative_intensity": (
                self.mechanical.rms_relative_intensity
            ),
            "mechanical_mean_power_w": self.mechanical.mean_power_w,
            "mechanical_rms_power_w": self.mechanical.rms_power_w,
            "mechanical_variability_index": self.mechanical.variability_index,
            "traffic_stress_mean": self.traffic_stress_mean,
        }
        row.update(self.rider_response.to_feature_row())
        return row


def _check_fraction_dict(name: str, values: dict[str, float]) -> None:
    total = 0.0
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name}: nome de faixa invalido ({key!r})")
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name}[{key!r}] deve ser finito e nao negativo")
        total += value
    if values and abs(total - 1.0) > 1e-6:
        raise ValueError(f"{name} deve somar 1 (recebido {total!r})")


def segment_rpe(
    segment: RouteSegment,
    *,
    rider: RiderModel,
    propulsion: PropulsionModel = DEFAULT_PROPULSION,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    config: ResponseConfig = DEFAULT_RESPONSE_CONFIG,
    pacing_owns_fatigue: bool = False,
) -> float:
    """RPE provisório de um único trecho, dado um ``RiderModel``."""
    mechanical = estimate_mechanical_intensity(
        (segment,), rider=rider, propulsion=propulsion
    )
    response = estimate_rider_response(
        mechanical,
        rider=rider,
        borg_scale=borg_scale,
        config=config,
        pacing_owns_fatigue=pacing_owns_fatigue,
    )
    return response.segment_rpes[0]


def estimate_intensity(
    segments: Sequence[RouteSegment],
    *,
    rider: RiderModel,
    propulsion: PropulsionModel = DEFAULT_PROPULSION,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    config: ResponseConfig = DEFAULT_RESPONSE_CONFIG,
    pacing_owns_fatigue: bool = False,
) -> IntensityEstimate:
    """Estima intensidade mecânica, RPE e resposta provisória de uma rota.

    A função é determinística e sempre produz um artefato, inclusive sem
    trechos. O tráfego é agregado apenas como descritor; ele não altera a
    intensidade mecânica nem o RPE.

    ``pacing_owns_fatigue`` repassa à resposta que o orçamento de ``W'`` já foi
    contabilizado pela política de ritmo (ver ``core.physics.pacing``).
    """
    segment_tuple = tuple(segments)
    for segment in segment_tuple:
        if not isinstance(segment, RouteSegment):
            raise TypeError("segments deve conter RouteSegment")

    mechanical = estimate_mechanical_intensity(
        segment_tuple, rider=rider, propulsion=propulsion
    )
    response = estimate_rider_response(
        mechanical,
        rider=rider,
        borg_scale=borg_scale,
        config=config,
        pacing_owns_fatigue=pacing_owns_fatigue,
    )

    distance_by_band: dict[str, float] = {name: 0.0 for name in INTENSITY_BAND_NAMES}
    duration_by_band: dict[str, float] = {name: 0.0 for name in INTENSITY_BAND_NAMES}
    total_distance = 0.0
    total_duration = 0.0
    traffic_distance = 0.0
    segment_bands: list[str] = []

    for segment, rpe in zip(segment_tuple, response.segment_rpes):
        band = band_for_rpe(rpe)
        segment_bands.append(band)
        distance_by_band[band] += segment.distance_m
        duration_by_band[band] += segment.duration_s
        total_distance += segment.distance_m
        total_duration += segment.duration_s
        traffic_distance += segment.traffic_stress * segment.distance_m

    distance_fraction = (
        {name: value / total_distance for name, value in distance_by_band.items()}
        if total_distance > 0.0
        else {}
    )
    duration_fraction = (
        {name: value / total_duration for name, value in duration_by_band.items()}
        if total_duration > 0.0
        else None
    )
    traffic_mean = (
        traffic_distance / total_distance if total_distance > 0.0 else None
    )

    return IntensityEstimate(
        borg_scale=borg_scale,
        mechanical=mechanical,
        rider_response=response,
        band_distance_fraction=distance_fraction,
        band_duration_fraction=duration_fraction,
        segment_bands=tuple(segment_bands),
        traffic_stress_mean=traffic_mean,
    )


def segments_from_traversal(
    path: TraversalPath,
    arcs: Sequence,
) -> tuple[RouteSegment, ...]:
    """Monta os trechos físicos a partir de um ``TraversalPath``.

    Cada ``TraversalResult`` vira um ``RouteSegment`` com trabalho no pedivela,
    trabalho na roda, distância, duração e descritores de elevação/tráfego.
    Requer ``arcs`` na **mesma** ordem dos resultados.
    """
    results = path.results
    if len(arcs) != len(results):
        raise ValueError(
            f"arcs ({len(arcs)}) e resultados ({len(results)}) de tamanhos diferentes"
        )
    segments: list[RouteSegment] = []
    for arc, result in zip(arcs, results):
        peak_grade = 0.0
        profile = tuple(getattr(arc, "elevation_profile", ()) or ())
        for previous, current in zip(profile, profile[1:]):
            dx = float(getattr(current, "distance_m", 0.0)) - float(getattr(previous, "distance_m", 0.0))
            if dx <= 0.0:
                continue
            z0 = getattr(previous, "elevation_m", None)
            z1 = getattr(current, "elevation_m", None)
            if z0 is not None and z1 is not None:
                peak_grade = max(peak_grade, 100.0 * (float(z1) - float(z0)) / dx)
        segments.append(
            RouteSegment(
                distance_m=max(0.0, float(result.distance_m)),
                duration_s=max(0.0, float(result.time_s)),
                rider_energy_j=max(0.0, float(result.rider_energy_j)),
                mechanical_energy_j=max(
                    0.0, float(result.mechanical_energy_j)
                ),
                ascent_m=max(0.0, float(result.ascent_m)),
                descent_m=max(0.0, float(result.descent_m)),
                traffic_stress=hw_stress(getattr(arc, "highway", None)),
                peak_grade_pct=max(0.0, peak_grade),
            )
        )
    return tuple(segments)


__all__ = [
    "INTENSITY_MODEL_ID",
    "INTENSITY_MODEL_VERSION",
    "INTENSITY_BAND_DEFINITION",
    "INTENSITY_BAND_NAMES",
    "INTENSITY_BAND_EDGES",
    "INTENSITY_BAND_LABELS",
    "DEMAND_BAND_DEFINITION",
    "DEMAND_BAND_NAMES",
    "DEMAND_BAND_EDGES",
    "DEMAND_BAND_LABELS",
    "RPE_LABELS",
    "IntensityCoefficients",
    "DEFAULT_INTENSITY_COEFFICIENTS",
    "IntensitySegment",
    "IntensityEstimate",
    "band_for_rpe",
    "demand_band_for_ratio",
    "rpe_label",
    "segment_rpe",
    "estimate_intensity",
    "segments_from_traversal",
    "hw_stress",
]
