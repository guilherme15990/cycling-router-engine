"""Condicoes ambientais da travessia.

Vento e densidade do ar sao parametros do ambiente, nao do grafo. A primeira
versao assume vento zero por padrao, mas o modelo de forcas ja aceita vento
de frente (positivo) ou de cauda (negativo).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from ._validation import finite, non_negative, positive

DEFAULT_GRAVITY_M_S2 = 9.80665
DEFAULT_AIR_DENSITY_KG_M3 = 1.225
DEFAULT_SAFE_MAX_SPEED_MPS = 22.0


@dataclass(frozen=True, slots=True)
class Environment:
    """Ambiente fisico da travessia.

    ``safe_max_speed_mps`` e o limite de seguranca adotado pelo modelo. Ele
    nao substitui limites legais por arco: e apenas um teto global para
    evitar que uma descida longa acelere indefinidamente. Use ``None`` para
    desligar.
    """

    air_density_kg_m3: float = DEFAULT_AIR_DENSITY_KG_M3
    headwind_mps: float = 0.0
    gravity_m_s2: float = DEFAULT_GRAVITY_M_S2
    safe_max_speed_mps: float | None = DEFAULT_SAFE_MAX_SPEED_MPS
    # Meteorological direction: where the wind comes FROM, clockwise from north.
    # None keeps the historical scalar headwind contract. A vector replaces it.
    wind_speed_mps: float | None = None
    wind_from_deg: float = 0.0

    def __post_init__(self) -> None:
        non_negative("air_density_kg_m3", self.air_density_kg_m3)
        finite("headwind_mps", self.headwind_mps)
        positive("gravity_m_s2", self.gravity_m_s2)
        if self.safe_max_speed_mps is not None:
            positive("safe_max_speed_mps", self.safe_max_speed_mps)
        if self.wind_speed_mps is not None:
            non_negative("wind_speed_mps", self.wind_speed_mps)
        finite("wind_from_deg", self.wind_from_deg)

    def for_geometry(self, geometry) -> "Environment":
        """Resolve a fixed wind vector against the directed arc's mean bearing.

        This matches TopoRide's arc-level approximation. It does not model
        crosswind drag or direction changes inside a curved arc.
        """
        if self.wind_speed_mps is None:
            return self
        if self.wind_speed_mps == 0:
            return replace(self, headwind_mps=0.0, wind_speed_mps=None)
        if len(geometry) < 2 or geometry[0] == geometry[-1]:
            raise ValueError("directional wind requires a directed arc geometry")
        (lat_a, lon_a), (lat_b, lon_b) = geometry[0], geometry[-1]
        mean_lat = math.radians((lat_a + lat_b) / 2)
        bearing = math.degrees(math.atan2((lon_b - lon_a) * math.cos(mean_lat), lat_b - lat_a))
        headwind = self.wind_speed_mps * math.cos(math.radians(self.wind_from_deg - bearing))
        return replace(self, headwind_mps=headwind, wind_speed_mps=None)
