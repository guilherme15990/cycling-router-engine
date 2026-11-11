"""Explicacao da selecao: por que cada rota foi escolhida.

A explicacao usa **comparacao real** contra as outras rotas candidatas. Nao
afirmamos "mais rapido" se a rota nao foi a de menor tempo do conjunto. Quando
nao ha conjunto de comparacao, produzimos uma descricao neutra das metricas em
vez de uma afirmacao de superioridade.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .evaluator import (
    ObjectiveEvaluator,
    ObjectiveValues,
    RouteObjectiveEvaluator,
    evaluate_route,
    objective_value,
)
from .profiles import RankingStrategy, RouteObjective, RouteProfile
from .route import Route
from .selection import SelectionReason


@dataclass(frozen=True, slots=True)
class RouteExplanation:
    """Justificativa textual de uma rota selecionada."""

    headline: str
    details: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "headline": self.headline,
            "details": list(self.details),
            "tradeoffs": list(self.tradeoffs),
        }


class RouteExplainer:
    """Produz explicacoes derivadas das metricas e do conjunto comparado."""

    def __init__(self, evaluator: ObjectiveEvaluator | None = None) -> None:
        self._evaluator = evaluator or RouteObjectiveEvaluator()

    def explain(
        self,
        route: Route,
        profile: RouteProfile,
        reason: SelectionReason,
        *,
        candidates: Iterable[Route] = (),
    ) -> RouteExplanation:
        pool = tuple(candidates)
        comparative = bool(pool)
        values = evaluate_route(route, self._evaluator)
        pool_values = (
            [evaluate_route(item, self._evaluator) for item in pool]
            if comparative
            else []
        )

        headline = self._headline(route, values, profile, comparative, pool_values)
        details = self._details(values, profile)
        tradeoffs = self._tradeoffs(values, profile, pool_values)
        if not reason.diversity_accepted:
            tradeoffs = tradeoffs + (
                "Rota semelhante a uma ja selecionada (alternativa proxima).",
            )
        return RouteExplanation(
            headline=headline, details=details, tradeoffs=tradeoffs
        )

    # ------------------------------------------------------------- interno
    def _headline(
        self,
        route: Route,
        values: ObjectiveValues,
        profile: RouteProfile,
        comparative: bool,
        pool_values: list[ObjectiveValues],
    ) -> str:
        if profile.ranking_strategy is RankingStrategy.WEIGHTED_NORMALIZED:
            if comparative:
                return (
                    "Melhor score combinado de tempo, distancia e subida "
                    "segundo as preferencias deste perfil."
                )
            return (
                "Score combinado de tempo, distancia e subida segundo as "
                "preferencias deste perfil."
            )

        primary = profile.primary
        own = objective_value(values, primary)
        if not comparative:
            return self._neutral_headline(primary, own)

        best = min(objective_value(other, primary) for other in pool_values)
        is_best = own <= best + self._tolerance(profile, primary)

        return {
            RouteObjective.TIME: self._time_headline,
            RouteObjective.DISTANCE: self._distance_headline,
            RouteObjective.ASCENT: self._ascent_headline,
            RouteObjective.ENERGY: self._energy_headline,
        }[primary](own, best, is_best)

    def _neutral_headline(self, primary: RouteObjective, own: float) -> str:
        return {
            RouteObjective.TIME: f"Tempo estimado de {_fmt_time(own)}.",
            RouteObjective.DISTANCE: f"Distancia total de {_fmt_distance(own)}.",
            RouteObjective.ASCENT: f"Subida acumulada de {_fmt_ascent(own)}.",
            RouteObjective.ENERGY: (
                f"Energia mecanica estimada de {_fmt_energy(own)}."
            ),
        }[primary]

    def _time_headline(self, own: float, best: float, is_best: bool) -> str:
        if is_best:
            return "Menor tempo estimado entre as alternativas."
        return (
            f"Tempo estimado de {_fmt_time(own)}; "
            f"{_fmt_time(own - best)} acima da alternativa mais rapida."
        )

    def _distance_headline(self, own: float, best: float, is_best: bool) -> str:
        if is_best:
            return "Menor distancia total entre as alternativas."
        return (
            f"Distancia total de {_fmt_distance(own)}; "
            f"{_fmt_distance(own - best)} acima da alternativa mais curta."
        )

    def _ascent_headline(self, own: float, best: float, is_best: bool) -> str:
        if is_best:
            return "Menor subida acumulada entre as alternativas."
        return (
            f"Subida acumulada de {_fmt_ascent(own)}; "
            f"{_fmt_ascent(own - best)} acima da alternativa mais plana."
        )

    def _energy_headline(self, own: float, best: float, is_best: bool) -> str:
        if is_best:
            return "Menor energia mecanica estimada entre as alternativas."
        return (
            f"Energia mecanica estimada de {_fmt_energy(own)}; "
            f"{_fmt_energy(own - best)} acima da alternativa mais economica."
        )

    def _details(
        self, values: ObjectiveValues, profile: RouteProfile
    ) -> tuple[str, ...]:
        lines = [
            f"Tempo estimado: {_fmt_time(values.time_s)}",
            f"Distancia: {_fmt_distance(values.distance_m)}",
            f"Subida acumulada: {_fmt_ascent(values.ascent_m)}",
        ]
        if RouteObjective.ENERGY in profile.objectives:
            lines.append(f"Energia mecanica: {_fmt_energy(values.energy_j)}")
        return tuple(lines)

    def _tradeoffs(
        self,
        values: ObjectiveValues,
        profile: RouteProfile,
        pool_values: list[ObjectiveValues],
    ) -> tuple[str, ...]:
        if not pool_values:
            return ()
        tradeoffs: list[str] = []
        ordered = (
            profile.objectives
            if profile.ranking_strategy is RankingStrategy.WEIGHTED_NORMALIZED
            else tuple(
                objective
                for objective in profile.objectives
                if objective is not profile.primary
            )
        )
        for objective in ordered:
            best = min(objective_value(other, objective) for other in pool_values)
            own = objective_value(values, objective)
            if own <= best + self._tolerance(profile, objective):
                continue
            tradeoffs.append(self._tradeoff_line(objective, own, best))
        return tuple(tradeoffs)

    def _tradeoff_line(
        self, objective: RouteObjective, own: float, best: float
    ) -> str:
        delta = own - best
        if objective is RouteObjective.TIME:
            return (
                f"Inclui {_fmt_time(delta)} a mais que a alternativa mais rapida."
            )
        if objective is RouteObjective.DISTANCE:
            return (
                f"E {_fmt_distance(delta)} mais longa que a alternativa mais "
                "curta."
            )
        if objective is RouteObjective.ASCENT:
            return (
                f"Inclui {_fmt_ascent(delta)} a mais de subida que a "
                "alternativa mais plana."
            )
        return (
            f"Consome {_fmt_energy(delta)} a mais que a alternativa mais "
            "economica."
        )

    @staticmethod
    def _tolerance(profile: RouteProfile, objective: RouteObjective) -> float:
        if profile.tolerance is None:
            return 0.0
        return profile.tolerance.for_objective(objective)


def explain_route(
    route: Route,
    profile: RouteProfile,
    reason: SelectionReason,
    *,
    candidates: Iterable[Route] = (),
    evaluator: ObjectiveEvaluator | None = None,
) -> RouteExplanation:
    """Atalho funcional para ``RouteExplainer().explain``."""
    return RouteExplainer(evaluator).explain(
        route, profile, reason, candidates=candidates
    )


# --------------------------------------------------------------------- formatos
def _fmt_time(seconds: float) -> str:
    if abs(seconds) >= 3600.0:
        return f"{seconds / 3600.0:.1f} h"
    if abs(seconds) >= 60.0:
        return f"{seconds / 60.0:.1f} min"
    return f"{seconds:.0f} s"


def _fmt_distance(meters: float) -> str:
    if abs(meters) >= 1000.0:
        return f"{meters / 1000.0:.2f} km"
    return f"{meters:.0f} m"


def _fmt_ascent(meters: float) -> str:
    return f"{meters:.0f} m"


def _fmt_energy(joules: float) -> str:
    if abs(joules) >= 1000.0:
        return f"{joules / 1000.0:.1f} kJ"
    return f"{joules:.0f} J"
