"""Treino reproduzivel e artefato versionado (ADR-027, inc. 3/4).

Cobrem:

* baseline auditavel (constante e linear) e sua proveniencia;
* **reprodutibilidade**: mesma entrada => mesmo ``model_version`` e payload;
* recusa de treinar **sem alvo observado** e **sem dados**;
* alinhamento treino/servico via o **mesmo** contrato de features;
* modelo nao escalar (zonas) recusado explicitamente.
"""

import pytest
from router.core.prediction.intelligence import (
    DEFAULT_FEATURE_NAMES,
    CyclistId,
    MeasurementQuality,
    ModelStore,
    ObservedEffort,
    Provenance,
    TargetKind,
    TrainingError,
    build_dataset,
    build_training_matrix,
    model_from_payload,
    train_and_save,
    train_constant_baseline,
    train_linear,
)
from router.core.prediction.intelligence.models import ConstantModel, LinearModel, ModelError, fit_constant, fit_linear
from router.core.physics.physical.effort import EFFORT_FEATURES_VERSION


def provenance() -> Provenance:
    return Provenance(source="test", version="1")


def power_row(power_w: float, *, extra_missing=False) -> dict:
    row = {name: None for name in DEFAULT_FEATURE_NAMES}
    row["duration_s"] = 600.0
    row["distance_m"] = 3000.0
    row["average_speed_mps"] = 5.0
    row["mechanical_work_j"] = power_w * 600.0 * 0.97
    row["rider_work_j"] = power_w * 600.0
    row["mean_power_w"] = power_w
    row["climbing_m"] = 10.0
    row["descending_m"] = 5.0
    row["braking_energy_j"] = 0.0
    row["aero_loss_j"] = 100.0
    row["rolling_loss_j"] = 200.0
    if extra_missing:
        row["mean_power_w"] = None
    return row


def make_obs(index, *, rpe, power_w, row=None, cid="c1", recorded_at=None, schema=None):
    kwargs = {}
    if schema is not None:
        kwargs["feature_schema_version"] = schema
    return ObservedEffort(
        observation_id=f"o{index}",
        cyclist_id=CyclistId(cid),
        duration_s=600.0,
        distance_m=3000.0,
        provenance=provenance(),
        observed_rpe=rpe,
        quality=MeasurementQuality.DEVICE_MEASURED,
        feature_row=row if row is not None else power_row(power_w),
        recorded_at=recorded_at,
        **kwargs,
    )


def linear_dataset(n=6) -> "object":
    # RPE observado = 10 + 0.02 * potencia (relacao linear exata).
    observations = [
        make_obs(i, rpe=10.0 + 0.02 * (100.0 + 10.0 * i), power_w=100.0 + 10.0 * i)
        for i in range(n)
    ]
    return build_dataset("train-v1", observations, provenance=provenance())


# ==========================================================================
# build_training_matrix: descarte reportado, nada imputado
# ==========================================================================
def test_matrix_drops_observations_without_target_and_reports():
    observations = [
        make_obs(0, rpe=12.0, power_w=100.0),
        ObservedEffort(
            observation_id="o1",
            cyclist_id=CyclistId("c1"),
            duration_s=600.0,
            distance_m=3000.0,
            provenance=provenance(),
            observed_rpe=None,
            observed_heart_rate_bpm=150.0,
            feature_row=power_row(110.0),
        ),
    ]
    dataset = build_dataset("v1", observations, provenance=provenance())
    rows, targets, report = build_training_matrix(
        dataset, TargetKind.RPE, ("mean_power_w",)
    )
    assert len(rows) == 1
    assert report.n_total == 2
    assert report.n_used == 1
    assert report.n_dropped_missing_target == 1


def test_matrix_drops_observations_missing_features_and_reports():
    observations = [
        make_obs(0, rpe=12.0, power_w=100.0),
        make_obs(1, rpe=13.0, power_w=110.0, row=power_row(110.0, extra_missing=True)),
    ]
    dataset = build_dataset("v1", observations, provenance=provenance())
    rows, targets, report = build_training_matrix(
        dataset, TargetKind.RPE, ("mean_power_w",)
    )
    assert report.n_used == 1
    assert report.n_dropped_missing_features == 1


def test_matrix_rejects_schema_divergence():
    observation = make_obs(0, rpe=12.0, power_w=100.0, schema="99")
    dataset = build_dataset("v1", [observation], provenance=provenance())
    with pytest.raises(TrainingError):
        build_training_matrix(dataset, TargetKind.RPE, ("mean_power_w",))


def test_matrix_rejects_unknown_feature_names():
    with pytest.raises(TrainingError):
        build_training_matrix(linear_dataset(), TargetKind.RPE, ("not_a_feature",))


def test_non_scalar_target_is_refused():
    dataset = linear_dataset()
    with pytest.raises(TrainingError):
        build_training_matrix(dataset, TargetKind.HEART_RATE_ZONES, ("mean_power_w",))


# ==========================================================================
# Baseline
# ==========================================================================
def test_constant_baseline_is_mean_of_observed_targets():
    dataset = linear_dataset()
    trained = train_constant_baseline(dataset, target=TargetKind.RPE)
    expected = sum(o.observed_rpe for o in dataset) / len(dataset)
    assert isinstance(model_from_payload(trained.artifact.payload), ConstantModel)
    assert trained.artifact.payload["value"] == pytest.approx(expected)
    assert trained.artifact.is_baseline


def test_constant_baseline_requires_data():
    empty = build_dataset("empty", [], provenance=provenance())
    with pytest.raises(TrainingError):
        train_constant_baseline(empty, target=TargetKind.RPE)


def test_linear_training_recovers_linear_relation():
    trained = train_linear(
        linear_dataset(),
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        calibration_method="ols@1",
    )
    payload = trained.artifact.payload
    assert payload["kind"] == "linear"
    assert payload["intercept"] == pytest.approx(10.0, abs=1e-9)
    assert payload["coefficients"][0] == pytest.approx(0.02, rel=1e-9)
    assert trained.artifact.calibration_method == "ols@1"


def test_linear_training_refuses_insufficient_rows():
    dataset = linear_dataset(n=2)  # 2 linhas para 11 features + intercepto
    with pytest.raises(TrainingError):
        train_linear(dataset, target=TargetKind.RPE)


def test_singular_system_is_not_masked():
    # Duas features colineares -> matriz normal singular.
    rows = [{"a": float(i), "b": 2.0 * i} for i in range(1, 6)]
    with pytest.raises(ModelError):
        fit_linear(rows, [float(i) for i in range(1, 6)], ["a", "b"])


# ==========================================================================
# Reprodutibilidade e persistencia
# ==========================================================================
def test_same_input_yields_same_model_version_and_payload():
    first = train_linear(
        linear_dataset(),
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        calibration_method="ols@1",
    )
    second = train_linear(
        linear_dataset(),
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        calibration_method="ols@1",
    )
    assert first.artifact.model_version == second.artifact.model_version
    assert first.artifact.payload == second.artifact.payload


def test_train_and_save_persists_locally(tmp_path):
    store = ModelStore(tmp_path / "models")
    trained = train_and_save(
        linear_dataset(),
        store,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        calibration_method="ols@1",
    )
    stored = store.load(trained.artifact.model_version)
    assert stored == trained.artifact


def test_feature_schema_version_is_recorded():
    trained = train_linear(
        linear_dataset(),
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    assert trained.artifact.effort_schema_version == EFFORT_FEATURES_VERSION


# ==========================================================================
# Modelos base isolados
# ==========================================================================
def test_constant_model_predicts_constant():
    assert ConstantModel(value=7.5).predict_row({}) == 7.5


def test_linear_model_requires_all_features():
    model = LinearModel(feature_names=("x",), coefficients=(2.0,), intercept=1.0)
    assert model.predict_row({"x": 3.0}) == pytest.approx(7.0)
    with pytest.raises(ModelError):
        model.predict_row({"x": None})


def test_fit_constant_average():
    assert fit_constant([1.0, 2.0, 3.0]).value == pytest.approx(2.0)
