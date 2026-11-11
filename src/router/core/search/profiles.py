"""Configuracao declarativa de perfis de rota (camada de selecao).

O perfil descreve **preferencia**, nao fisica. A mesmo arco, ciclista,
bicicleta e velocidade inicial produzem o mesmo resultado fisico em qualquer
perfil. O perfil apenas define:

1. quais objetivos entram na fronteira Pareto (``objectives``);
2. como ordenar as rotas nao dominadas (``ranking_strategy`` e ``tolerance``);
3. como normalizar o score do perfil equilibrado (``weights`` e ``references``);
4. como medir diversidade (``diversity``).

Nada de ``if profile == FASTEST`` espalhado: os cinco perfis sao valores da
mesma estrutura ``RouteProfile``.

A busca continua generica: ela recebe o ``SearchProfile`` derivado com
``to_search_profile()`` e nao conhece ranking nem diversidade.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from .objectives import (
    Objective,
    ObjectiveSet,
    PROFILE_OBJECTIVES,
    SearchProfile,
)

# ``RouteObjective`` e o objetivo do perfil. Reutilizamos o enum da busca para
# nao duplicar a lista de metricas.
RouteObjective = Objective


def _profile_objectives(profile_id: str) -> tuple[RouteObjective, ...]:
    """Objetivos Pareto derivados da **unica** tabela de objetivos.

    ``PROFILE_OBJECTIVES`` (``objectives.py``) e a fonte de verdade dos
    objetivos por perfil; ``PROFILE_CONFIG`` apenas acrescenta a politica de
    ranking/diversidade. Derivar aqui elimina a possibilidade de as duas
    tabelas divergirem (ADR-030, F1).
    """
    objective_set = PROFILE_OBJECTIVES[profile_id]
    return tuple(Objective(name) for name in objective_set.objectives)

_WEIGHT_SUM_TOLERANCE = 1e-6


class RankingStrategy(str, Enum):
    """Como ordenar rotas nao dominadas."""

    LEXICOGRAPHIC = "lexicographic"
    WEIGHTED_NORMALIZED = "weighted_normalized"


class DiversityMetric(str, Enum):
    """Metricas de semelhanca entre caminhos."""

    ARC_JACCARD = "arc_jaccard"
    SHARED_DISTANCE_RATIO = "shared_distance_ratio"


class DiversityFallback(str, Enum):
    """Politica quando a diversidade impede preencher as N rotas."""

    RETURN_FEWER = "return_fewer"
    RELAX_THRESHOLD = "relax_threshold"
    NONE = "none"


def _finite_non_negative(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"{name} deve ser nao negativo e finito (recebido {value!r})"
        )
    return value


def _finite_positive(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(
            f"{name} deve ser positivo e finito (recebido {value!r})"
        )
    return value


@dataclass(frozen=True, slots=True)
class RankingTolerance:
    """Tolerancia por objetivo no ranking lexicografico.

    Duas rotas so sao desempatadas por um objetivo quando a diferenca excede a
    tolerancia. Evita desempates instaveis por diferencas numericas minusculas
    de ``float``.
    """

    time_s: float = 0.0
    distance_m: float = 0.0
    ascent_m: float = 0.0
    energy_j: float = 0.0

    def __post_init__(self) -> None:
        for name in ("time_s", "distance_m", "ascent_m", "energy_j"):
            object.__setattr__(
                self, name, _finite_non_negative(name, getattr(self, name))
            )

    def for_objective(self, objective: RouteObjective) -> float:
        return float(getattr(self, objective.value, 0.0))

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
            "energy_j": self.energy_j,
        }


@dataclass(frozen=True, slots=True)
class ScoreReference:
    """Referencias fixas para o score normalizado do perfil equilibrado.

    As referencias **nao** sao derivadas do conjunto de candidatos: assim a
    classificacao de uma rota nao muda quando uma nova rota entra na fronteira.
    Precisam ser positivas para permitir a divisao.
    """

    time_s: float = 1800.0
    distance_m: float = 10_000.0
    ascent_m: float = 100.0
    energy_j: float = 0.0

    def __post_init__(self) -> None:
        for name in ("time_s", "distance_m", "ascent_m"):
            object.__setattr__(
                self, name, _finite_positive(name, getattr(self, name))
            )
        object.__setattr__(
            self, "energy_j", _finite_non_negative("energy_j", self.energy_j)
        )

    def for_objective(self, objective: RouteObjective) -> float:
        return float(getattr(self, objective.value, 0.0))

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
            "energy_j": self.energy_j,
        }


@dataclass(frozen=True, slots=True)
class BalancedWeights:
    """Pesos do score normalizado. Devem somar 1."""

    time_s: float = 1.0 / 3.0
    distance_m: float = 1.0 / 3.0
    ascent_m: float = 1.0 / 3.0

    def __post_init__(self) -> None:
        for name in ("time_s", "distance_m", "ascent_m"):
            object.__setattr__(
                self, name, _finite_non_negative(name, getattr(self, name))
            )
        total = self.time_s + self.distance_m + self.ascent_m
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"pesos devem somar 1 (somam {total!r})"
            )

    def for_objective(self, objective: RouteObjective) -> float:
        return float(getattr(self, objective.value, 0.0))

    def to_dict(self) -> dict[str, float]:
        return {
            "time_s": self.time_s,
            "distance_m": self.distance_m,
            "ascent_m": self.ascent_m,
        }


@dataclass(frozen=True, slots=True)
class DiversityRelaxation:
    """Limite e passo do relaxamento progressivo da diversidade."""

    max_threshold: float = 1.0
    step: float = 0.1

    def __post_init__(self) -> None:
        max_threshold = float(self.max_threshold)
        if not math.isfinite(max_threshold) or not 0.0 <= max_threshold <= 1.0:
            raise ValueError(
                "max_threshold deve estar em [0, 1] "
                f"(recebido {max_threshold!r})"
            )
        step = float(self.step)
        if not math.isfinite(step) or step <= 0.0:
            raise ValueError(
                f"step deve ser positivo e finito (recebido {step!r})"
            )
        object.__setattr__(self, "max_threshold", max_threshold)
        object.__setattr__(self, "step", step)

    def to_dict(self) -> dict[str, float]:
        return {"max_threshold": self.max_threshold, "step": self.step}


@dataclass(frozen=True, slots=True)
class DiversityConfig:
    """Configuracao de diversidade do perfil."""

    metric: DiversityMetric = DiversityMetric.SHARED_DISTANCE_RATIO
    max_similarity: float = 0.7
    fallback: DiversityFallback = DiversityFallback.RELAX_THRESHOLD
    relaxation: DiversityRelaxation = field(
        default_factory=DiversityRelaxation
    )

    def __post_init__(self) -> None:
        max_similarity = float(self.max_similarity)
        if not math.isfinite(max_similarity) or not 0.0 <= max_similarity <= 1.0:
            raise ValueError(
                "max_similarity deve estar em [0, 1] "
                f"(recebido {max_similarity!r})"
            )
        object.__setattr__(self, "max_similarity", max_similarity)

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric.value,
            "max_similarity": self.max_similarity,
            "fallback": self.fallback.value,
            "relaxation": self.relaxation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RouteProfile:
    """Preferencia de rota: objetivos Pareto, ranking e diversidade."""

    id: str
    objectives: tuple[RouteObjective, ...]
    ranking_strategy: RankingStrategy
    title: str = ""
    tolerance: RankingTolerance | None = None
    weights: BalancedWeights | None = None
    references: ScoreReference | None = None
    diversity: DiversityConfig = field(default_factory=DiversityConfig)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("RouteProfile.id nao pode ser vazio")
        if not self.objectives:
            raise ValueError(f"perfil {self.id!r}: nenhum objetivo")
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError(
                f"perfil {self.id!r}: objetivos duplicados ({self.objectives!r})"
            )
        if (
            self.ranking_strategy is RankingStrategy.WEIGHTED_NORMALIZED
            and (self.weights is None or self.references is None)
        ):
            raise ValueError(
                f"perfil {self.id!r}: ranking weighted_normalized exige "
                "weights e references"
            )

    # --------------------------------------------------------------- consulta
    @property
    def names(self) -> tuple[str, ...]:
        return tuple(objective.value for objective in self.objectives)

    @property
    def primary(self) -> RouteObjective:
        return self.objectives[0]

    @property
    def objective_set(self) -> ObjectiveSet:
        return ObjectiveSet(self.id, self.names)

    def to_search_profile(self) -> SearchProfile:
        """Visao da busca: apenas os objetivos Pareto (generica)."""
        return SearchProfile(self.id, self.objectives)

    def objective_value(self, values: Mapping[str, float] | object) -> float:
        """Valor do objetivo primario em um mapping/objeto de metricas."""
        if isinstance(values, Mapping):
            raw = values[self.primary.value]
        else:
            raw = getattr(values, self.primary.value)
        return float(raw)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "objectives": [objective.value for objective in self.objectives],
            "ranking_strategy": self.ranking_strategy.value,
            "tolerance": None if self.tolerance is None else self.tolerance.to_dict(),
            "weights": None if self.weights is None else self.weights.to_dict(),
            "references": (
                None if self.references is None else self.references.to_dict()
            ),
            "diversity": self.diversity.to_dict(),
        }


# ---------------------------------------------------------------------------
# Os cinco perfis concretos
# ---------------------------------------------------------------------------
_DIVERSITY = DiversityConfig()

# Tolerancias lexicograficas (valores iniciais, ainda nao calibrados).
_TOLERANCE_FAST = RankingTolerance(time_s=5.0, distance_m=20.0, ascent_m=2.0, energy_j=1000.0)
_TOLERANCE_SHORT = RankingTolerance(time_s=5.0, distance_m=10.0, ascent_m=2.0, energy_j=1000.0)
_TOLERANCE_FLAT = RankingTolerance(time_s=10.0, distance_m=20.0, ascent_m=1.0, energy_j=1000.0)
# O perfil eletrico prioriza energia (J) e depois tempo; a tolerancia de energia
# evita desempates instaveis por diferencas minusculas de consumo estimado.
_TOLERANCE_ELECTRIC = RankingTolerance(energy_j=1000.0, time_s=5.0)

# Score do perfil equilibrado: pesos iguais e rota de referencia nominal.
_BALANCED_WEIGHTS = BalancedWeights()
_BALANCED_REFERENCES = ScoreReference(
    time_s=1800.0, distance_m=10_000.0, ascent_m=100.0
)


PROFILE_CONFIG: dict[str, RouteProfile] = {
    "fastest": RouteProfile(
        id="fastest",
        title="Mais rápido",
        objectives=_profile_objectives("fastest"),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=_TOLERANCE_FAST,
        diversity=_DIVERSITY,
    ),
    "shortest": RouteProfile(
        id="shortest",
        title="Mais curto",
        objectives=_profile_objectives("shortest"),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=_TOLERANCE_SHORT,
        diversity=_DIVERSITY,
    ),
    "flattest": RouteProfile(
        id="flattest",
        title="Mais plano",
        objectives=_profile_objectives("flattest"),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=_TOLERANCE_FLAT,
        diversity=_DIVERSITY,
    ),
    "balanced": RouteProfile(
        id="balanced",
        title="Equilibrado",
        objectives=_profile_objectives("balanced"),
        ranking_strategy=RankingStrategy.WEIGHTED_NORMALIZED,
        weights=_BALANCED_WEIGHTS,
        references=_BALANCED_REFERENCES,
        diversity=_DIVERSITY,
    ),
    "electric": RouteProfile(
        id="electric",
        title="Elétrico",
        objectives=_profile_objectives("electric"),
        ranking_strategy=RankingStrategy.LEXICOGRAPHIC,
        tolerance=_TOLERANCE_ELECTRIC,
        diversity=_DIVERSITY,
    ),
}

PROFILE_IDS: tuple[str, ...] = tuple(PROFILE_CONFIG)


def route_profile_for(profile: str) -> RouteProfile:
    """Devolve o ``RouteProfile`` de um id, normalizando o nome."""
    key = profile.strip().lower()
    try:
        return PROFILE_CONFIG[key]
    except KeyError:
        raise ValueError(
            f"perfil desconhecido: {profile!r} (use: {', '.join(PROFILE_IDS)})"
        ) from None
