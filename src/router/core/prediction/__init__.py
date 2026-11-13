"""Camada 04 — Intelligence, ML & Prediction (ADR-027).

Esta camada transforma o **resultado fisico** de uma rota (``EffortFeatures``) em
uma **previsao fisiologica** e apoia a **classificacao** da rota, com **dois
processos ambos offline** (ADR-027 §0):

* **treinamento offline** — observacoes reais (``observations``) → features
  (``features``) → baseline/modelo (``models``, ``training``) → avaliacao
  (``model_eval``) → **artefato de modelo local versionado** (``model_store``);
* **inferencia local offline** — artefato carregado do disco (``ModelStore``)
  → preditor local (``predictor.LocalModelPredictor``) injetado no
  ``ml.predict_effort`` → classificacao predicao x preferencia
  (``classification``).

Principios (ADR-027):

* **nenhuma** etapa acessa a rede; modelo e dados sao **locais**;
* **nenhum** modelo ficticio, target sintetico tratado como observacao ou
  parametro de calibracao arbitrario;
* ausencia de modelo/observacao e **explicita** (``PredictionStatus``) — nunca um
  valor inventado;
* a predicao **nao** altera geometria, restricoes, fronteira nem ordem fisica:
  ela apoia a classificacao final.

Esta camada **nao** reimplementa fisica nem busca: consome ``physical``,
``physiology`` e ``ml`` como contratos preservados. A classificacao
predicao x preferencia vive em ``router.core.evaluation`` (ADR-031, D2).
"""

from .features import (
    ALL_FEATURE_NAMES,
    DEFAULT_FEATURE_NAMES,
    EXCLUDED_FROM_FEATURES,
    FEATURES_VERSION,
    feature_row,
    missing_features,
    select_row,
)
from .intensity import (
    DEFAULT_INTENSITY_COEFFICIENTS,
    INTENSITY_BAND_DEFINITION,
    INTENSITY_BAND_EDGES,
    INTENSITY_BAND_LABELS,
    INTENSITY_BAND_NAMES,
    INTENSITY_MODEL_ID,
    INTENSITY_MODEL_VERSION,
    IntensityCoefficients,
    IntensityEstimate,
    IntensitySegment,
    band_for_rpe,
    estimate_intensity,
    hw_stress,
    segment_rpe,
    segments_from_traversal,
)
from .mechanical import (
    DEFAULT_PROPULSION,
    MechanicalIntensity,
    PropulsionMode,
    PropulsionModel,
    PropulsionSplit,
    RoutePhysics,
    RouteSegment,
    estimate_mechanical_intensity,
)
from .response import (
    DEFAULT_RESPONSE_CONFIG,
    FatigueModel,
    ResponseConfig,
    RiderResponseEstimate,
    estimate_rider_response,
)
from .rider import rider_model_from_cyclist
from .model_eval import (
    CriteriaResult,
    EvaluationError,
    GroupMetrics,
    MinimalCriteria,
    ModelEvaluation,
    cyclist_split,
    evaluate_model,
    temporal_split,
)
from .model_store import (
    MODEL_ARTIFACT_FORMAT,
    MODEL_ARTIFACT_VERSION,
    REGISTRY_FILENAME,
    ModelArtifact,
    ModelArtifactError,
    ModelFormatError,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    check_compatibility,
)
from .models import (
    MODELS_VERSION,
    ConstantModel,
    LinearModel,
    ModelError,
    fit_constant,
    fit_linear,
    model_from_payload,
)
from .observations import (
    DATASET_FORMAT,
    DATASET_FORMAT_VERSION,
    OBSERVATIONS_VERSION,
    CyclistId,
    MeasurementQuality,
    ObservationDataset,
    ObservationError,
    ObservedEffort,
    Provenance,
    build_dataset,
    load_dataset,
    pseudonymize,
    save_dataset,
)
from .predictor import LocalModelPredictor, load_predictor
from .targets import (
    TARGETS_VERSION,
    HeartRateZoneDerivationMethod,
    TargetKind,
    ZoneDerivationUnavailable,
    ZONE_DERIVATION_METHODS,
    derive_heart_rate_zones,
    get_zone_derivation,
    register_zone_derivation,
)
from .training import (
    TrainedModel,
    TrainingError,
    TrainingReport,
    build_training_matrix,
    train_and_save,
    train_constant_baseline,
    train_linear,
)

__all__ = [
    # targets
    "TARGETS_VERSION",
    "TargetKind",
    "ZoneDerivationUnavailable",
    "HeartRateZoneDerivationMethod",
    "ZONE_DERIVATION_METHODS",
    "register_zone_derivation",
    "get_zone_derivation",
    "derive_heart_rate_zones",
    # observations
    "OBSERVATIONS_VERSION",
    "DATASET_FORMAT",
    "DATASET_FORMAT_VERSION",
    "ObservationError",
    "MeasurementQuality",
    "Provenance",
    "CyclistId",
    "pseudonymize",
    "ObservedEffort",
    "ObservationDataset",
    "save_dataset",
    "load_dataset",
    "build_dataset",
    # features
    "FEATURES_VERSION",
    "ALL_FEATURE_NAMES",
    "DEFAULT_FEATURE_NAMES",
    "EXCLUDED_FROM_FEATURES",
    "feature_row",
    "select_row",
    "missing_features",
    # intensity (ADR-028)
    "INTENSITY_MODEL_ID",
    "INTENSITY_MODEL_VERSION",
    "INTENSITY_BAND_DEFINITION",
    "INTENSITY_BAND_NAMES",
    "INTENSITY_BAND_EDGES",
    "INTENSITY_BAND_LABELS",
    "DEFAULT_INTENSITY_COEFFICIENTS",
    "IntensityCoefficients",
    "IntensitySegment",
    "IntensityEstimate",
    "hw_stress",
    "band_for_rpe",
    "segment_rpe",
    "estimate_intensity",
    "segments_from_traversal",
    # mechanical intensity / propulsion (ADR-034)
    "RouteSegment",
    "RoutePhysics",
    "MechanicalIntensity",
    "PropulsionMode",
    "PropulsionModel",
    "PropulsionSplit",
    "DEFAULT_PROPULSION",
    "estimate_mechanical_intensity",
    # provisional physiological response (ADR-034)
    "ResponseConfig",
    "DEFAULT_RESPONSE_CONFIG",
    "FatigueModel",
    "RiderResponseEstimate",
    "estimate_rider_response",
    "rider_model_from_cyclist",
    # models
    "MODELS_VERSION",
    "ModelError",
    "ConstantModel",
    "LinearModel",
    "model_from_payload",
    "fit_constant",
    "fit_linear",
    # model store
    "MODEL_ARTIFACT_FORMAT",
    "MODEL_ARTIFACT_VERSION",
    "REGISTRY_FILENAME",
    "ModelArtifactError",
    "ModelNotFoundError",
    "ModelIncompatibleError",
    "ModelFormatError",
    "ModelArtifact",
    "check_compatibility",
    "ModelStore",
    # training
    "TrainingError",
    "TrainingReport",
    "TrainedModel",
    "build_training_matrix",
    "train_constant_baseline",
    "train_linear",
    "train_and_save",
    # evaluation
    "EvaluationError",
    "GroupMetrics",
    "ModelEvaluation",
    "MinimalCriteria",
    "CriteriaResult",
    "evaluate_model",
    "temporal_split",
    "cyclist_split",
    # predictor
    "LocalModelPredictor",
    "load_predictor",
]
