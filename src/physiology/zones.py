"""Zonas cardiacas por **definicao identificada** (sem percentuais implicitos).

Uma zona e um intervalo explicito de frequencia cardiaca (bpm). O modelo de
zonas carrega um **identificador de definicao** e uma **versao**: nao ha
percentuais de FC maxima escondidos no codigo nem zonas "padrao" implicitamente
calibradas (ADR-025, decisao 8).

A distribuicao de tempo por zona e derivada de duracoes por zona e normalizada
para somar 1, dentro da tolerancia numerica — ou ``None`` quando nao ha tempo.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ._validation import finite, non_negative, positive

#: Tolerancia numerica para "a distribuicao soma 1".
DISTRIBUTION_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class HeartRateZoneDefinition:
    """Uma zona cardiaca: nome e intervalo explicito de bpm."""

    name: str
    lower_bpm: float
    upper_bpm: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("zona cardiaca exige nome nao vazio")
        non_negative("lower_bpm", self.lower_bpm)
        positive("upper_bpm", self.upper_bpm)
        if self.upper_bpm <= self.lower_bpm:
            raise ValueError(
                f"zona {self.name!r}: upper_bpm ({self.upper_bpm!r}) deve ser "
                f"maior que lower_bpm ({self.lower_bpm!r})"
            )


def validate_distribution(
    name: str,
    distribution: Mapping[str, float],
    *,
    tolerance: float = DISTRIBUTION_TOLERANCE,
) -> dict[str, float]:
    """Valida uma distribuicao nao negativa que soma 1 (dentro da tolerancia).

    Devolve uma **copia** imutavel-por-convencao (dict comum) dos valores.
    """
    if not distribution:
        raise ValueError(f"{name} nao pode ser vazia")
    cleaned: dict[str, float] = {}
    total = 0.0
    for zone, value in distribution.items():
        if not isinstance(zone, str) or not zone:
            raise ValueError(f"{name}: nome de zona invalido ({zone!r})")
        non_negative(f"{name}[{zone!r}]", value)
        cleaned[zone] = float(value)
        total += float(value)
    if total <= 0.0:
        raise ValueError(f"{name} soma zero; use None para representar ausencia")
    if abs(total - 1.0) > tolerance:
        raise ValueError(
            f"{name} deve somar 1 (recebido {total!r}, tolerancia {tolerance!r})"
        )
    return cleaned


@dataclass(frozen=True, slots=True)
class HeartRateZoneModel:
    """Modelo de zonas cardiacas com definicao e versao identificadas.

    As bordas sao **explicitas** em bpm. ``definition`` identifica a definicao
    (ex. um metodo de limiar) e ``version`` a sua versao. Nao ha percentuais
    implicatos de FC maxima.
    """

    definition: str
    version: str
    zones: tuple[HeartRateZoneDefinition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.definition, str) or not self.definition:
            raise ValueError("HeartRateZoneModel exige definition nao vazia")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("HeartRateZoneModel exige version nao vazia")
        if not self.zones:
            raise ValueError("HeartRateZoneModel exige ao menos uma zona")
        names = [zone.name for zone in self.zones]
        if len(set(names)) != len(names):
            raise ValueError(f"nomes de zona duplicados: {names!r}")

    @classmethod
    def from_bounds(
        cls,
        definition: str,
        version: str,
        bounds: Sequence[tuple[str, float, float]],
    ) -> "HeartRateZoneModel":
        """Constroi o modelo a partir de ``(nome, lower_bpm, upper_bpm)``."""
        return cls(
            definition=definition,
            version=version,
            zones=tuple(
                HeartRateZoneDefinition(name=name, lower_bpm=low, upper_bpm=high)
                for name, low, high in bounds
            ),
        )

    @property
    def zone_names(self) -> tuple[str, ...]:
        return tuple(zone.name for zone in self.zones)

    @property
    def identifier(self) -> str:
        """Identificador completo e estavel da definicao."""
        return f"{self.definition}@{self.version}"

    def distribution(
        self, durations_s: Mapping[str, float]
    ) -> dict[str, float] | None:
        """Distribuicao normalizada do tempo por zona, ou ``None`` sem tempo.

        ``durations_s`` mapeia nome de zona -> duracao em segundos. Zonas
        ausentes entram com 0. Quando o tempo total e zero, devolve ``None``
        (ausencia explicita), nunca uma distribuicao fabricada.
        """
        unknown = set(durations_s) - set(self.zone_names)
        if unknown:
            raise ValueError(
                f"duracoes para zonas desconhecidas: {sorted(unknown)!r}"
            )
        non_negative_total = 0.0
        cleaned: dict[str, float] = {}
        for name in self.zone_names:
            value = float(durations_s.get(name, 0.0))
            non_negative(f"durations_s[{name!r}]", value)
            cleaned[name] = value
            non_negative_total += value
        if non_negative_total <= 0.0:
            return None
        return {name: value / non_negative_total for name, value in cleaned.items()}


def is_normalized(
    distribution: Mapping[str, float],
    *,
    tolerance: float = DISTRIBUTION_TOLERANCE,
) -> bool:
    """True se a distribuicao e nao negativa e soma 1 dentro da tolerancia."""
    total = 0.0
    for value in distribution.values():
        if not math.isfinite(value) or value < 0.0:
            return False
        total += value
    return abs(total - 1.0) <= tolerance
