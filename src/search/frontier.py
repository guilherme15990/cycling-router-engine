"""Fronteira de Pareto por no (ou por chave generica).

A fronteira nao conhece o modelo fisico nem o perfil. Ela recebe um
``ObjectiveSet`` e mantem, entre os itens inseridos, apenas os nao dominados.
Os valores objetivo sao extraidos do proprio item (atributos ou ``Mapping``) ou
por uma funcao ``key``.

A granularidade da chave fica fora desta classe: enquanto a regra de dominancia
que considera a velocidade nao estiver fechada, a chave pode ser apenas o no;
depois pode virar ``(no, speed_bucket)`` sem alterar a fronteira.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import Any

from .dominance import Dominance
from .objectives import ObjectiveSet, dominates, epsilon_dominates

ValueKey = Callable[[Any], Sequence[float]]


class ParetoFrontier:
    """Colecao de itens nao dominados segundo um ``ObjectiveSet``."""

    def __init__(
        self,
        objectives: ObjectiveSet,
        *,
        key: ValueKey | None = None,
        epsilon: float = 0.0,
        max_size: int | None = None,
        on_discard: Callable[[Any], None] | None = None,
    ) -> None:
        if epsilon < 0.0:
            raise ValueError(f"epsilon deve ser >= 0 (recebido {epsilon!r})")
        if max_size is not None and max_size <= 0:
            raise ValueError(f"max_size deve ser positivo (recebido {max_size!r})")

        self.objectives = objectives
        self.epsilon = float(epsilon)
        self.max_size = max_size
        self._key = key
        self._on_discard = on_discard
        self._items: list[Any] = []
        self._values: list[tuple[float, ...]] = []

    # -------------------------------------------------------------- consulta
    @property
    def items(self) -> tuple[Any, ...]:
        return tuple(self._items)

    @property
    def values(self) -> tuple[tuple[float, ...], ...]:
        return tuple(self._values)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def contains(self, item: Any) -> bool:
        return any(existing is item for existing in self._items)

    def dominated_by(self, item: Any) -> bool:
        """True se algum item da fronteira domina ``item``."""
        values = self._values_of(item)
        return any(
            self._better(existing, values) for existing in self._values
        )

    # -------------------------------------------------------------- mutacao
    def insert(self, item: Any) -> bool:
        """Insere ``item`` mantendo a fronteira.

        Retorna ``True`` se o item entrou (e passou a ser nao dominado) e
        ``False`` se foi descartado por dominancia.
        """
        values = self._values_of(item)

        for existing in self._values:
            if self._better(existing, values):
                self._discard(item)
                return False

        survivor_items: list[Any] = []
        survivor_values: list[tuple[float, ...]] = []
        for existing_item, existing_values in zip(self._items, self._values):
            if self._better(values, existing_values):
                self._discard(existing_item)
            else:
                survivor_items.append(existing_item)
                survivor_values.append(existing_values)

        survivor_items.append(item)
        survivor_values.append(values)
        self._items = survivor_items
        self._values = survivor_values

        if self.max_size is not None and len(self._items) > self.max_size:
            self._truncate()
        return True

    def clear(self) -> None:
        self._items.clear()
        self._values.clear()

    def to_dict(self) -> dict[str, object]:
        return {
            "objectives": list(self.objectives.objectives),
            "size": len(self._items),
            "values": [list(values) for values in self._values],
        }

    # -------------------------------------------------------------- interno
    def _values_of(self, item: Any) -> tuple[float, ...]:
        if self._key is None:
            return self.objectives.values(item)
        return tuple(float(value) for value in self._key(item))

    def _better(
        self, values_a: tuple[float, ...], values_b: tuple[float, ...]
    ) -> bool:
        if self.epsilon > 0.0:
            return epsilon_dominates(values_a, values_b, self.epsilon)
        return dominates(values_a, values_b)

    def _discard(self, item: Any) -> None:
        if getattr(item, "active", None) is not None:
            try:
                setattr(item, "active", False)
            except (AttributeError, TypeError):
                pass
        if self._on_discard is not None:
            self._on_discard(item)

    def _truncate(self) -> None:
        """Corta para ``max_size`` mantendo os melhores por ordem lexicografica.

        E um controle de **desempenho**, nao uma garantia de otimalidade: pode
        remover rotas nao dominadas.
        """
        assert self.max_size is not None
        keep = sorted(
            range(len(self._items)), key=lambda index: self._values[index]
        )[: self.max_size]
        keep_set = set(keep)

        kept_items: list[Any] = []
        kept_values: list[tuple[float, ...]] = []
        for index, (item, values) in enumerate(zip(self._items, self._values)):
            if index in keep_set:
                kept_items.append(item)
                kept_values.append(values)
            else:
                self._discard(item)
        self._items = kept_items
        self._values = kept_values


class FrontierMap:
    """Fronteiras Pareto separadas por chave.

    A chave tipica e o id do no. Se a busca passar a comparar estados fisicos,
    a chave pode incluir o intervalo de velocidade, sem mudar esta classe.
    """

    def __init__(
        self,
        objectives: ObjectiveSet,
        *,
        epsilon: float = 0.0,
        max_labels_per_node: int | None = None,
        key: ValueKey | None = None,
        on_discard: Callable[[Any], None] | None = None,
    ) -> None:
        self.objectives = objectives
        self.epsilon = float(epsilon)
        self.max_labels_per_node = max_labels_per_node
        self._key = key
        self._on_discard = on_discard
        self._frontiers: dict[Hashable, ParetoFrontier] = {}

    def frontier(self, node_key: Hashable) -> ParetoFrontier:
        frontier = self._frontiers.get(node_key)
        if frontier is None:
            frontier = ParetoFrontier(
                self.objectives,
                key=self._key,
                epsilon=self.epsilon,
                max_size=self.max_labels_per_node,
                on_discard=self._on_discard,
            )
            self._frontiers[node_key] = frontier
        return frontier

    def insert(self, node_key: Hashable, item: Any) -> bool:
        return self.frontier(node_key).insert(item)

    def items(self, node_key: Hashable) -> tuple[Any, ...]:
        frontier = self._frontiers.get(node_key)
        return () if frontier is None else frontier.items

    def keys(self) -> Iterable[Hashable]:
        return self._frontiers.keys()

    def __len__(self) -> int:
        return len(self._frontiers)

    def __contains__(self, node_key: object) -> bool:
        return node_key in self._frontiers

    @property
    def total_items(self) -> int:
        return sum(len(frontier) for frontier in self._frontiers.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "objectives": list(self.objectives.objectives),
            "nodes": len(self._frontiers),
            "labels": self.total_items,
            "frontiers": {
                str(node_key): frontier.to_dict()
                for node_key, frontier in self._frontiers.items()
            },
        }


class FrontierManager:
    """Fronteiras nao dominadas por chave de estado fisico.

    A chave vem de ``dominance.key(state)`` — tipicamente ``(no, bucket)``.
    Dentro de uma chave, a comparacao usa ``dominance.dominates``. A mesma
    politica de buckets deve ser usada aqui e na regra de dominancia.

    Labels descartadas por dominancia sao marcadas como ``active = False`` para
    que a fila possa ignora-las depois.
    """

    def __init__(
        self,
        dominance: Dominance,
        *,
        max_labels_per_key: int | None = None,
        on_discard: Callable[[Any], None] | None = None,
    ) -> None:
        if max_labels_per_key is not None and max_labels_per_key <= 0:
            raise ValueError(
                f"max_labels_per_key deve ser positivo (recebido {max_labels_per_key!r})"
            )
        self.dominance = dominance
        self.max_labels_per_key = max_labels_per_key
        self._on_discard = on_discard
        self._frontiers: dict[Hashable, list[Any]] = {}

    # -------------------------------------------------------------- consulta
    @property
    def key_count(self) -> int:
        return len(self._frontiers)

    def key_of(self, state: Any) -> Hashable:
        return self.dominance.key(state)

    def frontier(self, key: Hashable) -> tuple[Any, ...]:
        return tuple(self._frontiers.get(key, ()))

    def labels_at(self, node_id: Hashable) -> tuple[Any, ...]:
        """Labels ativas de um no, somando todos os buckets.

        Usado para recuperar a fronteira de destino sem conhecer o bucket.
        """
        labels: list[Any] = []
        for key, frontier in self._frontiers.items():
            if isinstance(key, tuple) and key[0] == node_id:
                labels.extend(label for label in frontier if label.active)
        return tuple(labels)

    def active_labels(self) -> tuple[Any, ...]:
        labels: list[Any] = []
        for frontier in self._frontiers.values():
            labels.extend(label for label in frontier if label.active)
        return tuple(labels)

    @property
    def total_labels(self) -> int:
        return sum(len(frontier) for frontier in self._frontiers.values())

    def __len__(self) -> int:
        return len(self._frontiers)

    # -------------------------------------------------------------- mutacao
    def insert(self, candidate: Any) -> bool:
        """Insere ``candidate`` mantendo a fronteira da sua chave.

        Retorna ``False`` (e marca a label como inativa) se alguma label
        existente a domina.
        """
        key = self.dominance.key(candidate)
        frontier = self._frontiers.setdefault(key, [])

        for existing in frontier:
            if self.dominance.dominates(existing, candidate):
                self._discard(candidate)
                return False

        survivors: list[Any] = []
        for existing in frontier:
            if self.dominance.dominates(candidate, existing):
                self._discard(existing)
            else:
                survivors.append(existing)
        survivors.append(candidate)

        if (
            self.max_labels_per_key is not None
            and len(survivors) > self.max_labels_per_key
        ):
            survivors = self._truncate(survivors)

        self._frontiers[key] = survivors
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "keys": len(self._frontiers),
            "labels": self.total_labels,
            "objectives": list(self.dominance.objectives.objectives),
        }

    # -------------------------------------------------------------- interno
    def _discard(self, label: Any) -> None:
        try:
            label.active = False
        except (AttributeError, TypeError):
            pass
        if self._on_discard is not None:
            self._on_discard(label)

    def _truncate(self, labels: list[Any]) -> list[Any]:
        """Mantem os melhores por ordem lexicografica (controle de desempenho)."""
        assert self.max_labels_per_key is not None
        ranked = sorted(
            labels,
            key=lambda label: self.dominance.objectives.values(label),
        )
        kept = ranked[: self.max_labels_per_key]
        kept_ids = {id(label) for label in kept}
        for label in labels:
            if id(label) not in kept_ids:
                self._discard(label)
        return kept
