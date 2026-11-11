"""Leitura do payload Overpass (JSON) para estruturas simples."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
class OverpassData:
    nodes: dict[int, RawNode]
    ways: list[RawWay]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OverpassData":
        nodes: dict[int, RawNode] = {}
        ways: list[RawWay] = []

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

        return cls(nodes=nodes, ways=ways)
