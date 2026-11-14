"""Serializacao (JSON/GeoJSON) e resumo estatistico do grafo.

O formato JSON e versionado (``FORMAT``/``VERSION``). ``graph_from_dict``
recusa payloads de formato ou versao desconhecidos, de modo que uma mudanca
incompativel exige subir ``VERSION``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .model import (
    BicycleAccess,
    RoutingGraph,
    Arc,
    Link,
    Segment,
    ElevationQuality,
    ElevationSample,
    Node,
)
from .restrictions import AccessRule, RestrictionSource, TurnRestriction
from .surface import SURFACE_PRIORITY, Surface, SurfaceStatus

FORMAT = "roteamento.cycling-graph"
VERSION = 4


def graph_to_dict(graph: RoutingGraph) -> dict[str, Any]:
    """Converte o grafo em um dicionario pronto para JSON.

    Nodes, arcos e links saem ordenados por ``id`` (saida deterministica).

    Args:
        graph: Grafo a serializar.

    Returns:
        Dicionario com ``format``, ``version``, ``metadata``, ``nodes``,
        ``arcs`` e ``links``.
    """
    nodes = [
        {
            "id": node.id,
            "lat": node.lat,
            "lon": node.lon,
            "elevation_m": node.elevation_m,
            "osm_id": node.osm_id,
            "tags": dict(node.tags),
        }
        for node in sorted(graph.nodes.values(), key=lambda n: str(n.id))
    ]

    arcs = []
    for arc in sorted(graph.arcs.values(), key=lambda e: e.id):
        arcs.append(
            {
                "id": arc.id,
                "source": arc.source,
                "target": arc.target,
                "segments": [_segment_to_dict(segment) for segment in arc.segments],
            }
        )

    links = [
        {
            "id": link.id,
            "source": link.source,
            "target": link.target,
            "arcs": list(link.arc_ids),
        }
        for link in sorted(graph.links.values(), key=lambda item: item.id)
    ]

    turn_restrictions = [
        _turn_restriction_to_dict(restriction)
        for restriction in sorted(
            graph.turn_restrictions.values(), key=lambda item: item.id
        )
    ]

    return {
        "format": FORMAT,
        "version": VERSION,
        "metadata": _jsonable(dict(graph.metadata)),
        "nodes": nodes,
        "arcs": arcs,
        "links": links,
        "turn_restrictions": turn_restrictions,
    }


def graph_from_dict(payload: dict[str, Any]) -> RoutingGraph:
    """Reconstroi o grafo a partir do dicionario serializado.

    Args:
        payload: Dicionario no formato produzido por :func:`graph_to_dict`.

    Returns:
        O ``RoutingGraph`` reconstruido.

    Raises:
        ValueError: Se ``format`` ou ``version`` forem desconhecidos.
    """
    if payload.get("format") != FORMAT:
        raise ValueError(f"formato inesperado: {payload.get('format')!r}")
    version = payload.get("version")
    if version != VERSION:
        raise ValueError(f"versao nao suportada: {version!r} (esperado {VERSION})")

    graph = RoutingGraph()
    graph.metadata = dict(payload.get("metadata") or {})

    for raw in payload.get("nodes", []):
        graph.add_node(
            Node(
                id=raw["id"],
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                elevation_m=raw.get("elevation_m"),
                osm_id=raw.get("osm_id"),
                tags=dict(raw.get("tags") or {}),
            )
        )

    for raw in payload.get("arcs", []):
        segments = tuple(_segment_from_dict(item) for item in raw.get("segments") or ())
        graph.add_arc(
            Arc(
                id=str(raw["id"]),
                source=raw["source"],
                target=raw["target"],
                segments=segments,
            ),
            link=False,
        )

    raw_links = payload.get("links")
    if raw_links is None:
        graph.rebuild_links()
    else:
        for raw in raw_links:
            arc_ids = tuple(str(arc_id) for arc_id in raw.get("arcs") or ())
            graph.add_link(
                Link(
                    id=str(raw["id"]),
                    source=raw["source"],
                    target=raw["target"],
                    segments=graph.get_arc(arc_ids[0]).segments,
                    arc_ids=arc_ids,
                )
            )

    for raw in payload.get("turn_restrictions") or ():
        graph.add_turn_restriction(_turn_restriction_from_dict(raw))

    return graph


def save_json(graph: RoutingGraph, path: str | Path) -> Path:
    """Grava o grafo em JSON (UTF-8, identado) e devolve o caminho final.

    Cria os diretorios pais se necessario.

    Args:
        graph: Grafo a gravar.
        path: Destino do arquivo ``.json``.

    Returns:
        O ``Path`` gravado.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(graph_to_dict(graph), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def load_json(path: str | Path) -> RoutingGraph:
    """Le um grafo de um arquivo JSON produzido por :func:`save_json`.

    Args:
        path: Caminho do arquivo JSON.

    Returns:
        O ``RoutingGraph`` carregado.

    Raises:
        ValueError: Se o formato ou a versao nao forem suportados.
    """
    return graph_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def to_geojson(graph: RoutingGraph) -> dict[str, Any]:
    """Converte o grafo em uma ``FeatureCollection`` do GeoJSON.

    Gera uma ``Point`` por no e uma ``LineString`` por arco, com atributos
    (distancia, superficie, elevacao, acesso) em ``properties``.

    Args:
        graph: Grafo a converter.

    Returns:
        Dicionario GeoJSON (``FeatureCollection``).
    """
    features: list[dict[str, Any]] = []

    for node in graph.nodes.values():
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [node.lon, node.lat]},
                "properties": {
                    "kind": "node",
                    "id": node.id,
                    "elevation_m": node.elevation_m,
                },
            }
        )

    for arc in graph.arcs.values():
        geometry = arc.geometry or (
            (graph.get_node(arc.source).lat, graph.get_node(arc.source).lon),
            (graph.get_node(arc.target).lat, graph.get_node(arc.target).lon),
        )
        link = graph.link_of(arc.id)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in geometry],
                },
                "properties": {
                    "kind": "arc",
                    "id": arc.id,
                    "link_id": link.id if link is not None else None,
                    "source": arc.source,
                    "target": arc.target,
                    "distance_m": arc.distance_m,
                    "surface": arc.surface.value,
                    "surface_status": arc.surface_status.value,
                    "surface_priority": arc.surface_priority,
                    "elevation_gain_m": arc.elevation_gain_m,
                    "elevation_loss_m": arc.elevation_loss_m,
                    "elevation_source": arc.elevation_source,
                    "segment_count": arc.segment_count,
                    "highway": arc.highway,
                    "name": arc.name,
                    "osm_way_id": arc.osm_way_id,
                    "bicycle_access": arc.bicycle_access.value,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def save_geojson(graph: RoutingGraph, path: str | Path) -> Path:
    """Grava o grafo em GeoJSON (UTF-8) e devolve o caminho final.

    Args:
        graph: Grafo a gravar.
        path: Destino do arquivo ``.geojson``.

    Returns:
        O ``Path`` gravado.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(to_geojson(graph), ensure_ascii=False),
        encoding="utf-8",
    )
    return destination


def summarize(graph: RoutingGraph) -> dict[str, Any]:
    """Resume o grafo: totais, cobertura de elevacao e distribuicao por superficie.

    Args:
        graph: Grafo a resumir.

    Returns:
        Dicionario com contagens (nos/arcos/segmentos), comprimento e
        elevacao totais, cobertura de elevacao e distribuicoes por superficie,
        proveniencia e prioridade.
    """
    length_by_surface: dict[str, float] = defaultdict(float)
    arcs_by_surface: Counter[str] = Counter()
    length_by_priority: dict[int, float] = defaultdict(float)
    length_by_status: dict[str, float] = defaultdict(float)

    total_length = 0.0
    total_gain = 0.0
    total_loss = 0.0
    with_elevation = 0
    dismount_length = 0.0
    segments_total = 0
    elevation_sources: Counter[str] = Counter()

    for arc in graph.arcs.values():
        total_length += arc.distance_m
        total_gain += arc.elevation_gain_m
        total_loss += arc.elevation_loss_m
        if arc.has_elevation:
            with_elevation += 1
        if arc.bicycle_access is BicycleAccess.DISMOUNT:
            dismount_length += arc.distance_m
        if arc.elevation_source:
            elevation_sources[arc.elevation_source] += 1

        for segment in arc.segments:
            segments_total += 1
            length_by_surface[segment.surface.value] += segment.distance_m
            length_by_status[segment.surface_status.value] += segment.distance_m
            length_by_priority[segment.surface_priority] += segment.distance_m
            arcs_by_surface[segment.surface.value] += 1

    return {
        "nodes": graph.node_count,
        "arcs": graph.arc_count,
        "links": graph.link_count,
        "turn_restrictions": len(graph.turn_restrictions),
        "segments": segments_total,
        "total_length_m": round(total_length, 1),
        "total_length_km": round(total_length / 1000.0, 3),
        "total_ascent_m": round(total_gain, 1),
        "total_descent_m": round(total_loss, 1),
        "arcs_with_elevation": with_elevation,
        "elevation_coverage": round(with_elevation / graph.arc_count, 3)
        if graph.arc_count
        else 0.0,
        "elevation_sources": dict(sorted(elevation_sources.items())),
        "dismount_length_m": round(dismount_length, 1),
        "length_by_surface_m": {
            key: round(value, 1) for key, value in sorted(length_by_surface.items())
        },
        "length_by_surface_status_m": {
            key: round(value, 1) for key, value in sorted(length_by_status.items())
        },
        "arcs_by_surface": dict(sorted(arcs_by_surface.items())),
        "length_by_priority_m": {
            str(key): round(value, 1) for key, value in sorted(length_by_priority.items())
        },
        "surface_priorities": {
            surface.value: SURFACE_PRIORITY[surface] for surface in Surface
        },
        "simplification": graph.metadata.get("simplification"),
    }


# --------------------------------------------------------------------------
def _segment_to_dict(segment: Segment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "distance_m": segment.distance_m,
        "surface": segment.surface.value,
        "surface_status": segment.surface_status.value,
        "surface_priority": segment.surface_priority,
        "elevation_gain_m": segment.elevation_gain_m,
        "elevation_loss_m": segment.elevation_loss_m,
        "min_elevation_m": segment.min_elevation_m,
        "max_elevation_m": segment.max_elevation_m,
        "elevation_source": segment.elevation_source,
        "elevation_resolution_m": segment.elevation_resolution_m,
        "elevation_profile": [
            [
                sample.distance_m,
                sample.elevation_m,
                sample.source_id,
                sample.resolution_m,
                sample.quality.value if sample.quality is not None else None,
            ]
            for sample in segment.elevation_profile
        ],
        "highway": segment.highway,
        "name": segment.name,
        "osm_way_id": segment.osm_way_id,
        "bicycle_access": segment.bicycle_access.value,
        "max_speed_kmh": segment.max_speed_kmh,
        "restrictions": [
            {
                "scope": restriction.scope,
                "value": restriction.value,
                "condition": restriction.condition,
                "raw": restriction.raw,
            }
            for restriction in segment.restrictions
        ],
        "geometry": [[lat, lon] for lat, lon in segment.geometry],
        "tags": dict(segment.tags),
    }


def _segment_from_dict(raw: dict[str, Any]) -> Segment:
    profile = tuple(
        _sample_from_dict(item) for item in raw.get("elevation_profile") or ()
    )
    return Segment(
        id=str(raw["id"]),
        distance_m=float(raw["distance_m"]),
        surface=Surface(raw.get("surface", "unknown")),
        surface_status=SurfaceStatus(raw.get("surface_status", "unknown")),
        elevation_gain_m=float(raw.get("elevation_gain_m") or 0.0),
        elevation_loss_m=float(raw.get("elevation_loss_m") or 0.0),
        min_elevation_m=raw.get("min_elevation_m"),
        max_elevation_m=raw.get("max_elevation_m"),
        elevation_profile=profile,
        highway=raw.get("highway"),
        name=raw.get("name"),
        osm_way_id=raw.get("osm_way_id"),
        bicycle_access=BicycleAccess(raw.get("bicycle_access", "yes")),
        max_speed_kmh=raw.get("max_speed_kmh"),
        restrictions=tuple(
            AccessRule(
                scope=item["scope"],
                value=item["value"],
                condition=item.get("condition"),
                raw=item.get("raw", ""),
            )
            for item in raw.get("restrictions") or ()
        ),
        geometry=tuple(
            (float(lat), float(lon)) for lat, lon in raw.get("geometry") or ()
        ),
        tags=dict(raw.get("tags") or {}),
    )


def _sample_from_dict(item: Any) -> ElevationSample:
    """Le uma amostra ``[dist, ele, source_id, res, quality]`` (formato v4)."""
    distance = float(item[0])
    elevation = None if len(item) < 2 or item[1] is None else float(item[1])
    source_id = item[2] if len(item) > 2 and item[2] is not None else None
    resolution = float(item[3]) if len(item) > 3 and item[3] is not None else None
    quality_value = item[4] if len(item) > 4 else None
    quality = ElevationQuality(str(quality_value)) if quality_value is not None else None
    return ElevationSample(
        distance_m=distance,
        elevation_m=elevation,
        source_id=source_id,
        resolution_m=resolution,
        quality=quality,
    )


def _turn_restriction_to_dict(restriction: TurnRestriction) -> dict[str, Any]:
    source = restriction.source
    return {
        "id": restriction.id,
        "from_way_id": restriction.from_way_id,
        "to_way_id": restriction.to_way_id,
        "restriction_type": restriction.restriction_type,
        "via_node_id": restriction.via_node_id,
        "via_way_ids": list(restriction.via_way_ids),
        "condition": restriction.condition,
        "source": {
            "relation_id": source.relation_id if source else None,
            "version": source.version if source else None,
            "tags": dict(source.tags) if source else {},
        },
    }


def _turn_restriction_from_dict(raw: dict[str, Any]) -> TurnRestriction:
    source_raw = dict(raw.get("source") or {})
    return TurnRestriction(
        id=str(raw["id"]),
        from_way_id=int(raw["from_way_id"]),
        to_way_id=int(raw["to_way_id"]),
        restriction_type=str(raw["restriction_type"]),
        via_node_id=raw.get("via_node_id"),
        via_way_ids=tuple(int(value) for value in raw.get("via_way_ids") or ()),
        condition=raw.get("condition"),
        source=RestrictionSource(
            relation_id=source_raw.get("relation_id"),
            version=source_raw.get("version"),
            tags={str(key): str(value) for key, value in dict(source_raw.get("tags") or {}).items()},
        )
        if source_raw
        else None,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value
