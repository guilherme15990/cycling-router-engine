"""Contratos de fisiologia: escalas, zonas, metabolico e previsao (incremento 7).

Cobertura exigida:

* escala Borg explicita (padrao 6-20; CR10 suportada) e limites respeitados;
* zonas cardiacas por definicao identificada, com distribuicao nao negativa que
  soma 1 (dentro da tolerancia) ou ``None`` quando nao ha tempo;
* energia metabolica so com parametros calibrados (``None``, nunca 0);
* ``PhysiologicalPrediction`` nunca e medicao, e valida suas invariantes;
* ausencia explicita, imutabilidade, versionamento e serializacao.
"""

import dataclasses
import json
import pytest
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    DISTRIBUTION_TOLERANCE,
    PHYSIOLOGY_VERSION,
    BorgScale,
    HeartRateZoneDefinition,
    HeartRateZoneModel,
    MetabolicParameters,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
    metabolic_energy_j,
)
from router.core.physiology.zones import is_normalized, validate_distribution


# ==========================================================================
# Escala de Borg
# ==========================================================================
def test_default_borg_scale_is_6_20():
    assert DEFAULT_BORG_SCALE is BorgScale.RPE_6_20
    assert DEFAULT_BORG_SCALE.value == "6-20"
    assert DEFAULT_BORG_SCALE.min_score == 6.0
    assert DEFAULT_BORG_SCALE.max_score == 20.0


def test_cr10_scale_bounds_and_support():
    assert BorgScale.CR10.value == "cr10"
    assert BorgScale.CR10.min_score == 0.0
    assert BorgScale.CR10.max_score == 10.0


def test_contains_respects_scale_limits():
    assert BorgScale.RPE_6_20.contains(6.0)
    assert BorgScale.RPE_6_20.contains(20.0)
    assert not BorgScale.RPE_6_20.contains(5.99)
    assert not BorgScale.RPE_6_20.contains(21.0)
    assert BorgScale.CR10.contains(0.0)
    assert BorgScale.CR10.contains(10.0)
    assert not BorgScale.CR10.contains(10.5)
    # Nao finito nunca esta contido.
    assert not BorgScale.RPE_6_20.contains(float("nan"))


def test_clamp_limits_to_scale_extremes():
    assert BorgScale.RPE_6_20.clamp(0.0) == 6.0
    assert BorgScale.RPE_6_20.clamp(99.0) == 20.0
    assert BorgScale.RPE_6_20.clamp(13.0) == 13.0
    assert BorgScale.CR10.clamp(-1.0) == 0.0


def test_require_valid_accepts_inside_and_rejects_outside():
    assert BorgScale.RPE_6_20.require_valid(13.0) == 13.0
    with pytest.raises(ValueError):
        BorgScale.RPE_6_20.require_valid(5.0)
    with pytest.raises(ValueError):
        BorgScale.RPE_6_20.require_valid(25.0)
    with pytest.raises(ValueError):
        BorgScale.CR10.require_valid(float("inf"))


# ==========================================================================
# Zonas cardiacas
# ==========================================================================
def two_zone_model():
    return HeartRateZoneModel.from_bounds(
        "lthr", "1", [("z1", 100.0, 130.0), ("z2", 130.0, 160.0)]
    )


def test_zone_definition_rejects_invalid_bounds():
    zone = HeartRateZoneDefinition(name="z", lower_bpm=120.0, upper_bpm=140.0)
    assert zone.lower_bpm == 120.0 and zone.upper_bpm == 140.0
    with pytest.raises(ValueError):
        HeartRateZoneDefinition(name="z", lower_bpm=140.0, upper_bpm=120.0)
    with pytest.raises(ValueError):
        HeartRateZoneDefinition(name="", lower_bpm=100.0, upper_bpm=120.0)
    with pytest.raises(ValueError):
        HeartRateZoneDefinition(name="z", lower_bpm=-1.0, upper_bpm=120.0)


def test_zone_model_has_identified_definition_and_version():
    model = two_zone_model()
    assert model.definition == "lthr"
    assert model.version == "1"
    assert model.identifier == "lthr@1"
    assert model.zone_names == ("z1", "z2")


def test_zone_model_rejects_duplicate_names():
    with pytest.raises(ValueError):
        HeartRateZoneModel.from_bounds(
            "d", "1", [("z", 100.0, 120.0), ("z", 120.0, 140.0)]
        )


def test_zone_model_rejects_empty_definition_or_version():
    with pytest.raises(ValueError):
        HeartRateZoneModel.from_bounds("", "1", [("z", 100.0, 120.0)])
    with pytest.raises(ValueError):
        HeartRateZoneModel.from_bounds("d", "", [("z", 100.0, 120.0)])


def test_distribution_normalizes_and_sums_to_one():
    model = two_zone_model()
    dist = model.distribution({"z1": 30.0, "z2": 10.0})
    assert dist == pytest.approx({"z1": 0.75, "z2": 0.25})
    assert is_normalized(dist)
    assert abs(sum(dist.values()) - 1.0) <= DISTRIBUTION_TOLERANCE


def test_distribution_returns_none_when_no_time():
    model = two_zone_model()
    assert model.distribution({}) is None
    assert model.distribution({"z1": 0.0, "z2": 0.0}) is None


def test_distribution_rejects_negative_and_unknown_zones():
    model = two_zone_model()
    with pytest.raises(ValueError):
        model.distribution({"z1": -1.0, "z2": 10.0})
    with pytest.raises(ValueError):
        model.distribution({"nope": 10.0})


def test_validate_distribution_rejects_bad_sums_and_negatives():
    with pytest.raises(ValueError):
        validate_distribution("d", {})
    with pytest.raises(ValueError):
        validate_distribution("d", {"a": 0.5, "b": 0.6})
    with pytest.raises(ValueError):
        validate_distribution("d", {"a": -0.1, "b": 1.1})
    with pytest.raises(ValueError):
        validate_distribution("d", {"a": 0.0, "b": 0.0})
    cleaned = validate_distribution("d", {"a": 0.25, "b": 0.75})
    assert cleaned == {"a": 0.25, "b": 0.75}


def test_is_normalized_detects_non_normalized():
    assert is_normalized({"a": 0.5, "b": 0.5})
    assert not is_normalized({"a": 0.5, "b": 0.4})
    assert not is_normalized({"a": -0.1, "b": 1.1})


# ==========================================================================
# Energia metabolica
# ==========================================================================
def params(efficiency=0.25, version="v1", provenance="laboratorio"):
    return MetabolicParameters(
        gross_efficiency=efficiency, version=version, provenance=provenance
    )


def test_metabolic_parameters_require_provenance_and_version():
    with pytest.raises(ValueError):
        params(version="")
    with pytest.raises(ValueError):
        params(provenance="")
    with pytest.raises(ValueError):
        params(efficiency=0.0)
    with pytest.raises(ValueError):
        params(efficiency=1.5)
    # Eficiencia igual a 1 e o limite superior aceito.
    assert params(efficiency=1.0).gross_efficiency == 1.0


def test_metabolic_energy_none_without_parameters():
    """ADR-025, decisao 3: sem calibracao, a ausencia e None, nunca 0."""
    assert metabolic_energy_j(1000.0, None) is None


def test_metabolic_energy_scales_by_efficiency():
    assert metabolic_energy_j(1000.0, params(0.25)) == pytest.approx(4000.0)
    with pytest.raises(ValueError):
        metabolic_energy_j(-1.0, params())


# ==========================================================================
# Perfil do ciclista e contexto
# ==========================================================================
def test_rider_profile_is_optional_and_validated():
    empty = RiderProfile()
    assert empty.max_heart_rate_bpm is None
    assert empty.threshold_power_w is None
    ok = RiderProfile(max_heart_rate_bpm=185.0, threshold_power_w=280.0)
    assert ok.max_heart_rate_bpm == 185.0
    with pytest.raises(ValueError):
        RiderProfile(max_heart_rate_bpm=0.0)
    with pytest.raises(ValueError):
        RiderProfile(weight_kg=-1.0)


def test_prediction_context_validated():
    assert PredictionContext().duration_s is None
    with pytest.raises(ValueError):
        PredictionContext(duration_s=-1.0)
    with pytest.raises(ValueError):
        PredictionContext(temperature_c=float("inf"))
    with pytest.raises(ValueError):
        PredictionContext(notes=123)  # type: ignore[arg-type]


# ==========================================================================
# Contrato de previsao
# ==========================================================================
def make_prediction(**overrides):
    base = dict(
        borg_scale=DEFAULT_BORG_SCALE,
        predicted_rpe=13.0,
        heart_rate_zone_distribution=None,
        model_version="unavailable",
        status=PredictionStatus.AVAILABLE,
    )
    base.update(overrides)
    return PhysiologicalPrediction(**base)


def test_prediction_is_versioned_and_never_a_measurement():
    p = make_prediction()
    assert p.schema_version == PHYSIOLOGY_VERSION
    assert p.source == "prediction"
    assert p.is_measurement is False


def test_predicted_rpe_must_respect_scale_bounds():
    with pytest.raises(ValueError):
        make_prediction(predicted_rpe=5.0)  # abaixo de 6 (6-20)
    with pytest.raises(ValueError):
        make_prediction(predicted_rpe=25.0)  # acima de 20
    # CR10 tem outros limites.
    cr10 = make_prediction(borg_scale=BorgScale.CR10, predicted_rpe=8.0)
    assert cr10.predicted_rpe == 8.0
    with pytest.raises(ValueError):
        make_prediction(borg_scale=BorgScale.CR10, predicted_rpe=11.0)


def test_distribution_requires_identified_definition():
    with pytest.raises(ValueError):
        make_prediction(
            heart_rate_zone_distribution={"z1": 0.5, "z2": 0.5}
        )
    ok = make_prediction(
        heart_rate_zone_distribution={"z1": 0.5, "z2": 0.5},
        heart_rate_zone_definition="lthr@1",
    )
    assert ok.heart_rate_zone_distribution == {"z1": 0.5, "z2": 0.5}


def test_distribution_must_sum_to_one():
    with pytest.raises(ValueError):
        make_prediction(
            heart_rate_zone_distribution={"z1": 0.5, "z2": 0.6},
            heart_rate_zone_definition="lthr@1",
        )


def test_distribution_is_defensively_copied():
    source = {"z1": 0.5, "z2": 0.5}
    p = make_prediction(
        heart_rate_zone_distribution=source,
        heart_rate_zone_definition="lthr@1",
    )
    source["z1"] = 0.9
    assert p.heart_rate_zone_distribution == {"z1": 0.5, "z2": 0.5}


def test_confidence_requires_a_validation_method():
    with pytest.raises(ValueError):
        make_prediction(confidence=0.8)
    ok = make_prediction(confidence=0.8, confidence_method="cross-validation")
    assert ok.confidence == 0.8
    with pytest.raises(ValueError):
        make_prediction(confidence=1.5, confidence_method="cv")


def test_prediction_rejects_empty_model_version():
    with pytest.raises(ValueError):
        make_prediction(model_version="")


def test_unavailable_fields_lists_explicit_absences():
    p = make_prediction(heart_rate_zone_distribution=None)
    assert set(p.unavailable_fields) >= {
        "heart_rate_zone_distribution",
        "confidence",
        "metabolic_energy_j",
    }


def test_prediction_is_immutable():
    p = make_prediction()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.predicted_rpe = 10.0  # type: ignore[misc]


def test_prediction_to_dict_is_json_serializable():
    p = make_prediction(
        heart_rate_zone_distribution={"z1": 0.5, "z2": 0.5},
        heart_rate_zone_definition="lthr@1",
        confidence=0.7,
        confidence_method="cv",
        metabolic_energy_j=4000.0,
    )
    payload = p.to_dict()
    assert payload["schema_version"] == PHYSIOLOGY_VERSION
    assert payload["source"] == "prediction"
    assert payload["is_measurement"] is False
    assert payload["borg_scale"] == "6-20"
    json.dumps(payload)


# ==========================================================================
# Estado da previsao (diferenciacao de indisponibilidade)
# ==========================================================================
def test_status_is_available_only_for_available():
    assert PredictionStatus.AVAILABLE.is_available
    for status in (
        PredictionStatus.UNAVAILABLE,
        PredictionStatus.MODEL_NOT_CALIBRATED,
        PredictionStatus.INSUFFICIENT_DATA,
        PredictionStatus.CONTRACT_INCOMPATIBLE,
    ):
        assert not status.is_available


def test_status_must_be_prediction_status():
    with pytest.raises(ValueError):
        make_prediction(status="available")  # type: ignore[arg-type]


def test_available_requires_a_physiological_value():
    with pytest.raises(ValueError):
        make_prediction(predicted_rpe=None)


def test_limited_status_cannot_carry_physiological_values():
    for status in (
        PredictionStatus.UNAVAILABLE,
        PredictionStatus.MODEL_NOT_CALIBRATED,
        PredictionStatus.CONTRACT_INCOMPATIBLE,
    ):
        with pytest.raises(ValueError):
            make_prediction(status=status)  # predicted_rpe=13 por padrao


def test_insufficient_data_requires_missing_inputs():
    ok = make_prediction(
        predicted_rpe=None,
        status=PredictionStatus.INSUFFICIENT_DATA,
        missing_inputs=("weight_kg",),
    )
    assert ok.missing_inputs == ("weight_kg",)
    with pytest.raises(ValueError):
        make_prediction(predicted_rpe=None, status=PredictionStatus.INSUFFICIENT_DATA)


def test_missing_inputs_only_with_insufficient_data():
    with pytest.raises(ValueError):
        make_prediction(missing_inputs=("weight_kg",))


def test_limited_prediction_serializes_status_and_reason():
    p = make_prediction(
        predicted_rpe=None,
        status=PredictionStatus.MODEL_NOT_CALIBRATED,
        reason="sem calibracao",
    )
    payload = p.to_dict()
    assert payload["status"] == "model_not_calibrated"
    assert payload["reason"] == "sem calibracao"
    assert payload["predicted_rpe"] is None
    assert payload["missing_inputs"] == []
    json.dumps(payload)

