"""Serializacao estrita do envelope do contrato v1."""

from __future__ import annotations

import json
from pathlib import Path

from router.contracts.errors import RouteContractError
from router.contracts.route import RoutePlan


def dumps(plan: RoutePlan, *, indent: int | None = None) -> str:
    """Serializa o contrato em JSON **estrito** (sem ``default=str``)."""
    if not isinstance(plan, RoutePlan):
        raise TypeError("dumps espera RoutePlan")
    return json.dumps(
        plan.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    )


def loads(text: str) -> RoutePlan:
    """Le e valida um envelope JSON do contrato."""
    if not isinstance(text, str):
        raise TypeError("loads espera str")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RouteContractError(f"JSON invalido: {exc}") from exc
    return RoutePlan.from_dict(data)


def write_plan(path: str | Path, plan: RoutePlan, *, indent: int | None = 2) -> Path:
    """Grava o contrato em UTF-8 e devolve o caminho."""
    if not isinstance(plan, RoutePlan):
        raise TypeError("write_plan espera RoutePlan")
    target = Path(path)
    target.write_text(dumps(plan, indent=indent) + "\n", encoding="utf-8")
    return target


def read_plan(path: str | Path) -> RoutePlan:
    """Le um envelope do contrato de um arquivo UTF-8."""
    return loads(Path(path).read_text(encoding="utf-8"))
