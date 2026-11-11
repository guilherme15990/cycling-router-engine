"""Features de esforco derivadas da fisica (ADR-025, incremento 6).

Este modulo **nao** reimplementa fisica nem ML. Ele agrega, de forma
deterministica, resultados ja produzidos pela engine: recebe um
``TraversalPath`` (saida de ``PhysicalTraversal.traverse_path``) e devolve um
``EffortFeatures`` imutavel. Como depende **apenas** do resultado fisico, o
mesmo contrato alimenta qualquer rota — tradicional ou gerada por ML.

Dados ausentes sao representados **explicitamente** como ``None``, nunca
inventados:

* ``peak_power_w`` / ``power_variability`` — o modelo aplica potencia de
  pedivela **constante** em cada arco e nao retem a serie por passo; nao ha
  como derivar um pico ou uma variabilidade sem inventar granularidade;
* ``metabolic_energy_j`` — nao calibrado (ADR-025, decisao 3): ``None``, nunca
  ``0``;
* ``battery_energy_j`` — ``None`` enquanto a bicicleta for convencional.

A qualidade altimetrica (``ElevationUncertainty``), o status da simulacao
(``SimulationStatus``) e a limitacao numerica conhecida (regime regularizado
``MIN_SPEED_MPS``) sao **propagados** para que a precisao nao seja
superestimada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ._validation import finite, non_negative
from .models import ElevationUncertainty, SimulationStatus, TraversalPath

#: Versao do contrato de features. Aumentar apenas com mudanca incompativel.
EFFORT_FEATURES_VERSION = "1"

#: Campos que podem legitimamente ser ``None`` por falta de dados.
_OPTIONAL_FIELDS = (
    "peak_power_w",
    "power_variability",
    "battery_energy_j",
    "metabolic_energy_j",
    "elevation_coverage",
    "observed_elevation_coverage",
    "elevation_uncertainty",
)


@dataclass(frozen=True, slots=True)
class EffortFeatures:
    """Caracteristicas de esforco de uma rota, derivadas da fisica.

    Contrato **imutavel** e **versionado** (``schema_version``). Unidades
    explicitas no nome de cada campo. Nenhum campo fisiologico (Borg, FC,
    fadiga) pertence a este contrato.

    Energia: ``mechanical_work_j`` e o trabalho na **roda**;
    ``rider_work_j`` e o trabalho no **pedivela** (crank). ``mean_power_w`` e
    a potencia media no pedivela durante ``duration_s``. Os campos de perda
    (``aero_loss_j``/``rolling_loss_j``) sao **assinados** — um vento de cauda
    pode tornar a perda aerodinamica negativa.
    """

    duration_s: float
    distance_m: float
    average_speed_mps: float
    mechanical_work_j: float
    rider_work_j: float
    mean_power_w: float
    peak_power_w: float | None
    power_variability: float | None
    climbing_m: float
    descending_m: float
    elevation_coverage: float | None
    observed_elevation_coverage: float | None
    braking_energy_j: float
    aero_loss_j: float
    rolling_loss_j: float
    battery_energy_j: float | None
    metabolic_energy_j: float | None
    result_status: SimulationStatus
    used_min_speed_regularization: bool
    elevation_uncertainty: ElevationUncertainty | None

    #: Versao do contrato (constante da classe; nao e um dado da rota).
    schema_version: ClassVar[str] = EFFORT_FEATURES_VERSION

    def __post_init__(self) -> None:
        non_negative("duration_s", self.duration_s)
        non_negative("distance_m", self.distance_m)
        non_negative("average_speed_mps", self.average_speed_mps)
        non_negative("mechanical_work_j", self.mechanical_work_j)
        non_negative("rider_work_j", self.rider_work_j)
        non_negative("mean_power_w", self.mean_power_w)
        non_negative("climbing_m", self.climbing_m)
        non_negative("descending_m", self.descending_m)
        non_negative("braking_energy_j", self.braking_energy_j)
        non_negative("rolling_loss_j", self.rolling_loss_j)
        # A perda aerodinamica e assinada (vento de cauda pode ser negativo).
        finite("aero_loss_j", self.aero_loss_j)
        for name in ("peak_power_w", "power_variability", "battery_energy_j",
                     "metabolic_energy_j"):
            value = getattr(self, name)
            if value is not None:
                non_negative(name, value)
        for name in ("elevation_coverage", "observed_elevation_coverage"):
            coverage = getattr(self, name)
            if coverage is None:
                continue
            finite(name, coverage)
            if not 0.0 <= coverage <= 1.0:
                raise ValueError(
                    f"{name} deve estar em [0, 1] (recebido {coverage!r})"
                )

    @property
    def unavailable_fields(self) -> tuple[str, ...]:
        """Nomes dos campos indisponiveis (``None``) por falta de dados."""
        return tuple(
            name for name in _OPTIONAL_FIELDS if getattr(self, name) is None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "duration_s": round(self.duration_s, 3),
            "distance_m": round(self.distance_m, 3),
            "average_speed_mps": round(self.average_speed_mps, 4),
            "mechanical_work_j": round(self.mechanical_work_j, 3),
            "rider_work_j": round(self.rider_work_j, 3),
            "mean_power_w": round(self.mean_power_w, 3),
            "peak_power_w": (
                None if self.peak_power_w is None else round(self.peak_power_w, 3)
            ),
            "power_variability": (
                None
                if self.power_variability is None
                else round(self.power_variability, 4)
            ),
            "climbing_m": round(self.climbing_m, 3),
            "descending_m": round(self.descending_m, 3),
            "elevation_coverage": (
                None
                if self.elevation_coverage is None
                else round(self.elevation_coverage, 4)
            ),
            "observed_elevation_coverage": (
                None
                if self.observed_elevation_coverage is None
                else round(self.observed_elevation_coverage, 4)
            ),
            "braking_energy_j": round(self.braking_energy_j, 3),
            "aero_loss_j": round(self.aero_loss_j, 3),
            "rolling_loss_j": round(self.rolling_loss_j, 3),
            "battery_energy_j": (
                None
                if self.battery_energy_j is None
                else round(self.battery_energy_j, 3)
            ),
            "metabolic_energy_j": (
                None
                if self.metabolic_energy_j is None
                else round(self.metabolic_energy_j, 3)
            ),
            "result_status": self.result_status.value,
            "used_min_speed_regularization": self.used_min_speed_regularization,
            "unavailable_fields": list(self.unavailable_fields),
            "elevation_uncertainty": (
                None
                if self.elevation_uncertainty is None
                else self.elevation_uncertainty.to_dict()
            ),
        }


def extract_effort_features(path: TraversalPath) -> EffortFeatures:
    """Extrai ``EffortFeatures`` de um resultado fisico de rota.

    A extracao e **deterministica** e nao depende de ML nem do algoritmo de
    roteamento: a mesma ``TraversalPath`` produz sempre as mesmas features.

    Args:
        path: Resultado fisico de percorrer os arcos da rota em continuidade.

    Returns:
        ``EffortFeatures`` com unidades explicitas, qualidade altimetrica,
        status da simulacao e limitacao numerica propagados. Campos sem
        dados suficientes ficam ``None`` (ver ``unavailable_fields``).
    """
    metrics = path.metrics
    duration_s = max(0.0, float(metrics.time_s))
    distance_m = max(0.0, float(metrics.distance_m))
    rider_work_j = max(0.0, float(metrics.energy_j))
    mechanical_work_j = max(0.0, float(metrics.mechanical_energy_j))

    # Potencias derivadas apenas quando ha tempo > 0 (nunca divisao por zero).
    mean_power_w = rider_work_j / duration_s if duration_s > 0.0 else 0.0
    average_speed_mps = distance_m / duration_s if duration_s > 0.0 else 0.0

    uncertainty = path.elevation_uncertainty
    elevation_coverage = None if uncertainty is None else uncertainty.coverage
    if uncertainty is None or uncertainty.total_distance_m <= 0.0:
        observed_coverage = None
    else:
        observed_coverage = min(
            1.0, uncertainty.observed_distance_m / uncertainty.total_distance_m
        )

    return EffortFeatures(
        duration_s=duration_s,
        distance_m=distance_m,
        average_speed_mps=average_speed_mps,
        mechanical_work_j=mechanical_work_j,
        rider_work_j=rider_work_j,
        mean_power_w=mean_power_w,
        # Nao ha serie por passo na engine: pico e variabilidade ficam
        # explicitamente indisponiveis (nada e inferido).
        peak_power_w=None,
        power_variability=None,
        climbing_m=max(0.0, float(metrics.ascent_m)),
        descending_m=max(0.0, float(metrics.descent_m)),
        elevation_coverage=elevation_coverage,
        observed_elevation_coverage=observed_coverage,
        braking_energy_j=max(0.0, float(metrics.braking_energy_j)),
        aero_loss_j=float(metrics.aero_loss_j),
        rolling_loss_j=max(0.0, float(metrics.rolling_loss_j)),
        battery_energy_j=(
            None
            if metrics.battery_energy_j is None
            else float(metrics.battery_energy_j)
        ),
        # Nao calibrado: a ausencia e None, nunca 0 (ADR-025, decisao 3).
        metabolic_energy_j=None,
        result_status=path.result_status,
        used_min_speed_regularization=bool(path.used_min_speed_regularization),
        elevation_uncertainty=uncertainty,
    )
