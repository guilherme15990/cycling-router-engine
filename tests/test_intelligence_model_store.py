"""Artefato de modelo local, registro e carregamento offline (ADR-027, inc. 4/6).

Cobrem:

* persistencia **local** versionada (save/load) com registro
  ``model_version`` <-> ``dataset_version`` <-> schema de features <-> alvo;
* **compatibilidade** verificada no carregamento (``CONTRACT_INCOMPATIBLE``);
* artefato **ausente**/**corrompido** tratado de forma explicita — nunca por
  download nem por coercao silenciosa;
* o nome de arquivo e seguro (o ``model_version`` canonico pode conter ``:``).
"""

import json

import pytest

from intelligence import (
    MODEL_ARTIFACT_FORMAT,
    ModelArtifact,
    ModelFormatError,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    Provenance,
    TargetKind,
    check_compatibility,
)


def provenance() -> Provenance:
    return Provenance(source="intelligence.training/baseline", version="1")


def artifact(
    model_version="m:rpe:v1",
    *,
    dataset_version="v1",
    effort_schema_version="1",
    target=TargetKind.RPE,
    is_baseline=True,
    calibration_method=None,
    payload=None,
) -> ModelArtifact:
    return ModelArtifact(
        model_version=model_version,
        dataset_version=dataset_version,
        effort_schema_version=effort_schema_version,
        target=target,
        method="baseline-constant",
        provenance=provenance(),
        payload=payload if payload is not None else {"kind": "constant", "value": 13.0},
        is_baseline=is_baseline,
        calibration_method=calibration_method,
    )


# ==========================================================================
# Persistencia local e registro
# ==========================================================================
def test_save_and_load_round_trip(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact()
    store.save(original)
    assert store.load(original.model_version) == original


def test_filename_is_safe_and_version_key_is_canonical(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact("linear-candidate:rpe:v1")
    path = store.save(original)
    # O ': ' nao vai para o nome de arquivo...
    assert ":" not in path.name
    # ...mas o identificador canonico permanece na chave do registro.
    assert original.model_version in store.registry()


def test_registry_links_version_dataset_schema_and_target(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact(
        "m:rpe:v2",
        dataset_version="2026-01",
        effort_schema_version="1",
        target=TargetKind.HEART_RATE_POINT,
    )
    store.save(original)
    entry = store.registry()[original.model_version]
    assert entry["dataset_version"] == "2026-01"
    assert entry["effort_schema_version"] == "1"
    assert entry["target"] == "heart_rate_point"


def test_list_versions_and_exists(tmp_path):
    store = ModelStore(tmp_path / "models")
    assert store.list_versions() == ()
    store.save(artifact("a:rpe:v1"))
    store.save(artifact("b:rpe:v1"))
    assert store.list_versions() == ("a:rpe:v1", "b:rpe:v1")
    assert store.exists("a:rpe:v1")
    assert not store.exists("nope")


def test_find_filters_by_target_schema_and_baseline(tmp_path):
    store = ModelStore(tmp_path / "models")
    store.save(artifact("base:rpe:v1", is_baseline=True))
    store.save(
        artifact(
            "cand:rpe:v1",
            is_baseline=False,
            calibration_method="ols@1",
        )
    )
    store.save(
        artifact(
            "hr:v1",
            target=TargetKind.HEART_RATE_POINT,
            is_baseline=False,
        )
    )
    assert store.find(target=TargetKind.RPE) == ("base:rpe:v1", "cand:rpe:v1")
    assert store.find(is_baseline=True) == ("base:rpe:v1",)
    assert store.find(target=TargetKind.HEART_RATE_POINT) == ("hr:v1",)


# ==========================================================================
# Ausencia / corrupcao / incompatibilidade
# ==========================================================================
def test_missing_model_raises_not_found(tmp_path):
    store = ModelStore(tmp_path / "models")
    with pytest.raises(ModelNotFoundError):
        store.load("nope")


def test_corrupt_artifact_raises_format_error(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact()
    path = store.save(original)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ModelFormatError):
        store.load(original.model_version)


def test_unknown_format_is_rejected(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact()
    path = store.save(original)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["format"] = "other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelFormatError):
        store.load(original.model_version)


def test_incompatible_schema_is_refused_on_load(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact(effort_schema_version="99")
    store.save(original)
    # Carregar cru funciona...
    assert store.load(original.model_version) == original
    # ...mas o carregamento compativel recusa schema divergente.
    with pytest.raises(ModelIncompatibleError):
        store.load_compatible(original.model_version, "1")


def test_load_compatible_accepts_matching_schema(tmp_path):
    store = ModelStore(tmp_path / "models")
    original = artifact(effort_schema_version="1")
    store.save(original)
    assert store.load_compatible(original.model_version, "1") == original


def test_check_compatibility_rejects_mismatch():
    with pytest.raises(ModelIncompatibleError):
        check_compatibility(artifact(effort_schema_version="2"), "1")
    # Sem erro quando o schema casa.
    check_compatibility(artifact(effort_schema_version="1"), "1")


def test_artifact_requires_provenance_and_target():
    with pytest.raises(Exception):
        ModelArtifact(
            model_version="m",
            dataset_version="v",
            effort_schema_version="1",
            target="rpe",  # type: ignore[arg-type]
            method="x",
            provenance=provenance(),
            payload={},
        )


def test_artifact_to_dict_declares_format():
    payload = artifact().to_dict()
    assert payload["format"] == MODEL_ARTIFACT_FORMAT
    assert payload["format_version"] == "1"
    assert payload["target"] == "rpe"
