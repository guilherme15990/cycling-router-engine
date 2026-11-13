"""Testes do conjunto de rotas distintas (corte estrutural sem limiar fixo).

Cobre ``route_set.py``: o corte no maior salto (``natural_break``), a ausência
de separação (``no_break``), a seleção por ``N`` (``farthest-first``), o colapso
de duplicata exata e a validação de entrada. O foco é garantir que **não há
limiar arbitrário**: o corte é derivado só da distribuição de similaridades.
"""

from __future__ import annotations

import pytest

from router.core.search import (
    ROUTE_SET_DEFINITION,
    SearchRoute,
    SimilarityCut,
    farthest_first_maximin,
    natural_break_cut,
    select_distinct_routes,
    single_linkage_clusters,
)
from router.core.search.route_set import (
    METHOD_FARTHEST_FIRST,
    METHOD_NATURAL_BREAK,
    METHOD_NO_BREAK,
    METHOD_TRIVIAL,
)


# --------------------------------------------------------------------------- fixtures
def route(
    arc_ids,
    *,
    label_id: int = 0,
    lengths=None,
    distance_m: float | None = None,
) -> SearchRoute:
    arcs = tuple(arc_ids)
    if lengths is None:
        lengths = tuple(100.0 for _ in arcs)
    if distance_m is None:
        distance_m = float(sum(lengths)) if lengths else float(len(arcs)) * 100.0
    return SearchRoute(
        arc_ids=arcs,
        arc_lengths=tuple(lengths),
        time_s=100.0,
        distance_m=distance_m,
        ascent_m=10.0,
        descent_m=20.0,
        energy_j=1000.0,
        label_id=label_id,
    )


def near_a(label_id: int = 0) -> SearchRoute:
    return route(range(1, 11), label_id=label_id)


def near_b(label_id: int = 1) -> SearchRoute:
    # 9 arcos em comum com ``near_a``; Jaccard = 9/11 ~ 0.818.
    return route([*range(1, 10), 11], label_id=label_id)


def far_c(label_id: int = 2) -> SearchRoute:
    return route(range(20, 30), label_id=label_id)


# ------------------------------------------------------------------- corte derivado
def test_natural_break_cut_is_midpoint_of_largest_gap():
    cut = natural_break_cut([0.0, 0.0, 0.9])
    assert isinstance(cut, SimilarityCut)
    assert cut.lower == 0.0
    assert cut.upper == 0.9
    assert cut.gap == pytest.approx(0.9)
    assert cut.threshold == pytest.approx(0.45)


def test_natural_break_cut_none_when_uniform():
    assert natural_break_cut([0.5, 0.5, 0.5]) is None


def test_natural_break_cut_none_with_fewer_than_two_weights():
    assert natural_break_cut([]) is None
    assert natural_break_cut([0.7]) is None


def test_single_linkage_unions_edges_above_threshold():
    clusters = single_linkage_clusters(
        node_count=3,
        edges=[(0.8, 0, 1), (0.2, 1, 2)],
        threshold=0.5,
    )
    assert clusters == ((0, 1), (2,))


def test_farthest_first_seeds_best_and_picks_most_dissimilar():
    matrix = (
        (1.0, 0.9, 0.0, 0.1),
        (0.9, 1.0, 0.1, 0.0),
        (0.0, 0.1, 1.0, 0.8),
        (0.1, 0.0, 0.8, 1.0),
    )
    # Semente = 0; o mais dissimilar a {0} e 2 (sim 0.0).
    assert farthest_first_maximin(matrix, 2) == (0, 2)


# ------------------------------------------------------------------- modo None
def test_empty_input_returns_empty():
    selection = select_distinct_routes(())
    assert selection.count == 0
    assert selection.routes == ()


def test_single_route_is_kept():
    only = near_a()
    selection = select_distinct_routes((only,))
    assert selection.method == METHOD_TRIVIAL
    assert selection.routes == (only,)


def test_exact_duplicate_sequence_is_collapsed():
    selection = select_distinct_routes((near_a(0), near_a(1)))
    assert selection.method == METHOD_TRIVIAL
    assert selection.count == 1
    assert selection.routes[0].label_id == 0  # mantem a melhor colocada


def test_route_set_selection_reports_input_indices():
    routes = (near_a(0), near_b(1), far_c(2))
    selection = select_distinct_routes(routes)
    assert selection.indices == (0, 2)
    assert selection.routes == (routes[0], routes[2])


def test_trivial_collapse_reports_kept_index():
    first, duplicate = near_a(0), near_a(1)
    selection = select_distinct_routes((first, duplicate))
    assert selection.indices == (0,)
    assert selection.routes == (first,)


def test_natural_break_keeps_one_representative_per_group():
    selection = select_distinct_routes((near_a(0), near_b(1), far_c(2)))
    assert selection.method == METHOD_NATURAL_BREAK
    # Duas familias: {0,1} e {2}; representantes pelo menor indice.
    assert [r.label_id for r in selection.routes] == [0, 2]
    assert selection.cluster_sizes == (2, 1)
    assert selection.cut is not None


def test_natural_break_never_drops_the_best_ranked_route():
    # A melhor colocada e a que abre o grupo; deve sobreviver sempre.
    selection = select_distinct_routes((near_a(0), near_b(1), far_c(2)))
    assert selection.routes[0].label_id == 0


def test_pair_without_evidence_of_separation_is_not_collapsed():
    # Dois grupos sem salto -> nada e colapsado (ausencia explicita).
    selection = select_distinct_routes((near_a(0), far_c(1)))
    assert selection.method == METHOD_NO_BREAK
    assert selection.count == 2
    assert selection.cut is None


def test_two_near_duplicates_without_break_are_kept():
    # Sem mais rotas nao ha "maior salto": conservador, mantem ambas.
    selection = select_distinct_routes((near_a(0), near_b(1)))
    assert selection.method == METHOD_NO_BREAK
    assert selection.count == 2


# ------------------------------------------------------------------- modo N
def test_farthest_first_respects_max_routes():
    selection = select_distinct_routes(
        (near_a(0), near_b(1), far_c(2)), max_routes=2
    )
    assert selection.method == METHOD_FARTHEST_FIRST
    assert selection.count == 2


def test_farthest_first_prefers_the_dissimilar_route():
    selection = select_distinct_routes(
        (near_a(0), near_b(1), far_c(2)), max_routes=2
    )
    # Semente 0; o mais dissimilar a {0} e 2 (disjunta).
    assert [r.label_id for r in selection.routes] == [0, 2]


def test_max_routes_above_length_returns_everything():
    routes = (near_a(0), near_b(1), far_c(2))
    selection = select_distinct_routes(routes, max_routes=10)
    assert selection.count == 3


def test_max_routes_one_returns_best_only():
    selection = select_distinct_routes(
        (near_a(0), near_b(1), far_c(2)), max_routes=1
    )
    assert [r.label_id for r in selection.routes] == [0]


# ------------------------------------------------------------------- metrica
def test_similarity_function_is_used():
    # Todas disjuntas em arc_jaccard -> sem salto (mantem 4). Uma metrica
    # customizada cria dois grupos -> natural break escolhe 2 representantes.
    routes = tuple(route(range(10 * i, 10 * i + 10), label_id=i) for i in range(4))

    default = select_distinct_routes(routes)
    assert default.method == METHOD_NO_BREAK
    assert default.count == 4

    def by_parity(a: SearchRoute, b: SearchRoute) -> float:
        return 0.9 if a.label_id % 2 == b.label_id % 2 else 0.1

    custom = select_distinct_routes(routes, similarity=by_parity)
    assert custom.method == METHOD_NATURAL_BREAK
    # Grupos {0,2} e {1,3}; representante = menor indice de cada grupo.
    assert [r.label_id for r in custom.routes] == [0, 1]


# ------------------------------------------------------------------- entrada
@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "3"])
def test_invalid_max_routes_raises(bad):
    with pytest.raises(ValueError):
        select_distinct_routes((near_a(0),), max_routes=bad)


def test_deterministic():
    routes = (near_a(0), near_b(1), far_c(2))
    first = select_distinct_routes(routes)
    second = select_distinct_routes(routes)
    assert [r.label_id for r in first.routes] == [r.label_id for r in second.routes]


def test_to_dict_reports_provenance():
    selection = select_distinct_routes((near_a(0), near_b(1), far_c(2)))
    payload = selection.to_dict()
    assert payload["method"] == METHOD_NATURAL_BREAK
    assert payload["definition"] == ROUTE_SET_DEFINITION
    assert payload["selected"] == selection.count
    assert payload["cut"] is not None
    assert payload["cluster_sizes"] == [2, 1]
    assert payload["indices"] == [0, 2]
