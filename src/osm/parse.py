"""Estruturas OSM simples e leitura do **JSON OSM** (offline).

Este modulo define os conteineres neutros da ingestao OSM (``RawNode``,
``RawWay``, ``RawRelation``), o agregado :class:`OsmData` e o parser
``OsmData.from_payload`` para o **formato JSON OSM** (``{"elements": [...]}``).

Nada aqui acessa a rede: a fonte de producao do motor e o leitor local de
``.osm.pbf`` (:mod:`osm.pbf`). O ``from_payload`` permanece como parser do
dialeto JSON do OSM (util para testes e para snapshots em disco), **nao** como
ingestao online.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Valores de ``highway=*`` lidos por padrao na ingestao (rede ciclavel urbana).
DEFAULT_HIGHWAYS: tuple[str, ...] = (
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
)


@dataclass(frozen=True, slots=True)
class RawNode:
    id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawWay:
    id: int
    nodes: tuple[int, ...]
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawRelation:
    """Relacao OSM preservada sem interpretacao (ex.: ``type=restriction``).

    ``members`` guarda os membros brutos (cada um um dicionario com ``type``,
    ``ref`` e ``role``), tal como ``turn_restriction_from_relation`` espera.
    """

    id: int
    members: tuple[dict[str, object], ...] = ()
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OsmData:
    nodes: dict[int, RawNode]
    ways: list[RawWay]
    relations: list[RawRelation] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OsmData":
        nodes: dict[int, RawNode] = {}
        ways: list[RawWay] = []
        relations: list[RawRelation] = []

        for element in payload.get("elements", []):
            kind = element.get("type")
            if kind == "node":
                lat = element.get("lat")
                lon = element.get("lon")
                if lat is None or lon is None:
                    continue
                nodes[int(element["id"])] = RawNode(
                    id=int(element["id"]),
                    lat=float(lat),
                    lon=float(lon),
                    tags=dict(element.get("tags") or {}),
                )
            elif kind == "way":
                refs = tuple(int(ref) for ref in element.get("nodes") or ())
                if len(refs) < 2:
                    continue
                ways.append(
                    RawWay(
                        id=int(element["id"]),
                        nodes=refs,
                        tags=dict(element.get("tags") or {}),
                    )
                )
            elif kind == "relation":
                members = tuple(
                    dict(member) for member in element.get("members") or ()
                )
                relations.append(
                    RawRelation(
                        id=int(element["id"]),
                        members=members,  # type: ignore[arg-type]
                        tags=dict(element.get("tags") or {}),
                    )
                )

        return cls(nodes=nodes, ways=ways, relations=relations)
