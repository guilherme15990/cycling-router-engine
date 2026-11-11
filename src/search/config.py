"""Configuracao da busca multiobjetivo.

Os limites aqui sao controles de **desempenho**, nao garantias de otimalidade:
cortar labels pode remover rotas nao dominadas. ``speed_step_mps`` fica
reservado para a futura discretizacao do estado fisico (velocidade), que ainda
nao foi definida.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Parametros de execucao da busca."""

    max_labels_per_node: int | None = None
    speed_step_mps: float = 0.5
    epsilon_dominance: float = 0.0
    max_expansions: int | None = None

    def __post_init__(self) -> None:
        if self.max_labels_per_node is not None:
            _positive_int("max_labels_per_node", self.max_labels_per_node)
        if self.max_expansions is not None:
            _positive_int("max_expansions", self.max_expansions)
        if (
            not math.isfinite(self.speed_step_mps)
            or self.speed_step_mps <= 0.0
        ):
            raise ValueError(
                "speed_step_mps deve ser positivo e finito "
                f"(recebido {self.speed_step_mps!r})"
            )
        if (
            not math.isfinite(self.epsilon_dominance)
            or self.epsilon_dominance < 0.0
        ):
            raise ValueError(
                "epsilon_dominance deve ser nao negativo e finito "
                f"(recebido {self.epsilon_dominance!r})"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_labels_per_node": self.max_labels_per_node,
            "speed_step_mps": self.speed_step_mps,
            "epsilon_dominance": self.epsilon_dominance,
            "max_expansions": self.max_expansions,
        }


def _positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} deve ser um inteiro positivo (recebido {value!r})")
