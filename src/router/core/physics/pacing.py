"""Ritmo do ciclista: potencia por arco com orcamento anaerobio.

A politica ``hold-reference-speed@2`` combina duas ideias explicitas:

1. **demanda** (``demand.py``) — quanto custa manter o arco na velocidade de
   referencia declarada;
2. **orcamento** — o ciclista pode exceder sua potencia critica apenas ate
   gastar o trabalho anaerobio disponivel (``W'``).

Logo, a potencia humana em cada arco e::

    P_hum = min(P_req, CP + W'_restante / t_arco)

``t_arco`` e resolvido por ponto fixo (potencia -> velocidade de equilibrio ->
duracao). O saldo de ``W'`` e debitado com a duracao **real** da travessia e
recuperado a uma fracao declarada de ``(CP - P)`` quando a potencia fica abaixo
da critica.

Por que nao um teto fixo em ``P_ref``: o teto fixo proibe o ciclista de subir
uma rampa curta acima da referencia, entao ``P / P_ref`` nunca passa de 1 e o
RPE satura em 17. Com o orcamento, o esforco alto e pago e o RPE pode chegar a
18-20 — e a subida longa derruba o teto quando o tanque acaba.

A potencia nao e uma prescricao de treino: e a **hipotese de ritmo** usada para
estimar tempo e esforco. Nenhum valor aqui e uma medicao.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from router.core.data.graph.model import Arc

from .bicycle import BicycleModel
from .cyclist import CyclistModel
from .demand import ArcDemand, ArcDemandField, DemandScenario
from .environment import Environment
from .forces import equilibrium_speed_mps
from .models import SimulationState, TraversalPath, TraversalResult
from .traversal import traversal_path_from_results

#: Identidade estavel da politica de ritmo.
PACING_POLICY_ID = "hold-reference-speed"

#: Versao da politica (muda quando a regra de potencia muda).
PACING_POLICY_VERSION = "2"

#: Identificador completo ``id@versao``.
PACING_POLICY = f"{PACING_POLICY_ID}@{PACING_POLICY_VERSION}"

#: Trabalho anaerobio provisorio quando o ciclista nao declara CP/W'.
DEFAULT_W_PRIME_J = 15_000.0

#: Fracao da diferenca para CP recuperada por segundo abaixo da critica.
DEFAULT_RECOVERY_FACTOR = 0.5

#: Duracao maxima de um ponto fixo de potencia (segundos).
_MAX_ARC_DURATION_S = 24.0 * 3600.0

#: Tolerancia de convergencia do ponto fixo (segundos).
_FIXED_POINT_TOLERANCE_S = 0.25


class PacingParameterSource(str, Enum):
    """Proveniencia dos parametros de ritmo.

    Espelha ``physiology.ParameterSource`` sem importar fisiologia: a camada
    fisica nao depende da camada fisiologica (ADR-031).
    """

    MEASURED = "measured"
    PROFILE_ESTIMATED = "profile_estimated"
    CALIBRATED_MODEL_ESTIMATED = "calibrated_model_estimated"
    PROVISIONAL_DEFAULT = "provisional_default"


@dataclass(frozen=True, slots=True)
class ReferencePacing:
    """Parametros declarados da politica de ritmo.

    ``reference_speed_mps`` e uma **preferencia** de ritmo (o "ritmo
    planejado"), nunca derivada de ``P_ref``. ``critical_power_w`` e
    ``w_prime_j`` formam o orcamento anaerobio; quando nao ha medicao, o
    chamador usa ``w_prime_source=PROVISIONAL_DEFAULT``.
    """

    reference_speed_mps: float
    reference_power_w: float
    critical_power_w: float
    w_prime_j: float = DEFAULT_W_PRIME_J
    recovery_factor: float = DEFAULT_RECOVERY_FACTOR
    max_power_w: float | None = None
    critical_power_source: PacingParameterSource = (
        PacingParameterSource.PROVISIONAL_DEFAULT
    )
    w_prime_source: PacingParameterSource = (
        PacingParameterSource.PROVISIONAL_DEFAULT
    )

    def __post_init__(self) -> None:
        for name in (
            "reference_speed_mps",
            "reference_power_w",
            "critical_power_w",
            "w_prime_j",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} deve ser positivo e finito (recebido {value!r})")
        if not math.isfinite(self.recovery_factor) or not 0.0 < self.recovery_factor <= 1.0:
            raise ValueError(
                "recovery_factor deve estar em (0, 1] "
                f"(recebido {self.recovery_factor!r})"
            )
        if self.max_power_w is not None:
            if not math.isfinite(self.max_power_w) or self.max_power_w <= 0.0:
                raise ValueError(
                    f"max_power_w deve ser positivo (recebido {self.max_power_w!r})"
                )
        if not isinstance(self.critical_power_source, PacingParameterSource):
            raise ValueError("critical_power_source deve ser um PacingParameterSource")
        if not isinstance(self.w_prime_source, PacingParameterSource):
            raise ValueError("w_prime_source deve ser um PacingParameterSource")

    @property
    def w_prime_is_provisional(self) -> bool:
        return self.w_prime_source is PacingParameterSource.PROVISIONAL_DEFAULT

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": PACING_POLICY,
            "reference_speed_mps": round(self.reference_speed_mps, 4),
            "reference_speed_kmh": round(3.6 * self.reference_speed_mps, 3),
            "reference_power_w": round(self.reference_power_w, 3),
            "critical_power_w": round(self.critical_power_w, 3),
            "w_prime_j": round(self.w_prime_j, 3),
            "recovery_factor": round(self.recovery_factor, 4),
            "max_power_w": (
                None if self.max_power_w is None else round(self.max_power_w, 3)
            ),
            "critical_power_source": self.critical_power_source.value,
            "w_prime_source": self.w_prime_source.value,
            "w_prime_is_provisional": self.w_prime_is_provisional,
        }


@dataclass(frozen=True, slots=True)
class ArcPacing:
    """Ritmo aplicado a um arco, com o custo que o gerou."""

    arc_id: str
    demand_power_w: float
    demand_ratio: float
    achieved_power_w: float
    achieved_ratio: float
    duration_s: float
    w_prime_used_j: float
    w_prime_balance_j: float

    def __post_init__(self) -> None:
        for name in (
            "demand_power_w",
            "demand_ratio",
            "achieved_power_w",
            "achieved_ratio",
            "duration_s",
            "w_prime_used_j",
            "w_prime_balance_j",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} deve ser finito e nao negativo")

    def to_dict(self) -> dict[str, object]:
        return {
            "arc_id": self.arc_id,
            "demand_power_w": round(self.demand_power_w, 3),
            "demand_ratio": round(self.demand_ratio, 4),
            "achieved_power_w": round(self.achieved_power_w, 3),
            "achieved_ratio": round(self.achieved_ratio, 4),
            "duration_s": round(self.duration_s, 3),
            "w_prime_used_j": round(self.w_prime_used_j, 3),
            "w_prime_balance_j": round(self.w_prime_balance_j, 3),
        }


@dataclass(frozen=True, slots=True)
class PacedPath:
    """Resultado fisico de percorrer arcos com a politica de ritmo."""

    path: TraversalPath
    pacings: tuple[ArcPacing, ...]
    demands: tuple[ArcDemand, ...]
    pacing: ReferencePacing
    w_prime_start_j: float
    w_prime_end_j: float

    def __post_init__(self) -> None:
        if len(self.pacings) != len(self.path.results):
            raise ValueError("pacings deve ter o mesmo tamanho dos resultados")
        if len(self.demands) != len(self.path.results):
            raise ValueError("demands deve ter o mesmo tamanho dos resultados")
        if not isinstance(self.pacing, ReferencePacing):
            raise ValueError("pacing deve ser um ReferencePacing")

    @property
    def policy(self) -> str:
        return PACING_POLICY

    @property
    def w_prime_used_j(self) -> float:
        return max(0.0, self.w_prime_start_j - self.w_prime_end_j)

    @property
    def w_prime_fraction_end(self) -> float:
        if self.pacing.w_prime_j <= 0.0:
            return 0.0
        return max(
            0.0,
            min(1.0, 1.0 - self.w_prime_end_j / self.pacing.w_prime_j),
        )

    @property
    def distance_by_grade_bin_m(self) -> tuple[float, float, float]:
        totals = [0.0, 0.0, 0.0]
        for demand in self.demands:
            for index, value in enumerate(demand.distance_by_grade_bin_m):
                totals[index] += value
        return (totals[0], totals[1], totals[2])

    @property
    def mean_demand_power_w(self) -> float:
        total_distance = sum(demand.distance_m for demand in self.demands)
        if total_distance <= 0.0:
            return 0.0
        return (
            sum(
                demand.mean_power_w * demand.distance_m for demand in self.demands
            )
            / total_distance
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "pacing": self.pacing.to_dict(),
            "w_prime_start_j": round(self.w_prime_start_j, 3),
            "w_prime_end_j": round(self.w_prime_end_j, 3),
            "w_prime_used_j": round(self.w_prime_used_j, 3),
            "w_prime_fraction_end": round(self.w_prime_fraction_end, 4),
            "mean_demand_power_w": round(self.mean_demand_power_w, 3),
            "distance_by_grade_bin_m": [
                round(value, 3) for value in self.distance_by_grade_bin_m
            ],
            "arcs": [pacing.to_dict() for pacing in self.pacings],
            "path": self.path.to_dict(),
        }


def _resolve_arc_power(
    demand: ArcDemand,
    pacing: ReferencePacing,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    environment: Environment,
    w_balance_j: float,
    max_power_w: float,
) -> tuple[float, float]:
    """Potencia humana e duracao estimada para um arco.

    O teto depende de ``t`` e ``t`` depende da potencia; o ponto fixo e
    amortecido (media com a iteracao anterior) para nao oscilar em rampas onde
    a velocidade de equilibrio e muito sensivel a potencia.
    """
    length = max(0.0, demand.distance_m)
    if length <= 0.0:
        return 0.0, 0.0

    fallback_t = length / pacing.reference_speed_mps
    if demand.mean_power_w <= 0.0:
        return 0.0, fallback_t

    slope = math.atan(demand.mean_grade_pct / 100.0)
    mass = cyclist.mass_kg + bicycle.mass_kg
    t = fallback_t
    for _ in range(40):
        ceiling = min(
            max_power_w,
            pacing.critical_power_w + max(w_balance_j, 0.0) / max(t, 1.0),
        )
        power = min(demand.mean_power_w, ceiling)
        speed = equilibrium_speed_mps(
            slope,
            mass,
            cyclist.frontal_area_m2,
            bicycle,
            environment,
            power,
        )
        next_t = min(length / max(speed, 0.2), _MAX_ARC_DURATION_S)
        if abs(next_t - t) <= _FIXED_POINT_TOLERANCE_S:
            t = next_t
            break
        t = 0.5 * (t + next_t)

    ceiling = min(
        max_power_w,
        pacing.critical_power_w + max(w_balance_j, 0.0) / max(t, 1.0),
    )
    return max(0.0, min(demand.mean_power_w, ceiling)), t


def _update_w_prime(
    balance_j: float,
    power_w: float,
    duration_s: float,
    pacing: ReferencePacing,
) -> float:
    """Debita (acima de CP) ou recupera (abaixo) o saldo de ``W'``."""
    excess = power_w - pacing.critical_power_w
    if excess > 0.0:
        return max(0.0, balance_j - excess * duration_s)
    recovered = pacing.recovery_factor * (-excess) * duration_s
    return min(pacing.w_prime_j, balance_j + recovered)


def run_paced_path(
    arcs: Sequence[Arc],
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    *,
    pacing: ReferencePacing,
    demand_field: ArcDemandField | None = None,
    scenario: DemandScenario | None = None,
    initial_state: SimulationState | None = None,
    environment: Environment | None = None,
) -> PacedPath:
    """Percorre ``arcs`` aplicando a politica de ritmo.

    Reutiliza ``PhysicalTraversal.traverse`` arco a arco, propagando o estado
    fisico. A demanda vem de ``ArcDemandField`` (cache por arco e cenario).

    Args:
        arcs: Arcos direcionados, na ordem da rota.
        cyclist: Modelo fisico do ciclista.
        bicycle: Modelo da bicicleta.
        pacing: Parametros declarados da politica.
        demand_field: Cache de demanda; se omitido, cria um novo.
        scenario: Cenario da demanda; se omitido, derivado de ``cyclist``,
            ``bicycle``, ``environment`` e ``pacing.reference_speed_mps``.
        initial_state: Estado dinamico na entrada do primeiro arco.
        environment: Ambiente da travessia; padrao do cenario (ou ``Environment()``).

    Returns:
        ``PacedPath`` com a travessia agregada, o ritmo por arco e o saldo
        final de ``W'``.
    """
    if not isinstance(pacing, ReferencePacing):
        raise ValueError("pacing deve ser um ReferencePacing")
    field = demand_field if demand_field is not None else ArcDemandField()
    resolved_environment = (
        environment
        if environment is not None
        else (scenario.environment if scenario is not None else Environment())
    )
    if scenario is None:
        scenario = DemandScenario.from_models(
            cyclist.mass_kg,
            cyclist.frontal_area_m2,
            bicycle,
            resolved_environment,
            pacing.reference_speed_mps,
        )

    max_power_w = (
        pacing.max_power_w
        if pacing.max_power_w is not None
        else cyclist.max_power_w
    )
    if max_power_w <= 0.0:
        raise ValueError("max_power_w deve ser positivo para a politica de ritmo")

    state = initial_state if initial_state is not None else SimulationState()
    w_balance = pacing.w_prime_j
    results: list[TraversalResult] = []
    pacings: list[ArcPacing] = []
    demands: list[ArcDemand] = []

    for arc in arcs:
        demand = field.demand(arc, scenario)
        power, _estimated_s = _resolve_arc_power(
            demand,
            pacing,
            cyclist,
            bicycle,
            resolved_environment,
            w_balance,
            max_power_w,
        )
        result = field.traversal.traverse(
            arc,
            cyclist,
            bicycle,
            initial_state=state,
            environment=resolved_environment,
            power_w=power,
        )
        state = result.simulation_state
        start_balance = w_balance
        w_balance = _update_w_prime(w_balance, power, result.time_s, pacing)
        results.append(result)
        demands.append(demand)
        pacings.append(
            ArcPacing(
                arc_id=arc.id,
                demand_power_w=demand.mean_power_w,
                demand_ratio=demand.ratio_to(pacing.reference_power_w),
                achieved_power_w=power,
                achieved_ratio=power / pacing.reference_power_w,
                duration_s=result.time_s,
                w_prime_used_j=max(0.0, start_balance - w_balance),
                w_prime_balance_j=w_balance,
            )
        )

    path = traversal_path_from_results(
        tuple(results),
        initial_speed_mps=(
            initial_state.speed_mps if initial_state is not None else 0.0
        ),
    )
    return PacedPath(
        path=path,
        pacings=tuple(pacings),
        demands=tuple(demands),
        pacing=pacing,
        w_prime_start_j=pacing.w_prime_j,
        w_prime_end_j=w_balance,
    )


__all__ = [
    "PACING_POLICY_ID",
    "PACING_POLICY_VERSION",
    "PACING_POLICY",
    "DEFAULT_W_PRIME_J",
    "DEFAULT_RECOVERY_FACTOR",
    "PacingParameterSource",
    "ReferencePacing",
    "ArcPacing",
    "PacedPath",
    "run_paced_path",
]
