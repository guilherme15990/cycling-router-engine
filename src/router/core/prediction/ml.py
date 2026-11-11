"""Fronteira de inferencia fisiologica: protocolo e implementacoes honestas.

Este modulo (``prediction/ml.py``, ADR-032 D2) e a **fronteira de inferencia**:
consome ``EffortFeatures`` (produzidas pela fisica) e devolve
``PhysiologicalPrediction`` (contrato em ``core.physiology``). Nao conhece
roteamento nem recalcula fisica.

O protocolo ``EffortPredictor`` e a unica dependencia do resto do sistema: a
fisica entrega ``EffortFeatures`` e recebe uma ``PhysiologicalPrediction``. Um
modelo neural calibrado (futuro) implementa o mesmo protocolo sem tocar na
fisica nem no roteamento.

Nenhum modelo neural **treinado/calibrado** e incluido: sem dados e sem metodo
de validacao, um preditor nao pode produzir Borg/FC sem inventar. Por isso o
estado da previsao e sempre explicito (``PredictionStatus``):

* ``UNAVAILABLE`` — nenhum preditor configurado;
* ``MODEL_NOT_CALIBRATED`` — ha preditor, mas sem modelo fisiologico calibrado;
* ``INSUFFICIENT_DATA`` — faltam entradas exigidas pelo preditor;
* ``CONTRACT_INCOMPATIBLE`` — a versao do contrato de features nao e suportada;
* ``AVAILABLE`` — um modelo calibrado produziu a previsao.

``predict_effort`` e o ponto de entrada **guardado**: verifica a compatibilidade
do contrato e a presenca das entradas exigidas antes de delegar ao preditor,
sempre devolvendo ``None`` em vez de inventar valores fisiologicos.

Treinar/plugar um modelo calibrado e trabalho futuro; o seam esta pronto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from router.core.physics.effort import EFFORT_FEATURES_VERSION, EffortFeatures
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    BorgScale,
    MetabolicParameters,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
    metabolic_energy_j,
)

#: Versoes do contrato ``EffortFeatures`` que esta camada sabe consumir.
SUPPORTED_EFFORT_SCHEMA_VERSIONS = frozenset({EFFORT_FEATURES_VERSION})

#: Versao de modelo atribuida quando nao ha preditor configurado.
UNAVAILABLE_MODEL_VERSION = "unavailable"

__all__ = [
    "EffortPredictor",
    "MetabolicEnergyPredictor",
    "UnavailablePredictor",
    "predict_effort",
]


def missing_inputs(
    features: EffortFeatures,
    rider: RiderProfile,
    context: PredictionContext,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    """Nomes exigidos que **nao** estao presentes (``None``/ausentes).

    Um valor igual a ``0`` e valido; somente ``None`` (ou ausencia do campo) e
    tratado como dado faltante. Nada e estimado a partir de dados ausentes.
    """
    missing: list[str] = []
    for name in required:
        value = None
        found = False
        for source in (features, rider, context):
            if hasattr(source, name):
                value = getattr(source, name)
                found = True
                break
        if not found or value is None:
            missing.append(name)
    return tuple(missing)


def _limited(
    status: PredictionStatus,
    *,
    model_version: str,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    reason: str | None = None,
    missing: tuple[str, ...] = (),
    prediction_interval_s: float | None = None,
) -> PhysiologicalPrediction:
    """Previsao limitada: estado explicito e **nenhum** valor fisiologico."""
    return PhysiologicalPrediction(
        borg_scale=borg_scale,
        predicted_rpe=None,
        heart_rate_zone_distribution=None,
        model_version=model_version,
        status=status,
        reason=reason,
        missing_inputs=missing,
        prediction_interval_s=prediction_interval_s,
    )


@runtime_checkable
class EffortPredictor(Protocol):
    """Contrato minimo de um preditor fisiologico.

    Um preditor nao altera a geometria/restricoes da rota: ele recebe features
    fisicas ja calculadas e devolve uma previsao identificada. Pode declarar
    ``required_inputs`` (nomes de campos de ``EffortFeatures``/``RiderProfile``/
    ``PredictionContext``) para que ``predict_effort`` detecte dados faltantes.
    """

    @property
    def model_version(self) -> str:
        """Identificador versionado do modelo."""

    def predict(
        self,
        features: EffortFeatures,
        rider: RiderProfile,
        context: PredictionContext,
    ) -> PhysiologicalPrediction:
        """Produz uma ``PhysiologicalPrediction`` a partir das features."""


@dataclass(frozen=True, slots=True)
class UnavailablePredictor:
    """Preditor padrao: declara explicitamente a ausencia de modelo calibrado.

    Nao infere Borg, FC, fadiga nem pico de potencia — devolve ``None`` para
    tudo que exigiria calibracao. O estado e ``MODEL_NOT_CALIBRATED`` (ha o
    preditor, mas nao um modelo fisiologico calibrado): distinto de ausencia de
    preditor, que ``predict_effort`` marca como ``UNAVAILABLE``.
    """

    borg_scale: BorgScale = DEFAULT_BORG_SCALE
    model_version: str = UNAVAILABLE_MODEL_VERSION

    #: Nao exige nenhuma entrada adicional.
    required_inputs: ClassVar[tuple[str, ...]] = ()

    def predict(
        self,
        features: EffortFeatures,
        rider: RiderProfile,
        context: PredictionContext,
    ) -> PhysiologicalPrediction:
        return _limited(
            PredictionStatus.MODEL_NOT_CALIBRATED,
            model_version=self.model_version,
            borg_scale=self.borg_scale,
            reason="modelo fisiologico nao calibrado (sem dados de validacao)",
            prediction_interval_s=features.duration_s,
        )


@dataclass(frozen=True, slots=True)
class MetabolicEnergyPredictor:
    """Preditor que preenche **apenas** a energia metabolica.

    Usa parametros metabolicos explicitos (versionados, com proveniencia) e o
    trabalho no pedivela. Nao produz Borg, FC nem confianca — esses continuam
    indisponiveis (``status = MODEL_NOT_CALIBRATED`` para RPE/FC). A energia
    metabolica e independente do estado fisiologico.
    """

    metabolic: MetabolicParameters
    borg_scale: BorgScale = DEFAULT_BORG_SCALE

    #: O calculo metabolico exige o trabalho no pedivela.
    required_inputs: ClassVar[tuple[str, ...]] = ("rider_work_j",)

    @property
    def model_version(self) -> str:
        return f"metabolic@{self.metabolic.version}"

    def predict(
        self,
        features: EffortFeatures,
        rider: RiderProfile,
        context: PredictionContext,
    ) -> PhysiologicalPrediction:
        return PhysiologicalPrediction(
            borg_scale=self.borg_scale,
            predicted_rpe=None,
            heart_rate_zone_distribution=None,
            model_version=self.model_version,
            status=PredictionStatus.MODEL_NOT_CALIBRATED,
            reason="modelo fisiologico (RPE/FC) nao calibrado",
            prediction_interval_s=features.duration_s,
            metabolic_energy_j=metabolic_energy_j(
                features.rider_work_j, self.metabolic
            ),
        )


def predict_effort(
    features: EffortFeatures,
    rider: RiderProfile | None = None,
    context: PredictionContext | None = None,
    *,
    predictor: EffortPredictor | None = None,
) -> PhysiologicalPrediction:
    """Ponto de entrada guardado para a previsao de esforco.

    Diferencia, **sem inventar valores fisiologicos**:

    * ``CONTRACT_INCOMPATIBLE`` — ``features.schema_version`` nao suportada;
    * ``UNAVAILABLE`` — nenhum ``predictor`` foi fornecido;
    * ``INSUFFICIENT_DATA`` — faltam entradas exigidas pelo preditor;
    * ``MODEL_NOT_CALIBRATED`` / ``AVAILABLE`` — decididos pelo proprio preditor.

    O mesmo contrato vale para qualquer rota (tradicional ou gerada por ML): a
    fisica nao muda.
    """
    rider = rider if rider is not None else RiderProfile()
    context = context if context is not None else PredictionContext()

    if predictor is not None and not isinstance(predictor, EffortPredictor):
        raise TypeError("predictor deve implementar EffortPredictor")

    model_version = (
        predictor.model_version
        if predictor is not None
        else UNAVAILABLE_MODEL_VERSION
    )
    borg_scale = getattr(predictor, "borg_scale", DEFAULT_BORG_SCALE)

    schema_version = getattr(features, "schema_version", None)
    if schema_version not in SUPPORTED_EFFORT_SCHEMA_VERSIONS:
        return _limited(
            PredictionStatus.CONTRACT_INCOMPATIBLE,
            model_version=model_version,
            borg_scale=borg_scale,
            reason=(
                f"schema_version de EffortFeatures nao suportada "
                f"({schema_version!r}); suportadas: "
                f"{sorted(SUPPORTED_EFFORT_SCHEMA_VERSIONS)!r}"
            ),
            prediction_interval_s=None,
        )

    if predictor is None:
        return _limited(
            PredictionStatus.UNAVAILABLE,
            model_version=model_version,
            borg_scale=borg_scale,
            reason="nenhum preditor configurado",
            prediction_interval_s=features.duration_s,
        )

    required = tuple(getattr(predictor, "required_inputs", ()))
    missing = missing_inputs(features, rider, context, required)
    if missing:
        return _limited(
            PredictionStatus.INSUFFICIENT_DATA,
            model_version=model_version,
            borg_scale=borg_scale,
            reason=f"entradas ausentes: {list(missing)!r}",
            missing=missing,
            prediction_interval_s=features.duration_s,
        )

    return predictor.predict(features, rider, context)
