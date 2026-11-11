"""Testes do perfil ``Elétrico``: perfil generico + modelo energetico separado.

O perfil define *o que otimizar* (energia e tempo). O modelo energetico define
*como estimar o consumo*. A bateria e opcional e nao participa da busca.
"""

import pytest

from graph.model import RoutingGraph, Arc, Segment, Node
from physical import (
    BatteryConfig,
    EnergyModel,
    GenericEnergyConfig,
    TraversalResult,
)
from search import (
    PROFILE_CONFIG,
    Objective,
    ProfileSearchRequest,
    profile_for,
    route_profile_for,
    search_profile,
)

MASS_KG = 87.0


# --------------------------------------------------------------------------
# Configuracao do perfil
# --------------------------------------------------------------------------
def test_electric_profile_is_energy_then_time():
    profile = route_profile_for("electric")
    assert profile.objectives == (Objective.ENERGY, Objective.TIME)
    assert profile.primary is Objective.ENERGY
    assert profile.title == "Elétrico"
    assert PROFILE_CONFIG["electric"] is profile


def test_electric_search_profile_matches_selection_view():
    selection = route_profile_for("electric")
    search = profile_for("electric")
    assert search.names == ("energy_j", "time_s")
    assert selection.to_search_profile().names == search.names


# --------------------------------------------------------------------------
# Pipeline: estimativa energetica pos-selecao
# --------------------------------------------------------------------------
def make_arc(arc_id: str, source: int, target: int) -> Arc:
    segment = Segment(id=f"{arc_id}:0", distance_m=100.0)
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


class StubTraversal:
    """Travessia sintetica com energia mecanica conhecida por arco."""

    def __init__(self, table, mass_kg: float = MASS_KG) -> None:
        self.table = table
        self._mass_kg = mass_kg

    @property
    def mass_kg(self) -> float:
        return self._mass_kg

    def traverse(self, arc: Arc, initial_speed_mps: float) -> TraversalResult:
        spec = self.table[arc.id]
        energy = spec.get("energy_j", 1000.0)
        return TraversalResult(
            time_s=spec.get("time_s", 10.0),
            distance_m=spec.get("distance_m", arc.distance_m),
            ascent_m=spec.get("ascent_m", 0.0),
            descent_m=0.0,
            initial_speed_mps=float(initial_speed_mps),
            final_speed_mps=spec.get("final_speed_mps", 5.0),
            mechanical_energy_j=energy,
            rider_energy_j=energy / 0.97,
            arc_id=arc.id,
        )


def _tradeoff_graph() -> RoutingGraph:
    graph = RoutingGraph()
    for node_id in (1, 2, 3):
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.001 * node_id))
    for spec in (("direct", 1, 3), ("b1", 1, 2), ("b2", 2, 3)):
        graph.add_arc(make_arc(*spec))
    return graph


def _tradeoff_traversal() -> StubTraversal:
    # ``direct`` e mais rapido porem consome mais energia; ``b1``+``b2``
    # consomem menos energia porem levam mais tempo -> duas rotas nao dominadas.
    return StubTraversal(
        {
            "direct": {"time_s": 100.0, "distance_m": 1200.0, "energy_j": 5000.0},
            "b1": {"time_s": 80.0, "distance_m": 400.0, "energy_j": 1000.0},
            "b2": {"time_s": 70.0, "distance_m": 500.0, "energy_j": 1200.0},
        }
    )


def _request(**kwargs) -> ProfileSearchRequest:
    return ProfileSearchRequest(
        origin=1,
        destination=3,
        profile=route_profile_for("electric"),
        max_routes=2,
        **kwargs,
    )


def test_electric_prioritizes_lowest_energy_route():
    response = search_profile(_tradeoff_graph(), _tradeoff_traversal(), _request())

    assert response.reached is True
    assert response.count == 2
    # A rota de menor energia entra primeiro, mesmo sem ser a mais rapida.
    assert response.routes[0].route.arc_ids == ("b1", "b2")
    assert response.routes[1].route.arc_ids == ("direct",)


def test_electric_reports_generic_energy_estimates():
    response = search_profile(_tradeoff_graph(), _tradeoff_traversal(), _request())

    assert len(response.energy) == response.count
    first = response.energy[0]
    assert first.route_id == response.routes[0].route_id
    assert first.level == "generic"
    # Energia mecanica de b1+b2 e 2200 J; a eletrica aplica 1/eta (eta padrao).
    assert first.mechanical_energy_j == pytest.approx(2200.0)
    assert first.electrical_energy_j == pytest.approx(
        2200.0 / GenericEnergyConfig().drivetrain_efficiency
    )
    assert first.electrical_energy_wh == pytest.approx(
        first.electrical_energy_j / 3600.0
    )
    # Sem bateria configurada, nao ha autonomia nem percentual.
    assert first.battery is None


def test_electric_accepts_configured_model():
    model = EnergyModel.configured(
        GenericEnergyConfig(drivetrain_efficiency=0.5)
    )
    response = search_profile(
        _tradeoff_graph(), _tradeoff_traversal(), _request(energy_model=model)
    )

    first = response.energy[0]
    assert first.level == "personalized"
    assert first.electrical_energy_j == pytest.approx(2200.0 / 0.5)


def test_electric_battery_feasibility_when_configured():
    battery = BatteryConfig(capacity_wh=10.0, reserve_ratio=0.0)
    response = search_profile(
        _tradeoff_graph(), _tradeoff_traversal(), _request(battery=battery)
    )

    first = response.energy[0]
    assert first.battery is not None
    assert first.battery.feasible is True
    assert first.battery.to_dict()["feasible"] is True


def test_electric_battery_reports_insufficient_energy():
    battery = BatteryConfig(capacity_wh=0.5, reserve_ratio=0.1)
    response = search_profile(
        _tradeoff_graph(), _tradeoff_traversal(), _request(battery=battery)
    )

    first = response.energy[0]
    assert first.battery is not None
    assert first.battery.feasible is False
    assert first.battery.shortfall_wh > 0.0


def test_non_electric_profile_has_no_energy_estimates():
    request = ProfileSearchRequest(
        origin=1,
        destination=3,
        profile=route_profile_for("fastest"),
        max_routes=2,
    )
    response = search_profile(_tradeoff_graph(), _tradeoff_traversal(), request)

    assert response.energy == ()
    assert response.to_dict()["energy"] == []
