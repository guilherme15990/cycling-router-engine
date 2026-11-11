"""Artefato de modelo local, registro e carregamento offline (ADR-027, decisao 4).

Um modelo treinado e persistido como **artefato local versionado**, com um
**registro** que liga ``model_version`` a ``dataset_version``, a versao de
**schema de features** (``EffortFeatures.schema_version``), ao **alvo**, ao
**metodo** de treino e a proveniencia. O carregamento le o arquivo **do disco**,
**sem rede**, e **verifica compatibilidade** antes de usar.

Modelos ausentes, incompatveis ou corrompidos sao tratados de forma explicita
(``ModelNotFoundError`` / ``ModelIncompatibleError`` / ``ModelFormatError``) —
nunca por download automatico nem por coerccao silenciosa.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._validation import non_empty_str
from .observations import Provenance
from .targets import TargetKind

#: Identificador e versao do formato de artefato.
MODEL_ARTIFACT_FORMAT = "intelligence-model"
MODEL_ARTIFACT_VERSION = "1"

#: Nome do arquivo de registro dentro do diretorio do armazenamento.
REGISTRY_FILENAME = "registry.json"

#: Caracteres proibidos em nomes de arquivo (Windows/Linux) sao trocados por ``_``.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class ModelArtifactError(RuntimeError):
    """Erro base do artefato/armazenamento de modelo."""


class ModelNotFoundError(ModelArtifactError):
    """Modelo ausente no armazenamento local."""


class ModelIncompatibleError(ModelArtifactError):
    """Modelo incompativel com o schema de features/contrato em uso."""


class ModelFormatError(ModelArtifactError):
    """Artefato ilegivel/corrompido/em formato desconhecido."""


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Modelo treinado, persistido localmente, com proveniencia completa."""

    model_version: str
    dataset_version: str
    effort_schema_version: str
    target: TargetKind
    method: str
    provenance: Provenance
    payload: dict[str, Any]
    is_baseline: bool = False
    calibration_method: str | None = None
    format: str = MODEL_ARTIFACT_FORMAT
    format_version: str = MODEL_ARTIFACT_VERSION
    notes: str | None = None

    def __post_init__(self) -> None:
        non_empty_str("model_version", self.model_version)
        non_empty_str("dataset_version", self.dataset_version)
        non_empty_str("effort_schema_version", self.effort_schema_version)
        non_empty_str("method", self.method)
        if not isinstance(self.target, TargetKind):
            raise ModelArtifactError("target deve ser um TargetKind")
        if not isinstance(self.provenance, Provenance):
            raise ModelArtifactError("provenance deve ser um Provenance")
        if not isinstance(self.payload, dict):
            raise ModelArtifactError("payload deve ser um dict")
        if self.calibration_method is not None:
            non_empty_str("calibration_method", self.calibration_method)

    def compatible_with(self, effort_schema_version: str) -> bool:
        """True se o modelo consome exatamente o schema de features informado."""
        return self.effort_schema_version == effort_schema_version

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "format_version": self.format_version,
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "effort_schema_version": self.effort_schema_version,
            "target": self.target.value,
            "method": self.method,
            "calibration_method": self.calibration_method,
            "is_baseline": self.is_baseline,
            "provenance": self.provenance.to_dict(),
            "payload": self.payload,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelArtifact":
        if data.get("format") != MODEL_ARTIFACT_FORMAT:
            raise ModelFormatError(
                f"formato de artefato inesperado: {data.get('format')!r}"
            )
        if data.get("format_version") != MODEL_ARTIFACT_VERSION:
            raise ModelFormatError(
                f"versao de artefato nao suportada: "
                f"{data.get('format_version')!r} "
                f"(suportada {MODEL_ARTIFACT_VERSION!r})"
            )
        try:
            target = TargetKind(data["target"])
        except (KeyError, ValueError) as exc:
            raise ModelFormatError(f"alvo invalido no artefato: {exc}") from exc
        return cls(
            model_version=str(data["model_version"]),
            dataset_version=str(data["dataset_version"]),
            effort_schema_version=str(data["effort_schema_version"]),
            target=target,
            method=str(data["method"]),
            provenance=Provenance.from_dict(data["provenance"]),
            payload=dict(data.get("payload") or {}),
            is_baseline=bool(data.get("is_baseline", False)),
            calibration_method=data.get("calibration_method"),
            notes=data.get("notes"),
        )

    def registry_entry(self) -> dict[str, Any]:
        """Metadados resumidos que entram no registro do armazenamento."""
        return {
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "effort_schema_version": self.effort_schema_version,
            "target": self.target.value,
            "method": self.method,
            "calibration_method": self.calibration_method,
            "is_baseline": self.is_baseline,
            "provenance": self.provenance.to_dict(),
        }


def check_compatibility(
    artifact: ModelArtifact, effort_schema_version: str
) -> None:
    """Levanta ``ModelIncompatibleError`` se o modelo nao for compativel."""
    if artifact.format_version != MODEL_ARTIFACT_VERSION:
        raise ModelIncompatibleError(
            f"versao de artefato nao suportada: {artifact.format_version!r}"
        )
    if not artifact.compatible_with(effort_schema_version):
        raise ModelIncompatibleError(
            "modelo incompativel com o schema de features: "
            f"modelo={artifact.effort_schema_version!r}, "
            f"em uso={effort_schema_version!r}"
        )


class ModelStore:
    """Armazenamento **local** de artefatos de modelo + registro.

    Layout: ``{root}/{filename}.json`` para cada artefato e
    ``{root}/{REGISTRY_FILENAME}`` para o catalogo. O nome de arquivo e derivado
    de ``model_version`` de forma **estavel** e **segura** para o sistema de
    arquivos (o ``model_version`` canonico permanece como chave do registro, com
    ``:`` etc.). Nada acessa a rede.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # -- caminhos ---------------------------------------------------------
    def path_for(self, model_version: str) -> Path:
        non_empty_str("model_version", model_version)
        return self.root / _artifact_filename(model_version)

    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_FILENAME

    # -- artefatos --------------------------------------------------------
    def save(self, artifact: ModelArtifact) -> Path:
        """Grava o artefato **localmente** e atualiza o registro."""
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(artifact.model_version)
        _write_json_atomic(destination, artifact.to_dict())
        registry = self._read_registry()
        registry[artifact.model_version] = artifact.registry_entry()
        _write_json_atomic(self.registry_path, registry)
        return destination

    def load(self, model_version: str) -> ModelArtifact:
        """Le o artefato **do disco** (sem rede)."""
        source = self.path_for(model_version)
        if not source.exists():
            raise ModelNotFoundError(
                f"modelo ausente no armazenamento local: {model_version!r} "
                f"({source})"
            )
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelFormatError(
                f"artefato corrompido (JSON invalido) em {source}: {exc}"
            ) from exc
        return ModelArtifact.from_dict(payload)

    def load_compatible(
        self, model_version: str, effort_schema_version: str
    ) -> ModelArtifact:
        """Carrega e **verifica compatibilidade** com o schema de features."""
        artifact = self.load(model_version)
        check_compatibility(artifact, effort_schema_version)
        return artifact

    def exists(self, model_version: str) -> bool:
        return self.path_for(model_version).exists()

    def list_versions(self) -> tuple[str, ...]:
        """Versoes registradas (ordem estavel)."""
        return tuple(sorted(self._read_registry()))

    # -- registro ---------------------------------------------------------
    def registry(self) -> dict[str, dict[str, Any]]:
        return self._read_registry()

    def find(
        self,
        *,
        target: TargetKind | None = None,
        effort_schema_version: str | None = None,
        is_baseline: bool | None = None,
    ) -> tuple[str, ...]:
        """Versoes no registro que satisfazem os filtros (ordem estavel)."""
        matches: list[str] = []
        for version, entry in self._read_registry().items():
            if target is not None and entry.get("target") != target.value:
                continue
            if (
                effort_schema_version is not None
                and entry.get("effort_schema_version") != effort_schema_version
            ):
                continue
            if is_baseline is not None and bool(entry.get("is_baseline")) != is_baseline:
                continue
            matches.append(version)
        return tuple(sorted(matches))

    def _read_registry(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensivo
            raise ModelFormatError(
                f"registro corrompido em {self.registry_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):  # pragma: no cover - defensivo
            raise ModelFormatError("registro deve ser um objeto JSON")
        return data


def _artifact_filename(model_version: str) -> str:
    """Nome de arquivo **seguro e estavel** derivado de ``model_version``.

    Troca caracteres proibidos por ``_`` e acrescenta um digest curto do valor
    canonico, de modo que versoes distintas nunca colidam no sistema de arquivos
    (o ``model_version`` original continua sendo a chave do registro).
    """
    safe = _UNSAFE_FILENAME.sub("_", model_version).strip("._-")[:80] or "model"
    digest = hashlib.sha1(model_version.encode("utf-8")).hexdigest()[:8]
    return f"{safe}.{digest}.json"


def _write_json_atomic(destination: Path, payload: Any) -> None:
    """Escrita JSON o mais atomica possivel (temp + replace)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(destination)
