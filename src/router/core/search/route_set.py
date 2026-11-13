"""Conjunto de rotas distintas: corte estrutural sem limiar arbitrário.

A fronteira Pareto pode conter dezenas de rotas **não dominadas** que são,
geometricamente, quase a mesma rota (pequenos desvios trocam segundos por
metros). Apresentar todas enche a lista de repetições. Este módulo escolhe um
subconjunto **estruturalmente distinto** a partir de similaridades de arestas,
**sem valor de corte "chutado"**: o limiar é *derivado* da própria distribuição.

Dois modos, ambos livres de constante escolhida a dedo:

* ``max_routes is None`` — **corte no maior salto** (single-linkage): monta a
  árvore geradora máxima sobre o grafo de similaridades e corta no **maior
  intervalo** entre pesos consecutivos. O corte é o ponto médio desse intervalo;
  nada de ``0,7``. Devolve a rota **melhor colocada** de cada grupo, na ordem de
  preferência. Sem salto (pesos todos iguais, ou menos de 2 arestas) **não há
  evidência de separação** e nada é colapsado — a ausência é explícita, não
  imputada.
* ``max_routes is N`` — **farthest-first** (max-min Jaccard): começa pela rota
  melhor colocada e sempre acrescenta a mais dissimilar às já escolhidas, até
  ``N``. ``N`` é entrada declarada pelo chamador, não limiar.

Premissas e garantias:

* a entrada deve vir em **ordem de preferência** (índice 0 = melhor). A rota do
  índice 0 — a "pick" que a interface/ML destaca — **nunca é descartada**;
* a similaridade padrão é ``arc_jaccard`` (estrutural sobre as arestas
  direcionadas, ``diversity.py``); ``shared_distance_ratio`` também serve e o
  resultado foi invariante a essa escolha nos dados reais auditados;
* o colapso de **duplicata exata** (mesma sequência de arcos) precede o corte —
  não depende de limiar;
* custo ``O(n^2)`` em tempo; para ``arc_jaccard`` a seleção final usa uma
  visão sob demanda com memória ``O(n)``. O cache incremental do preview cresce
  apenas com os pares observados entre publicações.

Ref.: similaridade de caminhos (Jaccard/sobreposição) e o princípio de
"dissimilar paths"/natural break. A decisão está registrada no ``CHANGELOG``
(``[Unreleased]``); o corte é derivado dos dados, nunca fixado a mão.

.. note::
    Um ADR dedicado a esta seleção estrutural ainda não foi escrito; se
    necessário, ele deve consolidar ``structural-natural-break@1`` e o modo
    ``farthest-first``.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Iterator

from .diversity import arc_jaccard
from .route import SearchRoute

#: Identificador versionado da definição de conjunto (proveniência/auditoria).
ROUTE_SET_DEFINITION = "structural-natural-break@1"

#: Método usado quando o colapso é apenas de duplicatas exatas (n <= 1 distinto).
METHOD_TRIVIAL = "trivial"
#: Corte no maior salto da árvore geradora (``max_routes is None``).
METHOD_NATURAL_BREAK = "natural_break"
#: Sem salto: não há evidência de separação; nada é colapsado.
METHOD_NO_BREAK = "no_break"
#: Seleção por ``N`` declarado (``max_routes`` informado).
METHOD_FARTHEST_FIRST = "farthest_first"

#: Função de semelhança entre duas rotas: ``1`` = idênticas, ``0`` = disjuntas.
RouteSimilarity = Callable[[SearchRoute, SearchRoute], float]

_ACTIVE_SIMILARITY_CACHE: ContextVar["IncrementalSimilarityMatrix | None"] = (
    ContextVar("active_similarity_cache", default=None)
)


class _SimilarityRow(Sequence[float]):
    def __init__(self, keys, pairs):
        self._keys = keys
        self._pairs = pairs

    def __len__(self) -> int:
        return len(self._keys)

    def __getitem__(self, index: int) -> float:
        if index < 0:
            index += len(self._keys)
        if index < 0 or index >= len(self._keys):
            raise IndexError(index)
        if self._keys[index] == self._keys[self._row_index]:
            return 1.0
        return self._pairs[frozenset((self._keys[self._row_index], self._keys[index]))]

    def for_row(self, row_index: int) -> "_SimilarityRow":
        row = _SimilarityRow(self._keys, self._pairs)
        row._row_index = row_index
        return row


class _SimilarityMatrixView(Sequence[Sequence[float]]):
    def __init__(self, keys, pairs):
        self._keys = keys
        self._pairs = pairs

    def __len__(self) -> int:
        return len(self._keys)

    def __getitem__(self, index: int) -> Sequence[float]:
        if index < 0:
            index += len(self._keys)
        if index < 0 or index >= len(self._keys):
            raise IndexError(index)
        return _SimilarityRow(self._keys, self._pairs).for_row(index)


class _ArcSimilarityRow(Sequence[float]):
    def __init__(self, arc_sets, row_index: int) -> None:
        self._arc_sets = arc_sets
        self._row_index = row_index

    def __len__(self) -> int:
        return len(self._arc_sets)

    def __getitem__(self, index: int) -> float:
        if index < 0:
            index += len(self._arc_sets)
        if index < 0 or index >= len(self._arc_sets):
            raise IndexError(index)
        if index == self._row_index:
            return 1.0
        left = self._arc_sets[self._row_index]
        right = self._arc_sets[index]
        return 1.0 if not (left or right) else len(left & right) / len(left | right)


class _LazyArcSimilarityMatrix(Sequence[Sequence[float]]):
    """Matriz Jaccard sob demanda, sem alocar ``N x N`` no resultado final."""

    def __init__(self, routes: tuple[SearchRoute, ...]) -> None:
        self._arc_sets = tuple(frozenset(route.arc_ids) for route in routes)

    def __len__(self) -> int:
        return len(self._arc_sets)

    def __getitem__(self, index: int) -> Sequence[float]:
        if index < 0:
            index += len(self._arc_sets)
        if index < 0 or index >= len(self._arc_sets):
            raise IndexError(index)
        return _ArcSimilarityRow(self._arc_sets, index)


class IncrementalSimilarityMatrix:
    """Cache de similaridades que cresce por linhas/colunas novas.

    A identidade estrutural da rota e sua sequência de arcos. Em consultas
    sucessivas do preview, pares já comparados não são recalculados; somente
    os pares envolvendo uma rota nova entram no cache.
    """

    def __init__(self) -> None:
        self._pairs: dict[frozenset[tuple[str | None, ...]], float] = {}
        self._arc_sets: dict[tuple[str | None, ...], frozenset[str | None]] = {}

    def matrix(
        self,
        routes: tuple[SearchRoute, ...],
        similarity: RouteSimilarity,
    ) -> Sequence[Sequence[float]]:
        if similarity is not arc_jaccard:
            return _similarity_matrix(routes, similarity)
        keys = tuple(tuple(route.arc_ids) for route in routes)
        for route, key in zip(routes, keys):
            self._arc_sets.setdefault(key, frozenset(route.arc_ids))
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                pair = frozenset((left, right))
                if pair not in self._pairs:
                    left_arcs = self._arc_sets[left]
                    right_arcs = self._arc_sets[right]
                    self._pairs[pair] = (
                        1.0
                        if not (left_arcs or right_arcs)
                        else len(left_arcs & right_arcs) / len(left_arcs | right_arcs)
                    )
        return _SimilarityMatrixView(keys, self._pairs)


@contextmanager
def similarity_cache_scope(cache: IncrementalSimilarityMatrix) -> Iterator[None]:
    """Ativa um cache incremental apenas no escopo do preview atual."""
    token = _ACTIVE_SIMILARITY_CACHE.set(cache)
    try:
        yield
    finally:
        _ACTIVE_SIMILARITY_CACHE.reset(token)


@dataclass(frozen=True, slots=True)
class SimilarityCut:
    """Corte derivado do maior intervalo entre pesos de similaridade.

    ``threshold`` é o ponto médio do maior intervalo; uma aresta "une" (mesmo
    grupo) quando seu peso é **estritamente maior** que ele. ``lower``/``upper``
    são os pesos que delimitam o intervalo e ``gap`` o seu tamanho — expostos
    para auditoria (nada é escondido).
    """

    threshold: float
    gap: float
    lower: float
    upper: float

    def to_dict(self) -> dict[str, float]:
        return {
            "threshold": self.threshold,
            "gap": self.gap,
            "lower": self.lower,
            "upper": self.upper,
        }


@dataclass(frozen=True, slots=True)
class RouteSetSelection:
    """Conjunto de rotas distintas escolhido, com a proveniência do método.

    ``routes`` está na ordem de preferência (índice 0 = melhor) e ``indices``
    aponta para as posições correspondentes na **entrada** de
    ``select_distinct_routes`` (útil para mapear de volta a candidatas). ``cut``
    só existe no modo ``natural_break``; ``cluster_sizes`` traz o tamanho de cada
    grupo (desc, apenas no ``natural_break``).
    """

    routes: tuple[SearchRoute, ...]
    method: str
    definition: str = ROUTE_SET_DEFINITION
    indices: tuple[int, ...] = ()
    cut: SimilarityCut | None = None
    cluster_sizes: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        return len(self.routes)

    def __len__(self) -> int:
        return len(self.routes)

    def __iter__(self):
        return iter(self.routes)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "definition": self.definition,
            "selected": self.count,
            "indices": list(self.indices),
            "cut": None if self.cut is None else self.cut.to_dict(),
            "cluster_sizes": list(self.cluster_sizes),
        }


def natural_break_cut(weights: Sequence[float]) -> SimilarityCut | None:
    """Corte no maior intervalo entre pesos ordenados; ``None`` se não houver.

    Ordena os pesos e devolve o ponto médio do maior intervalo entre valores
    consecutivos. Devolve ``None`` quando há menos de dois pesos ou quando todos
    são iguais (intervalo zero) — **sem evidência de separação**, nada é
    colapsado. Não há limiar absoluto: o corte depende só dos dados.
    """
    values = sorted(float(w) for w in weights)
    if len(values) < 2:
        return None
    index = 0
    gap = values[1] - values[0]
    for position in range(1, len(values) - 1):
        candidate = values[position + 1] - values[position]
        if candidate > gap:
            gap = candidate
            index = position
    if gap <= 0.0:
        return None
    lower = values[index]
    upper = values[index + 1]
    return SimilarityCut(
        threshold=(lower + upper) / 2.0,
        gap=gap,
        lower=lower,
        upper=upper,
    )


def single_linkage_clusters(
    node_count: int,
    edges: Sequence[tuple[float, int, int]],
    threshold: float,
) -> tuple[tuple[int, ...], ...]:
    """Componentes conexas unindo arestas com peso ``> threshold``.

    ``edges`` são ``(peso, i, j)``. Devolve os grupos ordenados pelo menor
    índice (que é a posição de preferência), e cada grupo também crescente.
    """
    parent = list(range(node_count))

    def find(node: int) -> int:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for weight, a, b in edges:
        if weight <= threshold:
            continue
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    groups: dict[int, list[int]] = {}
    for node in range(node_count):
        groups.setdefault(find(node), []).append(node)
    return tuple(sorted((tuple(members) for members in groups.values()), key=lambda g: g[0]))


def farthest_first_maximin(
    similarity: Sequence[Sequence[float]], count: int
) -> tuple[int, ...]:
    """Seleciona ``count`` índices maximizando a dissimilaridade mínima.

    Semente = índice ``0`` (melhor colocado). A cada passo, acrescenta a rota de
    **menor** semelhança máxima com as já escolhidas (max-min Jaccard); empate
    resolvido pelo menor índice (determinístico). Devolve os índices em ordem
    crescente (= ordem de preferência).
    """
    n = len(similarity)
    if count <= 0 or n == 0:
        return ()
    if count >= n:
        return tuple(range(n))

    chosen = [0]
    nearest = [similarity[0][j] for j in range(n)]  # max sim a alguma escolhida
    while len(chosen) < count:
        best = -1
        best_value = math.inf
        for j in range(n):
            if nearest[j] >= 1.0:  # já escolhida (diagonal) ou idêntica
                continue
            if nearest[j] < best_value:
                best_value = nearest[j]
                best = j
        if best < 0:  # pragma: no cover - defensivo: não deveria ocorrer
            break
        chosen.append(best)
        row = similarity[best]
        for j in range(n):
            if row[j] > nearest[j]:
                nearest[j] = row[j]
    return tuple(sorted(chosen))


def select_distinct_routes(
    routes: Sequence[SearchRoute],
    *,
    max_routes: int | None = None,
    similarity: RouteSimilarity = arc_jaccard,
    matrix_cache: IncrementalSimilarityMatrix | None = None,
) -> RouteSetSelection:
    """Escolhe rotas distintas de ``routes`` (já em ordem de preferência).

    Args:
        routes: rotas em **ordem de preferência** (índice 0 = melhor). A rota 0
            nunca é descartada (é a "pick" — no fluxo E2E, a escolhida pelo ML).
        max_routes: ``None`` → ``natural_break`` (um representante por grupo);
            inteiro positivo ``N`` → ``farthest_first`` limitado a ``N``.
        similarity: função de semelhança (padrão ``arc_jaccard``).
        matrix_cache: cache opcional de similaridades incrementais para
            previews sucessivos. Não altera o resultado da seleção.

    Returns:
        ``RouteSetSelection`` com as rotas escolhidas (ordem de preferência),
        o método e a proveniência do corte.

    Raises:
        ValueError: ``max_routes`` não é ``None`` nem inteiro positivo.
    """
    if max_routes is not None and (
        isinstance(max_routes, bool)
        or not isinstance(max_routes, int)
        or max_routes <= 0
    ):
        raise ValueError(
            "max_routes deve ser um inteiro positivo ou None "
            f"(recebido {max_routes!r})"
        )

    ordered = tuple(routes)
    unique_indices, unique = _collapse_exact_duplicates(ordered)
    if len(unique) <= 1:
        return RouteSetSelection(
            routes=unique, indices=unique_indices, method=METHOD_TRIVIAL
        )

    active_cache = matrix_cache or _ACTIVE_SIMILARITY_CACHE.get()
    if active_cache is not None:
        matrix = active_cache.matrix(unique, similarity)
    elif similarity is arc_jaccard:
        matrix = _LazyArcSimilarityMatrix(unique)
    else:
        matrix = _similarity_matrix(unique, similarity)

    if max_routes is None:
        tree = _maximum_spanning_tree(matrix)
        cut = natural_break_cut([weight for weight, _, _ in tree])
        if cut is None:
            return RouteSetSelection(
                routes=unique, indices=unique_indices, method=METHOD_NO_BREAK
            )
        clusters = single_linkage_clusters(len(unique), tree, cut.threshold)
        positions = tuple(min(group) for group in clusters)
        sizes = tuple(sorted((len(group) for group in clusters), reverse=True))
        return RouteSetSelection(
            routes=tuple(unique[position] for position in positions),
            indices=tuple(unique_indices[position] for position in positions),
            method=METHOD_NATURAL_BREAK,
            cut=cut,
            cluster_sizes=sizes,
        )

    picks = farthest_first_maximin(matrix, min(max_routes, len(unique)))
    return RouteSetSelection(
        routes=tuple(unique[index] for index in picks),
        indices=tuple(unique_indices[index] for index in picks),
        method=METHOD_FARTHEST_FIRST,
    )


# --------------------------------------------------------------------------- internos
def _collapse_exact_duplicates(
    routes: tuple[SearchRoute, ...]
) -> tuple[tuple[int, ...], tuple[SearchRoute, ...]]:
    """Remove rotas com a **mesma sequência de arcos** (mantém a melhor).

    Devolve ``(índices_originais, rotas_únicas)`` — os índices apontam para a
    entrada, para o chamador mapear de volta às suas candidatas.
    """
    seen: set[tuple[str | None, ...]] = set()
    indices: list[int] = []
    kept: list[SearchRoute] = []
    for index, route in enumerate(routes):
        key = tuple(route.arc_ids)
        if key in seen:
            continue
        seen.add(key)
        indices.append(index)
        kept.append(route)
    return tuple(indices), tuple(kept)


def _similarity_matrix(
    routes: tuple[SearchRoute, ...], similarity: RouteSimilarity
) -> tuple[tuple[float, ...], ...]:
    n = len(routes)
    matrix = [[0.0] * n for _ in range(n)]
    if similarity is arc_jaccard:
        # Memoiza o conjunto de arcos de cada rota (O(n)) em vez de reconstruí-lo
        # a cada par (O(n^2)): ``SearchRoute.arc_set`` é recomputado a cada acesso
        # e a matriz é refeita a cada publicação do preview.
        arc_sets = [frozenset(route.arc_ids) for route in routes]
        for i in range(n):
            matrix[i][i] = 1.0
            arcs_i = arc_sets[i]
            for j in range(i + 1, n):
                arcs_j = arc_sets[j]
                value = 1.0 if not (arcs_i or arcs_j) else len(arcs_i & arcs_j) / len(arcs_i | arcs_j)
                matrix[i][j] = value
                matrix[j][i] = value
        return tuple(tuple(row) for row in matrix)
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            value = float(similarity(routes[i], routes[j]))
            matrix[i][j] = value
            matrix[j][i] = value
    return tuple(tuple(row) for row in matrix)


def _maximum_spanning_tree(
    matrix: Sequence[Sequence[float]],
) -> tuple[tuple[float, int, int], ...]:
    """Árvore geradora máxima (Prim, denso) em ``O(n^2)``.

    Devolve ``(peso, i, j)`` das ``n-1`` arestas; é o objeto sobre o qual o
    ``natural_break`` corta (equivalente ao single-linkage). Usar *todos* os
    pares em vez da árvore produz saltos espúrios — verificado nos dados reais.
    """
    n = len(matrix)
    if n < 2:
        return ()
    in_tree = [False] * n
    in_tree[0] = True
    best = [matrix[0][j] for j in range(n)]
    origin = [0] * n
    edges: list[tuple[float, int, int]] = []
    for _ in range(n - 1):
        weight = -math.inf
        pick = -1
        for j in range(n):
            if not in_tree[j] and best[j] > weight:
                weight, pick = best[j], j
        if pick < 0:  # pragma: no cover - defensivo: grafo completo conectado
            break
        edges.append((weight, origin[pick], pick))
        in_tree[pick] = True
        for j in range(n):
            if not in_tree[j] and matrix[pick][j] > best[j]:
                best[j] = matrix[pick][j]
                origin[j] = pick
    return tuple(edges)
