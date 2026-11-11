"""Preditor local calibrado a partir de artefato (ADR-027, incremento 6).

``LocalModelPredictor`` fecha a inferencia **local offline**: ele le um artefato
de modelo **do armazenamento local** (``intelligence.model_store``), reconstroi o
modelo, **verifica compatibilidade** com o schema de features em uso e implementa
o protocolo ``ml.EffortPredictor`` — **sem** tocar em fisica, roteamento ou no
proprio protocolo.

Honestidade (ADR-027, decisao 4/6):

* o preditor **so** declara ``AVAILABLE`` quando o artefato e **compativel** com o
  schema de ``EffortFeatures`` e o modelo foi **calibrado** (metodo de calibracao
  declarado no artefato) e o alvo tem campo de saida (RPE);
* quando algo falta, o estado e **explicito** — ``CONTRACT_INCOMPATIBLE``,
  ``MODEL_NOT_CALIBRATED`` ou ``INSUFFICIENT_DATA`` — e RPE/FC permanecem
  ``None``;
* **nenhuma** confianca numerica e atribuida: ``confidence`` exige
  ``confidence_method``; sem calibracao declarada permanece ``None``;
* um valor previsto fora dos limites da escala Borg e **limitado** aos extremos,
  de forma explicita (o motivo registra o recorte) — o contrato nunca aceita um
  RPE fora da escala.
"""

from __future__ import annotations

from dataclasses import dataclass

from physical.effort import EFFORT_FEATURES_VERSION, EffortFeatures
from physiology import (
    DEFAULT_BORG_SCALE,
    BorgScale,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
)

from .features import feature_row, missing_features
from .model_store import ModelArtifact, ModelStore
from .models import ConstantModel, LinearModel, model_from_payload
from .targets import TargetKind

#: Nome do tipo de modelo que sabe produzir uma previsao de RPE.
_SUPPORTED_RPE_MODELS = (ConstantModel, LinearModel)


def _limited(
    status: PredictionStatus,
    *,
    model_version: str,
    borg_scale: BorgScale,
    reason: str,
    missing: tuple[str, ...] = (),
    interval_s: float | None = None,
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
        prediction_interval_s=interval_s,
    )


@dataclass(frozen=True, slots=True)
class LocalModelPredictor:
    """``EffortPredictor`` apoiado em um artefato de modelo **local**.

    O preditor reconstroi o modelo a partir do ``payload`` do artefato e monta a
    linha de features **pelo mesmo contrato do treino** (``feature_row``). O
    status so e ``AVAILABLE`` quando o artefato e compativel, o alvo e RPE e o
    modelo foi calibrado (``artifact.calibration_method`` declarado).

    Args:
        artifact: Artefato local ja carregado e verificado contra o schema.
        model: Modelo reconstruido a partir de ``artifact.payload``.
        borg_scale: Escala de RPE em que a previsao e expressa.
        effort_schema_version: Schema de ``EffortFeatures`` em uso no servico.
    """

    artifact: ModelArtifact
    model: ConstantModel | LinearModel
    borg_scale: BorgScale = DEFAULT_BORG_SCALE
    effort_schema_version: str = EFFORT_FEATURES_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ModelArtifact):
            raise TypeError("artifact deve ser um ModelArtifact")
        if not isinstance(self.model, _SUPPORTED_RPE_MODELS):
            raise TypeError(
                "model deve ser ConstantModel ou LinearModel "
                f"(recebido {type(self.model).__name__})"
            )
        if not isinstance(self.borg_scale, BorgScale):
            raise TypeError("borg_scale deve ser um BorgScale")

    # ---------------------------------------------------------- propriedades
    @property
    def model_version(self) -> str:
        """Identificador versionado do modelo (do artefato)."""
        return self.artifact.model_version

    @property
    def is_calibrated(self) -> bool:
        """True apenas com metodo de calibracao declarado no artefato."""
        return self.artifact.calibration_method is not None

    @property
    def required_inputs(self) -> tuple[str, ...]:
        """Features exigidas pelo modelo (vazio quando nao calibrado/RPE)."""
        if not self.is_calibrated:
            return ()
        if self.artifact.target is not TargetKind.RPE:
            return ()
        if isinstance(self.model, LinearModel):
            return tuple(self.model.feature_names)
        return ()

    # ---------------------------------------------------------------- predicao
    def predict(
        self,
        features: EffortFeatures,
        rider: RiderProfile,
        context: PredictionContext,
    ) -> PhysiologicalPrediction:
        """Produz a previsao de RPE a partir das features, ou estado explicito."""
        interval_s = features.duration_s

        if not self.artifact.compatible_with(self.effort_schema_version):
            return _limited(
                PredictionStatus.CONTRACT_INCOMPATIBLE,
                model_version=self.model_version,
                borg_scale=self.borg_scale,
                reason=(
                    "modelo incompativel com o schema de EffortFeatures: "
                    f"modelo={self.artifact.effort_schema_version!r}, "
                    f"em uso={self.effort_schema_version!r}"
                ),
                interval_s=interval_s,
            )

        if self.artifact.target is not TargetKind.RPE:
            return _limited(
                PredictionStatus.MODEL_NOT_CALIBRATED,
                model_version=self.model_version,
                borg_scale=self.borg_scale,
                reason=(
                    f"alvo {self.artifact.target.value!r} nao possui campo de "
                    "saida de previsao disponivel (apenas RPE)"
                ),
                interval_s=interval_s,
            )

        if not self.is_calibrated:
            return _limited(
                PredictionStatus.MODEL_NOT_CALIBRATED,
                model_version=self.model_version,
                borg_scale=self.borg_scale,
                reason=(
                    "modelo sem metodo de calibracao declarado "
                    "(nenhum coeficiente fisiologico e usado sem calibracao)"
                ),
                interval_s=interval_s,
            )

        row = feature_row(features, rider, context)
        missing = missing_features(row, self.required_inputs)
        if missing:
            return _limited(
                PredictionStatus.INSUFFICIENT_DATA,
                model_version=self.model_version,
                borg_scale=self.borg_scale,
                reason=f"entradas ausentes: {list(missing)!r}",
                missing=missing,
                interval_s=interval_s,
            )

        value = float(self.model.predict_row(row))
        clamped = self.borg_scale.clamp(value)
        reason = None
        if clamped != value:
            reason = (
                f"RPE previsto {value:.6g} fora da escala "
                f"{self.borg_scale.value!r}; limitado a "
                f"[{self.borg_scale.min_score:g}, "
                f"{self.borg_scale.max_score:g}]"
            )
        return PhysiologicalPrediction(
            borg_scale=self.borg_scale,
            predicted_rpe=clamped,
            heart_rate_zone_distribution=None,
            model_version=self.model_version,
            status=PredictionStatus.AVAILABLE,
            reason=reason,
            prediction_interval_s=interval_s,
        )

    # ------------------------------------------------------------- fachadas
    @classmethod
    def from_store(
        cls,
        store: ModelStore,
        model_version: str,
        *,
        borg_scale: BorgScale = DEFAULT_BORG_SCALE,
        effort_schema_version: str = EFFORT_FEATURES_VERSION,
    ) -> "LocalModelPredictor":
        """Carrega o artefato **do disco** e monta o preditor local.

        A compatibilidade e verificada **no carregamento** (``load_compatible``);
        schema incompativel levanta ``ModelIncompatibleError`` e artefato ausente
        levanta ``ModelNotFoundError`` — nunca ha download automatico.
        """
        artifact = store.load_compatible(model_version, effort_schema_version)
        model = model_from_payload(artifact.payload)
        return cls(
            artifact=artifact,
            model=model,
            borg_scale=borg_scale,
            effort_schema_version=effort_schema_version,
        )


def load_predictor(
    store: ModelStore,
    model_version: str,
    *,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    effort_schema_version: str = EFFORT_FEATURES_VERSION,
) -> LocalModelPredictor:
    """Atalho para :meth:`LocalModelPredictor.from_store`."""
    return LocalModelPredictor.from_store(
        store,
        model_version,
        borg_scale=borg_scale,
        effort_schema_version=effort_schema_version,
    )
