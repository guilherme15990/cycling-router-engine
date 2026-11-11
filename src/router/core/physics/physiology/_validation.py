"""Validacoes numericas compartilhadas pelos contratos de fisiologia."""

from __future__ import annotations

import math
def positive(name: str, value: float) -> float:
    """Garante um valor finito e maior que zero."""
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} deve ser positivo e finito (recebido {value!r})")
    return value


def non_negative(name: str, value: float) -> float:
    """Garante um valor finito e maior ou igual a zero."""
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"{name} deve ser nao negativo e finito (recebido {value!r})"
        )
    return value


def finite(name: str, value: float) -> float:
    """Garante apenas que o valor e finito."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} deve ser finito (recebido {value!r})")
    return value


def unit_interval(name: str, value: float) -> float:
    """Garante um valor finito em ``[0, 1]``."""
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} deve estar em [0, 1] (recebido {value!r})")
    return value
