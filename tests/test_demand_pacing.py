"""Demanda do terreno, politica de ritmo e o cartao de esforco.

Propriedades que sustentam o cartao "Esforco e terreno":

* a demanda e uma propriedade do terreno — cacheada por arco e cenario e
  **independente** de ``P_ref`` (trocar a potencia de referencia reusa os
  valores ja calculados);
* as faixas de demanda (``d1``..``d5``) sao monotonas e usam os cortes
  declarados;
* o RPE **nao e censurado**: alcanca 20 (Borg) quando a potencia relativa
  chega a 1.5x a referencia;
* a contagem de entradas em via arterial sai da classe OSM, nao de
  ``hw_stress``;
* o contrato carrega ``demand``/``telemetry`` e **nunca** inventa zonas
  cardiacas.
"""

from __future__ import annotations

import pytest

from router.app.planning import plan_offline_routes, reference_pacing_for
from router.contracts import (
    DEMAND_BAND_DEFINITION,
    INTENSITY_BAND_DEFINITION,
    INTENSITY_BAND_EDGES,
    INTENSITY_BANDS,
    dumps,
    loads,
)
from router.core.data.elevation import CallableElevation
from router.core.data.osm.build import BuildOptions, attach_elevation, build_graph
from router.core.data.osm.parse import OsmData
from router.core.physics import BicycleModel, CyclistModel
from router.core.physics.demand import (
    DEMAND_MODEL_VERSION,
    GRADE_BIN_LABELS,
    ArcDemandField,
    DemandScenario,
    grade_bin_index,
)
from router.core.physics.environment import Environment
from router.core.prediction.intensity import (
    DEMAND_BAND_NAMES,
    band_for_rpe,
    demand_band_for_ratio,
    rpe_label,
    segment_rpe,
)
from router.core.prediction.mechanical import RouteSegment
from router.core.prediction.rider import rider_model_from_cyclist
from router.core.prediction.traffic import arterial_entries

# --------------------------------------------------------------------------
# Grafo minimo: quadrado 1-2-4 / 1-3-4 com elevacao.
# --------------------------------------------------------------------------
_NETWORK = {
    "elements": [
        {"type": "node", "id": 1, "lat": 0.000, "lon": 0.000},
        {"type": "node", "id": 2, "lat": 0.002, "lon": 0.000},
        {"type": "node", "id": 3, "lat": 0.000, "lon": 0.002},
        {"type": "node", "id": 4, "lat": 0.002, "lon": 0.002},
        {"type": "node", "id": 99, "lat": 0.020, "lon": 0.020},
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


class TestDemandIsTerrain:
    def test_cache_reuses_value_for_same_arc_and_scenario(self):
        graph = _graph()
        bicycle = BicycleModel()
        field = ArcDemandField()
        scenario = DemandScenario.from_models(
            75.0, 0.5, bicycle, Environment(), 15.0 / 3.6
        )
        arc = next(iter(graph.arcs.values()))

        first = field.demand(arc, scenario)
        second = field.demand(arc, scenario)

        assert first is second, "mesmo arco e cenario devem reusar o valor"

    def test_reference_power_does_not_change_demand(self):
        graph = _graph()
        bicycle = BicycleModel()
        field = ArcDemandField()
        scenario = DemandScenario.from_models(
            75.0, 0.5, bicycle, Environment(), 15.0 / 3.6
        )
        arc = next(iter(graph.arcs.values()))
        demand = field.demand(arc, scenario)

        # P_ref nao entra no cenario: so a razao muda, o custo do terreno nao.
        assert demand.ratio_to(200.0) != demand.ratio_to(250.0)
        assert demand.mean_power_w == demand.ratio_to(200.0) * 200.0
        assert field.demand(arc, scenario) is demand


class TestDemandBands:
    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            (0.0, "d1"),
            (0.5, "d1"),
            (0.5001, "d2"),
            (0.8, "d2"),
            (0.81, "d3"),
            (1.25, "d3"),
            (1.26, "d4"),
            (2.0, "d4"),
            (2.01, "d5"),
            (5.0, "d5"),
        ],
    )
    def test_edges(self, ratio, expected):
        assert demand_band_for_ratio(ratio) == expected

    def test_monotonic_across_ratios(self):
        order = [demand_band_for_ratio(r / 100.0) for r in range(0, 401)]
        ranks = [DEMAND_BAND_NAMES.index(name) for name in order]
        assert ranks == sorted(ranks), "faixa de demanda deve ser monotona"

    def test_negative_ratio_is_rejected(self):
        with pytest.raises(ValueError):
            demand_band_for_ratio(-0.1)


class TestGradeBins:
    def test_descents_count_in_the_first_bin(self):
        assert grade_bin_index(-5.0) == 0
        assert grade_bin_index(0.0) == 0
        assert grade_bin_index(4.0) == 0
        assert grade_bin_index(4.1) == 1
        assert grade_bin_index(8.0) == 1
        assert grade_bin_index(8.1) == 2

    def test_labels_have_three_bins(self):
        assert len(GRADE_BIN_LABELS) == 3


class TestRpeIsNotCensored:
    def _rpe(self, ratio: float) -> float:
        rider = rider_model_from_cyclist(_cyclist(), None)
        power = ratio * rider.reference_power_w
        segment = RouteSegment(
            distance_m=1000.0,
            duration_s=60.0,
            rider_energy_j=power * 60.0,
            mechanical_energy_j=power * 60.0,
        )
        return segment_rpe(segment, rider=rider)

    def test_reference_power_is_seventeen(self):
        assert self._rpe(1.0) == pytest.approx(17.0)

    def test_hard_effort_reaches_twenty(self):
        assert self._rpe(1.2) == pytest.approx(19.0)
        assert self._rpe(1.5) == pytest.approx(20.0)

    def test_label_and_band_at_the_top(self):
        assert rpe_label(20.0) == "máximo"
        assert band_for_rpe(18.0) == "i5"


class TestArterialEntries:
    def test_counts_entries_not_arcs(self):
        assert (
            arterial_entries(("primary", "primary", "residential", "primary"))
            == 2
        )

    def test_none_is_not_arterial(self):
        assert arterial_entries((None, "residential", None)) == 0

    def test_empty(self):
        assert arterial_entries(()) == 0


class TestContractCard:
    def _plan(self, **kwargs):
        return plan_offline_routes(
            _graph(),
            ORIGIN,
            DESTINATION,
            **kwargs,
        ).to_contract()

    def test_paced_route_exposes_demand_and_telemetry(self):
        cyclist = _cyclist()
        pacing = reference_pacing_for(cyclist, reference_speed_kmh=15.0)
        plan = self._plan(
            cyclist=cyclist, bicycle=BicycleModel(), pacing=pacing
        )
        assert plan.routes
        route = plan.routes[0]

        assert route.demand is not None
        assert route.demand.band_definition == DEMAND_BAND_DEFINITION
        assert set(route.demand.distance_fraction_by_band) == set(DEMAND_BAND_NAMES)
        assert route.demand.scenario.demand_model == DEMAND_MODEL_VERSION
        assert route.demand.scenario.policy == "hold-reference-speed@2"

        telemetry = route.telemetry
        assert telemetry is not None
        assert telemetry.heart_rate_zones is None, "zonas cardiacas nunca inventadas"
        assert telemetry.rpe_band in INTENSITY_BANDS
        assert telemetry.w_prime_start_j == pytest.approx(pacing.w_prime_j)

    def test_paced_plan_round_trips_through_contract(self):
        cyclist = _cyclist()
        pacing = reference_pacing_for(cyclist, reference_speed_kmh=15.0)
        plan = self._plan(cyclist=cyclist, bicycle=BicycleModel(), pacing=pacing)

        assert loads(dumps(plan)).to_dict() == plan.to_dict()

    def test_without_pacing_the_card_is_absent(self):
        plan = self._plan(cyclist=_cyclist(), bicycle=BicycleModel())
        assert plan.routes
        route = plan.routes[0]
        assert route.demand is None
        assert route.telemetry is not None
        assert route.telemetry.policy == "constant-power"
        assert route.telemetry.w_prime_start_j is None


def test_contract_mirrors_the_core_intensity_bands():
    """O contrato e o nucleo declaram os mesmos cortes e a mesma definicao."""
    from router.core.prediction.intensity import (
        INTENSITY_BAND_DEFINITION as core_definition,
        INTENSITY_BAND_EDGES as core_edges,
    )

    assert INTENSITY_BAND_EDGES == core_edges
    assert INTENSITY_BAND_DEFINITION == core_definition
