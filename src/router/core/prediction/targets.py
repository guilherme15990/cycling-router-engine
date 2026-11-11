"""Alvos de previsao e o contrato de derivacao de zonas (ADR-027, decisao 2).

Os alvos previstos sao declarados explicitamente e **separados**:

* **RPE** na escala de Borg identificada (padrao 6-20);
* **frequencia cardiaca** — pontual (bpm) e/ou distribuicao de tempo por zona,
  sob uma **definicao de zona individualizada e identificada**;
* **energia metabolica** permanece **separada** (``physiology.metabolic``):
  depende so de parametros explicitos e **nao** entra na previsao de RPE/FC.

Cada alvo so existe quando a **observacao correspondente** existe na **mesma**
definicao (ver ``intelligence.observations``). A escala e a definicao de zonas
precisam ser identicas entre treino e inferencia.

A derivacao individualizada das bordas de zona exige um **metodo nomeado e
versionado** registrado explicitamente. **Nenhum** metodo com percentuais de FC
maxima escondidos e embutido — sem metodo registrado, a derivacao e
explicitamente indisponivel (ADR-025, decisao 8).
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from router.core.physiology import HeartRateZoneModel, RiderProfile

#: Versao do contrato de alvos.
TARGETS_VERSION = "1"


class TargetKind(str, Enum):
    """Natureza do alvo previsto/observado."""

    #: Percepcao subjetiva de esforco (escala de Borg identificada).
    RPE = "rpe"
    #: Distribuicao de tempo por zona cardiaca (definicao identificada).
    HEART_RATE_ZONES = "heart_rate_zones"
    #: Frequencia cardiaca pontual (bpm).
    HEART_RATE_POINT = "heart_rate_point"


class ZoneDerivationUnavailable(RuntimeError):
    """Nao ha metodo de derivacao de zonas registrado/intercambiavel."""


@runtime_checkable
class HeartRateZoneDerivationMethod(Protocol):
    """Metodo **nomeado e versionado** para derivar zonas de um ciclista.

    O metodo recebe um ``RiderProfile`` e devolve um ``HeartRateZoneModel`` com
    ``definition`` + ``version`` e bordas **explicitas** em bpm. Percentuais de
    FC maxima, quando usados, precisam ser **declarados no proprio metodo** e
    identificados por ``method_id``+``version`` — nunca escondidos no codigo.
    """

    @property
    def method_id(self) -> str:
        """Identificador estavel do metodo (ex.: ``threshold-lthr``)."""

    @property
    def version(self) -> str:
        """Versao do metodo."""

    def derive(self, profile: RiderProfile) -> HeartRateZoneModel:
        """Deriva o modelo de zonas, ou levanta erro se faltarem dados."""


#: Registro de metodos de derivacao (vazio por padrao: sem percentuais embutidos).
ZONE_DERIVATION_METHODS: dict[str, HeartRateZoneDerivationMethod] = {}


def register_zone_derivation(method: HeartRateZoneDerivationMethod) -> str:
    """Registra um metodo de derivacao e devolve o identificador completo."""
    identifier = f"{method.method_id}@{method.version}"
    ZONE_DERIVATION_METHODS[identifier] = method
    return identifier


def get_zone_derivation(method_id: str) -> HeartRateZoneDerivationMethod:
    """Recupera um metodo registrado, ou levanta ``ZoneDerivationUnavailable``."""
    try:
        return ZONE_DERIVATION_METHODS[method_id]
    except KeyError as exc:
        raise ZoneDerivationUnavailable(
            f"metodo de derivacao de zonas nao registrado: {method_id!r} "
            "(nenhum percentual implicito e aplicado)"
        ) from exc


def derive_heart_rate_zones(
    profile: RiderProfile, method_id: str
) -> HeartRateZoneModel:
    """Deriva zonas do ciclista usando um metodo **explicito** registrado."""
    method = get_zone_derivation(method_id)
    model = method.derive(profile)
    if not isinstance(model, HeartRateZoneModel):  # pragma: no cover - defensivo
        raise ZoneDerivationUnavailable(
            f"metodo {method_id!r} nao devolveu um HeartRateZoneModel"
        )
    return model
