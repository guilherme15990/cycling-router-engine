import json
import pytest
from router.interfaces.cli import main
from router.core.data.graph.model import (
    BicycleAccess,
    RoutingGraph,
    Arc,
    Segment,
    ElevationSample,
    Node,
)
from router.core.data.graph.serialize import save_json
from router.core.data.graph.surface import Surface, SurfaceStatus
from router.core.physics.physical import (
    BicycleModel,
    CyclistModel,
    ArcProfile,
    Environment,
    PhysicalTraversal,
    RouteMetrics,
    SimulationState,
    aerodynamic_force_n,
    equilibrium_speed_mps,
    forces_at,
    traverse_arc,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def state(speed_mps: float) -> SimulationState:
    """Estado dinamico de entrada com trabalho zerado (ADR-025)."""
    return SimulationState.from_speed(speed_mps)


def make_arc(
    points: list[tuple[float, float]] | None = None,
    *,
    distance_m: float | None = None,
    arc_id: str = "e0",
    surface: Surface = Surface.ASPHALT,
    max_speed_kmh: float | None = None,
    dismount: bool = False,
) -> Arc:
    if points is None:
        if distance_m is None:
            distance_m = 100.0
        profile: tuple[ElevationSample, ...] = ()
        ascent = descent = 0.0
        minimum = maximum = None
    else:
        if distance_m is None:
            distance_m = points[-1][0]
        profile = tuple(
            ElevationSample(
                distance_m=distance,
                elevation_m=elevation,
                source_id="test",
                resolution_m=1.0,
            )
            for distance, elevation in points
        )
        ascent = sum(
            max(0.0, current[1] - previous[1])
            for previous, current in zip(points, points[1:])
        )
        descent = sum(
            max(0.0, previous[1] - current[1])
            for previous, current in zip(points, points[1:])
        )
        heights = [elevation for _, elevation in points]
        minimum, maximum = min(heights), max(heights)

    segment = Segment(
        id="s0",
        distance_m=distance_m,
        surface=surface,
        surface_status=SurfaceStatus.MAPPED,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        min_elevation_m=minimum,
        max_elevation_m=maximum,
        elevation_profile=profile,
        bicycle_access=(
            BicycleAccess.DISMOUNT if dismount else BicycleAccess.YES
        ),
        max_speed_kmh=max_speed_kmh,
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


@pytest.fixture
def cyclist() -> CyclistModel:
    return CyclistModel(
        mass_kg=75.0,
        max_power_w=600.0,
        sustainable_power_w=200.0,
        frontal_area_m2=0.5,
    )


@pytest.fixture
def bicycle() -> BicycleModel:
    return BicycleModel()


@pytest.fixture
def traversal() -> PhysicalTraversal:
    return PhysicalTraversal(step_m=5.0)


# --------------------------------------------------------------------------
# Perfil do arco
# --------------------------------------------------------------------------
def test_profile_steps_preserve_distance_ascent_and_descent():
    arc = make_arc([(0.0, 10.0), (100.0, 20.0), (200.0, 10.0)])
    profile = ArcProfile.from_arc(arc)

    assert profile.distance_m == pytest.approx(200.0)
    assert profile.ascent_m == pytest.approx(10.0)
    assert profile.descent_m == pytest.approx(10.0)
    assert profile.has_elevation
    assert profile.unknown_elevation_m == 0.0

    steps = profile.steps(step_m=10.0)
    assert sum(step.distance_m for step in steps) == pytest.approx(200.0)
    assert all(step.distance_m <= 10.0 + 1e-9 for step in steps)

    ascent = sum(max(0.0, step.elevation_delta_m or 0.0) for step in steps)
    descent = sum(max(0.0, -(step.elevation_delta_m or 0.0)) for step in steps)
    assert ascent == pytest.approx(10.0)
    assert descent == pytest.approx(10.0)

    assert steps[0].gradient_percent == pytest.approx(10.0)
    assert steps[-1].gradient_percent == pytest.approx(-10.0)


def test_profile_without_elevation_is_flat_and_unknown():
    arc = make_arc(distance_m=100.0)
    profile = ArcProfile.from_arc(arc)

    assert not profile.has_elevation
    assert profile.unknown_elevation_m == pytest.approx(100.0)
    steps = profile.steps(step_m=10.0)
    assert len(steps) == 10
    assert all(step.slope_rad == 0.0 for step in steps)
    assert all(step.elevation_start_m is None for step in steps)


def test_profile_converts_max_speed_from_kmh_to_mps():
    arc = make_arc([(0.0, 0.0), (100.0, 0.0)], max_speed_kmh=18.0)
    profile = ArcProfile.from_arc(arc)

    assert profile.segments[0].max_speed_mps == pytest.approx(5.0)
    assert profile.steps(step_m=10.0)[0].max_speed_mps == pytest.approx(5.0)


def test_profile_rejects_invalid_step():
    arc = make_arc([(0.0, 0.0), (100.0, 0.0)])
    with pytest.raises(ValueError):
        ArcProfile.from_arc(arc).steps(step_m=0.0)


# --------------------------------------------------------------------------
# Forcas e equilibrio
# --------------------------------------------------------------------------
def test_force_signs_and_aerodynamic_quadratic(bicycle):
    environment = Environment()

    uphill = forces_at(0.1, 5.0, 87.0, 0.5, bicycle, environment)
    downhill = forces_at(-0.1, 5.0, 87.0, 0.5, bicycle, environment)

    assert uphill.gravity_n > 0.0 > downhill.gravity_n
    assert uphill.rolling_n > 0.0
    assert downhill.rolling_n > 0.0
    assert uphill.total_n > downhill.total_n

    slow = aerodynamic_force_n(5.0, 0.9, 0.5, 1.225)
    fast = aerodynamic_force_n(10.0, 0.9, 0.5, 1.225)
    assert fast == pytest.approx(4.0 * slow)

    headwind = aerodynamic_force_n(5.0, 0.9, 0.5, 1.225, headwind_mps=5.0)
    tailwind = aerodynamic_force_n(5.0, 0.9, 0.5, 1.225, headwind_mps=-10.0)
    assert headwind > slow
    assert tailwind < 0.0


def test_equilibrium_speed_orders(bicycle):
    environment = Environment()
    flat = equilibrium_speed_mps(0.0, 87.0, 0.5, bicycle, environment, 200.0)
    uphill = equilibrium_speed_mps(0.05, 87.0, 0.5, bicycle, environment, 200.0)
    downhill = equilibrium_speed_mps(
        -0.05, 87.0, 0.5, bicycle, environment, 200.0
    )

    assert uphill < flat < downhill
    assert 6.0 < flat < 11.0
    assert downhill <= environment.safe_max_speed_mps


def test_equilibrium_zero_power_downhill_returns_terminal_speed(bicycle):
    environment = Environment()
    terminal = equilibrium_speed_mps(
        -0.05, 87.0, 0.5, bicycle, environment, 0.0
    )
    assert terminal > 0.0


# --------------------------------------------------------------------------
# Travessia
# --------------------------------------------------------------------------
def test_flat_traversal_reports_time_energy_and_no_battery(
    cyclist, bicycle, traversal
):
    arc = make_arc([(0.0, 0.0), (1000.0, 0.0)])
    result = traversal.traverse(arc, cyclist, bicycle)

    assert result.distance_m == pytest.approx(1000.0)
    assert result.time_s > 0.0
    assert result.final_speed_mps > 0.0
    assert result.average_speed_kmh > 15.0
    assert result.ascent_m == pytest.approx(0.0)
    assert result.descent_m == pytest.approx(0.0)
    assert result.mechanical_energy_j > 0.0
    assert result.rider_energy_j == pytest.approx(
        result.mechanical_energy_j / bicycle.drivetrain_efficiency,
        rel=1e-9,
    )
    assert result.battery_energy_j is None
    assert not result.stalled


def test_uphill_is_slower_than_flat(cyclist, bicycle, traversal):
    flat_arc = make_arc([(0.0, 0.0), (500.0, 0.0)], arc_id="flat")
    uphill_arc = make_arc([(0.0, 0.0), (500.0, 25.0)], arc_id="uphill")

    flat = traversal.traverse(
        flat_arc, cyclist, bicycle, initial_state=state(5.0)
    )
    uphill = traversal.traverse(
        uphill_arc, cyclist, bicycle, initial_state=state(5.0)
    )

    assert uphill.time_s > flat.time_s
    assert uphill.final_speed_mps < flat.final_speed_mps


def test_downhill_is_faster_than_flat(cyclist, bicycle, traversal):
    flat_arc = make_arc([(0.0, 0.0), (500.0, 0.0)], arc_id="flat")
    downhill_arc = make_arc([(0.0, 25.0), (500.0, 0.0)], arc_id="down")

    flat = traversal.traverse(
        flat_arc, cyclist, bicycle, initial_state=state(5.0)
    )
    downhill = traversal.traverse(
        downhill_arc, cyclist, bicycle, initial_state=state(5.0)
    )

    assert downhill.final_speed_mps > flat.final_speed_mps
    assert downhill.time_s < flat.time_s


def test_initial_speed_is_propagated(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 0.0), (100.0, 0.0)])

    from_rest = traversal.traverse(arc, cyclist, bicycle, initial_state=state(0.0))
    rolling = traversal.traverse(arc, cyclist, bicycle, initial_state=state(5.0))

    assert rolling.initial_speed_mps == pytest.approx(5.0)
    assert rolling.final_speed_mps > from_rest.final_speed_mps
    assert rolling.time_s < from_rest.time_s


def test_speed_limit_brakes_and_reports_energy(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 50.0), (500.0, 0.0)], max_speed_kmh=18.0)
    result = traversal.traverse(
        arc, cyclist, bicycle, initial_state=state(4.0)
    )

    assert result.final_speed_mps == pytest.approx(5.0, abs=1e-9)
    assert result.braking_energy_j > 0.0


def test_zero_power_uphill_decelerates(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 0.0), (50.0, 2.5)])
    result = traversal.traverse(
        arc, cyclist, bicycle, initial_state=state(5.0), power_w=0.0
    )

    assert result.final_speed_mps < 5.0
    assert result.rider_energy_j == 0.0
    assert result.mechanical_energy_j == 0.0


def test_zero_power_downhill_accelerates(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 2.5), (50.0, 0.0)])
    result = traversal.traverse(
        arc, cyclist, bicycle, initial_state=state(2.0), power_w=0.0
    )

    assert result.final_speed_mps > 2.0
    assert result.rider_energy_j == 0.0


def test_dismount_uses_walking_speed(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 0.0), (140.0, 0.0)], dismount=True)
    result = traversal.traverse(
        arc, cyclist, bicycle, initial_state=state(5.0)
    )

    assert result.final_speed_mps == pytest.approx(1.4)
    assert result.time_s == pytest.approx(100.0)
    assert result.rider_energy_j == 0.0
    assert result.mechanical_energy_j == 0.0
    assert result.braking_energy_j > 0.0


def test_surface_is_not_converted_to_rolling_resistance(
    cyclist, bicycle, traversal
):
    asphalt = make_arc(
        [(0.0, 0.0), (100.0, 0.0)], surface=Surface.ASPHALT, arc_id="a"
    )
    gravel = make_arc(
        [(0.0, 0.0), (100.0, 0.0)], surface=Surface.GRAVEL, arc_id="g"
    )

    asphalt_result = traversal.traverse(asphalt, cyclist, bicycle)
    gravel_result = traversal.traverse(gravel, cyclist, bicycle)

    assert asphalt_result.time_s == pytest.approx(gravel_result.time_s)


def test_headwind_increases_time_and_reduces_speed(cyclist, bicycle, traversal):
    arc = make_arc([(0.0, 0.0), (1000.0, 0.0)])

    calm = traversal.traverse(
        arc, cyclist, bicycle, environment=Environment(headwind_mps=0.0)
    )
    windy = traversal.traverse(
        arc, cyclist, bicycle, environment=Environment(headwind_mps=5.0)
    )

    assert windy.time_s > calm.time_s
    assert windy.final_speed_mps < calm.final_speed_mps


def test_traverse_path_propagates_speed(cyclist, bicycle, traversal):
    first = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="a")
    second = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="b")

    path = traversal.traverse_path([first, second], cyclist, bicycle)

    assert len(path) == 2
    assert path[1].initial_speed_mps == pytest.approx(
        path[0].final_speed_mps
    )


def test_traverse_path_reports_physical_state_and_metrics(
    cyclist, bicycle, traversal
):
    first = make_arc([(0.0, 10.0), (200.0, 0.0)], arc_id="a")
    second = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="b")

    path = traversal.traverse_path(
        [first, second], cyclist, bicycle, initial_state=state(0.0)
    )

    # O estado fisico de saida pertence ao modelo fisico, nao a busca.
    assert path.initial_speed_mps == pytest.approx(0.0)
    assert path.final_speed_mps == pytest.approx(path[-1].final_speed_mps)
    assert path.final_speed_mps == pytest.approx(path.results[-1].final_speed_mps)
    assert path.arc_ids == ("a", "b")

    # As metricas acumuladas batem com a soma dos arcos.
    assert path.metrics.time_s == pytest.approx(
        path[0].time_s + path[1].time_s
    )
    assert path.metrics.distance_m == pytest.approx(400.0)
    assert path.metrics.energy_j == pytest.approx(
        path[0].rider_energy_j + path[1].rider_energy_j
    )

    payload = path.to_dict()
    assert payload["arcs"] == 2
    assert payload["final_speed_mps"] == pytest.approx(
        path.final_speed_mps, abs=0.01
    )


def test_traverse_arc_helper(cyclist, bicycle):
    arc = make_arc([(0.0, 0.0), (100.0, 0.0)])
    result = traverse_arc(arc, cyclist, bicycle, step_m=10.0)

    assert result.arc_id == "e0"
    assert result.distance_m == pytest.approx(100.0)


# --------------------------------------------------------------------------
# Metricas e serializacao
# --------------------------------------------------------------------------
def test_route_metrics_accumulate(cyclist, bicycle, traversal):
    first = traversal.traverse(
        make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="a"),
        cyclist,
        bicycle,
    )
    second = traversal.traverse(
        make_arc([(0.0, 10.0), (200.0, 0.0)], arc_id="b"),
        cyclist,
        bicycle,
    )

    metrics = RouteMetrics.from_results([first, second])

    assert metrics.time_s == pytest.approx(first.time_s + second.time_s)
    assert metrics.distance_m == pytest.approx(
        first.distance_m + second.distance_m
    )
    assert metrics.ascent_m == pytest.approx(
        first.ascent_m + second.ascent_m
    )
    assert metrics.descent_m == pytest.approx(
        first.descent_m + second.descent_m
    )
    assert metrics.energy_j == pytest.approx(
        first.rider_energy_j + second.rider_energy_j
    )
    assert metrics.battery_energy_j is None


def test_traversal_result_to_dict(cyclist, bicycle, traversal):
    result = traversal.traverse(
        make_arc([(0.0, 0.0), (100.0, 0.0)]), cyclist, bicycle
    )
    payload = result.to_dict()

    assert payload["arc_id"] == "e0"
    assert payload["distance_m"] == pytest.approx(100.0)
    assert payload["battery_energy_j"] is None
    assert payload["average_speed_kmh"] == pytest.approx(
        result.average_speed_kmh, abs=0.01
    )


# --------------------------------------------------------------------------
# Validacao
# --------------------------------------------------------------------------
def test_invalid_models_are_rejected():
    with pytest.raises(ValueError):
        CyclistModel(
            mass_kg=0.0,
            max_power_w=600.0,
            sustainable_power_w=200.0,
            frontal_area_m2=0.5,
        )

    with pytest.raises(ValueError):
        CyclistModel(
            mass_kg=75.0,
            max_power_w=600.0,
            sustainable_power_w=700.0,
            frontal_area_m2=0.5,
        )

    with pytest.raises(ValueError):
        BicycleModel(drivetrain_efficiency=0.0)

    with pytest.raises(ValueError):
        BicycleModel(drivetrain_efficiency=1.1)

    with pytest.raises(ValueError):
        Environment(air_density_kg_m3=-1.0)

    with pytest.raises(ValueError):
        Environment(safe_max_speed_mps=0.0)

    with pytest.raises(ValueError):
        PhysicalTraversal(step_m=0.0)


def test_power_above_max_and_negative_speed_are_rejected(
    cyclist, bicycle, traversal
):
    arc = make_arc([(0.0, 0.0), (100.0, 0.0)])

    with pytest.raises(ValueError):
        traversal.traverse(arc, cyclist, bicycle, power_w=700.0)

    # A velocidade inicial negativa e rejeitada pelo contrato de estado
    # (``SimulationState``), nao mais por argumento solto do simulador.
    with pytest.raises(ValueError):
        SimulationState(speed_mps=-1.0)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_physical_json(tmp_path, capsys):
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    graph.add_arc(make_arc([(0.0, 0.0), (1000.0, 0.0)], arc_id="e0"))
    path = save_json(graph, tmp_path / "graph.json")

    exit_code = main(["physical", str(path), "--arc", "e0", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["arc_id"] == "e0"
    assert payload["distance_m"] == pytest.approx(1000.0)
    assert payload["time_s"] > 0.0
    assert payload["battery_energy_j"] is None


def test_cli_physical_reports_unknown_arc(tmp_path, capsys):
    graph = RoutingGraph()
    graph.add_node(Node(id=1, lat=0.0, lon=0.0))
    graph.add_node(Node(id=2, lat=0.0, lon=0.001))
    graph.add_arc(make_arc([(0.0, 0.0), (100.0, 0.0)], arc_id="e0"))
    path = save_json(graph, tmp_path / "graph.json")

    exit_code = main(["physical", str(path), "--arc", "missing"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "erro:" in captured.err
