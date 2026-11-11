"""Objetivos da busca multiobjetivo e objetivos por perfil.

A busca nao decide fisica nem preferencia de superficie. Ela apenas acumula
metricas e mantem alternativas nao dominadas. *Quais* metricas entram na
comparacao e definido por um ``ObjectiveSet``.

Um ``ObjectiveSet`` e apenas uma lista ordenada de nomes de metricas; a
comparacao usa ``dominates`` (minimizacao em todos os objetivos). Definir qual
conjunto cada perfil usa e uma decisao de produto, nao do algoritmo.

Observacao importante: dois perfis que usam o *mesmo* conjunto de objetivos tem
a mesma fronteira matematica; a preferencia apenas muda a selecao final. Por
isso os perfis abaixo usam conjuntos realmente diferentes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True, slots=True)
class ObjectiveSet:
    """Conjunto nomeado de objetivos a minimizar."""

    name: str
    objectives: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ObjectiveSet.name nao pode ser vazio")
        if not self.objectives:
            raise ValueError(
                f"ObjectiveSet {self.name!r}: nenhum objetivo definido"
            )
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError(
                f"ObjectiveSet {self.name!r}: objetivos duplicados "
                f"({self.objectives!r})"
            )
        for objective in self.objectives:
            if not isinstance(objective, str) or not objective:
                raise ValueError(
                    f"ObjectiveSet {self.name!r}: objetivo invalido "
                    f"({objective!r})"
                )

    def values(self, source: Any) -> tuple[float, ...]:
        """Extrai os valores na ordem dos objetivos.

        ``source`` pode ser um ``Mapping`` (nome -> valor) ou um objeto com
        atributos nomeados (por exemplo ``RouteMetrics`` ou um estado futuro).
        """
        return tuple(_value_of(source, name) for name in self.objectives)

    def dominates(self, a: Any, b: Any) -> bool:
        """``a`` domina ``b`` segundo este conjunto de objetivos."""
        return dominates(self.values(a), self.values(b))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "objectives": list(self.objectives)}

    def __len__(self) -> int:
        return len(self.objectives)

    def __iter__(self) -> Iterator[str]:
        return iter(self.objectives)


def _value_of(source: Any, name: str) -> float:
    if isinstance(source, Mapping):
        raw = source[name]
    else:
        raw = getattr(source, name)
    return float(raw)


def dominates(values_a: Sequence[float], values_b: Sequence[float]) -> bool:
    """Dominancia de Pareto por minimizacao.

    ``values_a`` domina ``values_b`` quando nao e pior em nenhum objetivo e
    estritamente melhor em pelo menos um. Igualdade nao e dominancia.
    """
    if len(values_a) != len(values_b):
        raise ValueError(
            "vetores de objetivo com tamanhos diferentes "
            f"({len(values_a)} != {len(values_b)})"
        )
    strictly_better = False
    for value_a, value_b in zip(values_a, values_b):
        if value_a > value_b:
            return False
        if value_a < value_b:
            strictly_better = True
    return strictly_better


def epsilon_dominates(
    values_a: Sequence[float],
    values_b: Sequence[float],
    epsilon: float,
) -> bool:
    """Dominancia com tolerancia ``epsilon`` por objetivo.

    Usada para conter explosao de labels: ``b`` e descartado quando ``a`` nao e
    pior por mais de ``epsilon`` e e melhor por mais de ``epsilon`` em algum
    objetivo. Com ``epsilon = 0`` recai em ``dominates``. E uma aproximacao:
    pode descartar labels nao dominadas no sentido estrito.
    """
    if epsilon < 0.0:
        raise ValueError(f"epsilon deve ser >= 0 (recebido {epsilon!r})")
    if len(values_a) != len(values_b):
        raise ValueError(
            "vetores de objetivo com tamanhos diferentes "
            f"({len(values_a)} != {len(values_b)})"
        )
    if epsilon == 0.0:
        return dominates(values_a, values_b)

    strictly_better = False
    for value_a, value_b in zip(values_a, values_b):
        if value_a > value_b + epsilon:
            return False
        if value_a < value_b - epsilon:
            strictly_better = True
    return strictly_better


# ---------------------------------------------------------------------------
# Objetivos por perfil (decisao de produto)
# ---------------------------------------------------------------------------
PROFILE_OBJECTIVES: dict[str, ObjectiveSet] = {
    "fastest": ObjectiveSet("fastest", ("time_s", "ascent_m")),
    "shortest": ObjectiveSet("shortest", ("distance_m", "time_s")),
    "flattest": ObjectiveSet("flattest", ("ascent_m", "distance_m")),
    "balanced": ObjectiveSet("balanced", ("time_s", "distance_m", "ascent_m")),
    "electric": ObjectiveSet("electric", ("energy_j", "time_s")),
}

PROFILE_NAMES: tuple[str, ...] = tuple(PROFILE_OBJECTIVES)


def objectives_for(profile: str) -> ObjectiveSet:
    """Devolve o ``ObjectiveSet`` de um perfil, normalizando o nome."""
    key = profile.strip().lower()
    try:
        return PROFILE_OBJECTIVES[key]
    except KeyError:
        raise ValueError(
            f"perfil desconhecido: {profile!r} "
            f"(use: {', '.join(PROFILE_NAMES)})"
        ) from None


# ---------------------------------------------------------------------------
# Objetivos tipados e perfil de busca
# ---------------------------------------------------------------------------
class Objective(str, Enum):
    """Metricas que podem participar da dominancia."""

    TIME = "time_s"
    DISTANCE = "distance_m"
    ASCENT = "ascent_m"
    ENERGY = "energy_j"


@dataclass(frozen=True, slots=True)
class SearchProfile:
    """Perfil de busca: identificador + objetivos ordenados.

    A ordem importa para a prioridade lexicografica da fila: o primeiro
    objetivo e o "principal" do perfil. A fronteira Pareto, porem, usa todos.
    """

    id: str
    objectives: tuple[Objective, ...]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SearchProfile.id nao pode ser vazio")
        if not self.objectives:
            raise ValueError(f"SearchProfile {self.id!r}: nenhum objetivo")
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError(
                f"SearchProfile {self.id!r}: objetivos duplicados "
                f"({self.objectives!r})"
            )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(objective.value for objective in self.objectives)

    @property
    def primary(self) -> Objective:
        return self.objectives[0]

    @property
    def objective_set(self) -> ObjectiveSet:
        return ObjectiveSet(self.id, self.names)

    def values(self, source: Any) -> tuple[float, ...]:
        return self.objective_set.values(source)

    def dominates(self, a: Any, b: Any) -> bool:
        return self.objective_set.dominates(a, b)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "objectives": [objective.value for objective in self.objectives],
            "primary": self.primary.value,
        }


PROFILES: dict[str, SearchProfile] = {
    name: SearchProfile(
        name, tuple(Objective(objective) for objective in objective_set.objectives)
    )
    for name, objective_set in PROFILE_OBJECTIVES.items()
}


def profile_for(profile: str) -> SearchProfile:
    """Devolve o ``SearchProfile`` de um perfil, normalizando o nome."""
    key = profile.strip().lower()
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(
            f"perfil desconhecido: {profile!r} (use: {', '.join(PROFILE_NAMES)})"
        ) from None
