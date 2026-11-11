"""Validacao interna dos blocos do contrato v1."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from router.contracts.errors import RouteContractError
from router.contracts.version import INTENSITY_BANDS


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RouteContractError(f"{name} deve ser um objeto (mapping)")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RouteContractError(f"{name} deve ser uma lista")
    return value


def _str(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise RouteContractError(f"{name} deve ser uma string nao vazia")
    return value


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise RouteContractError(f"{name} deve ser booleano")
    return value


def _int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RouteContractError(f"{name} deve ser inteiro")
    if minimum is not None and value < minimum:
        raise RouteContractError(f"{name} deve ser >= {minimum}")
    return value


def _float(
    value: Any,
    name: str,
    *,
    allow_none: bool = False,
    minimum: float | None = None,
) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RouteContractError(f"{name} deve ser numerico")
    number = float(value)
    if not math.isfinite(number):
        raise RouteContractError(f"{name} deve ser finito")
    if minimum is not None and number < minimum:
        raise RouteContractError(f"{name} deve ser >= {minimum}")
    return number


def _coord(value: Any, name: str) -> tuple[float, float]:
    if isinstance(value, Mapping):
        lat = _float(value.get("lat"), f"{name}.lat")
        lon = _float(value.get("lon"), f"{name}.lon")
    else:
        items = _sequence(value, name)
        if len(items) != 2:
            raise RouteContractError(f"{name} deve ter exatamente 2 coordenadas")
        lat = _float(items[0], f"{name}[0]")
        lon = _float(items[1], f"{name}[1]")
    assert lat is not None and lon is not None
    if not -90.0 <= lat <= 90.0:
        raise RouteContractError(f"{name}.lat fora de [-90, 90]")
    if not -180.0 <= lon <= 180.0:
        raise RouteContractError(f"{name}.lon fora de [-180, 180]")
    return (lat, lon)


def _coord_to_dict(coord: tuple[float, float]) -> dict[str, float]:
    return {"lat": round(coord[0], 7), "lon": round(coord[1], 7)}


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _fractions(values: Mapping[str, float], name: str) -> None:
    """Valida um mapa de fracoes por faixa (``i1``..``i5``)."""
    if not isinstance(values, Mapping):
        raise RouteContractError(f"{name} deve ser um objeto")
    for band, value in values.items():
        if band not in INTENSITY_BANDS:
            raise RouteContractError(f"{name}: faixa desconhecida {band!r}")
        _float(value, f"{name}.{band}", minimum=0.0)


def _fractions_from(values: Any, name: str) -> dict[str, float]:
    item = _mapping(values, name)
    _fractions(item, name)
    return {band: float(value) for band, value in item.items()}


