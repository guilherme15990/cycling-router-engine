"""Condicoes ambientais da travessia.

Vento e densidade do ar sao parametros do ambiente, nao do grafo. A primeira
versao assume vento zero por padrao, mas o modelo de forcas ja aceita vento
de frente (positivo) ou de cauda (negativo).
"""

from __future__ import annotations

from dataclasses import dataclass

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

    def __post_init__(self) -> None:
        non_negative("air_density_kg_m3", self.air_density_kg_m3)
        finite("headwind_mps", self.headwind_mps)
        positive("gravity_m_s2", self.gravity_m_s2)
        if self.safe_max_speed_mps is not None:
            positive("safe_max_speed_mps", self.safe_max_speed_mps)
