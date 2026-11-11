"""Baseline de caracterizacao da busca (Camada 03 — Optimization).

Congela o comportamento **atual** do ``MultiObjectiveAStar`` (fronteira Pareto
por perfil, custos, estados, limites de expansao e metricas de desempenho) em
grafos **deterministicos** com um modelo de travessia tabelado. Serve para
detectar qualquer mudanca de comportamento — atualizado apenas com justificativa
e comparacao registradas em ``docs/adr/026-baseline-busca.md``.

Filosofia igual a ``tests/test_physical_reference.py``:

* os cenario usam **stub** deterministico (sem integracao numerica) para que os
  valores sejam exatos e reprodutiveis em qualquer maquina;
* o cenario fisico (S6) e uma **caracterizacao** de integracao (aproximada);
* qualquer divergencia deve ser **investigada**, nunca "consertada" no teste.

Ref.: ADR-026 e ``docs/adr/026-auditoria-busca.md`` §9.
"""

import pytest

from graph.model import Arc, Node, RoutingGraph, Segment
from physical import (
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    TraversalResult,
)
from search import (
    MultiObjectiveAStar,
    PhysicalTraversalModel,
    ProfileSearchRequest,
    SearchConfig,
    route_arcs,
    profile_for,
    route_profile_for,
    search_profile,
)

MASS_KG = 87.0


# --------------------------------------------------------------------------
# Infra deterministica (igual em espirito a test_search_astar.py)
# --------------------------------------------------------------------------
def make_arc(arc_id: str, source: int, target: int, distance_m: float = 100.0) -> Arc:
    segment = Segment(id=f"{arc_id}:0", distance_m=distance_m)
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arc_specs, node_ids) -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.001 * node_id))
    for spec in arc_specs:
        graph.add_arc(make_arc(*spec))
    return graph


class StubTraversal:
    """Modelo fisico deterministico: tabela por ``arc.id``."""

    def __init__(self, table, mass_kg: float = MASS_KG) -> None:
        self.table = table
        self._mass_kg = mass_kg
        self.calls: list[tuple[str, float]] = []

    @property
    def mass_kg(self) -> float:
        return self._mass_kg

    def traverse(self, arc: Arc, initial_speed_mps: float) -> TraversalResult:
        self.calls.append((arc.id, float(initial_speed_mps)))
        spec = self.table[arc.id]
        energy = spec.get("energy_j", 1000.0)
        return TraversalResult(
            time_s=spec.get("time_s", 10.0),
            distance_m=spec.get("distance_m", arc.distance_m),
            ascent_m=spec.get("ascent_m", 0.0),
            descent_m=spec.get("descent_m", 0.0),
            initial_speed_mps=float(initial_speed_mps),
            final_speed_mps=spec.get("final_speed_mps", 5.0),
            mechanical_energy_j=energy,
            rider_energy_j=energy / 0.97,
            arc_id=arc.id,
        )


def _frontier(result):
    """Fronteira como (arcos, time, dist, asc, desc, E, v), na ordem devolvida."""
    rows = []
    for label in result.labels:
        rows.append(
            (
                tuple(route_arcs(label)),
                label.time_s,
                label.distance_m,
                label.ascent_m,
                label.descent_m,
                label.energy_j,
                label.physical_state.speed_mps,
            )
        )
    return rows


def _search(graph, table, profile, **cfg):
    config = SearchConfig(**cfg) if cfg else None
    astar = MultiObjectiveAStar(
        graph, StubTraversal(table), profile_for(profile), config=config
    )
    return astar.search(1, 3)


# --------------------------------------------------------------------------
# S1 — diamante com trade-off real (curto/rapido x plano/longo)
# --------------------------------------------------------------------------
S1_GRAPH = make_graph(
    [("a12", 1, 2), ("a23", 2, 3), ("a14", 1, 4), ("a43", 4, 3)],
    (1, 2, 3, 4),
)
S1_A = (("a12", "a23"), 200.0, 2000.0, 100.0, 0.0, 4000.0, 5.0)   # rapida/subida
S1_B = (("a14", "a43"), 300.0, 3000.0, 0.0, 0.0, 6000.0, 5.0)     # lenta/plana
S1_TABLE = {
    "a12": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a23": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a14": {"time_s": 150.0, "distance_m": 1500.0, "ascent_m": 0.0, "energy_j": 3000.0, "final_speed_mps": 5.0},
    "a43": {"time_s": 150.0, "distance_m": 1500.0, "ascent_m": 0.0, "energy_j": 3000.0, "final_speed_mps": 5.0},
}


def test_s1_frontier_per_profile():
    # fastest (time, ascent): nenhuma domina a outra.
    r = _search(S1_GRAPH, S1_TABLE, "fastest")
    assert r.reached and r.count == 2
    assert _frontier(r) == [S1_A, S1_B]
    assert (r.expanded, r.generated, r.truncated) == (3, 4, False)
    # Sem corte de fronteira: indicadores inativos.
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)


def test_s1_shortest_collapses_to_shortest_path():
    # shortest (distance, time): A domina B -> 1 rota.
    r = _search(S1_GRAPH, S1_TABLE, "shortest")
    assert r.count == 1 and _frontier(r) == [S1_A]
    assert (r.expanded, r.generated) == (3, 4)


def test_s1_flattest_orders_flat_first():
    r = _search(S1_GRAPH, S1_TABLE, "flattest")
    assert r.count == 2 and _frontier(r) == [S1_B, S1_A]


def test_s1_balanced_keeps_both():
    r = _search(S1_GRAPH, S1_TABLE, "balanced")
    assert r.count == 2 and _frontier(r) == [S1_A, S1_B]


def test_s1_electric_collapses_by_energy():
    # electric (energy, time): A domina B -> 1 rota.
    r = _search(S1_GRAPH, S1_TABLE, "electric")
    assert r.count == 1 and _frontier(r) == [S1_A]


# --------------------------------------------------------------------------
# S2 — label estritamente dominada e podada
# --------------------------------------------------------------------------
S2_GRAPH = make_graph([("a12", 1, 2), ("a23", 2, 3), ("a13", 1, 3)], (1, 2, 3))
S2_TABLE = {
    "a12": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a23": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a13": {"time_s": 300.0, "distance_m": 3000.0, "ascent_m": 200.0, "energy_j": 5000.0, "final_speed_mps": 5.0},
}


def test_s2_dominated_direct_arc_is_pruned():
    r = _search(S2_GRAPH, S2_TABLE, "fastest")
    assert r.count == 1
    assert _frontier(r) == [S1_A]
    assert (r.expanded, r.generated) == (2, 3)
    # Descarte por dominancia NAO e poda de fronteira.
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)


# --------------------------------------------------------------------------
# S3 — velocidades de saida em buckets diferentes nao sao comparadas
# --------------------------------------------------------------------------
S3_GRAPH = make_graph(
    [("a12", 1, 2), ("a23", 2, 3), ("a14", 1, 4), ("a43", 4, 3)],
    (1, 2, 3, 4),
)
S3_TABLE = {
    "a12": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a23": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    # metricas identicas, mas chega a 10 m/s (bucket 20) em vez de 5 m/s (bucket 10).
    "a14": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 10.0},
    "a43": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2000.0, "final_speed_mps": 10.0},
}


def test_s3_labels_in_different_speed_buckets_are_both_kept():
    r = _search(S3_GRAPH, S3_TABLE, "fastest")
    assert r.count == 2
    assert _frontier(r) == [S1_A, (("a14", "a43"), 200.0, 2000.0, 100.0, 0.0, 4000.0, 10.0)]
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)


# --------------------------------------------------------------------------
# S4 — limite de expansao interrompe e marca truncado
# --------------------------------------------------------------------------
def test_s4_max_expansions_truncates_before_reaching_destination():
    r = _search(S1_GRAPH, S1_TABLE, "fastest", max_expansions=1)
    assert r.truncated is True
    assert r.reached is False and r.count == 0
    assert (r.expanded, r.generated) == (1, 2)
    # Interrupcao nao e poda de fronteira: o outro indicador fica inativo.
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)


# --------------------------------------------------------------------------
# S5 — limite de labels por chave corta (node, bucket) nao dominadas
# --------------------------------------------------------------------------
S5_GRAPH = make_graph([("a12a", 1, 2), ("a12b", 1, 2), ("a23", 2, 3)], (1, 2, 3))
S5_TABLE = {
    # A e B chegam ao no 2 no MESMO bucket (5 m/s) com trade-off: nem A domina B
    # nem B domina A sob (time, ascent).
    "a12a": {"time_s": 90.0, "distance_m": 1000.0, "ascent_m": 100.0, "energy_j": 2000.0, "final_speed_mps": 5.0},
    "a12b": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 50.0, "energy_j": 2500.0, "final_speed_mps": 5.0},
    "a23": {"time_s": 100.0, "distance_m": 1000.0, "ascent_m": 0.0, "energy_j": 1000.0, "final_speed_mps": 5.0},
}
S5_A = (("a12a", "a23"), 190.0, 2000.0, 100.0, 0.0, 3000.0, 5.0)
S5_B = (("a12b", "a23"), 200.0, 2000.0, 50.0, 0.0, 3500.0, 5.0)


def test_s5_without_cap_both_non_dominated_labels_survive():
    r = _search(S5_GRAPH, S5_TABLE, "fastest")
    assert r.count == 2 and _frontier(r) == [S5_A, S5_B]
    assert (r.expanded, r.generated) == (3, 4)
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)


def test_s5_max_labels_per_node_keeps_lexicographic_best():
    r = _search(S5_GRAPH, S5_TABLE, "fastest", max_labels_per_node=1)
    assert r.count == 1 and _frontier(r) == [S5_A]
    assert (r.expanded, r.generated) == (2, 3)
    # A poda da fronteira passa a ser **sinalizada** (nao silenciosa).
    assert r.truncated is False
    assert r.frontier_truncated is True
    assert r.dropped_labels >= 1


# --------------------------------------------------------------------------
# S6 — caracterizacao com o modelo fisico real (integracao)
# --------------------------------------------------------------------------
S6_GRAPH = make_graph(
    [("a12", 1, 2, 1000.0), ("a23", 2, 3, 1000.0), ("a14", 1, 4, 1500.0), ("a43", 4, 3, 1500.0)],
    (1, 2, 3, 4),
)
S6_TIME_S = 227.78513224729633
S6_ENERGY_J = 55237.89456996935
S6_FINAL_SPEED_MPS = 9.044378672338993


def test_s6_physical_model_characterization():
    model = PhysicalTraversalModel(
        PhysicalTraversal(),
        CyclistModel(mass_kg=75.0, max_power_w=800.0, sustainable_power_w=250.0, frontal_area_m2=0.5),
        BicycleModel(),
        Environment(),
    )
    astar = MultiObjectiveAStar(S6_GRAPH, model, profile_for("fastest"), initial_speed_mps=0.0)
    r = astar.search(1, 3)

    assert r.reached and r.count == 1
    assert (r.expanded, r.generated, r.truncated) == (3, 4, False)
    assert (r.frontier_truncated, r.dropped_labels) == (False, 0)
    label = r.labels[0]
    assert tuple(route_arcs(label)) == ("a12", "a23")
    assert label.distance_m == pytest.approx(2000.0)
    assert label.ascent_m == pytest.approx(0.0)
    assert label.time_s == pytest.approx(S6_TIME_S, rel=1e-6)
    assert label.energy_j == pytest.approx(S6_ENERGY_J, rel=1e-6)
    assert label.physical_state.speed_mps == pytest.approx(S6_FINAL_SPEED_MPS, rel=1e-6)


# --------------------------------------------------------------------------
# S7 — fluxo completo (busca + selecao) por perfil
# --------------------------------------------------------------------------
def test_s7_search_profile_end_to_end():
    response = search_profile(
        S1_GRAPH,
        StubTraversal(S1_TABLE),
        ProfileSearchRequest(
            origin=1,
            destination=3,
            profile=route_profile_for("fastest"),
            max_routes=3,
        ),
    )
    assert response.reached
    assert response.pareto_route_count == 2
    assert response.count == 2
    assert [r.route.arc_ids for r in response.routes] == [("a12", "a23"), ("a14", "a43")]
    assert response.selection.diversity_relaxed is False
    assert response.selection.dropped_duplicates == 0
    assert response.selection.threshold_used == pytest.approx(0.7)
    # A resposta de pipeline tambem expoe os sinais de completude.
    assert (response.truncated, response.frontier_truncated) == (False, False)
    assert response.dropped_labels == 0


# --------------------------------------------------------------------------
# Reprodutibilidade — duas execucoes produzem a mesma fronteira
# --------------------------------------------------------------------------
def test_baseline_is_reproducible():
    first = _frontier(_search(S1_GRAPH, S1_TABLE, "fastest"))
    second = _frontier(_search(S1_GRAPH, S1_TABLE, "fastest"))
    assert first == second
