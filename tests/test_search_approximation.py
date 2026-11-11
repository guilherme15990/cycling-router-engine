"""Estimador aproximado versus simulador completo (ADR-025, incremento 8).

Cobertura exigida pelo incremento:

* contrato de ``TraversalModel`` e reuso da engine (nenhum parametro novo);
* o estimador **nao** declara teto de velocidade (busca admissivel sem presumir
  a qualidade do estimador);
* erro de tempo, energia e velocidade final, e custo computacional;
* caracterizacao honesta: o estimador pode subestimar **ou** superestimar o
  tempo (nao e um limite inferior garantido);
* impacto sobre a ordenacao das rotas (fronteira e ordem), sem presumir
  admissibilidade da heuristica;
* o estimador nao altera a fisica nem os resultados do simulador completo.
"""

import json
import pytest
from router.core.data.graph.model import Arc, ElevationSample, Node, RoutingGraph, Segment
from router.core.physics import (
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    SimulationState,
    SimulationStatus,
)
from router.core.search import (
    UNKNOWN_SPEED,
    MultiObjectiveAStar,
    TraversalModel,
    compare_rankings,
    compare_search_rankings,
    compare_traversals,
    estimate_arc,
    model_max_speed,
)
from router.core.search.approximate import (
    ArcEstimateError,
    ComparisonReport,
    RankingComparison,
    SearchRankingComparison,
    SteadyStateTraversalModel,
)

CYCLIST = CyclistModel(
    mass_kg=75.0,
    max_power_w=800.0,
    sustainable_power_w=250.0,
    frontal_area_m2=0.5,
)
BICYCLE = BicycleModel()
ENVIRONMENT = Environment()


# --------------------------------------------------------------------------
# Infra de teste: arcos com elevacao e grafo pequeno
# --------------------------------------------------------------------------
def make_arc(
    arc_id: str,
    points,
    *,
    source: int = 1,
    target: int = 2,
    max_speed_kmh: float | None = None,
) -> Arc:
    """Arco a partir de uma lista de ``(distancia_m, elevacao_m)``."""
    distance = points[-1][0]
    profile = tuple(
        ElevationSample(distance_m=d, elevation_m=e) for d, e in points
    )
    ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
    descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        max_speed_kmh=max_speed_kmh,
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def flat_arc(arc_id="flat", distance_m=300.0, **kwargs) -> Arc:
    return make_arc(arc_id, [(0.0, 100.0), (distance_m, 100.0)], **kwargs)


def build_graph(specs) -> RoutingGraph:
    """Grafo a partir de ``(id, origem, destino, distancia, ganho, limite)``.

    ``ganho`` positivo e subida; negativo e descida.
    """
    graph = RoutingGraph()
    node_ids = sorted(
        {node for spec in specs for node in (spec[1], spec[2])}
    )
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.001 * node_id))
    for arc_id, source, target, distance, rise, max_kmh in specs:
        graph.add_arc(
            make_arc(
                arc_id,
                [(0.0, 100.0), (float(distance), 100.0 + float(rise))],
                source=source,
                target=target,
                max_speed_kmh=max_kmh,
            )
        )
    return graph


# Caso deterministico medido: o estimador **contrai** a fronteira (3 -> 2 rotas),
# mantendo a rota preferida.
FRONTIER_CONTRACTION_SPECS = [
    ("a0", 1, 4, 100, 0, None),
    ("a1", 1, 2, 100, 40, None),
    ("a2", 2, 4, 150, 0, None),
    ("a3", 1, 3, 100, -80, 20),
    ("a4", 3, 4, 250, -20, 30),
]

# Caso deterministico medido: mesma fronteira de rotas, mas com **inversao** de
# ordem e troca da rota preferida.
ORDER_INVERSION_SPECS = [
    ("a0", 1, 4, 240, 80, None),
    ("a1", 1, 2, 240, 0, 30),
    ("a2", 2, 4, 120, 40, None),
    ("a3", 1, 3, 200, 40, None),
    ("a4", 3, 4, 200, -20, None),
]


def steady_model(**overrides) -> SteadyStateTraversalModel:
    base = dict(cyclist=CYCLIST, bicycle=BICYCLE, environment=ENVIRONMENT)
    base.update(overrides)
    return SteadyStateTraversalModel(**base)


# ==========================================================================
# Contrato do estimador (TraversalModel)
# ==========================================================================
def test_steady_state_model_satisfies_traversal_model():
    model = steady_model()
    assert isinstance(model, TraversalModel)
    assert model.mass_kg == pytest.approx(
        CYCLIST.mass_kg + BICYCLE.mass_kg
    )


def test_steady_state_model_traverse_matches_estimate_arc():
    arc = flat_arc()
    model = steady_model(power_w=150.0)
    via_model = model.traverse(arc, 0.0)
    direct = estimate_arc(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(0.0),
        environment=ENVIRONMENT,
        power_w=150.0,
    )
    assert via_model.time_s == pytest.approx(direct.time_s)
    assert via_model.final_speed_mps == pytest.approx(direct.final_speed_mps)


def test_estimator_does_not_declare_speed_ceiling():
    # Sem ``max_speed_mps`` -> teto **desconhecido** (nao "ilimitado").
    model = steady_model()
    assert not hasattr(model, "max_speed_mps")
    assert model_max_speed(model) is UNKNOWN_SPEED


def test_estimator_search_uses_weak_but_admissible_time_bound():
    graph = build_graph([("e0", 1, 2, 200, 0, None)])
    astar = MultiObjectiveAStar(graph, steady_model(), "fastest")
    # Teto desconhecido -> limite de tempo da heuristica padrao cai para 0.
    assert astar.heuristic.max_speed_mps is None

    result = astar.search(1, 2)
    assert result.reached
    assert result.labels[0].time_s > 0.0


def test_estimate_arc_rejects_invalid_power():
    arc = flat_arc()
    with pytest.raises(ValueError):
        estimate_arc(arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=-1.0)
    with pytest.raises(ValueError):
        estimate_arc(
            arc,
            CYCLIST,
            BICYCLE,
            environment=ENVIRONMENT,
            power_w=CYCLIST.max_power_w + 1.0,
        )


def test_estimate_arc_returns_valid_and_accumulated_state():
    arc = flat_arc()
    state_in = SimulationState.from_speed(0.0)
    result = estimate_arc(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=state_in,
        environment=ENVIRONMENT,
        power_w=150.0,
    )
    assert result.distance_m == pytest.approx(300.0)
    assert result.time_s > 0.0
    assert result.final_speed_mps >= 0.0
    assert result.mechanical_energy_j >= 0.0
    assert result.arc_id == "flat"
    # O estado de saida acumula tempo e trabalho a partir da entrada.
    assert result.state.elapsed_s == pytest.approx(result.time_s)
    assert result.state.rider_work_j == pytest.approx(result.rider_energy_j)
    assert result.state.wheel_work_j == pytest.approx(
        result.mechanical_energy_j
    )


# ==========================================================================
# Fidelidade a engine (sem fisica nova)
# ==========================================================================
def test_estimate_respects_segment_speed_limit():
    arc = make_arc("lim", [(0.0, 100.0), (400.0, 60.0)], max_speed_kmh=20.0)
    estimate = estimate_arc(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=0.0
    )
    reference = PhysicalTraversal(step_m=5.0).traverse(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=0.0
    )
    limit_mps = 20.0 / 3.6
    assert estimate.final_speed_mps <= limit_mps + 1e-9
    # Na descida limitada, o estimador coincide com o simulador no teto.
    assert estimate.final_speed_mps == pytest.approx(
        reference.final_speed_mps, abs=1e-6
    )


def test_stalled_arc_flags_min_speed_regularization():
    steep = make_arc("steep", [(0.0, 100.0), (50.0, 150.0)])
    result = estimate_arc(
        steep, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=5.0
    )
    assert result.stalled is True
    assert result.used_min_speed_regularization is True
    # O tempo usa o piso de velocidade (50 m / 0.5 m/s = 100 s).
    assert result.time_s == pytest.approx(100.0)


def test_status_reflects_missing_elevation():
    # Segmento sem amostras de elevacao: o status propaga da engine.
    segment = Segment(id="ne:0", distance_m=200.0)
    arc = Arc(id="ne", source=1, target=2, segments=(segment,))
    result = estimate_arc(arc, CYCLIST, BICYCLE, environment=ENVIRONMENT)
    assert result.result_status is SimulationStatus.DEGRADED


# ==========================================================================
# Caracterizacao do erro (nao presumption de admissibilidade)
# ==========================================================================
def test_flat_from_rest_underestimates_time():
    arc = flat_arc(distance_m=200.0)
    reference = PhysicalTraversal(step_m=5.0).traverse(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=200.0
    )
    estimate = estimate_arc(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=200.0
    )
    # Partindo do repouso, o estimador ignora a aceleracao -> subestima o tempo.
    assert estimate.time_s < reference.time_s


def test_high_entry_speed_overestimates_time():
    arc = flat_arc(distance_m=300.0)
    state_in = SimulationState.from_speed(14.0)
    reference = PhysicalTraversal(step_m=5.0).traverse(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=state_in,
        environment=ENVIRONMENT,
        power_w=100.0,
    )
    estimate = estimate_arc(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=state_in,
        environment=ENVIRONMENT,
        power_w=100.0,
    )
    # Entrando rapido, o estimador ignora a velocidade de entrada -> SUPERESTIMA
    # o tempo. Prova que o custo do estimador NAO e um limite inferior garantido.
    assert estimate.time_s > reference.time_s


def test_estimate_ignores_entry_speed_limitation():
    arc = flat_arc(distance_m=300.0)
    from_rest = estimate_arc(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(0.0),
        environment=ENVIRONMENT,
        power_w=100.0,
    )
    from_speed = estimate_arc(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(14.0),
        environment=ENVIRONMENT,
        power_w=100.0,
    )
    # Limitacao conhecida e documentada: o estimador e **independente do estado**.
    assert from_rest.time_s == pytest.approx(from_speed.time_s)
    assert from_rest.final_speed_mps == pytest.approx(from_speed.final_speed_mps)

    ref_rest = PhysicalTraversal(step_m=5.0).traverse(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=100.0
    )
    ref_speed = PhysicalTraversal(step_m=5.0).traverse(
        arc,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(14.0),
        environment=ENVIRONMENT,
        power_w=100.0,
    )
    # O simulador completo, ao contrario, depende do estado de entrada.
    assert ref_rest.time_s != pytest.approx(ref_speed.time_s)


def test_energy_error_tracks_time_error_when_pedaling():
    arc = flat_arc(distance_m=300.0)
    report = compare_traversals(
        [arc], CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    error = report.arcs[0]
    # Em regime de pedalada constante, E = P * t -> o erro de energia acompanha
    # o erro de tempo.
    assert error.time_rel_error is not None
    assert error.energy_rel_error == pytest.approx(
        error.time_rel_error, rel=1e-9, abs=1e-12
    )


# ==========================================================================
# Relatorio de erro e custo
# ==========================================================================
def test_compare_traversals_reports_error_and_cost():
    arcs = [
        flat_arc("f", distance_m=240.0),
        make_arc("c", [(0.0, 100.0), (200.0, 160.0)]),
    ]
    report = compare_traversals(
        arcs, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    assert isinstance(report, ComparisonReport)
    assert report.count == 2
    assert report.estimate_steps == 2  # 1 passo por arco
    assert report.reference_steps > report.estimate_steps
    assert report.step_reduction_factor > 1.0
    assert report.max_final_speed_abs_error >= 0.0
    for arc_error in report.arcs:
        assert isinstance(arc_error, ArcEstimateError)
        assert arc_error.estimate_steps == 1
        assert arc_error.reference_steps >= 1


def test_compare_traversals_empty_is_neutral():
    report = compare_traversals([], CYCLIST, BICYCLE, environment=ENVIRONMENT)
    assert report.count == 0
    assert report.reference_steps == 0
    assert report.estimate_steps == 0
    assert report.step_reduction_factor is None
    assert report.max_time_rel_error is None
    assert report.max_energy_rel_error is None
    assert report.max_final_speed_abs_error == 0.0


def test_comparison_report_is_json_serializable():
    report = compare_traversals(
        [flat_arc()], CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    payload = report.to_dict()
    json.dumps(payload)
    assert payload["count"] == 1
    assert "arcs" in payload
    assert payload["arcs"][0]["arc_id"] == "flat"


# ==========================================================================
# Metrica de ordenacao (por chave + por valor)
# ==========================================================================
def test_compare_rankings_detects_inversion():
    reference = [("A", (1.0, 10.0)), ("B", (2.0, 5.0))]
    estimate = [("B", (2.0, 5.0)), ("A", (1.0, 10.0))]
    ranking = compare_rankings(reference, estimate)
    assert isinstance(ranking, RankingComparison)
    assert ranking.inverted_pairs == 1
    assert ranking.total_shared_pairs == 1
    assert ranking.identical_order is False
    assert ranking.same_key_set is True
    assert ranking.order_agreement == pytest.approx(0.0)
    assert ranking.keys_value_changed == 0


def test_compare_rankings_preserves_order_and_reports_value_change():
    reference = [("A", (1.0, 10.0)), ("B", (2.0, 5.0))]
    estimate = [("A", (1.5, 12.0)), ("B", (2.5, 6.0))]
    ranking = compare_rankings(reference, estimate)
    assert ranking.inverted_pairs == 0
    assert ranking.identical_order is True
    assert ranking.order_agreement == pytest.approx(1.0)
    assert ranking.keys_value_changed == 2
    assert ranking.max_objective_delta == pytest.approx(2.0)


def test_compare_rankings_detects_key_set_difference():
    reference = [("A", (1.0,)), ("B", (2.0,))]
    estimate = [("A", (1.0,)), ("C", (3.0,))]
    ranking = compare_rankings(reference, estimate)
    assert ranking.same_key_set is False
    assert ranking.reference_count == 2
    assert ranking.estimate_count == 2
    assert ranking.total_shared_pairs == 0
    assert ranking.order_agreement is None


def test_compare_rankings_tolerance_suppresses_small_deltas():
    reference = [("A", (1.0,))]
    estimate = [("A", (1.0 + 1e-9,))]
    ranking = compare_rankings(reference, estimate, tolerance=1e-6)
    assert ranking.keys_value_changed == 0
    assert ranking.identical_order is True


# ==========================================================================
# Impacto sobre a ordenacao das rotas (busca completa)
# ==========================================================================
def test_search_ranking_preserves_top_route_and_contracts_frontier():
    graph = build_graph(FRONTIER_CONTRACTION_SPECS)
    comparison = compare_search_rankings(
        graph, 1, 4, "balanced", CYCLIST, BICYCLE, environment=ENVIRONMENT
    )
    assert isinstance(comparison, SearchRankingComparison)
    assert comparison.profile_id == "balanced"
    assert comparison.ranking.reference_count == 3
    assert comparison.ranking.estimate_count == 2
    assert comparison.same_route_set is False
    assert comparison.same_top_route is True
    assert comparison.ranking.max_objective_delta > 0.0
    # A fronteira do estimador contem apenas 2 das 3 rotas.
    assert comparison.ranking.same_key_set is False


def test_search_ranking_detects_inversion_and_top_route_flip():
    graph = build_graph(ORDER_INVERSION_SPECS)
    comparison = compare_search_rankings(
        graph, 1, 4, "balanced", CYCLIST, BICYCLE, environment=ENVIRONMENT
    )
    assert comparison.ranking.reference_count == 3
    assert comparison.ranking.estimate_count == 3
    assert comparison.same_route_set is True
    # O estimador troca a rota preferida e inverte um par.
    assert comparison.same_top_route is False
    assert comparison.ranking.inverted_pairs == 1
    assert comparison.ranking.total_shared_pairs == 3
    assert comparison.ranking.order_agreement == pytest.approx(2.0 / 3.0)


def test_search_ranking_comparison_is_json_serializable():
    graph = build_graph(ORDER_INVERSION_SPECS)
    comparison = compare_search_rankings(
        graph, 1, 4, "balanced", CYCLIST, BICYCLE, environment=ENVIRONMENT
    )
    json.dumps(comparison.to_dict())


def test_search_ranking_is_reproducible():
    graph = build_graph(ORDER_INVERSION_SPECS)
    first = compare_search_rankings(
        graph, 1, 4, "balanced", CYCLIST, BICYCLE, environment=ENVIRONMENT
    )
    second = compare_search_rankings(
        graph, 1, 4, "balanced", CYCLIST, BICYCLE, environment=ENVIRONMENT
    )
    assert first.to_dict() == second.to_dict()


# ==========================================================================
# Nao-regressao: o estimador nao altera o simulador completo
# ==========================================================================
def test_comparison_does_not_mutate_reference_results():
    arc = flat_arc()
    before = PhysicalTraversal(step_m=5.0).traverse(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    # Rodar o estimador e o relatorio no meio nao muda o resultado do simulador.
    estimate_arc(arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0)
    compare_traversals(
        [arc], CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    after = PhysicalTraversal(step_m=5.0).traverse(
        arc, CYCLIST, BICYCLE, environment=ENVIRONMENT, power_w=150.0
    )
    assert before == after
