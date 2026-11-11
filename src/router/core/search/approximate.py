"""Estimador fisico aproximado para a busca (ADR-025, incremento 8).

Este modulo **nao** substitui o simulador completo (`physical.PhysicalTraversal`).
Ele oferece uma alternativa **mais barata** para a busca e um **relatorio de
erro** contra o simulador, para que a decisao de usa-lo seja sempre consciente.

Modelo aproximado: **equilibrio por arco** (velocidade constante).

Para cada arco, em vez de integrar passo a passo, assume-se que o ciclista
alcanca a velocidade de **equilibrio** para a inclinacao **media** do arco
(`atan2(ascent - descent, distance)`) e a percorre em velocidade constante:

* velocidade final = velocidade de equilibrio;
* ``tempo = distancia / max(velocidade, MIN_SPEED_MPS)``;
* energia do pedivela = ``potencia_disponivel * tempo`` (mesma convencao do
  simulador: so ha trabalho enquanto se pedala);
* perdas aerodinamica/rolamento a partir das forcas de resistencia naquela
  velocidade.

**Nenhum parametro fisico novo e introduzido.** Reutilizam-se
``equilibrium_speed_mps``, ``forces_at``, ``ArcProfile.from_arc``,
``classify_status`` e o piso ``MIN_SPEED_MPS`` — os mesmos da engine. As
diferencas sao de **resolucao** (um passo por arco), nao de fisica.

Advertencia de admissibilidade (ADR-025, decisao de desempenho): o estimador
**nao** declara ``max_speed_mps``. A busca o trata como teto **desconhecido**
(``UNKNOWN_SPEED``), entao a heuristica padrao de tempo cai para o limite ``0``
(sempre admissivel, porem fraca). O estimador **nao** deve ser tratado
automaticamente como heuristica admissivel do A*: seus custos sao aproximados e
podem **subestimar** (partida do repouso) ou **superestimar** (entrada em alta
velocidade) o tempo real (ver ``tests/test_search_approximation.py``).
"""

from __future__ import annotations

import math
from collections.abc import Hashable
from dataclasses import dataclass

from router.core.data.graph.model import Arc, RoutingGraph
from router.core.physics.physical.arc_profile import ArcProfile
from router.core.physics.physical.bicycle import BicycleModel
from router.core.physics.physical.cyclist import CyclistModel
from router.core.physics.physical.environment import Environment
from router.core.physics.physical.forces import equilibrium_speed_mps, forces_at
from router.core.physics.physical.models import (
    SimulationState,
    TraversalResult,
    classify_status,
)
from router.core.physics.physical.speed import MIN_SPEED_MPS

from .objectives import SearchProfile

_EPS = 1e-9


def _check_power(cyclist: CyclistModel, power_w: float) -> float:
    if not math.isfinite(power_w) or power_w < 0.0:
        raise ValueError(
            f"power_w deve ser nao negativo e finito (recebido {power_w!r})"
        )
    if power_w > cyclist.max_power_w:
        raise ValueError(
            f"power_w ({power_w}) excede max_power_w ({cyclist.max_power_w})"
        )
    return power_w


def _arc_speed_limit(profile: ArcProfile) -> float | None:
    limits = [
        segment.max_speed_mps
        for segment in profile.segments
        if segment.max_speed_mps is not None
    ]
    return min(limits) if limits else None


def estimate_arc(
    arc: Arc,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    initial_state: SimulationState | None = None,
    environment: Environment | None = None,
    power_w: float | None = None,
    *,
    require_elevation: bool = False,
) -> TraversalResult:
    """Travessia **aproximada** de ``arc`` por equilibrio de velocidade.

    Mesma assinatura de ``PhysicalTraversal.traverse`` (estado de entrada
    ``SimulationState``). Nao altera o arco nem o perfil: apenas os resume em um
    unico passo de inclinacao media. Ver o cabecalho do modulo para as
    garantias (nenhum parametro fisico novo) e a advertencia de admissibilidade.
    """
    environment = environment or Environment()
    state = initial_state if initial_state is not None else SimulationState()
    initial_speed = max(0.0, float(state.speed_mps))

    power = (
        cyclist.default_power_w if power_w is None else float(power_w)
    )
    _check_power(cyclist, power)

    profile = ArcProfile.from_arc(arc)
    distance = profile.distance_m
    mass = cyclist.mass_kg + bicycle.mass_kg
    efficiency = bicycle.drivetrain_efficiency
    uncertainty = profile.elevation_uncertainty
    status = classify_status(uncertainty, require_elevation=require_elevation)

    # Inclinacao media do arco a partir do balanco de elevacao (mesma hipotese
    # de terreno plano da engine quando a elevacao e desconhecida).
    net_rise = profile.ascent_m - profile.descent_m
    slope_rad = math.atan2(net_rise, distance)

    limit = _arc_speed_limit(profile)
    speed_limit = environment.safe_max_speed_mps
    if limit is None:
        limit = speed_limit
    elif speed_limit is not None:
        limit = min(limit, speed_limit)

    # Velocidade de equilibrio (a engine resolve P(v) = P_disponivel).
    equilibrium = equilibrium_speed_mps(
        slope_rad,
        mass,
        cyclist.frontal_area_m2,
        bicycle,
        environment,
        power,
        drivetrain_efficiency=efficiency,
        max_speed_mps=limit,
    )
    speed = max(0.0, equilibrium)
    travel_speed = max(speed, MIN_SPEED_MPS)
    time_s = distance / travel_speed

    # Trabalho apenas enquanto se pedala; a velocidade no teto implica coasting.
    at_limit = limit is not None and speed >= limit - _EPS
    pedaling = power > 0.0 and not at_limit
    if pedaling:
        rider_energy = power * time_s
        wheel_energy = rider_energy * efficiency
    else:
        rider_energy = 0.0
        wheel_energy = 0.0

    breakdown = forces_at(
        slope_rad,
        travel_speed,
        mass,
        cyclist.frontal_area_m2,
        bicycle,
        environment,
    )
    stalled = speed < MIN_SPEED_MPS

    final_state = SimulationState(
        elapsed_s=state.elapsed_s + time_s,
        speed_mps=speed,
        wheel_work_j=state.wheel_work_j + wheel_energy,
        rider_work_j=state.rider_work_j + rider_energy,
        braking_loss_j=state.braking_loss_j,
    )

    return TraversalResult(
        time_s=time_s,
        distance_m=distance,
        ascent_m=profile.ascent_m,
        descent_m=profile.descent_m,
        initial_speed_mps=initial_speed,
        final_speed_mps=speed,
        mechanical_energy_j=wheel_energy,
        rider_energy_j=rider_energy,
        braking_energy_j=0.0,
        stalled=stalled,
        arc_id=arc.id,
        state=final_state,
        elevation_uncertainty=uncertainty,
        result_status=status,
        aero_loss_j=breakdown.aerodynamic_n * distance,
        rolling_loss_j=breakdown.rolling_n * distance,
        used_min_speed_regularization=stalled,
    )


@dataclass(frozen=True, slots=True)
class SteadyStateTraversalModel:
    """``TraversalModel`` aproximado por equilibrio (uso em busca).

    Intencionalmente **nao** expoe ``max_speed_mps``: a busca o ve como teto
    desconhecido (``UNKNOWN_SPEED``) e a heuristica padrao de tempo cai para
    ``0``. Assim a busca permanece admissivel por construcao, sem presuncao
    sobre a qualidade do estimador (ADR-025, decisao de desempenho).
    """

    cyclist: CyclistModel
    bicycle: BicycleModel
    environment: Environment | None = None
    power_w: float | None = None
    require_elevation: bool = False

    @property
    def mass_kg(self) -> float:
        return self.cyclist.mass_kg + self.bicycle.mass_kg

    def traverse(
        self, arc: Arc, initial_speed_mps: float
    ) -> TraversalResult:
        return estimate_arc(
            arc,
            self.cyclist,
            self.bicycle,
            initial_state=SimulationState.from_speed(initial_speed_mps),
            environment=self.environment,
            power_w=self.power_w,
            require_elevation=self.require_elevation,
        )


# ==========================================================================
# Comparacao estimador x simulador (relatorio)
# ==========================================================================
def _relative(error: float, reference: float) -> float | None:
    """Erro relativo, ou ``None`` quando a referencia e desprezivel."""
    denominator = abs(reference)
    if denominator <= 1e-6:
        return None
    return abs(error) / denominator


@dataclass(frozen=True, slots=True)
class ArcEstimateError:
    """Erro do estimador contra o simulador para um arco."""

    arc_id: str
    reference_time_s: float
    estimate_time_s: float
    time_rel_error: float | None
    reference_energy_j: float
    estimate_energy_j: float
    energy_rel_error: float | None
    reference_final_speed_mps: float
    estimate_final_speed_mps: float
    final_speed_error_mps: float
    reference_steps: int
    estimate_steps: int

    def to_dict(self) -> dict[str, object]:
        return {
            "arc_id": self.arc_id,
            "reference_time_s": round(self.reference_time_s, 4),
            "estimate_time_s": round(self.estimate_time_s, 4),
            "time_rel_error": (
                None if self.time_rel_error is None else round(self.time_rel_error, 6)
            ),
            "reference_energy_j": round(self.reference_energy_j, 3),
            "estimate_energy_j": round(self.estimate_energy_j, 3),
            "energy_rel_error": (
                None
                if self.energy_rel_error is None
                else round(self.energy_rel_error, 6)
            ),
            "reference_final_speed_mps": round(self.reference_final_speed_mps, 4),
            "estimate_final_speed_mps": round(self.estimate_final_speed_mps, 4),
            "final_speed_error_mps": round(self.final_speed_error_mps, 4),
            "reference_steps": self.reference_steps,
            "estimate_steps": self.estimate_steps,
        }


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Relatorio agregado estimador x simulador.

    Custos sao contados em **avaliacoes de passo** (uma por ``advance_speed`` no
    simulador; ``1`` por arco no estimador), um proxy deterministico do custo
    computacional.
    """

    arcs: tuple[ArcEstimateError, ...]
    max_time_rel_error: float | None
    mean_time_rel_error: float | None
    max_energy_rel_error: float | None
    mean_energy_rel_error: float | None
    max_final_speed_abs_error: float
    reference_steps: int
    estimate_steps: int

    @property
    def count(self) -> int:
        return len(self.arcs)

    @property
    def step_reduction_factor(self) -> float | None:
        """Razao de avaliacoes de passo ``simulador / estimador``."""
        if self.estimate_steps <= 0:
            return None
        return self.reference_steps / self.estimate_steps

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "max_time_rel_error": self.max_time_rel_error,
            "mean_time_rel_error": self.mean_time_rel_error,
            "max_energy_rel_error": self.max_energy_rel_error,
            "mean_energy_rel_error": self.mean_energy_rel_error,
            "max_final_speed_abs_error": round(self.max_final_speed_abs_error, 4),
            "reference_steps": self.reference_steps,
            "estimate_steps": self.estimate_steps,
            "step_reduction_factor": (
                None
                if self.step_reduction_factor is None
                else round(self.step_reduction_factor, 3)
            ),
            "arcs": [arc.to_dict() for arc in self.arcs],
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _max(values: list[float]) -> float | None:
    return max(values) if values else None


def compare_traversals(
    arcs: list[Arc],
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    *,
    initial_state: SimulationState | None = None,
    environment: Environment | None = None,
    power_w: float | None = None,
    reference_step_m: float = 5.0,
) -> ComparisonReport:
    """Compara o simulador completo e o estimador arco a arco.

    O simulador completo e ``PhysicalTraversal(step_m=reference_step_m)``; o
    estimador e ``estimate_arc``. ``reference_steps`` conta as avaliacoes de
    passo do simulador (``ceil(distancia / passo)`` equivalente, via perfil).
    """
    from router.core.physics.physical.traversal import PhysicalTraversal

    environment = environment or Environment()
    state = initial_state if initial_state is not None else SimulationState()
    traversal = PhysicalTraversal(step_m=reference_step_m)

    errors: list[ArcEstimateError] = []
    reference_steps = 0
    estimate_steps = 0
    time_rels: list[float] = []
    energy_rels: list[float] = []
    speed_abs: list[float] = []

    for arc in arcs:
        reference = traversal.traverse(
            arc,
            cyclist,
            bicycle,
            initial_state=state,
            environment=environment,
            power_w=power_w,
        )
        estimate = estimate_arc(
            arc,
            cyclist,
            bicycle,
            initial_state=state,
            environment=environment,
            power_w=power_w,
        )
        steps = len(traversal.steps(arc))
        reference_steps += steps
        estimate_steps += 1

        time_rel = _relative(
            estimate.time_s - reference.time_s, reference.time_s
        )
        energy_rel = _relative(
            estimate.mechanical_energy_j - reference.mechanical_energy_j,
            reference.mechanical_energy_j,
        )
        speed_err = abs(
            estimate.final_speed_mps - reference.final_speed_mps
        )
        if time_rel is not None:
            time_rels.append(time_rel)
        if energy_rel is not None:
            energy_rels.append(energy_rel)
        speed_abs.append(speed_err)

        errors.append(
            ArcEstimateError(
                arc_id=arc.id,
                reference_time_s=reference.time_s,
                estimate_time_s=estimate.time_s,
                time_rel_error=time_rel,
                reference_energy_j=reference.mechanical_energy_j,
                estimate_energy_j=estimate.mechanical_energy_j,
                energy_rel_error=energy_rel,
                reference_final_speed_mps=reference.final_speed_mps,
                estimate_final_speed_mps=estimate.final_speed_mps,
                final_speed_error_mps=speed_err,
                reference_steps=steps,
                estimate_steps=1,
            )
        )

    return ComparisonReport(
        arcs=tuple(errors),
        max_time_rel_error=_max(time_rels),
        mean_time_rel_error=_mean(time_rels),
        max_energy_rel_error=_max(energy_rels),
        mean_energy_rel_error=_mean(energy_rels),
        max_final_speed_abs_error=max(speed_abs) if speed_abs else 0.0,
        reference_steps=reference_steps,
        estimate_steps=estimate_steps,
    )


# ==========================================================================
# Impacto sobre a ordenacao de rotas
# ==========================================================================
@dataclass(frozen=True, slots=True)
class RankingComparison:
    """Impacto de uma ordenacao alternativa sobre **as mesmas rotas**.

    Cada ranking e uma lista ordenada de pares ``(chave, vetor_de_objetivos)``,
    onde a chave identifica a rota (por exemplo, sua sequencia de arcos). Separar
    a **identidade** da rota do **valor** do objetivo permite medir duas coisas
    distintas:

    * ``inverted_pairs`` — pares de rotas cuja ordem relativa muda (inversao);
      zero significa que a ordenacao relativa foi **preservada**;
    * ``max_objective_delta`` / ``keys_value_changed`` — o quanto os **valores**
      dos objetivos das rotas comuns mudaram.

    Nada aqui presume que a ordenacao alternativa seja admissivel: e apenas uma
    medicao da diferenca observada.
    """

    reference_count: int
    estimate_count: int
    same_key_set: bool
    identical_order: bool
    inverted_pairs: int
    total_shared_pairs: int
    keys_value_changed: int
    max_objective_delta: float
    tolerance: float

    @property
    def order_agreement(self) -> float | None:
        """Fracao de pares comuns cuja ordem relativa e preservada."""
        if self.total_shared_pairs <= 0:
            return None
        return 1.0 - self.inverted_pairs / self.total_shared_pairs

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_count": self.reference_count,
            "estimate_count": self.estimate_count,
            "same_key_set": self.same_key_set,
            "identical_order": self.identical_order,
            "inverted_pairs": self.inverted_pairs,
            "total_shared_pairs": self.total_shared_pairs,
            "order_agreement": (
                None
                if self.order_agreement is None
                else round(self.order_agreement, 6)
            ),
            "keys_value_changed": self.keys_value_changed,
            "max_objective_delta": round(self.max_objective_delta, 6),
            "tolerance": self.tolerance,
        }


def compare_rankings(
    reference: list[tuple[Hashable, tuple[float, ...]]],
    estimate: list[tuple[Hashable, tuple[float, ...]]],
    *,
    tolerance: float = 1e-6,
) -> RankingComparison:
    """Compara duas ordenacoes de rotas por **chave** e por **valor**.

    ``reference`` e ``estimate`` sao listas ordenadas de ``(chave, vetor)``. A
    concordancia de ordem e medida na interseccao das chaves (inversoes de pares,
    tipo Kendall); a diferenca de valor usa o maximo desvio absoluto por
    coordenada entre as rotas comuns.
    """
    ref_keys = [key for key, _ in reference]
    est_keys = [key for key, _ in estimate]
    ref_pos = {key: index for index, key in enumerate(ref_keys)}
    est_pos = {key: index for index, key in enumerate(est_keys)}
    shared = [key for key in ref_keys if key in est_pos]

    inversions = 0
    for a in range(len(shared)):
        for b in range(a + 1, len(shared)):
            first, second = shared[a], shared[b]
            if (est_pos[first] - est_pos[second]) * (
                ref_pos[first] - ref_pos[second]
            ) < 0:
                inversions += 1

    ref_vec = dict(reference)
    est_vec = dict(estimate)
    max_delta = 0.0
    changed = 0
    for key in shared:
        ref_coords = ref_vec[key]
        est_coords = est_vec[key]
        width = min(len(ref_coords), len(est_coords))
        delta = max(
            (abs(est_coords[i] - ref_coords[i]) for i in range(width)),
            default=0.0,
        )
        max_delta = max(max_delta, delta)
        if delta > tolerance:
            changed += 1

    return RankingComparison(
        reference_count=len(ref_keys),
        estimate_count=len(est_keys),
        same_key_set=set(ref_keys) == set(est_keys),
        identical_order=ref_keys == est_keys,
        inverted_pairs=inversions,
        total_shared_pairs=len(shared) * (len(shared) - 1) // 2,
        keys_value_changed=changed,
        max_objective_delta=max_delta,
        tolerance=tolerance,
    )


@dataclass(frozen=True, slots=True)
class SearchRankingComparison:
    """Impacto do estimador sobre a **fronteira de busca** de um perfil.

    Executa A* duas vezes — uma com o simulador completo, outra com o estimador
    — e compara as fronteiras:

    * ``ranking`` — concordancia de ordem (rotas identificadas pela sequencia de
      arcos) e diferenca de valor dos objetivos;
    * ``same_route_set`` — se as duas buscas chegaram ao **mesmo conjunto** de
      sequencias de arcos (independe de ordem);
    * ``same_top_route`` — se a rota preferida (primeira) coincide;
    * ``reference_expanded`` / ``estimate_expanded`` — nos expandidos em cada
      busca (impacto no custo da busca, nao so na travessia).

    Nada aqui presume que a fronteira do estimador seja correta ou admissivel:
    os dois lados sao medidos lado a lado.
    """

    profile_id: str
    ranking: RankingComparison
    same_route_set: bool
    same_top_route: bool
    reference_expanded: int
    estimate_expanded: int
    reference_arc_sequences: tuple[tuple[str | None, ...], ...]
    estimate_arc_sequences: tuple[tuple[str | None, ...], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "ranking": self.ranking.to_dict(),
            "same_route_set": self.same_route_set,
            "same_top_route": self.same_top_route,
            "reference_expanded": self.reference_expanded,
            "estimate_expanded": self.estimate_expanded,
            "reference_arc_sequences": [
                list(seq) for seq in self.reference_arc_sequences
            ],
            "estimate_arc_sequences": [
                list(seq) for seq in self.estimate_arc_sequences
            ],
        }


def compare_search_rankings(
    graph: RoutingGraph,
    origin: Hashable,
    destination: Hashable,
    profile: SearchProfile | str,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    *,
    initial_speed_mps: float = 0.0,
    environment: Environment | None = None,
    power_w: float | None = None,
    reference_step_m: float = 5.0,
    tolerance: float = 1e-6,
) -> SearchRankingComparison:
    """Compara a fronteira de busca do simulador completo e do estimador.

    O simulador completo usa ``PhysicalTraversalModel`` (passo
    ``reference_step_m``); o estimador usa ``SteadyStateTraversalModel``. Ambos
    usam a heuristica **padrao** de ``MultiObjectiveAStar`` — admissivel nos dois
    casos, porque o estimador nao declara teto de velocidade e o limite de tempo
    cai para ``0``. Ou seja: comparamos as fronteiras **sem** tratar o estimador
    como heuristica admissivel (ADR-025, decisao de desempenho).
    """
    from .astar import MultiObjectiveAStar
    from .objectives import profile_for
    from .traversal_model import PhysicalTraversalModel
    from router.core.physics.physical.traversal import PhysicalTraversal

    environment = environment or Environment()
    resolved = profile_for(profile) if isinstance(profile, str) else profile

    reference_model = PhysicalTraversalModel(
        PhysicalTraversal(step_m=reference_step_m),
        cyclist,
        bicycle,
        environment,
        power_w,
    )
    estimate_model = SteadyStateTraversalModel(
        cyclist=cyclist,
        bicycle=bicycle,
        environment=environment,
        power_w=power_w,
    )

    reference = MultiObjectiveAStar(
        graph,
        reference_model,
        resolved,
        initial_speed_mps=initial_speed_mps,
    ).search(origin, destination)
    estimate = MultiObjectiveAStar(
        graph,
        estimate_model,
        resolved,
        initial_speed_mps=initial_speed_mps,
    ).search(origin, destination)

    ref_routes = reference.arc_sequences()
    est_routes = estimate.arc_sequences()
    ref_vectors = [
        (sequence, resolved.values(label))
        for sequence, label in zip(ref_routes, reference.labels)
    ]
    est_vectors = [
        (sequence, resolved.values(label))
        for sequence, label in zip(est_routes, estimate.labels)
    ]
    ranking = compare_rankings(ref_vectors, est_vectors, tolerance=tolerance)

    return SearchRankingComparison(
        profile_id=resolved.id,
        ranking=ranking,
        same_route_set=set(ref_routes) == set(est_routes),
        same_top_route=ref_routes[:1] == est_routes[:1],
        reference_expanded=reference.expanded,
        estimate_expanded=estimate.expanded,
        reference_arc_sequences=ref_routes,
        estimate_arc_sequences=est_routes,
    )
