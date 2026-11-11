"""Fila de prioridade lexicografica da busca multiobjetivo.

O vetor ``f = g + h`` e comparado lexicograficamente (menor primeiro), com
desempate deterministico pela ordem de insercao. A fila define apenas a **ordem
de exploracao**; ela nao e a fronteira Pareto.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Sequence
from typing import Any


class LexicographicQueue:
    """Fila de prioridade ordenada por um vetor de prioridade."""

    def __init__(self) -> None:
        self._heap: list[tuple[tuple[float, ...], int, Any]] = []
        self._counter = itertools.count()

    def push(self, label: Any, priority: Sequence[float]) -> None:
        key = tuple(float(value) for value in priority)
        heapq.heappush(self._heap, (key, next(self._counter), label))

    def pop(self) -> tuple[Any, tuple[float, ...]]:
        if not self._heap:
            raise IndexError("fila de prioridade vazia")
        key, _, label = heapq.heappop(self._heap)
        return label, key

    def peek_priority(self) -> tuple[float, ...] | None:
        return None if not self._heap else self._heap[0][0]

    @property
    def is_empty(self) -> bool:
        return not self._heap

    def __len__(self) -> int:
        return len(self._heap)
