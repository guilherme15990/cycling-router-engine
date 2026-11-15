"""Coordenador da camada fisica: arco -> perfil -> passos -> resultado.

O ``PhysicalTraversal`` nao decide rota. Ele recebe um arco direcionado e o
estado de movimento na entrada, simula os passos e devolve tempo, energia e
velocidade final.

A propagacao de velocidade entre arcos consecutivas pertence a **esta**
camada (ver ``traverse_path``), nao a busca Pareto. A busca apenas carrega o
estado fisico devolvido (``TraversalPath.final_speed_mps``) e o reutiliza como
velocidade inicial da proxima travessia.
"""

from __future__ import annotations

from dataclasses import fields
from enum import Enum
from typing import Sequence

from router.core.data.graph.model import Arc
from ._validation import non_negative, positive
from .bicycle import BicycleModel
from .cyclist import CyclistModel
from .arc_profile import ArcProfile, ProfileStep, RoadProfileCache
from .motion import MotionEngine
from .propulsion import ConstantPower
from router.core.data.mobility import MobilityProfile
from .environment import Environment
from .forces import equilibrium_speed_mps
from .models import (
    PhysicsRouteMetrics,
    SimulationState,
    SimulationStatus,
    TraversalPath,
    TraversalResult,
    classify_status,
    worst_status,
)
from .speed import advance_speed

DEFAULT_STEP_M = 5.0
DEFAULT_ACCELERATION_LIMIT_MPS2 = 2.0


class SimulationMode(str, Enum):
    """Modo de execucao da integracao espacial (ADR-025, decisao 6, item 1).

    Os passos sao **configuracoes explicitas**, nao constantes universais de
    precisao. ``SEARCH`` prioriza desempenho; ``VALIDATION`` refina o passo
    para inspecao de convergencia. O modo ``VALIDATION`` nao garante alta
    fidelidade na partida do repouso enquanto persistir a limitacao do modelo
    de baixa velocidade (``MIN_SPEED_MPS``).
    """

    SEARCH = "search"
    VALIDATION = "validation"


#: Passo espacial padrao (metros) de cada modo.
DEFAULT_STEP_M_BY_MODE: dict[SimulationMode, float] = {
    SimulationMode.SEARCH: DEFAULT_STEP_M,
    SimulationMode.VALIDATION: 1.0,
}


def _resolve_mode(mode: "SimulationMode | str") -> SimulationMode:
    if isinstance(mode, SimulationMode):
        return mode
    return SimulationMode(mode)


class PhysicalTraversal(RoadProfileCache):
    """Simula a travessia fisica de arcos do grafo.

    O objeto mantem cache de perfis e passos por ``arc.id``. Como arcos do
    grafo e do grafo de consulta sao imutaveis, o cache e seguro. Se uma nova
    arco reutilizar o mesmo id, a identidade do objeto invalida o cache.
    """

    def __init__(
        self,
        step_m: float | None = None,
        acceleration_limit_mps2: float = DEFAULT_ACCELERATION_LIMIT_MPS2,
        require_elevation: bool = False,
        *,
        mode: SimulationMode | str = SimulationMode.SEARCH,
    ) -> None:
        self.mode = _resolve_mode(mode)
        resolved_step = (
            DEFAULT_STEP_M_BY_MODE[self.mode] if step_m is None else float(step_m)
        )
        positive("step_m", resolved_step)
        non_negative("acceleration_limit_mps2", acceleration_limit_mps2)

        super().__init__(step_m=resolved_step, require_elevation=require_elevation)
        self.acceleration_limit_mps2 = float(acceleration_limit_mps2)
        self._equilibrium: dict[tuple[object, ...], tuple[tuple[float, float], ...]] = {}
        self._motion_engine = MotionEngine(geometry=self)

    def equilibrium_speeds(
        self,
        arc: Arc,
        cyclist: CyclistModel,
        bicycle: BicycleModel,
        environment: Environment,
        power_w: float,
    ) -> tuple[tuple[float, float], ...]:
        """Precalcula limites de equilibrio invariantes à velocidade inicial.

        Cada par é ``(pedalando, rolando)`` e acompanha ``self.steps(arc)``.
        O resultado depende apenas do arco, dos modelos e do cenário da
        consulta; a velocidade inicial continua sendo aplicada por
        :func:`advance_speed`. Isso permite reutilizar a bisseção quando A*
        visita o mesmo arco a partir de labels com velocidades diferentes.
        """
        key = (
            arc.id,
            id(arc),
            self.step_m,
            self.acceleration_limit_mps2,
            cyclist.mass_kg + bicycle.mass_kg,
            cyclist.frontal_area_m2,
            bicycle,
            environment,
            float(power_w),
        )
        cached = self._equilibrium.get(key)
        if cached is not None:
            return cached

        mass = cyclist.mass_kg + bicycle.mass_kg
        values = tuple(
            (
                equilibrium_speed_mps(
                    step.slope_rad,
                    mass,
                    cyclist.frontal_area_m2,
                    bicycle,
                    environment,
                    power_w,
                    max_speed_mps=step.max_speed_mps,
                ),
                equilibrium_speed_mps(
                    step.slope_rad,
                    mass,
                    cyclist.frontal_area_m2,
                    bicycle,
                    environment,
                    0.0,
                    max_speed_mps=step.max_speed_mps,
                ),
            )
            for step in self.steps(arc)
        )
        self._equilibrium[key] = values
        return values

    # -------------------------------------------------------------- travessia
    def traverse(
        self,
        arc: Arc,
        cyclist: CyclistModel,
        bicycle: BicycleModel,
        initial_state: SimulationState | None = None,
        environment: Environment | None = None,
        power_w: float | None = None,
    ) -> TraversalResult:
        """Percorre ``arc`` e devolve o resultado fisico.

        Args:
            arc: Arco direcionada a percorrer.
            cyclist: Modelo do ciclista (massa, potencia, area frontal).
            bicycle: Modelo da bicicleta (massa, rolamento, aerodinamica).
            initial_state: Estado dinamico na entrada do arco
                (``SimulationState``). Padrao: repouso a velocidade zero.
            environment: Ambiente (ar, vento, gravidade). Padrao se omitido.
            power_w: Potencia no pedivela. Se omitida, usa
                ``cyclist.default_power_w``.

        Returns:
            ``TraversalResult`` com tempo, energia mecanica, energia de
            frenagem, subida/descida e velocidade final. O estado dinamico de
            saida fica em ``TraversalResult.simulation_state``.

        Raises:
            ValueError: Se ``power_w`` for negativo ou exceder
                ``cyclist.max_power_w``.
        """
        power = cyclist.default_power_w if power_w is None else float(power_w)
        non_negative("power_w", power)
        if power > cyclist.max_power_w:
            raise ValueError(f"power_w ({power}) excede max_power_w ({cyclist.max_power_w})")
        mobility = MobilityProfile.from_bicycle(
            cyclist, bicycle, acceleration_limit_mps2=self.acceleration_limit_mps2,
        )
        result = self._motion_engine.traverse(
            arc, mobility, ConstantPower(power), initial_state=initial_state,
            environment=environment,
        )
        # Preserve the historical result type/serialization for existing users.
        return TraversalResult(**{item.name: getattr(result, item.name) for item in fields(TraversalResult)})

    def traverse_path(
        self,
        arcs: Sequence[Arc],
        cyclist: CyclistModel,
        bicycle: BicycleModel,
        initial_state: SimulationState | None = None,
        environment: Environment | None = None,
        power_w: float | None = None,
    ) -> TraversalPath:
        """Percorre arcos em sequencia propagando o estado fisico.

        O estado dinamico de saida de cado arco e o estado de entrada da
        proxima (``SimulationState``). Como ``TraversalPath`` se comporta como
        uma sequencia, os resultados individuais continuam acessiveis por
        indice e iteracao.
        """
        state = initial_state if initial_state is not None else SimulationState()
        results: list[TraversalResult] = []
        for arc in arcs:
            result = self.traverse(
                arc,
                cyclist,
                bicycle,
                initial_state=state,
                environment=environment,
                power_w=power_w,
            )
            results.append(result)
            state = result.simulation_state

        return traversal_path_from_results(
            results,
            initial_speed_mps=(
                initial_state.speed_mps if initial_state is not None else 0.0
            ),
        )


def traversal_path_from_results(
    results: Sequence[TraversalResult],
    *,
    initial_speed_mps: float = 0.0,
) -> TraversalPath:
    """Agrega resultados ja simulados em um ``TraversalPath``.

    Usado por ``traverse_path`` e por quem escolhe a potencia **por arco**
    (``pacing.run_paced_path``): a agregacao de metricas, incerteza e status e
    a mesma, sem duplicar a logica.
    """
    if not results:
        return TraversalPath(
            results=(),
            metrics=PhysicsRouteMetrics.from_results(()),
            initial_speed_mps=max(0.0, float(initial_speed_mps)),
            final_speed_mps=max(0.0, float(initial_speed_mps)),
        )

    uncertainty = None
    for result in results:
        if result.elevation_uncertainty is None:
            continue
        uncertainty = (
            result.elevation_uncertainty
            if uncertainty is None
            else uncertainty.combine(result.elevation_uncertainty)
        )

    return TraversalPath(
        results=tuple(results),
        metrics=PhysicsRouteMetrics.from_results(results),
        initial_speed_mps=max(0.0, float(initial_speed_mps)),
        final_speed_mps=results[-1].final_speed_mps,
        elevation_uncertainty=uncertainty,
        result_status=worst_status(*(r.result_status for r in results)),
        used_min_speed_regularization=any(
            r.used_min_speed_regularization for r in results
        ),
    )


def traverse_arc(
    arc: Arc,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    initial_state: SimulationState | None = None,
    environment: Environment | None = None,
    power_w: float | None = None,
    *,
    step_m: float | None = None,
    mode: SimulationMode | str = SimulationMode.SEARCH,
) -> TraversalResult:
    """Atalho para uma travessia isolada, sem reutilizar cache.

    Cria um ``PhysicalTraversal`` efemero a cada chamada. Para percorrer varias
    arcos (ou reusar cache de perfis), crie um ``PhysicalTraversal`` e chame
    ``traverse``/``traverse_path``.

    Args:
        arc: Arco direcionada a percorrer.
        cyclist: Modelo do ciclista.
        bicycle: Modelo da bicicleta.
        initial_state: Estado dinamico na entrada do arco.
        environment: Ambiente (padrao se omitido).
        power_w: Potencia no pedivela (padrao do ciclista se omitida).
        step_m: Passo espacial explicito, em metros. Se omitido, usa o padrao
            do ``mode`` (``DEFAULT_STEP_M_BY_MODE``).
        mode: Modo de execucao (``SEARCH`` por padrao; ``VALIDATION`` refina).

    Returns:
        ``TraversalResult`` do arco.

    Example:
        >>> result = traverse_arc(arc, cyclist, bicycle, power_w=200.0)
        >>> round(result.time_s, 3) >= 0.0
        True
    """
    return PhysicalTraversal(step_m=step_m, mode=mode).traverse(
        arc,
        cyclist,
        bicycle,
        initial_state=initial_state,
        environment=environment,
        power_w=power_w,
    )
