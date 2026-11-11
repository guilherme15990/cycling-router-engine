"""Contrato de previsao fisiologica (ADR-025, decisao 03).

Uma ``PhysiologicalPrediction`` **nunca** e uma medicao: e uma estimativa
identificada, com escala explicita, versao do modelo e — quando existir —
definicao das zonas cardiacas. A ausencia de dados e representada como ``None``,
nunca inventada.

Regras do contrato:

* a escala Borg e **explicita** e o valor previsto respeita seus limites;
* uma distribuicao de tempo por zona e **nao negativa** e **soma 1**;
* ``confidence`` só pode ser numerica se um **metodo de validacao** for
  declarado — nao se atribui confianca sem calibracao;
* ``is_measurement`` e sempre ``False`` (o sistema nao pode apresentar previsao
  como medicao);
* ``status`` distingue **por que** a previsao fisiológica (RPE/FC) esta
  limitada. Quando o status nao e ``AVAILABLE``, nenhum valor de RPE/FC existe:
  a ausencia e explicita e nada e inventado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from ._validation import finite, non_negative, positive, unit_interval
from .scales import DEFAULT_BORG_SCALE, BorgScale
from .zones import validate_distribution

#: Versao do contrato de previsao fisiologica.
PHYSIOLOGY_VERSION = "1"


class PredictionStatus(str, Enum):
    """Estado da previsao **fisiologica** (RPE e zonas cardiacas).

    Descreve *por que* os valores fisiologicos existem ou nao, sem confundir
    categorias distintas:

    * ``AVAILABLE`` — um modelo calibrado produziu RPE e/ou distribuicao de
      zonas;
    * ``UNAVAILABLE`` — nenhum preditor foi configurado;
    * ``MODEL_NOT_CALIBRATED`` — ha preditor, mas o modelo fisiologico nao esta
      calibrado (nao se infere sem calibracao);
    * ``INSUFFICIENT_DATA`` — faltam entradas exigidas pelo preditor;
    * ``CONTRACT_INCOMPATIBLE`` — a versao do contrato de features nao e
      suportada.

    A energia metabolica (``PhysiologicalPrediction.metabolic_energy_j``) e uma
    estimativa **independente** deste estado: depende apenas de parametros
    explicitos, nao do modelo de RPE/FC.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MODEL_NOT_CALIBRATED = "model_not_calibrated"
    INSUFFICIENT_DATA = "insufficient_data"
    CONTRACT_INCOMPATIBLE = "contract_incompatible"

    @property
    def is_available(self) -> bool:
        """True apenas para ``AVAILABLE``."""
        return self is PredictionStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class RiderProfile:
    """Perfil fisiologico do ciclista, **parcial e opcional**.

    Todos os campos sao opcionais: a ausencia e explicita (``None``). Nenhum
    valor fisiologico e inferido a partir de dados nao fornecidos.
    """

    max_heart_rate_bpm: float | None = None
    resting_heart_rate_bpm: float | None = None
    threshold_power_w: float | None = None
    weight_kg: float | None = None

    def __post_init__(self) -> None:
        if self.max_heart_rate_bpm is not None:
            positive("max_heart_rate_bpm", self.max_heart_rate_bpm)
        if self.resting_heart_rate_bpm is not None:
            positive("resting_heart_rate_bpm", self.resting_heart_rate_bpm)
        if self.threshold_power_w is not None:
            non_negative("threshold_power_w", self.threshold_power_w)
        if self.weight_kg is not None:
            positive("weight_kg", self.weight_kg)


@dataclass(frozen=True, slots=True)
class PredictionContext:
    """Contexto opcional da predicao.

    Nada aqui e inferido: campos ausentes ficam ``None``. O contexto nao altera
    a geometria nem as restricoes da rota.
    """

    temperature_c: float | None = None
    duration_s: float | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.temperature_c is not None:
            finite("temperature_c", self.temperature_c)
        if self.duration_s is not None:
            non_negative("duration_s", self.duration_s)
        if self.notes is not None and not isinstance(self.notes, str):
            raise ValueError("notes deve ser str ou None")


@dataclass(frozen=True, slots=True)
class PhysiologicalPrediction:
    """Estimativa fisiologica identificada — nunca uma medicao.

    ``predicted_rpe`` respeita os limites de ``borg_scale``. Uma
    ``heart_rate_zone_distribution`` exige ``heart_rate_zone_definition``
    (identificacao da definicao) e soma 1. ``confidence`` numerica exige
    ``confidence_method``.

    ``status`` diz por que a previsao de RPE/FC existe ou nao. Quando
    ``status.is_available`` e ``False``, ``predicted_rpe`` e
    ``heart_rate_zone_distribution`` sao obrigatoriamente ``None`` — a
    indisponibilidade nunca vira valor inventado.
    """

    borg_scale: BorgScale
    predicted_rpe: float | None
    heart_rate_zone_distribution: dict[str, float] | None
    model_version: str
    status: PredictionStatus
    confidence: float | None = None
    confidence_method: str | None = None
    heart_rate_zone_definition: str | None = None
    prediction_interval_s: float | None = None
    metabolic_energy_j: float | None = None
    reason: str | None = None
    missing_inputs: tuple[str, ...] = ()

    #: Versao do contrato (constante da classe).
    schema_version: ClassVar[str] = PHYSIOLOGY_VERSION
    #: Natureza do dado: sempre uma previsao, nunca uma medicao.
    source: ClassVar[str] = "prediction"

    def __post_init__(self) -> None:
        if not isinstance(self.status, PredictionStatus):
            raise ValueError("status deve ser um PredictionStatus")
        if not isinstance(self.model_version, str) or not self.model_version:
            raise ValueError("PhysiologicalPrediction exige model_version nao vazia")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("reason deve ser str ou None")

        if not isinstance(self.missing_inputs, tuple):
            object.__setattr__(self, "missing_inputs", tuple(self.missing_inputs))
        for name in self.missing_inputs:
            if not isinstance(name, str) or not name:
                raise ValueError("missing_inputs deve conter nomes nao vazios")

        if self.predicted_rpe is not None:
            self.borg_scale.require_valid(self.predicted_rpe)
        if self.confidence is not None:
            unit_interval("confidence", self.confidence)
            if not isinstance(self.confidence_method, str) or not (
                self.confidence_method
            ):
                raise ValueError(
                    "confidence numerica exige confidence_method "
                    "(nao se atribui confianca sem metodo de validacao)"
                )
        if self.heart_rate_zone_distribution is not None:
            if not isinstance(self.heart_rate_zone_definition, str) or not (
                self.heart_rate_zone_definition
            ):
                raise ValueError(
                    "distribuicao de zonas exige heart_rate_zone_definition"
                )
            cleaned = validate_distribution(
                "heart_rate_zone_distribution",
                self.heart_rate_zone_distribution,
            )
            object.__setattr__(self, "heart_rate_zone_distribution", cleaned)
        if self.prediction_interval_s is not None:
            non_negative("prediction_interval_s", self.prediction_interval_s)
        if self.metabolic_energy_j is not None:
            non_negative("metabolic_energy_j", self.metabolic_energy_j)

        # Invariantes de estado: indisponibilidade nunca carrega valor inventado.
        has_physiological = (
            self.predicted_rpe is not None
            or self.heart_rate_zone_distribution is not None
        )
        if self.status.is_available:
            if not has_physiological:
                raise ValueError(
                    "status AVAILABLE exige predicted_rpe ou "
                    "heart_rate_zone_distribution"
                )
        elif has_physiological:
            raise ValueError(
                f"status {self.status.value!r} nao pode conter RPE/FC "
                "(nada e inventado)"
            )

        if self.status is PredictionStatus.INSUFFICIENT_DATA:
            if not self.missing_inputs:
                raise ValueError(
                    "status INSUFFICIENT_DATA exige missing_inputs nao vazio"
                )
        elif self.missing_inputs:
            raise ValueError(
                "missing_inputs so existe com status INSUFFICIENT_DATA"
            )

    @property
    def is_measurement(self) -> bool:
        """Sempre ``False``: a previsao nao e uma medicao."""
        return False

    @property
    def unavailable_fields(self) -> tuple[str, ...]:
        """Campos indisponiveis (``None``) por falta de dados/calibracao."""
        optional = (
            "predicted_rpe",
            "heart_rate_zone_distribution",
            "confidence",
            "metabolic_energy_j",
        )
        return tuple(name for name in optional if getattr(self, name) is None)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "is_measurement": self.is_measurement,
            "status": self.status.value,
            "reason": self.reason,
            "missing_inputs": list(self.missing_inputs),
            "borg_scale": self.borg_scale.value,
            "predicted_rpe": (
                None if self.predicted_rpe is None else round(self.predicted_rpe, 2)
            ),
            "heart_rate_zone_distribution": (
                None
                if self.heart_rate_zone_distribution is None
                else {
                    zone: round(value, 4)
                    for zone, value in self.heart_rate_zone_distribution.items()
                }
            ),
            "heart_rate_zone_definition": self.heart_rate_zone_definition,
            "model_version": self.model_version,
            "confidence": self.confidence,
            "confidence_method": self.confidence_method,
            "prediction_interval_s": (
                None
                if self.prediction_interval_s is None
                else round(self.prediction_interval_s, 3)
            ),
            "metabolic_energy_j": (
                None
                if self.metabolic_energy_j is None
                else round(self.metabolic_energy_j, 3)
            ),
            "unavailable_fields": list(self.unavailable_fields),
        }
