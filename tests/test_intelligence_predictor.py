"""Preditor local calibrado a partir de artefato + integracao (ADR-027, inc. 6).

Cobrem:

* o protocolo ``EffortPredictor`` e satisfeito sem alterar o contrato;
* ``AVAILABLE`` **so** com artefato compativel, alvo RPE e metodo de calibracao;
* estados explicitos: ``CONTRACT_INCOMPATIBLE``, ``MODEL_NOT_CALIBRATED`` e
  ``INSUFFICIENT_DATA`` (com ``missing_inputs``);
* carregamento **local** do disco; ausente/incompativel tratados explicitamente;
* recorte explicito de RPE fora da escala;
* independencia de rede e de roteamento (import estaticamente verificado).
"""

import ast
import inspect
import pytest
import router.core.prediction.predictor as predictor_module
from router.core.prediction import (
    ConstantModel,
    CyclistId,
    LinearModel,
    LocalModelPredictor,
    MeasurementQuality,
    ModelArtifact,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    ObservedEffort,
    Provenance,
    TargetKind,
    load_predictor,
    train_linear,
    build_dataset,
)
from _intelligence_fixtures import features
from router.core.prediction.ml import EffortPredictor, predict_effort
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
)


def provenance() -> Provenance:
    return Provenance(source="test", version="1")


def artifact(
    *,
    model_version="m:rpe:v1",
    target=TargetKind.RPE,
    effort_schema_version="1",
    calibration_method="ols@1",
    payload=None,
) -> ModelArtifact:
    return ModelArtifact(
        model_version=model_version,
        dataset_version="v1",
        effort_schema_version=effort_schema_version,
        target=target,
        method="linear-candidate",
        provenance=provenance(),
        payload=payload if payload is not None else {"kind": "constant", "value": 13.0},
        is_baseline=False,
        calibration_method=calibration_method,
    )


def constant_predictor(value=13.0, **kwargs) -> LocalModelPredictor:
    return LocalModelPredictor(
        artifact=artifact(**kwargs),
        model=ConstantModel(value=value),
    )


# ==========================================================================
# Protocolo e disponibilidade
# ==========================================================================
def test_preditor_satisfies_protocol():
    assert isinstance(constant_predictor(), EffortPredictor)


def test_model_version_comes_from_artifact():
    assert constant_predictor().model_version == "m:rpe:v1"


def test_calibrated_model_is_available():
    prediction = predict_effort(features(), predictor=constant_predictor(13.0))
    assert prediction.status is PredictionStatus.AVAILABLE
    assert prediction.predicted_rpe == pytest.approx(13.0)
    assert prediction.model_version == "m:rpe:v1"
    assert prediction.is_measurement is False
    # Nenhuma confianca e atribuida sem metodo de validacao.
    assert prediction.confidence is None


def test_uncalibrated_model_is_not_available():
    predictor = constant_predictor(calibration_method=None)
    assert predictor.is_calibrated is False
    prediction = predict_effort(features(), predictor=predictor)
    assert prediction.status is PredictionStatus.MODEL_NOT_CALIBRATED
    assert prediction.predicted_rpe is None


def test_non_rpe_target_is_not_available():
    predictor = constant_predictor(target=TargetKind.HEART_RATE_POINT)
    prediction = predict_effort(features(), predictor=predictor)
    assert prediction.status is PredictionStatus.MODEL_NOT_CALIBRATED
    assert prediction.predicted_rpe is None


def test_incompatible_schema_is_contract_incompatible():
    predictor = constant_predictor(effort_schema_version="99")
    prediction = predict_effort(features(), predictor=predictor)
    assert prediction.status is PredictionStatus.CONTRACT_INCOMPATIBLE
    assert prediction.predicted_rpe is None
    assert "99" in (prediction.reason or "")


# ==========================================================================
# Dados ausentes e recorte de escala
# ==========================================================================
def test_missing_required_input_reports_insufficient_data():
    predictor = LocalModelPredictor(
        artifact=artifact(),
        model=LinearModel(
            feature_names=("weight_kg",), coefficients=(0.1,), intercept=5.0
        ),
    )
    assert predictor.required_inputs == ("weight_kg",)
    missing = predict_effort(features(), RiderProfile(), predictor=predictor)
    assert missing.status is PredictionStatus.INSUFFICIENT_DATA
    assert missing.missing_inputs == ("weight_kg",)
    assert missing.predicted_rpe is None

    present = predict_effort(
        features(), RiderProfile(weight_kg=70.0), predictor=predictor
    )
    assert present.status is PredictionStatus.AVAILABLE
    assert present.predicted_rpe == pytest.approx(12.0)


def test_out_of_scale_prediction_is_clamped_explicitly():
    predictor = constant_predictor(30.0)  # 30 esta fora da escala 6-20
    prediction = predict_effort(features(), predictor=predictor)
    assert prediction.status is PredictionStatus.AVAILABLE
    assert prediction.predicted_rpe == pytest.approx(DEFAULT_BORG_SCALE.max_score)
    assert "fora da escala" in (prediction.reason or "")


# ==========================================================================
# Carregamento local (offline) e erros explicitos
# ==========================================================================
def test_from_store_loads_offline(tmp_path):
    store = ModelStore(tmp_path / "models")
    store.save(artifact())
    predictor = LocalModelPredictor.from_store(store, "m:rpe:v1")
    assert predictor.model_version == "m:rpe:v1"
    assert predict_effort(features(), predictor=predictor).status is PredictionStatus.AVAILABLE


def test_from_store_missing_model_raises(tmp_path):
    store = ModelStore(tmp_path / "models")
    with pytest.raises(ModelNotFoundError):
        LocalModelPredictor.from_store(store, "nope")


def test_from_store_incompatible_model_raises(tmp_path):
    store = ModelStore(tmp_path / "models")
    store.save(artifact(effort_schema_version="99"))
    with pytest.raises(ModelIncompatibleError):
        LocalModelPredictor.from_store(store, "m:rpe:v1")


def test_load_predictor_helper(tmp_path):
    store = ModelStore(tmp_path / "models")
    store.save(artifact())
    predictor = load_predictor(store, "m:rpe:v1")
    assert predict_effort(features(), predictor=predictor).status is PredictionStatus.AVAILABLE


# ==========================================================================
# Integracao: treino -> armazenamento -> predicao
# ==========================================================================
def test_trained_calibrated_model_predicts_end_to_end(tmp_path):
    observations = [
        ObservedEffort(
            observation_id=f"o{i}",
            cyclist_id=CyclistId("c1"),
            duration_s=600.0,
            distance_m=3000.0,
            provenance=provenance(),
            observed_rpe=10.0 + 0.02 * (100.0 + 10.0 * i),
            quality=MeasurementQuality.DEVICE_MEASURED,
            feature_row={"mean_power_w": 100.0 + 10.0 * i},
        )
        for i in range(6)
    ]
    dataset = build_dataset("v1", observations, provenance=provenance())
    store = ModelStore(tmp_path / "models")
    from router.core.prediction import train_and_save

    trained = train_and_save(
        dataset,
        store,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        calibration_method="ols@1",
    )
    predictor = load_predictor(store, trained.artifact.model_version)
    prediction = predict_effort(
        features(mean_power_w=150.0), predictor=predictor
    )
    assert prediction.status is PredictionStatus.AVAILABLE
    assert prediction.predicted_rpe == pytest.approx(13.0, abs=1e-9)


# ==========================================================================
# Determinismo e independencia
# ==========================================================================
def test_prediction_is_deterministic_and_does_not_mutate_features():
    f = features()
    before = f.to_dict()
    predictor = constant_predictor()
    first = predict_effort(f, predictor=predictor)
    second = predict_effort(f, predictor=predictor)
    assert first == second
    assert f.to_dict() == before


def test_predictor_module_has_no_network_or_routing_imports():
    tree = ast.parse(inspect.getsource(predictor_module))
    absolute, relative = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                absolute.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            name = (node.module or "").split(".")[0]
            (relative if node.level else absolute).add(name)
    assert "search" not in absolute
    assert {"socket", "urllib", "http"}.isdisjoint(absolute)
    assert relative <= {"", "features", "model_store", "models", "targets", "classification", "observations", "training"}
