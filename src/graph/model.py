"""Modelo do grafo ciclavel: nos, segmentos, links e arcos.

Um **Link** e a ligacao fisica (nao-direcionada) entre dois **Node**. Ele
guarda os **Segment** (trechos homogeneos) que o compoem. Um **Arc** e uma
possibilidade de deslocamento **direcionado** sobre um link: cada link pode
ter um ou dois arcos (um por sentido permitido).

A direcao vive apenas nos arcos; a verdade fisica (distancia, superficie,
perfil de elevacao e restricoes) vive nos segmentos. Assim a contracao de nos
de grau 2 nao perde dados fisicos nem de acesso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Hashable, Iterable, Iterator, Mapping, Sequence

from .restrictions import AccessRule, TurnRestriction
from .surface import SURFACE_PRIORITY, Surface, SurfaceStatus

Coord = tuple[float, float]


def concat_geometry(geometries: Iterable[Sequence[Coord]]) -> tuple[Coord, ...]:
    """Concatena geometrias removendo pontos de juncao duplicados.

    Fonte unica da geometria derivada de ``Link``/``Arc`` (ver ADR-024).
    """
    points: list[Coord] = []
    for geometry in geometries:
        for point in geometry:
            if points and points[-1] == point:
                continue
            points.append(point)
    return tuple(points)


class GraphError(ValueError):
    """Erro de consistencia ao montar o grafo."""


class BicycleAccess(str, Enum):
    """Permissao de circulacao observada no OSM."""

    YES = "yes"
    DISMOUNT = "dismount"


def _dominant(
    pairs: Sequence[tuple[Any, float]] | Iterator[tuple[Any, float]],
) -> Any:
    weights: dict[Any, float] = {}
    order: dict[Any, int] = {}
    for index, (value, length) in enumerate(pairs):
        if value is None:
            continue
        weights[value] = weights.get(value, 0.0) + length
        order.setdefault(value, index)

    if not weights:
        return None
    return max(weights, key=lambda value: (weights[value], -order[value]))


class ElevationQuality(str, Enum):
    """Qualidade explicita de uma amostra de elevacao (ver ADR-024, decisao 3).

    ``MEASURED`` fica reservado a fontes que documentam uma medicao
    instrumental. ``ele=*`` do OSM e uma altitude **declarada pela fonte**
    (``SOURCE_REPORTED``), nao necessariamente medida.
    """

    MEASURED = "measured"
    DEM_SAMPLED = "dem_sampled"
    SOURCE_REPORTED = "source_reported"
    INTERPOLATED = "interpolated"
    ESTIMATED = "estimated"
    MISSING = "missing"


# Estados sustentados por fonte de elevacao (nao por preenchimento posterior).
OBSERVED_ELEVATION_QUALITIES = frozenset(
    {
        ElevationQuality.MEASURED,
        ElevationQuality.DEM_SAMPLED,
        ElevationQuality.SOURCE_REPORTED,
    }
)


@dataclass(frozen=True, slots=True)
class ElevationSample:
    """Altitude a ``distance_m`` metros do inicio do segmento.

    ``source_id`` e ``resolution_m`` preservam a proveniencia; ``quality``
    registra se o valor foi medido, amostrado de um DEM, declarado pela fonte,
    interpolado ou estimado. ``MISSING`` significa elevacao **indisponivel** —
    nunca altitude zero nem terreno plano.

    Quando ``quality`` nao e informada, ela e inferida a partir da presenca da
    altitude: ``MISSING`` se ``elevation_m is None``; caso contrario
    ``DEM_SAMPLED``.
    """

    distance_m: float
    elevation_m: float | None
    source_id: str | None = None
    resolution_m: float | None = None
    quality: ElevationQuality | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.distance_m) or self.distance_m < 0:
            raise GraphError(
                f"amostra de elevacao: distance_m invalida ({self.distance_m!r})"
            )
        if self.resolution_m is not None and (
            not math.isfinite(self.resolution_m) or self.resolution_m <= 0
        ):
            raise GraphError(
                f"amostra de elevacao: resolution_m invalida ({self.resolution_m!r})"
            )
        if self.source_id is not None and not isinstance(self.source_id, str):
            raise GraphError("amostra de elevacao: source_id precisa ser str ou None")

        quality = self.quality
        if quality is None:
            quality = (
                ElevationQuality.MISSING
                if self.elevation_m is None
                else ElevationQuality.DEM_SAMPLED
            )
            object.__setattr__(self, "quality", quality)

        if quality is ElevationQuality.MISSING:
            if self.elevation_m is not None:
                raise GraphError(
                    "amostra MISSING nao pode ter elevation_m (nunca zero)"
                )
        elif self.elevation_m is None or not math.isfinite(self.elevation_m):
            raise GraphError(
                f"amostra {quality.value} exige elevation_m numerica finita"
            )

    @property
    def is_missing(self) -> bool:
        return self.elevation_m is None

    @property
    def is_observed(self) -> bool:
        """Dado sustentado por fonte de elevacao (nao por preenchimento posterior).

        Cobre ``MEASURED``, ``DEM_SAMPLED`` e ``SOURCE_REPORTED``; estimativas e
        interpolacoes nao contam como observadas (ver ADR-024, decisao 3).
        """
        return self.quality in OBSERVED_ELEVATION_QUALITIES


@dataclass(frozen=True, slots=True)
class Segment:
    """Trecho homogeneo de uma via, entre dois pontos de forma do OSM."""

    id: str
    distance_m: float
    surface: Surface = Surface.UNKNOWN
    surface_status: SurfaceStatus = SurfaceStatus.UNKNOWN

    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    elevation_profile: tuple[ElevationSample, ...] = ()

    geometry: tuple[Coord, ...] = ()
    highway: str | None = None
    name: str | None = None
    osm_way_id: int | None = None
    bicycle_access: BicycleAccess = BicycleAccess.YES
    max_speed_kmh: float | None = None
    restrictions: tuple[AccessRule, ...] = ()
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.distance_m <= 0:
            raise GraphError(f"segmento {self.id}: distance_m deve ser positiva")
        if self.elevation_gain_m < 0 or self.elevation_loss_m < 0:
            raise GraphError(f"segmento {self.id}: ganho/perda nao podem ser negativos")

    @property
    def surface_priority(self) -> int:
        return SURFACE_PRIORITY[self.surface]

    @property
    def has_elevation(self) -> bool:
        return sum(1 for sample in self.elevation_profile if sample.elevation_m is not None) >= 2

    @property
    def elevation_source(self) -> str | None:
        return self.elevation_profile[0].source_id if self.elevation_profile else None

    @property
    def elevation_resolution_m(self) -> float | None:
        values = {
            sample.resolution_m
            for sample in self.elevation_profile
            if sample.resolution_m is not None
        }
        return max(values) if values else None

    @property
    def elevation_delta_m(self) -> float | None:
        if not self.has_elevation:
            return None
        start = self.elevation_profile[0].elevation_m
        end = self.elevation_profile[-1].elevation_m
        if start is None or end is None:
            return None
        return end - start

    def reversed_segment(self, segment_id: str | None = None) -> "Segment":
        total = self.distance_m
        profile = tuple(
            ElevationSample(
                distance_m=total - sample.distance_m,
                elevation_m=sample.elevation_m,
                source_id=sample.source_id,
                resolution_m=sample.resolution_m,
                quality=sample.quality,
            )
            for sample in reversed(self.elevation_profile)
        )
        return replace(
            self,
            id=segment_id or f"{self.id}:rev",
            elevation_gain_m=self.elevation_loss_m,
            elevation_loss_m=self.elevation_gain_m,
            elevation_profile=profile,
            geometry=tuple(reversed(self.geometry)),
        )


class _Passage:
    """Agregacoes de um conjunto de segmentos, **independentes de sentido**.

    Classe base comum de :class:`Arc` e :class:`Link`: ambos percorrem os
    mesmos segmentos, mas so o arco carrega direcao e, portanto, ganho/perda de
    elevacao. Nao instancie diretamente.
    """

    __slots__ = ()

    id: str
    segments: tuple[Segment, ...]

    @property
    def distance_m(self) -> float:
        return round(sum(segment.distance_m for segment in self.segments), 3)

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def geometry(self) -> tuple[Coord, ...]:
        """Geometria derivada dos segmentos (o ``Segment`` e a fonte de verdade).

        Pontos de juncao duplicados entre segmentos consecutivos sao removidos.
        Ver ADR-024, decisao 1.
        """
        return concat_geometry(segment.geometry for segment in self.segments)

    @property
    def surface(self) -> Surface:
        """Superficie dominante por comprimento (a verdade fica nos segmentos)."""
        return _dominant((segment.surface, segment.distance_m) for segment in self.segments)

    @property
    def surface_priority(self) -> int:
        return SURFACE_PRIORITY[self.surface]

    @property
    def surface_status(self) -> SurfaceStatus:
        statuses = {segment.surface_status for segment in self.segments}
        if statuses == {SurfaceStatus.MAPPED}:
            return SurfaceStatus.MAPPED
        if SurfaceStatus.INFERRED in statuses:
            return SurfaceStatus.INFERRED
        if statuses == {SurfaceStatus.UNKNOWN}:
            return SurfaceStatus.UNKNOWN
        return SurfaceStatus.UNMAPPED

    @property
    def surfaces(self) -> frozenset[Surface]:
        return frozenset(segment.surface for segment in self.segments)

    @property
    def min_elevation_m(self) -> float | None:
        values = {
            segment.min_elevation_m
            for segment in self.segments
            if segment.min_elevation_m is not None
        }
        return min(values) if values else None

    @property
    def max_elevation_m(self) -> float | None:
        values = {
            segment.max_elevation_m
            for segment in self.segments
            if segment.max_elevation_m is not None
        }
        return max(values) if values else None

    @property
    def has_elevation(self) -> bool:
        return any(segment.has_elevation for segment in self.segments)

    @property
    def elevation_source(self) -> str | None:
        sources = {
            segment.elevation_source
            for segment in self.segments
            if segment.elevation_source
        }
        if not sources:
            return None
        return sources.pop() if len(sources) == 1 else "mixed"

    @property
    def elevation_resolution_m(self) -> float | None:
        values = {
            segment.elevation_resolution_m
            for segment in self.segments
            if segment.elevation_resolution_m is not None
        }
        return max(values) if values else None

    @property
    def highway(self) -> str | None:
        return _dominant(
            (segment.highway, segment.distance_m) for segment in self.segments
        )

    @property
    def name(self) -> str | None:
        return _dominant(
            (segment.name, segment.distance_m) for segment in self.segments
        )

    @property
    def osm_way_id(self) -> int | None:
        values = {segment.osm_way_id for segment in self.segments}
        return values.pop() if len(values) == 1 else None

    @property
    def bicycle_access(self) -> BicycleAccess:
        if any(
            segment.bicycle_access is BicycleAccess.DISMOUNT
            for segment in self.segments
        ):
            return BicycleAccess.DISMOUNT
        return BicycleAccess.YES

    @property
    def max_speed_kmh(self) -> float | None:
        values = [
            segment.max_speed_kmh
            for segment in self.segments
            if segment.max_speed_kmh is not None
        ]
        return min(values) if values else None

    @property
    def restrictions(self) -> tuple[AccessRule, ...]:
        return tuple(
            restriction
            for segment in self.segments
            for restriction in segment.restrictions
        )

    @property
    def tags(self) -> Mapping[str, str]:
        if len(self.segments) == 1:
            return self.segments[0].tags
        return {}


@dataclass(frozen=True, slots=True)
class Arc(_Passage):
    """Possibilidade de deslocamento **direcionado** sobre um :class:`Link`.

    Composto por um ou mais segmentos consecutivos, percorridos de ``source``
    para ``target``. O ganho/perda de elevacao e o perfil sao relativos a esse
    sentido.
    """

    id: str
    source: Hashable
    target: Hashable
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise GraphError(f"arco {self.id}: origem e destino iguais")
        if not self.segments:
            raise GraphError(f"arco {self.id}: sem segmentos")
        if self.distance_m <= 0:
            raise GraphError(f"arco {self.id}: distance_m deve ser positiva")

    # ------------------------------------------------------------ elevacao
    @property
    def elevation_gain_m(self) -> float:
        return round(sum(segment.elevation_gain_m for segment in self.segments), 3)

    @property
    def elevation_loss_m(self) -> float:
        return round(sum(segment.elevation_loss_m for segment in self.segments), 3)

    @property
    def elevation_profile(self) -> tuple[ElevationSample, ...]:
        """Perfil consolidado, com distancias relativas ao inicio do arco."""
        samples: list[ElevationSample] = []
        offset = 0.0
        for segment in self.segments:
            for sample in segment.elevation_profile:
                samples.append(
                    ElevationSample(
                        distance_m=offset + sample.distance_m,
                        elevation_m=sample.elevation_m,
                        source_id=sample.source_id,
                        resolution_m=sample.resolution_m,
                        quality=sample.quality,
                    )
                )
            offset += segment.distance_m
        return tuple(samples)

    @property
    def elevation_delta_m(self) -> float | None:
        profile = self.elevation_profile
        if len(profile) < 2:
            return None
        start = profile[0].elevation_m
        end = profile[-1].elevation_m
        if start is None or end is None:
            return None
        return end - start

    def reversed_arc(self, arc_id: str | None = None) -> "Arc":
        """Arco oposto do mesmo link: ganho/perda e direcao trocados.

        A geometria **nao** e armazenada: ela e derivada dos segmentos, que ja
        sao invertidos aqui (ver ADR-024, decisao 1).
        """
        return replace(
            self,
            id=arc_id or f"{self.id}:rev",
            source=self.target,
            target=self.source,
            segments=tuple(
                segment.reversed_segment(f"{segment.id}:rev")
                for segment in reversed(self.segments)
            ),
        )


@dataclass(frozen=True, slots=True)
class Link(_Passage):
    """Ligacao fisica e **nao-direcionada** entre dois :class:`Node`.

    Existe uma unica vez por par fisico, mesmo em vias de mao dupla. Os
    ``segments`` sao a verdade fisica; ``arc_ids`` lista os arcos (um ou dois,
    um por sentido) que a percorrem.
    """

    id: str
    source: Hashable
    target: Hashable
    segments: tuple[Segment, ...]
    arc_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise GraphError(f"link {self.id}: origem e destino iguais")
        if not self.segments:
            raise GraphError(f"link {self.id}: sem segmentos")
        if self.distance_m <= 0:
            raise GraphError(f"link {self.id}: distance_m deve ser positiva")

    @property
    def is_bidirectional(self) -> bool:
        return len(self.arc_ids) > 1


@dataclass(frozen=True, slots=True)
class Node:
    """Posicao geografica (intersecao ou ponto topologico).

    ``elevation_m`` e opcional (pode nao existir DEM).
    """

    id: Hashable
    lat: float
    lon: float
    elevation_m: float | None = None
    osm_id: int | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def coord(self) -> tuple[float, float]:
        return (self.lat, self.lon)


class RoutingGraph:
    """Grafo direcionado com adjacencia por origem.

    A busca percorre **arcos**; cado arco pertence a um **link** (ligacao
    fisica). Arcos paralelos sao permitidos (ruas duplicadas existem no OSM). A
    identidade de um arco ou link e o seu ``id``.
    """

    def __init__(self) -> None:
        self._nodes: dict[Hashable, Node] = {}
        self._arcs: dict[str, Arc] = {}
        self._outgoing: dict[Hashable, list[str]] = {}
        self._links: dict[str, Link] = {}
        self._arc_to_link: dict[str, str] = {}
        self._turn_restrictions: dict[str, TurnRestriction] = {}
        self._geometry_cache: dict[
            str, tuple[tuple[Segment, ...], tuple[Coord, ...]]
        ] = {}
        self.metadata: dict[str, Any] = {}

    # ------------------------------------------------------------------ nos
    def add_node(self, node: Node) -> None:
        if node.id in self._nodes:
            raise GraphError(f"no duplicado: {node.id}")
        self._nodes[node.id] = node
        self._outgoing.setdefault(node.id, [])

    def get_node(self, node_id: Hashable) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise GraphError(f"no desconhecido: {node_id}") from exc

    def set_node_elevation(self, node_id: Hashable, elevation_m: float | None) -> None:
        node = self.get_node(node_id)
        self._nodes[node_id] = replace(node, elevation_m=elevation_m)

    @property
    def nodes(self) -> Mapping[Hashable, Node]:
        return self._nodes

    def node_ids(self) -> Iterator[Hashable]:
        return iter(self._nodes)

    # ---------------------------------------------------------------- links
    def add_link(self, link: Link) -> None:
        """Registra um link explicito (os arcos precisam existir)."""
        if link.id in self._links:
            raise GraphError(f"link duplicado: {link.id}")
        for arc_id in link.arc_ids:
            if arc_id not in self._arcs:
                raise GraphError(f"link {link.id}: arco desconhecido {arc_id!r}")
            self._arc_to_link[arc_id] = link.id
        self._links[link.id] = link

    def get_link(self, link_id: str) -> Link:
        try:
            return self._links[link_id]
        except KeyError as exc:
            raise GraphError(f"link desconhecido: {link_id}") from exc

    def link_of(self, arc_id: str) -> Link | None:
        """Link fisico ao qual o arco pertence (``None`` se nao registrado)."""
        link_id = self._arc_to_link.get(arc_id)
        return self._links.get(link_id) if link_id is not None else None

    @property
    def links(self) -> Mapping[str, Link]:
        return self._links

    def incident_links(self, node_id: Hashable) -> list[Link]:
        return [
            link
            for link in self._links.values()
            if link.source == node_id or link.target == node_id
        ]

    def rebuild_links(self) -> None:
        """Reconstroi os links a partir dos arcos (pares ``id``/``id:rev``)."""
        self._links.clear()
        self._arc_to_link.clear()
        for arc_id in sorted(self._arcs):
            if arc_id in self._arc_to_link:
                continue
            if arc_id.endswith(":rev"):
                base = arc_id[: -len(":rev")]
                if base in self._arcs:
                    continue  # tratado pelo arco base
            reverse_id = f"{arc_id}:rev"
            arc = self._arcs[arc_id]
            arc_ids = (arc_id, reverse_id) if reverse_id in self._arcs else (arc_id,)
            self.add_link(
                Link(
                    id=arc_id,
                    source=arc.source,
                    target=arc.target,
                    segments=arc.segments,
                    arc_ids=arc_ids,
                )
            )

    # ----------------------------------------------------- restricoes de conversao
    def add_turn_restriction(self, restriction: TurnRestriction) -> None:
        """Registra uma restricao de conversao (nao altera a busca)."""
        if restriction.id in self._turn_restrictions:
            raise GraphError(
                f"restricao de conversao duplicada: {restriction.id}"
            )
        self._turn_restrictions[restriction.id] = restriction

    def get_turn_restriction(self, restriction_id: str) -> TurnRestriction:
        try:
            return self._turn_restrictions[restriction_id]
        except KeyError as exc:
            raise GraphError(
                f"restricao de conversao desconhecida: {restriction_id}"
            ) from exc

    @property
    def turn_restrictions(self) -> Mapping[str, TurnRestriction]:
        return self._turn_restrictions

    def turn_restrictions_at(self, node_id: Hashable) -> list[TurnRestriction]:
        """Restricoes cujo ponto de transicao e ``node_id``."""
        return [
            restriction
            for restriction in self._turn_restrictions.values()
            if restriction.via_node_id == node_id
        ]

    # ----------------------------------------------------------- geometria
    def arc_geometry(self, arc_id: str) -> tuple[Coord, ...]:
        """Geometria derivada do arco, com cache amarrado aos segmentos.

        O ``Segment`` continua sendo a fonte unica da geometria. O cache guarda,
        junto da geometria, a **identidade** da tupla de segmentos que a gerou:
        se o arco for trocado por outro com segmentos diferentes (mesmo por um
        caminho que nao passe por ``_invalidate_geometry``), a identidade muda e
        a geometria e recomputada (ver ADR-024, decisao 1).
        """
        arc = self.get_arc(arc_id)
        segments = arc.segments
        cached = self._geometry_cache.get(arc_id)
        if cached is not None and cached[0] is segments:
            return cached[1]
        geometry = arc.geometry
        self._geometry_cache[arc_id] = (segments, geometry)
        return geometry

    def _invalidate_geometry(self) -> None:
        if self._geometry_cache:
            self._geometry_cache.clear()

    # --------------------------------------------------------------- arcos
    def add_arc(self, arc: Arc, *, link: bool = True) -> None:
        """Adiciona um arco. Por padrao cria tambem o link fisico de um arco."""
        self._insert_arc(arc)
        if link and arc.id not in self._arc_to_link:
            self.add_link(
                Link(
                    id=arc.id,
                    source=arc.source,
                    target=arc.target,
                    segments=arc.segments,
                    arc_ids=(arc.id,),
                )
            )

    def _insert_arc(self, arc: Arc) -> None:
        if arc.source not in self._nodes:
            raise GraphError(f"arco {arc.id}: origem desconhecida {arc.source!r}")
        if arc.target not in self._nodes:
            raise GraphError(f"arco {arc.id}: destino desconhecido {arc.target!r}")
        if arc.id in self._arcs:
            raise GraphError(f"arco duplicado: {arc.id}")
        self._arcs[arc.id] = arc
        self._outgoing[arc.source].append(arc.id)
        self._invalidate_geometry()

    def add_bidirectional(self, arc: Arc, reverse_id: str | None = None) -> tuple[Arc, Arc]:
        """Adiciona o arco, a sua inversa e o link fisico que agrupa os dois."""
        self._insert_arc(arc)
        reverse = arc.reversed_arc(reverse_id)
        self._insert_arc(reverse)
        self.add_link(
            Link(
                id=arc.id,
                source=arc.source,
                target=arc.target,
                segments=arc.segments,
                arc_ids=(arc.id, reverse.id),
            )
        )
        return arc, reverse

    def replace_arc(self, arc: Arc) -> None:
        """Substitui um arco existente (usado ao anexar elevacao)."""
        if arc.id not in self._arcs:
            raise GraphError(f"arco inexistente: {arc.id}")
        self._arcs[arc.id] = arc
        link_id = self._arc_to_link.get(arc.id)
        if link_id is not None and link_id == arc.id:
            # Arco canonico do link: propaga os segmentos fisicos atualizados.
            self._links[link_id] = replace(self._links[link_id], segments=arc.segments)
        self._invalidate_geometry()

    def get_arc(self, arc_id: str) -> Arc:
        try:
            return self._arcs[arc_id]
        except KeyError as exc:
            raise GraphError(f"arco desconhecido: {arc_id}") from exc

    @property
    def arcs(self) -> Mapping[str, Arc]:
        return self._arcs

    def out_arcs(self, node_id: Hashable) -> list[Arc]:
        return [self._arcs[arc_id] for arc_id in self._outgoing.get(node_id, ())]

    def neighbors(self, node_id: Hashable) -> list[Node]:
        return [self._nodes[arc.target] for arc in self.out_arcs(node_id)]

    def in_arcs(self, node_id: Hashable) -> list[Arc]:
        return [arc for arc in self._arcs.values() if arc.target == node_id]

    def copy(self) -> "RoutingGraph":
        """Copia estrutural rasa (nos, arcos e links sao imutaveis)."""
        clone = RoutingGraph()
        clone._nodes = dict(self._nodes)
        clone._arcs = dict(self._arcs)
        clone._outgoing = {
            node_id: list(arc_ids) for node_id, arc_ids in self._outgoing.items()
        }
        clone._links = dict(self._links)
        clone._arc_to_link = dict(self._arc_to_link)
        clone._turn_restrictions = dict(self._turn_restrictions)
        clone.metadata = dict(self.metadata)
        return clone

    def remove_arc(self, arc_id: str) -> Arc:
        try:
            arc = self._arcs.pop(arc_id)
        except KeyError as exc:
            raise GraphError(f"arco inexistente: {arc_id}") from exc
        self._outgoing[arc.source].remove(arc_id)
        self._invalidate_geometry()

        link_id = self._arc_to_link.pop(arc_id, None)
        if link_id is not None:
            link = self._links.get(link_id)
            if link is not None:
                remaining = tuple(item for item in link.arc_ids if item != arc_id)
                if remaining:
                    self._links[link_id] = replace(link, arc_ids=remaining)
                else:
                    del self._links[link_id]
        return arc

    @property
    def arc_count(self) -> int:
        return len(self._arcs)

    @property
    def link_count(self) -> int:
        return len(self._links)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def __len__(self) -> int:
        return len(self._arcs)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes
