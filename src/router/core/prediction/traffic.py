"""Estresse de tráfego como eixo separado da intensidade (ADR-034, D5).

A escala é **ordinal de engenharia**, sem base fisiológica. Ela descreve
exposição/conforto e **nunca** entra na intensidade mecânica nem no RPE: uma
via movimentada não aumenta a potência necessária para vencer a rota.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Escala de estresse de trafego por classe de via OSM (origem: `toporide`
#: `features.ts` — `hwStress`; escala ordinal sem fonte fisiológica).
_HW_STRESS: dict[str, float] = {
    "cycleway": 0.05,
    "residential": 0.25,
    "living_street": 0.25,
    "tertiary": 0.5,
    "tertiary_link": 0.5,
    "secondary": 0.75,
    "secondary_link": 0.75,
    "primary": 0.95,
    "primary_link": 0.95,
    "trunk": 1.2,
    "trunk_link": 1.2,
}

#: Estresse de trafego para classe de via desconhecida.
_HW_STRESS_DEFAULT = 0.4


def hw_stress(highway: str | None) -> float:
    """Estresse de tráfego (ordinal) de uma classe de via OSM.

    Escala **heurística** de exposição, sem fonte fisiológica. Classe
    ausente/desconhecida usa ``_HW_STRESS_DEFAULT``.
    """
    if highway is None:
        return _HW_STRESS_DEFAULT
    return _HW_STRESS.get(highway, _HW_STRESS_DEFAULT)


#: Classes OSM consideradas vias arteriais (tráfego de passagem).
ARTERIAL_HIGHWAY_CLASSES: frozenset[str] = frozenset(
    {"trunk", "trunk_link", "primary", "primary_link"}
)


def arterial_entries(highways: Sequence[str | None]) -> int:
    """Numero de **entradas** em via arterial ao longo de uma sequencia.

    Trechos consecutivos na mesma arterial contam uma unica entrada: o numero
    descreve cruzamentos/insercoes na via, nao a soma de arcos. O sinal de
    ``hw_stress`` nao e usado — a contagem sai direto da classe OSM.
    """
    entries = 0
    previous = False
    for highway in highways:
        current = highway in ARTERIAL_HIGHWAY_CLASSES
        if current and not previous:
            entries += 1
        previous = current
    return entries


__all__ = [
    "ARTERIAL_HIGHWAY_CLASSES",
    "arterial_entries",
    "hw_stress",
]
