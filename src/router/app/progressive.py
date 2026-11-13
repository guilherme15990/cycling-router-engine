"""Planejamento progressivo com resultados validados e cobertura explícita."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from threading import Event, RLock
from typing import Any, Callable, Sequence

from router.app.multi_leg import _merge_leg_plans, _same_coord
from router.app.planning import plan_offline_routes
from router.contracts import RoutePlan, RouteRequest
from router.core.search.astar import SearchCancelled
from router.core.search import route_profile_for


@dataclass(frozen=True, slots=True)
class RouteProgress:
    """Evento do planejamento; ``plan`` é parcial até o estado terminal.

    ``completed`` e ``no_route`` cobrem apenas o grafo fornecido. Não são
    certificados de otimalidade fora dele. ``interrupted`` preserva o último
    plano válido; nunca equivale a ausência de rota.
    """
    status: str
    plan: RoutePlan | None = None
    leg: int | None = None
    legs: int = 1
    details: dict[str, Any] | None = None


def plan_routes_progressive(
    graph: Any, origin: tuple[float, float], destination: tuple[float, float], *,
    cyclist: Any, bicycle: Any, profile: str = "fastest",
    stops: Sequence[tuple[float, float]] = (),
    on_event: Callable[[RouteProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    **options: Any,
) -> RouteProgress:
    """Busca por zonas crescentes até esgotar a cobertura do grafo.

    Cada candidata publicada passa pela avaliação completa, predição de
    intensidade e segurança. A fila e fronteira de cada busca permanecem vivas
    entre notificações; ao ampliar a zona, uma nova busca é necessária. Em
    múltiplas pernas, cada perna busca independentemente e só um trajeto
    completo é publicado. O modelo v1 não encadeia fadiga entre pernas.

    A busca não impõe limite silencioso de tempo, regiões ou labels. A API
    permite cancelamento cooperativo; sem cancelamento, a conclusão depende
    também de a busca multiobjetivo terminar no modelo de estados adotado.
    """
    profile = route_profile_for(profile).id
    points = (origin, *tuple((float(a), float(b)) for a, b in stops), destination)
    for a, b in zip(points, points[1:]):
        if _same_coord(a, b):
            raise ValueError("pontos consecutivos iguais")
    # O grafo offline pode representar a cidade inteira, mas a busca de uma
    # consulta começa no corredor da BBox. Só cresce quando a rede local não
    # conecta os pontos; explorar a cidade inteira por padrão desperdiça custo.
    use_full_graph = bool(options.pop("use_full_graph", False))
    growth = float(options.get("growth", 1.6))
    if not math.isfinite(growth) or growth <= 1:
        raise ValueError("growth deve ser maior que 1")
    # A expansão é guiada pela cobertura, não pelo limite antigo de iterações.
    requested_iterations = options.pop("max_iterations", None)
    if requested_iterations is not None and use_full_graph:
        raise ValueError("A busca progressiva cobre o grafo; max_iterations não se aplica")
    if "explore_full_coverage" in options or "on_progress" in options:
        raise ValueError("Use on_event para acompanhar a busca progressiva")
    total = len(points) - 1
    request = RouteRequest(origin=origin, destination=destination, profile=profile,
                           stops=tuple(points[1:-1]))
    lock, abort = RLock(), Event()
    leg_plans: dict[int, RoutePlan] = {}
    latest: RoutePlan | None = None

    def cancelled() -> bool:
        return abort.is_set() or (should_cancel is not None and should_cancel())

    def send(event: RouteProgress) -> None:
        if on_event is not None:
            on_event(event)

    def update(index: int, status: str, plan: RoutePlan | None, details: dict) -> None:
        nonlocal latest
        with lock:
            if cancelled():
                raise SearchCancelled("Busca cancelada pelo usuário")
            if plan is not None and plan.routes:
                leg_plans[index] = plan
                if len(leg_plans) == total:
                    ordered = [leg_plans[i] for i in range(total)]
                    latest = ordered[0] if total == 1 else _merge_leg_plans(ordered, request=request)
                    send(RouteProgress("route_found", latest, index + 1, total, details))
                    return
            send(RouteProgress("searching", latest, index + 1, total, details))

    def run_leg(index: int):
        opts = dict(options)
        # Simuladores possuem caches mutáveis; cada thread recebe sua cópia.
        if opts.get("traversal") is not None:
            opts["traversal"] = deepcopy(opts["traversal"])
        return plan_offline_routes(
            graph, points[index], points[index + 1], cyclist=cyclist, bicycle=bicycle,
            profile=profile, max_iterations=(1 if use_full_graph else (requested_iterations or 8)), explore_full_coverage=False,
            use_full_graph=use_full_graph,
            should_cancel=cancelled,
            on_progress=lambda status, plan, details: update(index, status, plan, details),
            **opts,
        ).to_contract()

    try:
        if total == 1:
            completed = [run_leg(0)]
        else:
            completed = [None] * total
            with ThreadPoolExecutor(max_workers=total, thread_name_prefix="route-leg") as executor:
                jobs = {executor.submit(run_leg, i): i for i in range(total)}
                try:
                    for job in as_completed(jobs):
                        completed[jobs[job]] = job.result()
                except BaseException:
                    abort.set()
                    raise
        final = completed[0] if total == 1 else _merge_leg_plans(completed, request=request)
        if final.routes:
            terminal = RouteProgress("completed", final, legs=total,
                                     details={"coverage": "provided_graph", "optimality_proven": False})
        elif latest is not None:
            terminal = RouteProgress("interrupted", latest, legs=total,
                details={"reason": "Busca final não retornou a rota parcial; resultado mantido sem garantia de conclusão."})
        else:
            terminal = RouteProgress("no_route", final, legs=total,
                details={"coverage": "provided_graph", "optimality_proven": False,
                         "reason": "Nenhuma rota elegível foi retornada na cobertura do grafo fornecido."})
    except SearchCancelled:
        terminal = RouteProgress("interrupted", latest, legs=total,
                                 details={"reason": "Cancelada pelo usuário"})
    send(terminal)
    return terminal
