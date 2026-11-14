"""Superficie: classificacao observavel + prioridade ordinal.

Duas coisas sao mantidas separadas de proposito (ver ALGO CICLISTA.md):

* ``Surface`` descreve o que foi observado nas tags do OpenStreetMap;
* ``SURFACE_PRIORITY`` e uma politica de preferencia do roteador.

A prioridade e ordinal (1 melhor que 2), nao uma grandeza fisica. Nenhum
coeficiente de resistencia ao rolamento e derivado dela aqui.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class Surface(str, Enum):
    ASPHALT = "asphalt"
    CONCRETE = "concrete"
    PAVED = "paved"
    COMPACTED_GRAVEL = "compacted_gravel"
    GRAVEL = "gravel"
    DIRT = "dirt"
    SAND = "sand"
    UNKNOWN = "unknown"


class SurfaceStatus(str, Enum):
    """Proveniencia da classificacao de superficie.

    ``UNKNOWN`` significa que o OSM nao informa. ``UNMAPPED`` significa que o
    valor existe mas nao pertence a taxonomia atual — os dois resultam em
    ``Surface.UNKNOWN``, mas nao sao a mesma coisa. ``INFERRED`` fica reservado
    para enriquecimento futuro (imagem, sensor etc.), que deve ser distinguivel
    de dado observado.
    """

    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


# Prioridade de superficie (1 = excelente ... 6 = desconhecida).
# Mesma tabela do documento; pode ser trocada sem alterar o grafo.
SURFACE_PRIORITY: dict[Surface, int] = {
    Surface.ASPHALT: 1,
    Surface.CONCRETE: 1,
    Surface.PAVED: 2,
    Surface.COMPACTED_GRAVEL: 3,
    Surface.GRAVEL: 4,
    Surface.DIRT: 4,
    Surface.SAND: 5,
    Surface.UNKNOWN: 6,
}

# Valores de ``surface=*`` do OSM agrupados nas categorias acima.
OSM_SURFACE_MAP: dict[str, Surface] = {
    "asphalt": Surface.ASPHALT,
    "chipseal": Surface.ASPHALT,
    "concrete": Surface.CONCRETE,
    "concrete:plates": Surface.CONCRETE,
    "concrete:lanes": Surface.CONCRETE,
    "cement": Surface.CONCRETE,
    "paved": Surface.PAVED,
    "paving_stones": Surface.PAVED,
    "paving_stones:30": Surface.PAVED,
    "sett": Surface.PAVED,
    "cobblestone": Surface.PAVED,
    "unhewn_cobblestone": Surface.PAVED,
    "stone": Surface.PAVED,
    "metal": Surface.PAVED,
    "wood": Surface.PAVED,
    "tartan": Surface.PAVED,
    "rubber": Surface.PAVED,
    "compacted": Surface.COMPACTED_GRAVEL,
    "fine_gravel": Surface.COMPACTED_GRAVEL,
    "gravel": Surface.GRAVEL,
    "pebblestone": Surface.GRAVEL,
    "dirt": Surface.DIRT,
    "earth": Surface.DIRT,
    "ground": Surface.DIRT,
    "grass": Surface.DIRT,
    "grass_paver": Surface.DIRT,
    "mud": Surface.DIRT,
    "woodchips": Surface.DIRT,
    "sand": Surface.SAND,
    "unknown": Surface.UNKNOWN,
}

# ``tracktype=grade1..grade5`` refina caminhos sem ``surface`` explicito.
OSM_TRACKTYPE_MAP: dict[str, Surface] = {
    "grade1": Surface.COMPACTED_GRAVEL,
    "grade2": Surface.GRAVEL,
    "grade3": Surface.DIRT,
    "grade4": Surface.DIRT,
    "grade5": Surface.SAND,
}


def surface_observation(tags: Mapping[str, str]) -> tuple[Surface, SurfaceStatus]:
    """Deriva superficie e proveniencia apenas do que esta mapeado no OSM.

    Nao inventa superficie a partir de ``highway=*``. Nao trata ``unknown`` como
    ruim: desconhecido e desconhecido, nao ``avoid``.
    """
    raw = (tags.get("surface") or "").strip().lower()
    if raw:
        mapped = OSM_SURFACE_MAP.get(raw)
        if mapped is not None:
            return mapped, SurfaceStatus.MAPPED
        if raw.startswith("paving_stones") or raw.startswith("sett:"):
            return Surface.PAVED, SurfaceStatus.MAPPED
        if raw.startswith("concrete"):
            return Surface.CONCRETE, SurfaceStatus.MAPPED
        if raw.startswith("asphalt"):
            return Surface.ASPHALT, SurfaceStatus.MAPPED
        if raw.startswith("compacted") or raw.startswith("fine_gravel"):
            return Surface.COMPACTED_GRAVEL, SurfaceStatus.MAPPED
        if raw.startswith("gravel") or raw.startswith("pebble"):
            return Surface.GRAVEL, SurfaceStatus.MAPPED
        if raw.startswith("dirt") or raw.startswith("earth") or raw.startswith("ground"):
            return Surface.DIRT, SurfaceStatus.MAPPED
        # Valor existe, mas nao sabemos classificar: nao confundir com ausencia.
        return Surface.UNKNOWN, SurfaceStatus.UNMAPPED

    tracktype = (tags.get("tracktype") or "").strip().lower()
    if tracktype in OSM_TRACKTYPE_MAP:
        return OSM_TRACKTYPE_MAP[tracktype], SurfaceStatus.MAPPED

    return Surface.UNKNOWN, SurfaceStatus.UNKNOWN


def surface_from_tags(tags: Mapping[str, str]) -> Surface:
    """Atalho somente da superficie observada (ver ``surface_observation``)."""
    return surface_observation(tags)[0]


def surface_priority(surface: Surface) -> int:
    """Prioridade ordinal de uma superficie (1 = melhor).

    A prioridade e uma **politica de preferencia** do roteador, nao uma
    grandeza fisica: 1 e preferivel a 2, mas a distancia entre valores nao tem
    significado. A tabela vem de ``SURFACE_PRIORITY``.

    Args:
        surface: Superficie observada (``Surface``).

    Returns:
        Inteiro de 1 (asfalto/concreto) a 6 (desconhecida).

    Example:
        >>> surface_priority(Surface.ASPHALT)
        1
        >>> surface_priority(Surface.UNKNOWN)
        6
    """
    return SURFACE_PRIORITY[surface]
