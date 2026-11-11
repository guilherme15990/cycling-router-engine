"""Construcao do grafo ciclavel a partir de dados do OpenStreetMap.

Pipeline desta camada:

1. selecionar ``way`` s ciclaveis (``highway``, ``bicycle``, ``access``);
2. cortar as vias nos cruzamentos, mantendo os pontos de forma no interior
   dos arcos (a geometria completa e preservada);
3. derivar sentido de circulacao (``oneway``, ``oneway:bicycle``, rotatoria);
4. calcular comprimento real com haversine;
5. derivar superficie das tags (``surface``, ``tracktype``);
6. opcionalmente anexar elevacao por perfil amostrado ao longo do arco.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from geo import Coord, bbox_of, line_length_m
from graph.model import BicycleAccess, RoutingGraph, Arc, Segment, Node
from graph.restrictions import parse_conditional_restrictions
from graph.surface import Surface, surface_from_tags, surface_observation
from .parse import OverpassData, RawNode, RawWay

DEFAULT_ALLOWED_HIGHWAYS: frozenset[str] = frozenset(
    {
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
        "track",
        "path",
        "cycleway",
        "footway",
        "pedestrian",
        "bridleway",
        "cycleway_link",
    }
)

DEFAULT_EXCLUDED_HIGHWAYS: frozenset[str] = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "steps",
        "construction",
        "proposed",
        "raceway",
        "bus_guideway",
        "corridor",
    }
)

FOOTWAY_HIGHWAYS = frozenset({"footway", "pedestrian", "bridleway"})
ALLOWED_BICYCLE_VALUES = frozenset({"yes", "designated", "permissive", "official"})


@dataclass(frozen=True)
class BuildOptions:
    """Opcoes de construcao do grafo ciclavel a partir das ``way`` s do OSM.

    Attributes:
        allowed_highways: Valores de ``highway=*`` aceitos como ciclaveis.
        excluded_highways: Valores de ``highway=*`` sempre descartados.
        allow_footway: Incluir ``footway``/``pedestrian``/``bridleway`` mesmo
            sem ``bicycle`` explicito.
        allow_dismount: Tratar ``bicycle=dismount`` como trecho a pe, em vez de
            descartar.
        split_at_junctions: Cortar as vias nos cruzamentos (recomendado; sem
            isso cada ``way`` vira um arco unico).
        min_arc_length_m: Descarta segmentos mais curtos que este valor.
    """

    allowed_highways: frozenset[str] = DEFAULT_ALLOWED_HIGHWAYS
    excluded_highways: frozenset[str] = DEFAULT_EXCLUDED_HIGHWAYS
    allow_footway: bool = False
    allow_dismount: bool = True
    split_at_junctions: bool = True
    min_arc_length_m: float = 0.5


@dataclass(frozen=True)
class BuildStats:
    """Contadores do processo de construcao (auditoria do que foi descartado).

    Attributes:
        ways_total: ``way`` s lidas do payload.
        ways_selected: ``way`` s aceitas como ciclaveis.
        segments: Segmentos adicionados ao grafo.
        skipped_missing_nodes: Trechos descartados por nos ausentes no payload.
        skipped_short: Trechos descartados por comprimento abaixo do minimo.
        skipped_loops: Trechos descartados por comecar e terminar no mesmo no.
    """

    ways_total: int
    ways_selected: int
    segments: int
    skipped_missing_nodes: int
    skipped_short: int
    skipped_loops: int


class _BuildReport:
    def __init__(self) -> None:
        self.ways_total = 0
        self.ways_selected = 0
        self.segments = 0
        self.skipped_missing_nodes = 0
        self.skipped_short = 0
        self.skipped_loops = 0

    def as_stats(self) -> BuildStats:
        return BuildStats(
            ways_total=self.ways_total,
            ways_selected=self.ways_selected,
            segments=self.segments,
            skipped_missing_nodes=self.skipped_missing_nodes,
            skipped_short=self.skipped_short,
            skipped_loops=self.skipped_loops,
        )


# --------------------------------------------------------------------------
# Tags OSM -> atributos do grafo
# --------------------------------------------------------------------------
def bicycle_access(tags: dict[str, str], options: BuildOptions) -> BicycleAccess | None:
    """Retorna a permissao ciclavel, ou ``None`` se o trecho deve ser ignorado."""
    highway = (tags.get("highway") or "").strip().lower()
    if not highway or highway in options.excluded_highways:
        return None
    if highway not in options.allowed_highways:
        return None

    bicycle = (tags.get("bicycle") or "").strip().lower()
    access = (tags.get("access") or "").strip().lower()

    if bicycle in {"no", "private", "use_sidepath"}:
        return None

    if (
        access in {"no", "private"}
        and bicycle not in ALLOWED_BICYCLE_VALUES
        and bicycle != "dismount"
    ):
        return None

    if highway in FOOTWAY_HIGHWAYS:
        if not options.allow_footway and bicycle not in ALLOWED_BICYCLE_VALUES:
            return None

    if bicycle == "dismount":
        return BicycleAccess.DISMOUNT if options.allow_dismount else None

    return BicycleAccess.YES


def allowed_directions(tags: dict[str, str]) -> tuple[bool, bool]:
    """``(ida, volta)`` considerando ``oneway`` e excecoes para bicicletas."""
    oneway = (tags.get("oneway") or "").strip().lower()
    bike_oneway = (tags.get("oneway:bicycle") or "").strip().lower()
    junction = (tags.get("junction") or "").strip().lower()

    forward, backward = True, True

    if oneway in {"yes", "true", "1"}:
        forward, backward = True, False
    elif oneway in {"-1", "reverse"}:
        forward, backward = False, True

    if junction in {"roundabout", "circular"} and oneway not in {"no", "false", "0"}:
        forward, backward = True, False

    if bike_oneway in {"no", "false", "0"}:
        forward, backward = True, True
    elif bike_oneway in {"yes", "true", "1"}:
        forward, backward = True, False
    elif bike_oneway == "-1":
        forward, backward = False, True

    for key in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both"):
        if "opposite" in (tags.get(key) or "").lower():
            forward, backward = True, True

    return forward, backward


def parse_max_speed_kmh(raw: str | None) -> float | None:
    if not raw:
        return None
    text = raw.strip().lower()
    if text in {"none", "signals", "walk", "variable"}:
        return None
    multiplier = 1.0
    if text.endswith("mph"):
        text = text[: -len("mph")].strip()
        multiplier = 1.609344
    elif text.endswith("km/h"):
        text = text[: -len("km/h")].strip()
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    value *= multiplier
    return value if value > 0 else None


def parse_elevation(tags: dict[str, str]) -> float | None:
    raw = tags.get("ele")
    if raw is None:
        return None
    try:
        return float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Split das vias
# --------------------------------------------------------------------------
def _dedupe_consecutive(sequence: Iterable[int]) -> tuple[int, ...]:
    result: list[int] = []
    for value in sequence:
        if not result or result[-1] != value:
            result.append(value)
    return tuple(result)


def _junction_nodes(ways: Sequence[RawWay]) -> set[int]:
    usage: Counter[int] = Counter()
    for way in ways:
        usage.update(set(way.nodes))

    splits: set[int] = set()
    for way in ways:
        if not way.nodes:
            continue
        splits.add(way.nodes[0])
        splits.add(way.nodes[-1])
    splits.update(node_id for node_id, count in usage.items() if count > 1)
    return splits


def _split_way(way: RawWay, splits: set[int]) -> list[tuple[int, ...]]:
    nodes = _dedupe_consecutive(way.nodes)
    if len(nodes) < 2:
        return []
    if not splits:
        return [nodes]

    chains: list[tuple[int, ...]] = []
    current: list[int] = [nodes[0]]
    for node_id in nodes[1:]:
        current.append(node_id)
        if node_id in splits:
            chains.append(tuple(current))
            current = [node_id]
    return chains


# --------------------------------------------------------------------------
# Montagem
# --------------------------------------------------------------------------
def build_graph(
    data: OverpassData,
    options: BuildOptions | None = None,
) -> tuple[RoutingGraph, BuildStats]:
    """Monta o grafo topologico (sem elevacao amostrada).

    Seleciona as ``way`` s ciclaveis, corta-as nos cruzamentos, deriva sentido
    de circulacao, superficie e restricoes, e calcula o comprimento real por
    haversine. A elevacao fica para :func:`attach_elevation`.

    Args:
        data: Nos e vias lidos do Overpass (``OverpassData``).
        options: Opcoes de selecao e corte. Usa ``BuildOptions()`` se omitido.

    Returns:
        Tupla ``(graph, stats)`` com o ``RoutingGraph`` montado e os
        contadores de :class:`BuildStats`.

    Example:
        >>> from osm.overpass import OverpassClient
        >>> from osm.build import build_graph
        >>> data = OverpassClient(cache_dir=".cache").fetch((-23.56, -46.65, -23.54, -46.62))  # doctest: +SKIP
        >>> graph, stats = build_graph(data)  # doctest: +SKIP
        >>> graph.node_count, graph.arc_count  # doctest: +SKIP
        (99, 177)
    """
    options = options or BuildOptions()
    report = _BuildReport()
    report.ways_total = len(data.ways)

    selected: list[RawWay] = []
    access_by_way: dict[int, BicycleAccess] = {}
    for way in data.ways:
        access = bicycle_access(way.tags, options)
        if access is None:
            continue
        selected.append(way)
        access_by_way[way.id] = access
    report.ways_selected = len(selected)

    splits = _junction_nodes(selected) if options.split_at_junctions else set()
    if not options.split_at_junctions:
        for way in selected:
            if way.nodes:
                splits.add(way.nodes[0])
                splits.add(way.nodes[-1])

    graph = RoutingGraph()
    segments: list[tuple[RawWay, tuple[int, ...], tuple[Coord, ...], float]] = []

    for way in selected:
        for chain in _split_way(way, splits):
            if len(chain) < 2:
                continue
            if any(node_id not in data.nodes for node_id in chain):
                report.skipped_missing_nodes += 1
                continue
            coords = tuple(
                (data.nodes[node_id].lat, data.nodes[node_id].lon) for node_id in chain
            )
            length = line_length_m(coords)
            if length < options.min_arc_length_m:
                report.skipped_short += 1
                continue
            segments.append((way, chain, coords, length))

    endpoints: set[int] = set()
    for _, chain, _, _ in segments:
        endpoints.add(chain[0])
        endpoints.add(chain[-1])

    for node_id in sorted(endpoints):
        source: RawNode = data.nodes[node_id]
        graph.add_node(
            Node(
                id=node_id,
                lat=source.lat,
                lon=source.lon,
                elevation_m=parse_elevation(source.tags),
                osm_id=node_id,
                tags=dict(source.tags),
            )
        )

    counter: Counter[int] = Counter()
    for way, chain, coords, length in segments:
        counter[way.id] += 1
        arc_id = f"w{way.id}:{counter[way.id] - 1}"
        if chain[0] == chain[-1]:
            report.skipped_loops += 1
            continue

        tags = dict(way.tags)
        surface, surface_status = surface_observation(tags)
        segment = Segment(
            id=arc_id,
            distance_m=round(length, 3),
            surface=surface,
            surface_status=surface_status,
            highway=(tags.get("highway") or None),
            name=tags.get("name") or None,
            osm_way_id=way.id,
            bicycle_access=access_by_way[way.id],
            max_speed_kmh=parse_max_speed_kmh(tags.get("maxspeed")),
            restrictions=parse_conditional_restrictions(tags),
            geometry=coords,
            tags=tags,
        )
        arc = Arc(
            id=arc_id,
            source=chain[0],
            target=chain[-1],
            segments=(segment,),
        )

        forward, backward = allowed_directions(tags)
        if forward and backward:
            graph.add_bidirectional(arc, reverse_id=f"{arc_id}:rev")
        elif forward:
            graph.add_arc(arc)
        elif backward:
            graph.add_arc(arc.reversed_arc(arc_id))
        else:  # pragma: no cover - defensivo
            continue
        report.segments += 1

    if segments:
        all_coords: list[Coord] = []
        for _, _, coords, _ in segments:
            all_coords.extend(coords)
        graph.metadata.update(
            {
                "source": "openstreetmap",
                "bbox": bbox_of(all_coords),
                "ways_used": len(selected),
            }
        )

    return graph, report.as_stats()


def attach_elevation(
    graph: RoutingGraph,
    provider: object,
    spacing_m: float = 25.0,
    smoothing: int = 3,
) -> None:
    """Calcula perfil, ganho e perda de elevacao para todas os arcos.

    Modifica o grafo **no lugar**: atualiza a elevacao dos nos e substitui cada
    segmento por uma copia enriquecida com o perfil amostrado.

    Args:
        graph: Grafo a enriquecer (modificado no lugar).
        provider: Qualquer objeto com ``elevations(coords)`` (ver
            :mod:`elevation`), como ``OpenTopoDataElevation``.
        spacing_m: Espacamento da amostragem ao longo da geometria, em metros.
        smoothing: Janela da media movel que reduz ruido do DEM (``1`` desliga).

    Raises:
        TypeError: Se ``provider`` nao expoe ``elevations(coords)``.

    Example:
        >>> from elevation import CallableElevation, MemoElevation
        >>> provider = CallableElevation(lambda lat, lon: 760.0 + lat * 100.0)
        >>> attach_elevation(graph, provider, spacing_m=10.0)  # doctest: +SKIP
    """
    from elevation import MemoElevation, build_elevation_profile

    if not hasattr(provider, "elevations"):
        raise TypeError("provider de elevacao precisa implementar elevations(coords)")

    memo = MemoElevation(provider)  # type: ignore[arg-type]

    nodes = list(graph.nodes.values())
    if nodes:
        elevations = memo.elevations([node.coord for node in nodes])
        for node, elevation in zip(nodes, elevations):
            graph.set_node_elevation(node.id, elevation)

    for arc in list(graph.arcs.values()):
        segments: list[Segment] = []
        for segment in arc.segments:
            if len(segment.geometry) < 2:
                segments.append(segment)
                continue
            stats = build_elevation_profile(
                segment.geometry, memo, spacing_m=spacing_m, smoothing=smoothing
            )
            segments.append(
                replace(
                    segment,
                    elevation_gain_m=stats.gain_m,
                    elevation_loss_m=stats.loss_m,
                    min_elevation_m=stats.min_elevation_m,
                    max_elevation_m=stats.max_elevation_m,
                    elevation_profile=stats.profile,
                )
            )
        graph.replace_arc(replace(arc, segments=tuple(segments)))


def surface_of(tags: dict[str, str]) -> Surface:
    """Atalho para inspecao de tags."""
    return surface_from_tags(tags)
