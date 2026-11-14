"""Adaptador do modelo fisico para a busca.

A busca nao conhece as equacoes fisicas nem os modelos de ciclista/bicicleta.
Ela conversa com um ``TraversalModel``: algo que sabe a massa total em movimento
e que, dada um arco e a velocidade de entrada, devolve um
``TraversalResult``.

``PhysicalTraversalModel`` liga o ``PhysicalTraversal`` aos modelos e ao
ambiente. A busca continua sem receber perfil nem superficie.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from router.core.data.graph.model import Arc
from router.core.physics.bicycle import BicycleModel
from router.core.physics.cyclist import CyclistModel
from router.core.physics.environment import (
    DEFAULT_SAFE_MAX_SPEED_MPS,
    Environment,
)
from router.core.physics.models import SimulationState, TraversalResult
from router.core.physics.traversal import PhysicalTraversal


class _UnknownSpeedSentinel:
    """Sentinela: o modelo **nao declara** seu teto de velocidade.

    Distinta de ``None``, que significa "declaradamente ilimitado". Sem os tres
    estados separados, um guard de heuristica nao consegue ser correto: nao da
    para punir um modelo que apenas nao expoe o atributo.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN_SPEED"


#: Usado quando o ``TraversalModel`` nao informa ``max_speed_mps``.
#: (Finito conhecido -> ``float``; ilimitado declarado -> ``None``.)
UNKNOWN_SPEED = _UnknownSpeedSentinel()


@runtime_checkable
class TraversalModel(Protocol):
    """Contrato minimo que a busca espera do modelo fisico.

    Um modelo pode, **opcionalmente**, anunciar ``max_speed_mps``:

    * ``float`` — teto real conhecido (m/s);
    * ``None`` — declaradamente ilimitado;
    * atributo ausente — desconhecido (``UNKNOWN_SPEED``).

    A busca usa esse valor para manter a heuristica de tempo admissivel. Modelos
    que nao expoem o atributo sao tratados como "teto desconhecido" e a
    heuristica cai para o limite de tempo ``0`` (sempre admissivel, porem mais
    fraca).
    """

    @property
    def mass_kg(self) -> float:
        """Massa total em movimento (ciclista + bicicleta)."""

    def traverse(
        self, arc: Arc, initial_speed_mps: float
    ) -> TraversalResult:
        """Travessia fisica de ``arc`` a partir de ``initial_speed_mps``."""


@dataclass
class PhysicalTraversalModel:
    """Liga ``PhysicalTraversal`` aos modelos de ciclista, bicicleta e ambiente."""

    traversal: PhysicalTraversal
    cyclist: CyclistModel
    bicycle: BicycleModel
    environment: Environment | None = None
    power_w: float | None = None
    _traversal_cache: OrderedDict[tuple[str, float], tuple[Arc, TraversalResult]] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    cache_enabled: bool = True
    _cache_signature: tuple | None = field(default=None, init=False, repr=False)
    _traversal_cache_limit: int = field(default=100_000, init=False, repr=False)
    traversal_cache_hits: int = field(default=0, init=False)
    traversal_cache_misses: int = field(default=0, init=False)

    @property
    def mass_kg(self) -> float:
        return self.cyclist.mass_kg + self.bicycle.mass_kg

    @property
    def max_speed_mps(self) -> float | None:
        """Teto de velocidade efetivo do modelo (m/s), ou ``None`` se ilimitado.

        Reflete ``Environment.safe_max_speed_mps``. Quando o ambiente e omitido,
        o modelo fisico usa ``Environment()`` por padrao, logo o teto tambem e o
        padrao. A busca usa este valor para manter a heuristica de tempo
        **admissivel** (ela so e valida se este teto for real).
        """
        if self.environment is None:
            return DEFAULT_SAFE_MAX_SPEED_MPS
        return self.environment.safe_max_speed_mps

    def traverse(
        self, arc: Arc, initial_speed_mps: float
    ) -> TraversalResult:
        """Travessia fisica de ``arc`` a partir de ``initial_speed_mps``.

        A busca transporta apenas a velocidade (contrato ``TraversalModel``);
        aqui ela e convertida no estado dinamico canonico ``SimulationState``
        exigido pelo simulador fisico (ADR-025, decisao 1).
        """
        signature = (
            self.traversal, self.traversal.step_m,
            self.traversal.acceleration_limit_mps2, self.traversal.require_elevation,
            self.cyclist, self.bicycle, self.environment, self.power_w,
        )
        if signature != self._cache_signature:
            self._traversal_cache.clear()
            self._cache_signature = signature
        key = (arc.id, float(initial_speed_mps))
        cached = self._traversal_cache.get(key) if self.cache_enabled else None
        if cached is not None and cached[0] is arc:
            self._traversal_cache.move_to_end(key)
            self.traversal_cache_hits += 1
            return cached[1]

        self.traversal_cache_misses += 1
        result = self.traversal.traverse(
            arc,
            self.cyclist,
            self.bicycle,
            initial_state=SimulationState.from_speed(initial_speed_mps),
            environment=self.environment,
            power_w=self.power_w,
        )
        if self.cache_enabled:
            self._traversal_cache[key] = (arc, result)
        if len(self._traversal_cache) > self._traversal_cache_limit:
            self._traversal_cache.popitem(last=False)
        return result

    def arc_lower_bounds(self, arc: Arc) -> dict[str, float]:
        """Same directed-segment totals as ArcProfile, without preparing steps.

        Full elevation/force preparation is deferred until an arc is actually
        traversed. These totals are exactly the metrics returned by the model.
        """
        distance = arc.distance_m
        ascent = sum(segment.elevation_gain_m for segment in arc.segments)
        ceiling = self.max_speed_mps
        return {
            "distance_m": distance,
            "ascent_m": ascent,
            "time_s": distance / ceiling if ceiling is not None else 0.0,
            "energy_j": 0.0,
        }
