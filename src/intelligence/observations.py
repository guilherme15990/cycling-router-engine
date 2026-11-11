"""Contrato de observacao real e persistencia local (ADR-027, decisao 1).

O treino usa **observacoes reais** de esforco, com **identidade pseudonimizada**
do ciclista, **qualidade** e **proveniencia** explicitas e **alvo observado**
presente. Sem fonte real e sem alvo observado, **nao se treina**: esta camada
nunca fabrica alvos (um RPE derivado de formula **nao** e uma observacao).

A persistencia e **local** (arquivo JSON versionado), no mesmo espirito do
padrao de ``graph.serialize`` e de ``physiology.metabolic`` (``version`` +
``provenance``). Nada aqui acessa a rede.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from physical.effort import EFFORT_FEATURES_VERSION
from physiology import BorgScale, DEFAULT_BORG_SCALE

from ._validation import finite, non_empty_str, non_negative, positive
from .targets import TargetKind

#: Versao do contrato de observacao.
OBSERVATIONS_VERSION = "1"

#: Identificador/versao do formato de arquivo persistido.
DATASET_FORMAT = "intelligence-dataset"
DATASET_FORMAT_VERSION = "1"


class ObservationError(ValueError):
    """Erro de **dados** em uma observacao ou conjunto de observacoes."""


class MeasurementQuality(str, Enum):
    """Como o esforco observado foi obtido (nunca inferido)."""

    #: Medido por dispositivo (potencia/FC confiaveis).
    DEVICE_MEASURED = "device_measured"
    #: Relatado pelo proprio ciclista (ex.: Borg percebido).
    SELF_REPORTED = "self_reported"
    #: Qualidade desconhecida — marcada, **nao** silenciosamente aceita.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Origem versionada de um dado (reutiliza o padrao do projeto)."""

    source: str
    version: str
    notes: str | None = None

    def __post_init__(self) -> None:
        non_empty_str("source", self.source)
        non_empty_str("version", self.version)
        if self.notes is not None and not isinstance(self.notes, str):
            raise ObservationError("notes deve ser str ou None")

    def to_dict(self) -> dict[str, object]:
        return {"source": self.source, "version": self.version, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provenance":
        return cls(
            source=str(data["source"]),
            version=str(data["version"]),
            notes=data.get("notes"),
        )


@dataclass(frozen=True, slots=True)
class CyclistId:
    """Identificador **pseudonimizado** do ciclista.

    Valor estavel entre sessoes e **nao reversivel** (ver :func:`pseudonymize`).
    Nunca carrega dados pessoais e nunca e usado como feature — serve para
    validacao por ciclista (decisao 5).
    """

    value: str

    def __post_init__(self) -> None:
        non_empty_str("cyclist_id", self.value)

    def __str__(self) -> str:  # pragma: no cover - conveniencia
        return self.value


def pseudonymize(identifier: str, *, salt: str) -> CyclistId:
    """Pseudonimo **estavel e nao reversivel** a partir de um identificador.

    Usa ``sha256(salt || \\x00 || identifier)``. O ``salt`` e obrigatorio: sem
    ele nao ha pseudonimizacao; nao existe valor padrao (nao se "adivinha").
    """
    non_empty_str("identifier", identifier)
    non_empty_str("salt", salt)
    digest = hashlib.sha256(
        (salt + "\x00" + identifier).encode("utf-8")
    ).hexdigest()
    return CyclistId("cyc_" + digest[:32])


@dataclass(frozen=True, slots=True)
class ObservedEffort:
    """Um esforco **observado** associado a uma rota percorrida.

    Regras de honestidade:

    * precisa ter **ao menos um alvo observado** (RPE ou FC) — caso contrario a
      observacao e inutilizavel e a construcao falha;
    * o RPE e validado contra a **escala** declarada;
    * os dados de features (``feature_row``) sao opcionais e, quando presentes,
      devem ter sido obtidos pelo **mesmo** extrator do servico
      (``extract_effort_features``); a ``feature_schema_version`` amarra a
      compatibilidade.
    """

    observation_id: str
    cyclist_id: CyclistId
    duration_s: float
    distance_m: float
    provenance: Provenance
    borg_scale: BorgScale = DEFAULT_BORG_SCALE
    observed_rpe: float | None = None
    observed_heart_rate_bpm: float | None = None
    heart_rate_zone_durations_s: dict[str, float] | None = None
    heart_rate_zone_definition: str | None = None
    feature_schema_version: str = EFFORT_FEATURES_VERSION
    feature_row: dict[str, float | None] | None = None
    recorded_at: str | None = None
    quality: MeasurementQuality = MeasurementQuality.UNKNOWN
    notes: str | None = None

    def __post_init__(self) -> None:
        non_empty_str("observation_id", self.observation_id)
        if not isinstance(self.cyclist_id, CyclistId):
            raise ObservationError("cyclist_id deve ser um CyclistId")
        if not isinstance(self.provenance, Provenance):
            raise ObservationError("provenance deve ser um Provenance")
        if not isinstance(self.borg_scale, BorgScale):
            raise ObservationError("borg_scale deve ser um BorgScale")
        non_negative("duration_s", self.duration_s)
        non_negative("distance_m", self.distance_m)

        if self.observed_rpe is not None:
            self.borg_scale.require_valid(self.observed_rpe)
        if self.observed_heart_rate_bpm is not None:
            positive("observed_heart_rate_bpm", self.observed_heart_rate_bpm)

        if self.heart_rate_zone_durations_s is not None:
            if not isinstance(self.heart_rate_zone_definition, str) or not (
                self.heart_rate_zone_definition
            ):
                raise ObservationError(
                    "duracoes por zona exigem heart_rate_zone_definition"
                )
            for zone, value in self.heart_rate_zone_durations_s.items():
                non_empty_str("zona cardiaca", zone)
                non_negative(f"heart_rate_zone_durations_s[{zone!r}]", value)

        if not self.observed_targets:
            raise ObservationError(
                f"observacao {self.observation_id!r} sem alvo observado "
                "(RPE e/ou FC); nao se treina sem observacao"
            )

        if self.feature_row is not None:
            for name, value in self.feature_row.items():
                non_empty_str("nome de feature", name)
                if value is not None:
                    finite(f"feature_row[{name!r}]", value)

        if self.recorded_at is not None:
            try:
                datetime.fromisoformat(self.recorded_at)
            except ValueError as exc:
                raise ObservationError(
                    f"recorded_at invalido (ISO 8601 esperado): {self.recorded_at!r}"
                ) from exc

    @property
    def observed_targets(self) -> tuple[TargetKind, ...]:
        """Alvos efetivamente observados nesta entrada (ordem estavel)."""
        kinds: list[TargetKind] = []
        if self.observed_rpe is not None:
            kinds.append(TargetKind.RPE)
        if self.heart_rate_zone_durations_s is not None:
            kinds.append(TargetKind.HEART_RATE_ZONES)
        if self.observed_heart_rate_bpm is not None:
            kinds.append(TargetKind.HEART_RATE_POINT)
        return tuple(kinds)

    def has_target(self, kind: TargetKind) -> bool:
        return kind in self.observed_targets

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "cyclist_id": self.cyclist_id.value,
            "duration_s": self.duration_s,
            "distance_m": self.distance_m,
            "provenance": self.provenance.to_dict(),
            "borg_scale": self.borg_scale.value,
            "observed_rpe": self.observed_rpe,
            "observed_heart_rate_bpm": self.observed_heart_rate_bpm,
            "heart_rate_zone_durations_s": (
                None
                if self.heart_rate_zone_durations_s is None
                else dict(self.heart_rate_zone_durations_s)
            ),
            "heart_rate_zone_definition": self.heart_rate_zone_definition,
            "feature_schema_version": self.feature_schema_version,
            "feature_row": (
                None if self.feature_row is None else dict(self.feature_row)
            ),
            "recorded_at": self.recorded_at,
            "quality": self.quality.value,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObservedEffort":
        row = data.get("feature_row")
        durations = data.get("heart_rate_zone_durations_s")
        return cls(
            observation_id=str(data["observation_id"]),
            cyclist_id=CyclistId(str(data["cyclist_id"])),
            duration_s=float(data["duration_s"]),
            distance_m=float(data["distance_m"]),
            provenance=Provenance.from_dict(data["provenance"]),
            borg_scale=BorgScale(data.get("borg_scale", DEFAULT_BORG_SCALE.value)),
            observed_rpe=(
                None if data.get("observed_rpe") is None
                else float(data["observed_rpe"])
            ),
            observed_heart_rate_bpm=(
                None if data.get("observed_heart_rate_bpm") is None
                else float(data["observed_heart_rate_bpm"])
            ),
            heart_rate_zone_durations_s=(
                None if durations is None else {str(k): float(v) for k, v in durations.items()}
            ),
            heart_rate_zone_definition=data.get("heart_rate_zone_definition"),
            feature_schema_version=str(
                data.get("feature_schema_version", EFFORT_FEATURES_VERSION)
            ),
            feature_row=(
                None if row is None else {str(k): (None if v is None else float(v)) for k, v in row.items()}
            ),
            recorded_at=data.get("recorded_at"),
            quality=MeasurementQuality(data.get("quality", MeasurementQuality.UNKNOWN.value)),
            notes=data.get("notes"),
        )


@dataclass(frozen=True, slots=True)
class ObservationDataset:
    """Conjunto de observacoes reais, versionado e com proveniencia."""

    dataset_version: str
    observations: tuple[ObservedEffort, ...]
    provenance: Provenance
    notes: str | None = None

    def __post_init__(self) -> None:
        non_empty_str("dataset_version", self.dataset_version)
        if not isinstance(self.provenance, Provenance):
            raise ObservationError("provenance deve ser um Provenance")
        if not isinstance(self.observations, tuple):
            object.__setattr__(self, "observations", tuple(self.observations))
        for observation in self.observations:
            if not isinstance(observation, ObservedEffort):
                raise ObservationError(
                    "observations deve conter ObservedEffort"
                )
        ids = [observation.observation_id for observation in self.observations]
        if len(set(ids)) != len(ids):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ObservationError(f"observation_id duplicado: {duplicates!r}")

    def __len__(self) -> int:
        return len(self.observations)

    def __iter__(self):
        return iter(self.observations)

    def with_observation(self, observation: ObservedEffort) -> "ObservationDataset":
        """Devolve um novo conjunto com a observacao acrescentada."""
        return ObservationDataset(
            dataset_version=self.dataset_version,
            observations=self.observations + (observation,),
            provenance=self.provenance,
            notes=self.notes,
        )

    def with_target(self, kind: TargetKind) -> tuple[ObservedEffort, ...]:
        """Observacoes que possuem o alvo indicado (ordem preservada)."""
        return tuple(o for o in self.observations if o.has_target(kind))

    def to_dict(self) -> dict[str, object]:
        return {
            "format": DATASET_FORMAT,
            "format_version": DATASET_FORMAT_VERSION,
            "observations_version": OBSERVATIONS_VERSION,
            "dataset_version": self.dataset_version,
            "provenance": self.provenance.to_dict(),
            "notes": self.notes,
            "observations": [o.to_dict() for o in self.observations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObservationDataset":
        if data.get("format") != DATASET_FORMAT:
            raise ObservationError(
                f"formato inesperado: {data.get('format')!r} "
                f"(esperado {DATASET_FORMAT!r})"
            )
        if data.get("format_version") != DATASET_FORMAT_VERSION:
            raise ObservationError(
                f"versao de formato nao suportada: {data.get('format_version')!r} "
                f"(suportada {DATASET_FORMAT_VERSION!r})"
            )
        return cls(
            dataset_version=str(data["dataset_version"]),
            observations=tuple(
                ObservedEffort.from_dict(item) for item in data.get("observations", [])
            ),
            provenance=Provenance.from_dict(data["provenance"]),
            notes=data.get("notes"),
        )


def save_dataset(dataset: ObservationDataset, path: str | Path) -> Path:
    """Persiste o conjunto **localmente** (JSON versionado)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dataset.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def load_dataset(path: str | Path) -> ObservationDataset:
    """Le um conjunto persistido **localmente** (sem rede)."""
    source = Path(path)
    if not source.exists():
        raise ObservationError(f"conjunto de observacoes nao encontrado: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ObservationError(f"JSON invalido em {source}: {exc}") from exc
    return ObservationDataset.from_dict(payload)


def build_dataset(
    dataset_version: str,
    observations: Iterable[ObservedEffort],
    *,
    provenance: Provenance,
    notes: str | None = None,
) -> ObservationDataset:
    """Atalho para montar um ``ObservationDataset`` a partir de um iteravel."""
    return ObservationDataset(
        dataset_version=dataset_version,
        observations=tuple(observations),
        provenance=provenance,
        notes=notes,
    )
