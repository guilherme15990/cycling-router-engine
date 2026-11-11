"""Modos de execucao e regime regularizado (ADR-025, decisao 6).

A decisao 6 foi aceita com limitacao conhecida:

* dois modos de execucao — ``SEARCH`` (passo padrao 5 m, desempenho) e
  ``VALIDATION`` (passo padrao 1 m, refinamento). Os passos sao configuracoes
  explicitas, nao constantes universais de precisao;
* a regularizacao ``MIN_SPEED_MPS=0.5`` permanece como hipotese numerica e
  limitacao fisica conhecida. Quando ela e usada, o resultado o **expoe**
  (``used_min_speed_regularization``), para que a precisao nao seja
  superestimada;
* o modo ``VALIDATION`` **nao** garante alta fidelidade na partida do repouso
  enquanto persistir a limitacao de baixa velocidade — este modulo documenta
  isso em vez de mascara-lo.

Estes testes nao substituem o integrador: apenas caracterizam os modos e a
exposicao do regime regularizado.
"""

import json
import pytest
from router.interfaces.cli import main
from router.core.data.graph.model import Arc, ElevationSample, Node, RoutingGraph, Segment
from router.core.data.graph.serialize import save_json
from router.core.physics.physical import (
    BicycleModel,
    CyclistModel,
    Environment,
    MIN_SPEED_MPS,
    PhysicalTraversal,
    SimulationMode,
    SimulationState,
    DEFAULT_STEP_M,
    DEFAULT_STEP_M_BY_MODE,
    traverse_arc,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
MASS = CYCLIST.mass_kg + BICYCLE.mass_kg


def make_arc(points, *, arc_id="e0"):
    distance = points[-1][0]
    profile = tuple(
        ElevationSample(distance_m=d, elevation_m=e) for d, e in points
    )
    ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
    descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id="s0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


def traverse(points, *, step_m, v0=0.0, power_w=None, environment=None):
    return PhysicalTraversal(step_m=step_m).traverse(
        make_arc(points),
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(v0),
        environment=environment,
        power_w=power_w,
    )


# ==========================================================================
# Passos padrao por modo
# ==========================================================================
def test_mode_step_defaults_are_explicit_configs():
    assert DEFAULT_STEP_M_BY_MODE[SimulationMode.SEARCH] == DEFAULT_STEP_M
    assert DEFAULT_STEP_M_BY_MODE[SimulationMode.SEARCH] == 5.0
    assert DEFAULT_STEP_M_BY_MODE[SimulationMode.VALIDATION] == 1.0


def test_search_is_the_default_mode():
    traversal = PhysicalTraversal()
    assert traversal.mode is SimulationMode.SEARCH
    assert traversal.step_m == pytest.approx(DEFAULT_STEP_M_BY_MODE[SimulationMode.SEARCH])


def test_validation_mode_uses_finer_default_step():
    traversal = PhysicalTraversal(mode=SimulationMode.VALIDATION)
    assert traversal.mode is SimulationMode.VALIDATION
    assert traversal.step_m == pytest.approx(DEFAULT_STEP_M_BY_MODE[SimulationMode.VALIDATION])


def test_explicit_step_overrides_mode_default():
    traversal = PhysicalTraversal(step_m=2.5, mode=SimulationMode.VALIDATION)
    assert traversal.step_m == pytest.approx(2.5)
    assert traversal.mode is SimulationMode.VALIDATION


def test_mode_accepts_string_values():
    traversal = PhysicalTraversal(mode="validation")
    assert traversal.mode is SimulationMode.VALIDATION


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        PhysicalTraversal(mode="turbo")


def test_traverse_arc_shares_mode_semantics():
    arc = make_arc([(0.0, 0.0), (1000.0, 0.0)])
    search = traverse_arc(arc, CYCLIST, BICYCLE)
    validation = traverse_arc(arc, CYCLIST, BICYCLE, mode=SimulationMode.VALIDATION)
    # O modo muda o passo usado (e, portanto, o tempo no regime grosso).
    assert search.time_s != pytest.approx(validation.time_s, rel=1e-9)
    # O passo explicito volta a mandar no modo.
    overridden = traverse_arc(arc, CYCLIST, BICYCLE, step_m=5.0)
    assert overridden.time_s == pytest.approx(search.time_s, rel=1e-12)


# ==========================================================================
# Regime regularizado exposto no resultado
# ==========================================================================
def test_rest_start_flags_regularization():
    """Partir do repouso usa o piso de velocidade e o resultado o declara."""
    r = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=5.0, v0=0.0)
    assert r.used_min_speed_regularization is True
    assert r.to_dict()["used_min_speed_regularization"] is True


def test_smooth_start_does_not_flag_regularization():
    """Acima do piso, o passo suave nao usa a regularizacao."""
    r = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=5.0, v0=5.0)
    assert r.used_min_speed_regularization is False
    assert r.to_dict()["used_min_speed_regularization"] is False


def test_regularization_boundary_at_min_speed():
    below = traverse([(0.0, 0.0), (200.0, 0.0)], step_m=5.0, v0=MIN_SPEED_MPS * 0.9)
    at = traverse([(0.0, 0.0), (200.0, 0.0)], step_m=5.0, v0=MIN_SPEED_MPS)
    assert below.used_min_speed_regularization is True
    assert at.used_min_speed_regularization is False


def test_path_aggregates_regularization():
    """Um caminho regulariza se algum arco regularizou."""
    first = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="a")
    second = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="b")
    # Parte do repouso: o primeiro arco regulariza e o caminho o propaga.
    path = PhysicalTraversal(step_m=5.0).traverse_path(
        [first, second], CYCLIST, BICYCLE, initial_state=SimulationState.from_speed(0.0)
    )
    assert path.used_min_speed_regularization is True
    assert path.to_dict()["used_min_speed_regularization"] is True

    # Partindo acima do piso, nenhum arco regulariza.
    fast = PhysicalTraversal(step_m=5.0).traverse_path(
        [first, second], CYCLIST, BICYCLE, initial_state=SimulationState.from_speed(6.0)
    )
    assert fast.used_min_speed_regularization is False


def test_regularization_metadata_present_in_json():
    r = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=5.0, v0=0.0)
    payload = r.to_dict()
    assert "used_min_speed_regularization" in payload
    assert payload["used_min_speed_regularization"] is True


# ==========================================================================
# Comparacao entre modos: VALIDATION aproxima do refinamento fino
# ==========================================================================
def _flat_time(step_m, v0):
    return traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=step_m, v0=v0).time_s


def test_validation_is_closer_to_reference_than_search_smooth():
    """No regime suave, o modo VALIDATION fica mais perto da referencia."""
    reference = _flat_time(0.02, v0=5.0)
    err_search = abs(_flat_time(5.0, v0=5.0) - reference)
    err_validation = abs(_flat_time(1.0, v0=5.0) - reference)
    assert err_validation < err_search
    # E o erro do SEARCH permanece pequeno e documentado (< 0.2%).
    assert err_search / reference < 2e-3


def test_validation_from_rest_is_closer_but_limit_still_flagged():
    """Partida do repouso: VALIDATION aproxima, mas a limitacao persiste.

    O modo VALIDATION **nao** garante alta fidelidade na partida enquanto o
    modelo de baixa velocidade existir. Aqui apenas registramos que a
    regularizacao continua sendo usada e que o resultado se mantem numa banda
    modesta — nunca que esteja "validado" nesse regime.
    """
    reference = _flat_time(0.02, v0=0.0)
    coarse = _flat_time(5.0, v0=0.0)
    validation = _flat_time(1.0, v0=0.0)
    assert abs(validation - reference) < abs(coarse - reference)
    assert abs(validation - reference) / reference < 1e-2
    # A regularizacao e usada em ambos os modos na partida do repouso.
    assert traverse(
        [(0.0, 0.0), (1000.0, 0.0)], step_m=1.0, v0=0.0
    ).used_min_speed_regularization is True


def test_modes_differ_but_both_remain_first_order_smooth():
    """Os modos usam passos distintos; a engine permanece primeira ordem."""
    reference = _flat_time(0.02, v0=5.0)
    err_search = abs(_flat_time(5.0, v0=5.0) - reference)
    err_validation = abs(_flat_time(1.0, v0=5.0) - reference)
    # Razao ~5 (primeira ordem: 5x menor passo -> ~5x menos erro).
    assert 3.0 < err_search / err_validation < 8.0


# ==========================================================================
# Impacto do passo sobre a ordenacao de rotas candidatas
# ==========================================================================
def _candidates():
    # Candidato A: plano, mais rapido. Candidato B: ondulado, mais lento.
    a = make_arc([(0.0, 0.0), (1000.0, 0.0)], arc_id="A")
    b = make_arc(
        [(0.0, 0.0), (250.0, 12.0), (500.0, 0.0), (750.0, 12.0), (1000.0, 0.0)],
        arc_id="B",
    )
    return a, b


@pytest.mark.parametrize(
    "mode", [SimulationMode.SEARCH, SimulationMode.VALIDATION]
)
def test_candidate_ordering_is_stable_across_modes(mode):
    """A ordenacao de candidatos se mantem entre SEARCH e VALIDATION.

    O passo altera os tempos absolutos (~1.5% do grosso para o fino), mas nao
    inverte candidatos claramente separados. Aqui A (plano) e sempre mais rapido
    que B (ondulado) em ambos os modos.
    """
    a, b = _candidates()
    traversal = PhysicalTraversal(mode=mode)
    t_a = traversal.traverse(a, CYCLIST, BICYCLE).time_s
    t_b = traversal.traverse(b, CYCLIST, BICYCLE).time_s
    assert t_a < t_b


def test_step_does_not_flip_candidate_ordering():
    """Comparacao direta: SEARCH e VALIDATION escolhem o mesmo vencedor."""
    a, b = _candidates()
    search = PhysicalTraversal(mode=SimulationMode.SEARCH)
    validation = PhysicalTraversal(mode=SimulationMode.VALIDATION)

    t_a_search = search.traverse(a, CYCLIST, BICYCLE).time_s
    t_b_search = search.traverse(b, CYCLIST, BICYCLE).time_s
    t_a_validation = validation.traverse(a, CYCLIST, BICYCLE).time_s
    t_b_validation = validation.traverse(b, CYCLIST, BICYCLE).time_s

    # Mesmo vencedor (A) nos dois modos.
    assert t_a_search < t_b_search
    assert t_a_validation < t_b_validation

    # A margem permanece do mesmo lado e com ordem de grandeza comparavel.
    margin_search = (t_b_search - t_a_search) / t_a_search
    margin_validation = (t_b_validation - t_a_validation) / t_a_validation
    assert margin_search > 0.0
    assert margin_validation > 0.0
    assert 0.5 < margin_search / margin_validation < 2.0


# ==========================================================================
# CLI: o modo e uma configuracao explicita e visivel no JSON
# ==========================================================================
def test_cli_mode_validation_refines_and_flags_regularization(tmp_path, capsys):
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    graph.add_arc(make_arc([(0.0, 0.0), (1000.0, 0.0)], arc_id="e0"))
    path = save_json(graph, tmp_path / "graph.json")

    exit_code = main(
        ["physical", str(path), "--arc", "e0", "--mode", "validation", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    # Partida do repouso em modo de validacao: o regime regularizado e exposto.
    assert payload["used_min_speed_regularization"] is True
    # O modo de validacao usa passo de 1 m (tempo diferente do SEARCH de 5 m).
    assert payload["time_s"] == pytest.approx(128.5754, rel=1e-3)


def test_cli_rejects_unknown_mode(tmp_path):
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    graph.add_arc(make_arc([(0.0, 0.0), (100.0, 0.0)], arc_id="e0"))
    path = save_json(graph, tmp_path / "graph.json")

    with pytest.raises(SystemExit):
        main(["physical", str(path), "--arc", "e0", "--mode", "turbo"])
