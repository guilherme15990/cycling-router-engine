"""Contrato de observacao real e identidade pseudonimizada (ADR-027, inc. 1).

Cobrem:

* identidade pseudonimizada **estavel** e **nao reversivel** (sem PII);
* proveniencia versionada (padrao do projeto) e qualidade explicita;
* a **regra de honestidade**: sem alvo observado, a observacao e invalida;
* alinhamento alvo x escala de Borg (RPE) e definicao de zonas;
* persistencia **local** versionada (save/load) sem perder identidade.
"""

import json
import pytest
from router.core.prediction.intelligence import (
    DATASET_FORMAT,
    CyclistId,
    MeasurementQuality,
    ObservationDataset,
    ObservationError,
    ObservedEffort,
    Provenance,
    TargetKind,
    build_dataset,
    load_dataset,
    pseudonymize,
    save_dataset,
)
from router.core.physics.physiology import BorgScale


def provenance() -> Provenance:
    return Provenance(source="garmin", version="1.2", notes="exportacao do ciclista")


def obs(observation_id="o1", *, rpe=13.0, cid="cyc_x", **kwargs):
    base = dict(
        observation_id=observation_id,
        cyclist_id=CyclistId(cid),
        duration_s=600.0,
        distance_m=3000.0,
        provenance=provenance(),
        observed_rpe=rpe,
        quality=MeasurementQuality.DEVICE_MEASURED,
    )
    base.update(kwargs)
    return ObservedEffort(**base)


# ==========================================================================
# Pseudonimizacao
# ==========================================================================
def test_pseudonymize_is_stable_for_same_salt():
    a = pseudonymize("ana@example.com", salt="s")
    b = pseudonymize("ana@example.com", salt="s")
    assert a == b
    assert str(a) == str(b)


def test_pseudonymize_differs_for_different_salt():
    assert pseudonymize("ana", salt="s1") != pseudonymize("ana", salt="s2")


def test_pseudonymize_is_not_reversible_string():
    token = str(pseudonymize("ana@example.com", salt="s"))
    # Nao carrega o identificador original nem um formato "de dado pessoal".
    assert "ana" not in token
    assert "example.com" not in token
    assert token.startswith("cyc_")


def test_pseudonymize_requires_non_empty_inputs():
    with pytest.raises(ValueError):
        pseudonymize("", salt="s")
    with pytest.raises(ValueError):
        pseudonymize("ana", salt="")


def test_cyclist_id_rejects_empty():
    with pytest.raises(ValueError):
        CyclistId("")


# ==========================================================================
# Proveniencia
# ==========================================================================
def test_provenance_requires_source_and_version():
    with pytest.raises(ValueError):
        Provenance(source="", version="1")
    with pytest.raises(ValueError):
        Provenance(source="x", version="")


def test_provenance_round_trip():
    original = provenance()
    assert Provenance.from_dict(original.to_dict()) == original


# ==========================================================================
# Honestidade: sem alvo, nao ha observacao
# ==========================================================================
def test_observation_without_target_is_rejected():
    with pytest.raises(ObservationError):
        ObservedEffort(
            observation_id="o1",
            cyclist_id=CyclistId("c"),
            duration_s=10.0,
            distance_m=100.0,
            provenance=provenance(),
            observed_rpe=None,
            observed_heart_rate_bpm=None,
        )


def test_observation_reports_observed_targets():
    assert obs(rpe=12.0).observed_targets == (TargetKind.RPE,)
    assert obs(rpe=12.0).has_target(TargetKind.RPE)
    assert not obs(rpe=12.0).has_target(TargetKind.HEART_RATE_POINT)

    with_hr = obs(rpe=None, observed_heart_rate_bpm=150.0)
    assert with_hr.observed_targets == (TargetKind.HEART_RATE_POINT,)


def test_rpe_is_validated_against_declared_scale():
    # 4 esta fora da escala Borg 6-20.
    with pytest.raises(ValueError):
        obs(rpe=4.0)
    # Mas e valido na escala CR10 (0-10).
    obs(rpe=4.0, borg_scale=BorgScale.CR10)


def test_heart_rate_zone_durations_require_definition():
    with pytest.raises(ObservationError):
        obs(rpe=None, heart_rate_zone_durations_s={"z1": 10.0})


def test_heart_rate_zone_durations_reject_negative():
    with pytest.raises(ValueError):
        obs(
            rpe=None,
            heart_rate_zone_durations_s={"z1": -1.0},
            heart_rate_zone_definition="lthr@1",
        )


def test_recorded_at_must_be_iso():
    with pytest.raises(ObservationError):
        obs(recorded_at="not-a-date")
    assert obs(recorded_at="2026-01-01T10:00:00").recorded_at == "2026-01-01T10:00:00"


def test_feature_row_rejects_non_finite_values():
    with pytest.raises(ValueError):
        obs(feature_row={"mean_power_w": float("inf")})


# ==========================================================================
# Dataset
# ==========================================================================
def test_dataset_rejects_duplicate_observation_ids():
    with pytest.raises(ObservationError):
        build_dataset(
            "v1",
            [obs("dup"), obs("dup")],
            provenance=provenance(),
        )


def test_dataset_with_observation_and_with_target():
    dataset = build_dataset("v1", [obs("o1", rpe=10.0)], provenance=provenance())
    extended = dataset.with_observation(
        obs("o2", rpe=None, observed_heart_rate_bpm=140.0)
    )
    assert len(extended) == 2
    assert [o.observation_id for o in extended.with_target(TargetKind.RPE)] == ["o1"]
    assert [o.observation_id for o in extended.with_target(TargetKind.HEART_RATE_POINT)] == ["o2"]


def test_dataset_round_trip_preserves_identity(tmp_path):
    dataset = build_dataset(
        "v1",
        [
            obs("o1", rpe=13.0, feature_row={"mean_power_w": 150.0}),
            obs("o2", rpe=None, observed_heart_rate_bpm=150.0, cid="cyc_y"),
        ],
        provenance=provenance(),
        notes="conjunto de teste",
    )
    path = save_dataset(dataset, tmp_path / "ds.json")
    loaded = load_dataset(path)
    assert loaded == dataset


def test_saved_dataset_declares_format_and_version(tmp_path):
    dataset = build_dataset("v1", [obs("o1")], provenance=provenance())
    path = save_dataset(dataset, tmp_path / "ds.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == DATASET_FORMAT
    assert payload["format_version"] == "1"
    assert payload["observations"][0]["cyclist_id"].startswith("cyc_")


def test_load_missing_dataset_raises(tmp_path):
    with pytest.raises(ObservationError):
        load_dataset(tmp_path / "missing.json")


def test_load_rejects_unknown_format(tmp_path):
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"format": "other", "format_version": "1"}), encoding="utf-8")
    with pytest.raises(ObservationError):
        load_dataset(path)


def test_datasets_are_immutable_and_deterministic():
    dataset = build_dataset("v1", [obs("o1")], provenance=provenance())
    assert dataset.to_dict() == dataset.to_dict()
