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

from enum import Enum
from typing import Sequence

from router.core.data.graph.model import Arc
from ._validation import non_negative, positive
from .bicycle import BicycleModel
from .cyclist import CyclistModel
from .arc_profile import ArcProfile, ProfileStep
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


class PhysicalTraversal:
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

        self.step_m = resolved_step
        self.acceleration_limit_mps2 = float(acceleration_limit_mps2)
        #: Se True, um trecho sem elevacao produz ``UNAVAILABLE`` em vez de
        #: ``DEGRADED`` (ADR-025, decisao 2).
        self.require_elevation = bool(require_elevation)
        self._profiles: dict[str, tuple[Arc, ArcProfile]] = {}
        self._steps: dict[str, tuple[Arc, tuple[ProfileStep, ...]]] = {}
        self._equilibrium: dict[
            tuple[object, ...], tuple[tuple[float, float], ...]
        ] = {}

    # ------------------------------------------------------------------ cache
    def profile(self, arc: Arc) -> ArcProfile:
        cached = self._profiles.get(arc.id)
        if cached is not None and cached[0] is arc:
            return cached[1]
        profile = ArcProfile.from_arc(arc)
        self._profiles[arc.id] = (arc, profile)
        return profile

    def steps(self, arc: Arc) -> tuple[ProfileStep, ...]:
        cached = self._steps.get(arc.id)
        if cached is not None and cached[0] is arc:
            return cached[1]
        steps = self.profile(arc).steps(self.step_m)
        self._steps[arc.id] = (arc, steps)
        return steps

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
        environment = environment or Environment()
        state = initial_state if initial_state is not None else SimulationState()
        initial_speed = state.speed_mps

        power = cyclist.default_power_w if power_w is None else float(power_w)
        non_negative("power_w", power)
        if power > cyclist.max_power_w:
            raise ValueError(
                f"power_w ({power}) excede max_power_w ({cyclist.max_power_w})"
            )

        profile = self.profile(arc)
        steps = self.steps(arc)
        mass = cyclist.mass_kg + bicycle.mass_kg
        equilibrium = self.equilibrium_speeds(
            arc, cyclist, bicycle, environment, power
        )

        speed = max(0.0, float(initial_speed))
        time_s = 0.0
        wheel_energy_j = 0.0
        rider_energy_j = 0.0
        braking_energy_j = 0.0
        aero_loss_j = 0.0
        rolling_loss_j = 0.0
        stalled = False
        used_regularization = False

        for index, step in enumerate(steps):
            equilibrium_speed, terminal_speed = equilibrium[index]
            step_result = advance_speed(
                distance_m=step.distance_m,
                slope_rad=step.slope_rad,
                initial_speed_mps=speed,
                available_power_w=power,
                mass_kg=mass,
                frontal_area_m2=cyclist.frontal_area_m2,
                bicycle=bicycle,
                environment=environment,
                max_speed_mps=step.max_speed_mps,
                acceleration_limit_mps2=self.acceleration_limit_mps2,
                equilibrium_speed_mps_value=equilibrium_speed,
                terminal_speed_mps_value=terminal_speed,
            )
            time_s += step_result.time_s
            wheel_energy_j += step_result.wheel_energy_j
            rider_energy_j += step_result.rider_energy_j
            braking_energy_j += step_result.braking_energy_j
            aero_loss_j += step_result.aero_loss_j
            rolling_loss_j += step_result.rolling_loss_j
            stalled = stalled or step_result.stalled
            used_regularization = used_regularization or step_result.regularized
            speed = step_result.final_speed_mps

        final_state = SimulationState(
            elapsed_s=state.elapsed_s + time_s,
            speed_mps=speed,
            wheel_work_j=state.wheel_work_j + wheel_energy_j,
            rider_work_j=state.rider_work_j + rider_energy_j,
            braking_loss_j=state.braking_loss_j + braking_energy_j,
        )

        uncertainty = profile.elevation_uncertainty
        status = classify_status(
            uncertainty, require_elevation=self.require_elevation
        )

        return TraversalResult(
            time_s=time_s,
            distance_m=profile.distance_m,
            ascent_m=profile.ascent_m,
            descent_m=profile.descent_m,
            initial_speed_mps=max(0.0, float(initial_speed)),
            final_speed_mps=speed,
            mechanical_energy_j=wheel_energy_j,
            rider_energy_j=rider_energy_j,
            battery_energy_j=None,
            braking_energy_j=braking_energy_j,
            stalled=stalled,
            arc_id=arc.id,
            state=final_state,
            elevation_uncertainty=uncertainty,
            result_status=status,
            aero_loss_j=aero_loss_j,
            rolling_loss_j=rolling_loss_j,
            used_min_speed_regularization=used_regularization,
        )

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
