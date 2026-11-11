"""Avaliacao de qualidade preditiva do modelo (ADR-027, inc. 5).

Cobrem:

* erro (MAE/RMSE), **vies** e generalizacao **por ciclista**;
* validacao **temporal** (teste no futuro) e **por ciclista** (nao visto);
* criterios minimos definidos **antes** do treino e aplicados;
* ausencia de **vazamento** (conjuntos disjuntos) — teste explicito.
"""

import pytest
from router.core.prediction import (
    ConstantModel,
    CyclistId,
    EvaluationError,
    LinearModel,
    MeasurementQuality,
    MinimalCriteria,
    ObservedEffort,
    Provenance,
    TargetKind,
    build_dataset,
    cyclist_split,
    evaluate_model,
    temporal_split,
)


def provenance() -> Provenance:
    return Provenance(source="test", version="1")


def obs(index, *, rpe, cid="c1", recorded_at=None, power=100.0):
    return ObservedEffort(
        observation_id=f"o{index}",
        cyclist_id=CyclistId(cid),
        duration_s=600.0,
        distance_m=3000.0,
        provenance=provenance(),
        observed_rpe=rpe,
        quality=MeasurementQuality.DEVICE_MEASURED,
        feature_row={"mean_power_w": power},
        recorded_at=recorded_at,
    )


def pairwise_dataset():
    # 4 observacoes, RPE 10..13, potencia igual -> erro constante do modelo.
    return build_dataset(
        "v1",
        [obs(i, rpe=10.0 + i, recorded_at=f"2026-01-0{i + 1}T10:00:00") for i in range(4)],
        provenance=provenance(),
    )


# ==========================================================================
# Metricas
# ==========================================================================
def test_metrics_are_exact_for_constant_error():
    dataset = pairwise_dataset()
    model = ConstantModel(value=11.5)  # preve 11.5 para RPE 10,11,12,13
    evaluation = evaluate_model(
        model,
        dataset.observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
        split_scheme="insample",
    )
    # erros (previsto - observado): 1.5, 0.5, -0.5, -1.5
    assert evaluation.n == 4
    assert evaluation.mae == pytest.approx(1.0)
    assert evaluation.rmse == pytest.approx((5.0 / 4.0) ** 0.5)
    assert evaluation.bias == pytest.approx(0.0)
    assert evaluation.split_scheme == "insample"


def test_per_group_reports_each_cyclist():
    observations = [
        obs(0, rpe=10.0, cid="a"),
        obs(1, rpe=12.0, cid="a"),
        obs(2, rpe=14.0, cid="b"),
    ]
    model = ConstantModel(value=12.0)
    evaluation = evaluate_model(
        model,
        observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    groups = {g.key: g for g in evaluation.per_group}
    assert set(groups) == {"a", "b"}
    assert groups["a"].n == 2
    assert groups["b"].n == 1


def test_evaluation_requires_usable_observations():
    with pytest.raises(EvaluationError):
        evaluate_model(
            ConstantModel(value=1.0),
            [],
            target=TargetKind.RPE,
            feature_names=("mean_power_w",),
        )


def test_evaluation_skips_observations_missing_features():
    observations = [obs(0, rpe=12.0, power=100.0), obs(1, rpe=13.0, power=110.0)]
    observations[1] = ObservedEffort(
        observation_id="o1",
        cyclist_id=CyclistId("c1"),
        duration_s=600.0,
        distance_m=3000.0,
        provenance=provenance(),
        observed_rpe=13.0,
        feature_row=None,
    )
    evaluation = evaluate_model(
        ConstantModel(value=12.0),
        observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    assert evaluation.n == 1


# ==========================================================================
# Splits (separacao de conjuntos)
# ==========================================================================
def test_temporal_split_puts_test_in_the_future():
    dataset = pairwise_dataset()
    train, test = temporal_split(dataset, test_fraction=0.25)
    assert all(
        t.recorded_at <= min(x.recorded_at for x in test.observations)
        for t in train.observations
    )
    assert set(o.observation_id for o in train.observations).isdisjoint(
        o.observation_id for o in test.observations
    )


def test_temporal_split_requires_timestamps():
    dataset = build_dataset("v1", [obs(0, rpe=10.0)], provenance=provenance())
    with pytest.raises(EvaluationError):
        temporal_split(dataset, test_fraction=0.5)


def test_temporal_split_validates_fraction():
    dataset = pairwise_dataset()
    with pytest.raises(EvaluationError):
        temporal_split(dataset, test_fraction=1.0)


def test_cyclist_split_keeps_test_cyclists_unseen():
    observations = [
        obs(0, rpe=10.0, cid="a"),
        obs(1, rpe=11.0, cid="a"),
        obs(2, rpe=12.0, cid="b"),
        obs(3, rpe=13.0, cid="c"),
    ]
    dataset = build_dataset("v1", observations, provenance=provenance())
    train, test = cyclist_split(dataset, test_fraction=0.34)
    train_cyclists = {o.cyclist_id.value for o in train.observations}
    test_cyclists = {o.cyclist_id.value for o in test.observations}
    assert train_cyclists.isdisjoint(test_cyclists)
    assert test_cyclists  # nao vazio


def test_cyclist_split_requires_two_cyclists():
    dataset = build_dataset(
        "v1", [obs(0, rpe=10.0, cid="a"), obs(1, rpe=11.0, cid="a")],
        provenance=provenance(),
    )
    with pytest.raises(EvaluationError):
        cyclist_split(dataset, test_fraction=0.5)


# ==========================================================================
# Criterios minimos
# ==========================================================================
def test_criteria_accept_when_within_bounds():
    dataset = pairwise_dataset()
    evaluation = evaluate_model(
        LinearModel(feature_names=("mean_power_w",), coefficients=(1.0,), intercept=-88.0),
        dataset.observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    # modelo perfeito: RPE = 1.0*100 - 88 = 12? nao: 12 vs 10..13 -> erro.
    result = MinimalCriteria(max_mae=5.0, max_abs_bias=5.0).check(evaluation)
    assert result.accepted
    assert result.reasons == ()


def test_criteria_reject_and_explain():
    dataset = pairwise_dataset()
    evaluation = evaluate_model(
        ConstantModel(value=20.0),
        dataset.observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    result = MinimalCriteria(max_mae=1.0, max_abs_bias=0.5).check(evaluation)
    assert not result.accepted
    assert any("MAE" in reason for reason in result.reasons)
    assert any("vies" in reason for reason in result.reasons)


def test_criteria_min_n_applies():
    dataset = pairwise_dataset()
    evaluation = evaluate_model(
        ConstantModel(value=12.0),
        dataset.observations,
        target=TargetKind.RPE,
        feature_names=("mean_power_w",),
    )
    result = MinimalCriteria(max_mae=100.0, max_abs_bias=100.0, min_n=10).check(evaluation)
    assert not result.accepted
    assert any("insuficiente" in reason for reason in result.reasons)


def test_criteria_validate_bounds():
    with pytest.raises(ValueError):
        MinimalCriteria(max_mae=-1.0, max_abs_bias=1.0)
    with pytest.raises(EvaluationError):
        MinimalCriteria(max_mae=1.0, max_abs_bias=1.0, min_n=0)


# ==========================================================================
# Ausencia de vazamento (teste explicito)
# ==========================================================================
def test_train_and_test_sets_are_disjoint_no_leakage():
    observations = [
        obs(i, rpe=10.0 + i, cid=("a" if i < 3 else "b"),
            recorded_at=f"2026-01-{i + 1:02d}T10:00:00")
        for i in range(6)
    ]
    dataset = build_dataset("v1", observations, provenance=provenance())
    train, test = temporal_split(dataset, test_fraction=0.34)
    train_ids = {o.observation_id for o in train.observations}
    test_ids = {o.observation_id for o in test.observations}
    assert train_ids.isdisjoint(test_ids)
    assert train_ids | test_ids == {o.observation_id for o in dataset.observations}
