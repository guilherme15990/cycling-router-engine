"""Contrato entre a busca generica e a selecao por perfil.

O fluxo completo:

    ProfileSearchRequest
        -> MultiObjectiveAStar            (fronteira Pareto)
        -> reconstrucao em Route
        -> ProfileRanker                  (ranking por perfil)
        -> RouteSelector                  (diversidade + fallback)
        -> RouteExplainer                 (justificativa)
        -> estimativa energetica          (opcional: perfil Elétrico / bateria)
        -> ProfileSearchResponse

A busca continua generica: ela recebe apenas os objetivos Pareto do perfil e
nao conhece ranking nem diversidade. O perfil **nao** altera a fisica. A
estimativa energetica e um estagio **posterior** a selecao: converte a energia
mecanica acumulada em energia eletrica (Wh) e, se houver bateria configurada,
avalia a viabilidade. Ela nao muda a fronteira nem a ordem das rotas.
"""

from __future__ import annotations

import math
from collections.abc import Hashable
from dataclasses import dataclass

from graph.model import RoutingGraph
from physical.energy_model import BatteryConfig, EnergyModel
from .astar import MultiObjectiveAStar
from .candidates import RouteCandidatePool, RouteDeduplicator
from .config import SearchConfig
from .energy import RouteEnergyEstimate, estimate_route_energy
from .explanation import RouteExplanation, RouteExplainer
from .heuristic import Heuristic
from .objectives import Objective
from .profiles import RouteProfile
from .selection import (
    SelectedRoute,
    SelectionConfig,
    select_candidates,
)
from .traversal_model import TraversalModel


@dataclass(frozen=True, slots=True)
class ProfileSearchRequest:
    """Pedido de busca para um perfil concreto.

    ``energy_model`` e ``battery`` sao **opcionais**: sem eles, o perfil
    ``Elétrico`` usa o modelo energetico generico e nao promete autonomia. A
    bateria nao e requisito para rotear — ela so habilita a viabilidade.
    """

    origin: Hashable
    destination: Hashable
    profile: RouteProfile
    max_routes: int = 3
    initial_speed_mps: float = 0.0
    energy_model: EnergyModel | None = None
    battery: BatteryConfig | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_routes, bool)
            or not isinstance(self.max_routes, int)
            or self.max_routes <= 0
        ):
            raise ValueError(
                "max_routes deve ser um inteiro positivo "
                f"(recebido {self.max_routes!r})"
            )
        if (
            not math.isfinite(self.initial_speed_mps)
            or self.initial_speed_mps < 0.0
        ):
            raise ValueError(
                "initial_speed_mps deve ser nao negativo e finito "
                f"(recebido {self.initial_speed_mps!r})"
            )


@dataclass(frozen=True, slots=True)
class SelectionMetadata:
    """Metadados da selecao apresentados junto com a resposta."""

    requested_count: int
    selected_count: int
    diversity_relaxed: bool
    selection_strategy: str
    threshold_used: float | None = None
    dropped_duplicates: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_count": self.requested_count,
            "selected_count": self.selected_count,
            "diversity_relaxed": self.diversity_relaxed,
            "selection_strategy": self.selection_strategy,
            "threshold_used": self.threshold_used,
            "dropped_duplicates": self.dropped_duplicates,
        }


@dataclass(frozen=True, slots=True)
class ProfileSearchResponse:
    """Resposta: rotas selecionadas + explicacoes alinhadas por indice.

    ``energy`` traz a estimativa energetica por rota (alinhada por indice),
    presente quando o perfil usa energia, ha modelo configurado ou bateria.
    """

    profile_id: str
    routes: tuple[SelectedRoute, ...]
    pareto_route_count: int
    selection: SelectionMetadata
    explanations: tuple[RouteExplanation, ...] = ()
    energy: tuple[RouteEnergyEstimate, ...] = ()
    reached: bool = False

    @property
    def count(self) -> int:
        return len(self.routes)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "reached": self.reached,
            "pareto_route_count": self.pareto_route_count,
            "selection": self.selection.to_dict(),
            "routes": [route.to_dict() for route in self.routes],
            "explanations": [
                explanation.to_dict() for explanation in self.explanations
            ],
            "energy": [estimate.to_dict() for estimate in self.energy],
        }


def search_profile(
    graph: RoutingGraph,
    traversal: TraversalModel,
    request: ProfileSearchRequest,
    *,
    heuristic: Heuristic | None = None,
    search_config: SearchConfig | None = None,
    selection_config: SelectionConfig | None = None,
    deduplicator: RouteDeduplicator | None = None,
    explain: bool = True,
) -> ProfileSearchResponse:
    """Executa busca + selecao para um ``ProfileSearchRequest``.

    Por padrao usa ``min_routes = max_routes`` na selecao para que a politica de
    fallback do perfil realmente preencha as N rotas. O perfil define a config de
    diversidade; ``selection_config`` permite sobrescrever. ``deduplicator``
    controla como o pool de candidatas colapsa rotas equivalentes.

    Args:
        graph: Grafo (normalmente o grafo de consulta ja com origem/destino).
        traversal: Modelo de travessia (fisica). O perfil nao o altera.
        request: Pedido com origem, destino, perfil e limites.
        heuristic: Heuristica admissivel. Usa a padrao se omitida.
        search_config: Ajustes de desempenho da busca. Padrao se omitido.
        selection_config: Sobrescreve a config de selecao derivada do perfil.
        deduplicator: Estrategia de deduplicacao do pool (``RouteDeduplicator``).
        explain: Se ``True``, gera explicacoes alinhadas as rotas.

    Returns:
        ``ProfileSearchResponse`` com rotas selecionadas, metadados da selecao,
        explicacoes e (quando aplicavel) estimativa energetica por rota.

    Example:
        >>> from search import ProfileSearchRequest, route_profile_for
        >>> request = ProfileSearchRequest(
        ...     origin=query.origin_id,
        ...     destination=query.destination_id,
        ...     profile=route_profile_for("fastest"),
        ... )
        >>> response = search_profile(query.graph, traversal, request)  # doctest: +SKIP
        >>> response.count  # doctest: +SKIP
        1
    """
    astar = MultiObjectiveAStar(
        graph,
        traversal,
        request.profile.to_search_profile(),
        heuristic=heuristic,
        config=search_config,
        initial_speed_mps=request.initial_speed_mps,
    )
    result = astar.search(request.origin, request.destination)

    pool = RouteCandidatePool.from_result(result, deduplicator=deduplicator)
    config = selection_config or SelectionConfig(
        max_routes=request.max_routes,
        min_routes=request.max_routes,
        diversity=request.profile.diversity,
    )
    selection = select_candidates(
        pool.candidates, request.profile, config
    )

    explanations: tuple[RouteExplanation, ...] = ()
    if explain:
        explainer = RouteExplainer()
        candidates = selection.routes
        explanations = tuple(
            explainer.explain(
                selected.route,
                request.profile,
                selected.selection_reason,
                candidates=candidates,
            )
            for selected in selection.selected
        )

    diversity = config.diversity or request.profile.diversity
    metadata = SelectionMetadata(
        requested_count=request.max_routes,
        selected_count=len(selection.selected),
        diversity_relaxed=selection.diversity_relaxed,
        selection_strategy=(
            f"{request.profile.ranking_strategy.value}/"
            f"{diversity.fallback.value}"
        ),
        threshold_used=selection.threshold_used,
        dropped_duplicates=pool.dropped_duplicates,
    )

    energy = _estimate_energy(request, selection.selected)

    return ProfileSearchResponse(
        profile_id=request.profile.id,
        routes=selection.selected,
        pareto_route_count=selection.pareto_route_count,
        selection=metadata,
        explanations=explanations,
        energy=energy,
        reached=result.reached,
    )


def _estimate_energy(
    request: ProfileSearchRequest, selected
) -> tuple[RouteEnergyEstimate, ...]:
    """Estima a energia eletrica das rotas selecionadas.

    So calcula quando faz sentido: perfil que usa energia, modelo energetico
    configurado ou bateria informada. Caso contrario devolve ``()`` para nao
    alterar a resposta dos demais perfis.
    """
    uses_energy = Objective.ENERGY in request.profile.objectives
    if not (uses_energy or request.energy_model or request.battery):
        return ()
    model = request.energy_model or EnergyModel.generic()
    return tuple(
        estimate_route_energy(
            selected_route.route,
            model,
            battery=request.battery,
            route_id=selected_route.route_id,
        )
        for selected_route in selected
    )
