"""Consulta offline completa: zona -> busca -> fisica -> ML -> classificacao.

Cobrem a orquestracao `planning.plan_offline_routes` (ADR-025 §10, ADR-026
§3/§5, ADR-027 §0/§3/§6) sobre um grafo ciclavel pequeno construido a partir de
OSM em memoria + elevacao local:

* a busca roda numa **zona** recortada do grafo (nunca o grafo inteiro) e o box
  final cobre os pinos e o tracado de todas as rotas;
* cada rota descoberta recebe fisica **completa**, `EffortFeatures`, previsao e
  classificacao;
* a ausencia de modelo calibrado e **explicita** (nunca um RPE inventado);
* um preditor calibrado (stub) produz RPE e a classificacao respeita a
  preferencia declarada, sem reordenar nada;
* o fluxo roda com a **rede desabilitada** (``offline``).
"""

from __future__ import annotations

import json
import pytest
from router.core.data.elevation import CallableElevation
from router.core.data.geo import bbox_contains, bbox_of
from router.core.evaluation import EffortPreference
from router.core.prediction.ml import UnavailablePredictor
from router.offline import is_network_disabled, offline
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics import BicycleModel, CyclistModel
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    PhysiologicalPrediction,
    PredictionStatus,
)
from router.app.planning import PlannedRoute, RoutePlanResult, plan_offline_routes

# --------------------------------------------------------------------------
# Grafo de teste: quadrado 1-2-4 / 1-3-4 com elevacao, mais um no distante.
# --------------------------------------------------------------------------
_NETWORK = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.000},
        {"type": "node", "id": 2, "lat": 0.002, "lon": 0.000},
        {"type": "node", "id": 3, "lat": 0.000, "lon": 0.002},
        {"type": "node", "id": 4, "lat": 0.002, "lon": 0.002},
        {"type": "node", "id": 99, "lat": 0.020, "lon": 0.020},  # fora do box
        {
            "type": "way",
            "id": 101,
            "nodes": [1, 2],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
        {
            "type": "way",
            "id": 102,
            "nodes": [2, 4],
            "tags": {"highway": "residential", "surface": "asphalt"},
        },
        {
            "type": "way",
            "id": 103,
            "nodes": [1, 3],
            "tags": {"highway": "residential", "surface": "gravel"},
        },
        {
            "type": "way",
            "id": 104,
            "nodes": [3, 4],
            "tags": {"highway": "residential", "surface": "gravel"},
        },
        {
            "type": "way",
            "id": 105,
            "nodes": [4, 99],
            "tags": {"highway": "residential"},
        },
    ]
}

ORIGIN = (0.000, 0.000)
DESTINATION = (0.002, 0.002)


def _graph():
    graph, _ = build_graph(OsmData.from_payload(_NETWORK), BuildOptions())
    attach_elevation(
        graph,
        CallableElevation(
            lambda lat, lon: 700.0 + 3000.0 * lat, resolution_m=30.0
        ),
        spacing_m=20.0,
    )
    return graph


def _cyclist() -> CyclistModel:
    return CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )


def _plan(**kwargs) -> RoutePlanResult:
    return plan_offline_routes(
        _graph(),
        ORIGIN,
        DESTINATION,
        cyclist=_cyclist(),
        bicycle=BicycleModel(),
        **kwargs,
    )


class _StubRpePredictor:
    """Preditor calibrado **de teste**: RPE fixo, sem zonas cardiacas."""

    model_version = "stub-rpe@1"
    borg_scale = DEFAULT_BORG_SCALE
    required_inputs: tuple[str, ...] = ()

    def __init__(self, rpe: float = 13.0) -> None:
        self._rpe = rpe

    def predict(self, features, rider, context) -> PhysiologicalPrediction:
        return PhysiologicalPrediction(
            borg_scale=DEFAULT_BORG_SCALE,
            predicted_rpe=self._rpe,
            heart_rate_zone_distribution=None,
            model_version=self.model_version,
            status=PredictionStatus.AVAILABLE,
            prediction_interval_s=features.duration_s,
        )


# --------------------------------------------------------------------------
# Descoberta basica
# --------------------------------------------------------------------------
def test_plan_discovers_connected_route_with_physics():
    plan = _plan()

    assert isinstance(plan, RoutePlanResult)
    assert plan.reached is True
    assert plan.count >= 1
    assert plan.profile_id == "fastest"

    for route in plan.routes:
        assert isinstance(route, PlannedRoute)
        assert route.candidate.metrics.distance_m > 0.0
        # Fisica completa: a avaliacao traz metricas coerentes com a rota.
        assert route.assessment.metrics.distance_m > 0.0
        assert route.assessment.outcome is not None
        assert route.classification.outcome is route.assessment.outcome


def test_plan_zone_is_smaller_than_whole_graph():
    plan = _plan()

    # O grafo carregado tem o no distante (99); a zona de busca nao o inclui.
    assert plan.graph_nodes > plan.zone_nodes
    assert plan.graph_arcs > plan.zone_arcs
    assert plan.zone_nodes >= 2
    assert plan.zone_arcs >= 1


def test_plan_bbox_contains_pins_and_covers_search_box():
    plan = _plan()

    pins_box = bbox_of([ORIGIN, DESTINATION])
    assert bbox_contains(plan.bbox, pins_box)
    # O box final (altimetria/exibicao) e a uniao do corredor com o tracado das
    # rotas devolvidas: sempre cobre o corredor em que a busca ocorreu.
    assert bbox_contains(plan.bbox, plan.search_bbox)


def test_plan_normalizes_profile_name():
    plan = _plan(profile="FASTEST")
    assert plan.profile_id == "fastest"


def test_plan_max_routes_limits_discovered_routes():
    full = _plan()
    limited = _plan(max_routes=1)
    assert limited.count == min(1, full.count)
    assert limited.count >= 1


def test_progressive_search_is_explicitly_incomplete():
    plan = _plan(
        search_mode="progressive",
        max_routes=1,
        progressive_max_similarity=0.0,
    )

    assert plan.count == 1
    assert plan.search_mode == "progressive"
    assert plan.search_complete is False
    assert plan.optimality_proven is False
    payload = plan.to_contract().to_dict()
    assert payload["search_mode"] == "progressive"
    assert payload["search_complete"] is False
    assert payload["optimality_proven"] is False


def test_plan_to_dict_is_json_serializable():
    payload = _plan().to_dict()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert '"route_count"' in encoded
    assert payload["reached"] is True


# --------------------------------------------------------------------------
# Ordem de eventos (ADR-028 D7): ML em toda rota, box por ultimo, recomendada
# --------------------------------------------------------------------------
def test_plan_marks_recommended_route():
    plan = _plan()
    assert plan.recommended is plan.routes[0]
    assert plan.routes[0].recommended is True
    assert all(route.recommended is False for route in plan.routes[1:])


def test_plan_recommended_route_id_in_dict():
    plan = _plan()
    payload = plan.to_dict()
    assert payload["recommended_route_id"] == plan.routes[0].route_id
    assert payload["routes"][0]["recommended"] is True


# --------------------------------------------------------------------------
# Completude da busca: indicadores preservados no relatorio e expostos na CLI
# --------------------------------------------------------------------------
def test_plan_to_dict_preserves_search_completeness_indicators():
    """O relatorio final (JSON) conserva os indicadores de completude."""
    payload = _plan().to_dict()
    # Sem caps configurados, nenhum limite atuou (nenhum sinal ligado).
    assert payload["truncated"] is False
    assert payload["frontier_truncated"] is False
    assert payload["dropped_labels"] == 0


def test_print_plan_warns_when_frontier_is_pruned(capsys):
    import dataclasses
    from router.interfaces.cli.render import print_plan

    pruned = dataclasses.replace(
        _plan(), frontier_truncated=True, dropped_labels=3
    )
    print_plan(pruned)
    out = capsys.readouterr().out
    # A poda da fronteira nao fica silenciosa na interface.
    assert "fronteira podada" in out
    assert "3 label" in out
    assert "max_labels_per_node" in out


def test_print_plan_warns_when_search_is_interrupted(capsys):
    import dataclasses
    from router.interfaces.cli.render import print_plan

    interrupted = dataclasses.replace(_plan(), truncated=True)
    print_plan(interrupted)
    out = capsys.readouterr().out
    assert "interrompida" in out
    assert "max_expansions" in out


def test_plan_every_route_receives_intensity_estimate():
    """Toda rota passa pela ML (estimativa de intensidade), sempre."""
    plan = _plan()

    assert plan.count >= 1
    for route in plan.routes:
        estimate = route.intensity
        assert estimate.calibrated is False
        assert estimate.is_measurement is False
        assert sum(estimate.band_distance_fraction.values()) == pytest.approx(1.0)
        assert estimate.band_duration_fraction is not None
        assert sum(estimate.band_duration_fraction.values()) == pytest.approx(1.0)
        # A estimativa nao preenche o contrato fisiologico.
        assert route.prediction.heart_rate_zone_distribution is None


def test_plan_intensity_present_without_predictor():
    plan = _plan()
    assert all(route.predicted_rpe is None for route in plan.routes)
    assert all(route.mean_rpe > 0.0 for route in plan.routes)


def test_plan_final_box_covers_search_zone_after_ml():
    """O box final (pos-ML) cobre a zona buscada e os pinos."""
    plan = _plan()
    assert bbox_contains(plan.bbox, plan.search_bbox)
    assert bbox_contains(plan.bbox, bbox_of([ORIGIN, DESTINATION]))


def test_plan_recommended_with_max_routes_single():
    limited = _plan(max_routes=1)
    assert limited.count == 1
    assert limited.recommended is limited.routes[0]
    assert limited.recommended.recommended is True


def test_plan_collapse_similar_uses_route_set_selection(monkeypatch):
    """O E2E passa as candidatas pela selecao estrutural (``route_set``)."""
    import router.app.planning as planning
    from router.core.search.route_set import METHOD_FARTHEST_FIRST, RouteSetSelection

    seen: list[int | None] = []

    def only_best(routes, *, max_routes=None, similarity=None):
        seen.append(max_routes)
        return RouteSetSelection(
            routes=(routes[0],), indices=(0,), method=METHOD_FARTHEST_FIRST
        )

    monkeypatch.setattr(planning, "select_distinct_routes", only_best)
    plan = _plan()
    assert seen == [None]  # colapso ligado por padrao
    assert plan.count == 1  # so a "pick" sobreviveu
    assert plan.recommended is plan.routes[0]


def test_plan_collapse_similar_false_bypasses_selection(monkeypatch):
    """``collapse_similar=False`` mantem a fronteira inteira (comparacao)."""
    import router.app.planning as planning

    def never_called(*args, **kwargs):  # pragma: no cover - falha se chamado
        raise AssertionError("select_distinct_routes nao deveria ser chamado")

    monkeypatch.setattr(planning, "select_distinct_routes", never_called)
    plan = _plan(collapse_similar=False)
    assert plan.count == plan.pareto_route_count
    assert plan.count >= 1


def test_plan_default_collapse_never_adds_routes():
    """O colapso estrutural so pode reduzir (nunca inventa rotas)."""
    default = _plan()
    raw = _plan(collapse_similar=False)
    assert default.count <= raw.count
    assert default.count <= default.pareto_route_count
    # A recomendada (primeira elegivel na ordem) e a mesma nos dois modos.
    assert default.recommended.route_id == raw.recommended.route_id



# --------------------------------------------------------------------------
# Honestidade da previsao
# --------------------------------------------------------------------------
def test_plan_without_predictor_declares_unavailable():
    plan = _plan()

    for route in plan.routes:
        assert route.predicted_rpe is None
        assert route.prediction_status is PredictionStatus.UNAVAILABLE
        assert route.outcome.value == "physical_only"
        assert "physiology_unavailable" in route.assessment.limitations
    assert plan.physiological_count == 0


def test_plan_with_uncalibrated_predictor_is_explicit():
    plan = _plan(predictor=UnavailablePredictor())

    for route in plan.routes:
        assert route.predicted_rpe is None
        assert route.prediction_status is PredictionStatus.MODEL_NOT_CALIBRATED
        assert "physiology_model_not_calibrated" in route.assessment.limitations


def test_plan_with_calibrated_predictor_predicts_rpe():
    plan = _plan(predictor=_StubRpePredictor(rpe=13.0))

    assert plan.count >= 1
    assert plan.physiological_count == plan.count
    for route in plan.routes:
        assert route.predicted_rpe == pytest.approx(13.0)
        assert route.prediction_status is PredictionStatus.AVAILABLE
        assert route.outcome.value == "physiological"
        # Zonas cardiacas NAO sao previstas (permanecem ausentes).
        assert route.prediction.heart_rate_zone_distribution is None


def test_plan_classification_respects_declared_preference():
    fitted = _plan(
        predictor=_StubRpePredictor(rpe=13.0),
        preference=EffortPreference(acceptable_rpe=(10.0, 15.0)),
    )
    assert all(route.classification.fits_preference is True for route in fitted.routes)

    missed = _plan(
        predictor=_StubRpePredictor(rpe=13.0),
        preference=EffortPreference(acceptable_rpe=(15.0, 18.0)),
    )
    assert all(
        route.classification.fits_preference is False for route in missed.routes
    )


# --------------------------------------------------------------------------
# Parametros invalidos (erro explicito, nunca mascarado)
# --------------------------------------------------------------------------
def test_plan_unknown_profile_raises():
    with pytest.raises(ValueError):
        _plan(profile="turbo")


@pytest.mark.parametrize("value", [0, -1, True])
def test_plan_invalid_max_routes_raises(value):
    with pytest.raises(ValueError):
        _plan(max_routes=value)


# --------------------------------------------------------------------------
# Offline (sem rede)
# --------------------------------------------------------------------------
def test_plan_runs_with_network_disabled():
    with offline():
        assert is_network_disabled()
        plan = _plan(predictor=_StubRpePredictor())
    assert plan.count >= 1
    assert not is_network_disabled()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _saved_graph(tmp_path):
    from router.core.data.graph.serialize import save_json

    path = tmp_path / "graph.json"
    save_json(_graph(), str(path))
    return path


def test_cli_plan_json(tmp_path, capsys):
    from router.interfaces.cli import main

    path = _saved_graph(tmp_path)
    code = main(
        [
            "plan",
            str(path),
            "--origin",
            "0.0,0.0",
            "--destination",
            "0.002,0.002",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["format"] == "roteamento.cycling-route-plan"
    assert payload["version"] == 1
    assert payload["reached"] is True
    assert payload["route_count"] >= 1
    assert payload["zone"]["arcs"] < payload["zone"]["graph_arcs"]


def test_cli_plan_text_output(tmp_path, capsys):
    from router.interfaces.cli import main

    path = _saved_graph(tmp_path)
    code = main(
        [
            "plan",
            str(path),
            "--origin",
            "0.0,0.0",
            "--destination",
            "0.002,0.002",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "rotas descobertas" in captured.out
    assert "zona de busca" in captured.out
    # A interface expoe o estado de seguranca e rotula a intensidade como
    # nao calibrada (ADR-029 D2/D6, ADR-028).
    assert "seguranca:" in captured.out
    assert "intensidade (nao calibrada)" in captured.out
    assert "[recomendada]" in captured.out


def test_cli_plan_rpe_bounds_must_be_paired(tmp_path, capsys):
    from router.interfaces.cli import main

    path = _saved_graph(tmp_path)
    code = main(
        [
            "plan",
            str(path),
            "--origin",
            "0.0,0.0",
            "--destination",
            "0.002,0.002",
            "--rpe-min",
            "10",
        ]
    )
    assert code == 2
    assert "--rpe-min" in capsys.readouterr().err


def test_cli_plan_model_args_must_be_paired(tmp_path, capsys):
    from router.interfaces.cli import main

    path = _saved_graph(tmp_path)
    code = main(
        [
            "plan",
            str(path),
            "--origin",
            "0.0,0.0",
            "--destination",
            "0.002,0.002",
            "--model-store",
            str(tmp_path / "models"),
        ]
    )
    assert code == 2
    assert "--model-version" in capsys.readouterr().err
