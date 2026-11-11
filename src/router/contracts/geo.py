"""Tipos geograficos canonicos do contrato v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from router.contracts._validation import _float, _mapping

#: Coordenada geografica em graus decimais: ``(lat, lon)``.
LL = tuple[float, float]


@dataclass(frozen=True, slots=True)
class BBox:
    """Caixa geografica (graus)."""

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        _float(self.south, "bbox.south")
        _float(self.west, "bbox.west")
        _float(self.north, "bbox.north")
        _float(self.east, "bbox.east")
        if self.south > self.north or self.west > self.east:
            raise RouteContractError("bbox invalido: south<=north e west<=east")

    def to_dict(self) -> dict[str, float]:
        return {
            "south": round(self.south, 7),
            "west": round(self.west, 7),
            "north": round(self.north, 7),
            "east": round(self.east, 7),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BBox":
        item = _mapping(data, "bbox")
        return cls(
            south=_float(item.get("south"), "bbox.south"),  # type: ignore[arg-type]
            west=_float(item.get("west"), "bbox.west"),  # type: ignore[arg-type]
            north=_float(item.get("north"), "bbox.north"),  # type: ignore[arg-type]
            east=_float(item.get("east"), "bbox.east"),  # type: ignore[arg-type]
        )


