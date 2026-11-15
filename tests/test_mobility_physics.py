from dataclasses import replace
import math

import pytest

from router.core.data.mobility import MobilityProfile, power_in_watts
from router.core.data.graph.model import Arc, Segment, ElevationSample
from router.core.data.graph.surface import Surface
from router.core.physics import PhysicalTraversal, CyclistModel, BicycleModel, Environment, SimulationState
from router.core.physics.motion import MotionEngine, MotionTraversalModel
from router.core.physics.propulsion import ConstantPower, ConstantForce, PropulsionOutput
from router.core.physics.road_load import StandardRoadLoad


def road(arc_id="r", distance=100, delta=5, surface=Surface.ASPHALT):
    return Arc(arc_id, 1, 2, (Segment(
        "s", distance, surface=surface, elevation_gain_m=max(0, delta), elevation_loss_m=max(0, -delta),
        elevation_profile=(ElevationSample(0, 700), ElevationSample(distance, 700 + delta)),
    ),))


def test_minimal_configuration_has_explicit_assumptions_and_optional_weight():
    profile = MobilityProfile.create()
    assert profile.mass_total_kg == 87
    assert profile.propulsion_type.value == "manual"
    assert "generic_vehicle_preset@1" in profile.assumptions
    assert dict(profile.parameter_sources)["mass_total_kg"] == "partly_estimated"
    personalized = MobilityProfile.create(rider_mass_kg=90, vehicle_mass_kg=10, load_mass_kg=7)
    assert personalized.mass_total_kg == 107
    assert dict(personalized.parameter_sources)["mass_total_kg"] == "provided"


def test_units_and_access_class_are_independent_of_energy_source():
    assert power_in_watts(1, "cv") == 735.49875
    assert power_in_watts(1, "kW") == 1000
    for source in ("combustion", "electric"):
        assert MobilityProfile.create("motorcar", source).transport_mode == "motorcar"
    assert MobilityProfile.create("bicycle", "electric").transport_mode == "electric_bicycle"
    assert "pedal_assist_access_class_assumed" in MobilityProfile.create("bicycle", "electric").assumptions
    with pytest.raises(ValueError):
        MobilityProfile.create(power_w=250, power_cv=1)
    with pytest.raises(ValueError):
        power_in_watts(10, "force")


@pytest.mark.parametrize("speed", [0, 4, 15])
@pytest.mark.parametrize("delta", [-15, 0, 12])
def test_generic_motion_matches_legacy_bicycle_numerics(speed, delta):
    cyclist = CyclistModel(75, 900, 220, .5)
    bicycle = BicycleModel()
    environment = Environment(headwind_mps=1.5)
    mobility = MobilityProfile.from_bicycle(cyclist, bicycle)
    arc = road(delta=delta)
    before = PhysicalTraversal(step_m=5).traverse(
        arc, cyclist, bicycle, initial_state=SimulationState.from_speed(speed), environment=environment)
    after = MotionEngine(step_m=5).traverse(
        arc, mobility, ConstantPower(220), initial_state=SimulationState.from_speed(speed), environment=environment)
    for name in ("time_s", "distance_m", "ascent_m", "descent_m", "final_speed_mps", "mechanical_energy_j",
                 "rider_energy_j", "braking_energy_j", "aero_loss_j", "rolling_loss_j"):
        assert getattr(after, name) == pytest.approx(getattr(before, name), rel=1e-12, abs=1e-9), name
    assert after.used_min_speed_regularization == before.used_min_speed_regularization
    assert after.source_energy_j is None


def test_force_limited_launch_and_source_energy_balance():
    profile = MobilityProfile.create("small_electric_vehicle", mass_total_kg=100,
                                     cda_m2=0, rolling_resistance=0, drive_efficiency=.8)
    result = MotionEngine(step_m=5).traverse(road(distance=5, delta=0), profile,
                                          ConstantForce(20, source_efficiency=.5))
    assert result.final_speed_mps == pytest.approx(math.sqrt(2))
    assert result.mechanical_energy_j == pytest.approx(100)
    assert result.drive_input_energy_j == pytest.approx(125)
    assert result.battery_energy_j == pytest.approx(250)
    assert result.source_energy_j == result.battery_energy_j


def test_road_surface_and_wind_are_combined_with_mobility():
    profile = MobilityProfile.create()
    arc = road(delta=0, surface=Surface.GRAVEL)
    plain = MotionEngine(step_m=5).traverse(arc, profile, ConstantPower(200))
    configured = MotionEngine(step_m=5, road_load=StandardRoadLoad(((Surface.GRAVEL, .04),)))
    rough = configured.traverse(arc, profile, ConstantPower(200))
    windy = configured.traverse(arc, profile, ConstantPower(200), environment=Environment(headwind_mps=5))
    assert rough.time_s > plain.time_s
    assert rough.mechanical_energy_j > plain.mechanical_energy_j
    assert windy.time_s > rough.time_s


def test_equilibrium_cache_includes_total_mass():
    engine = MotionEngine()
    light = MobilityProfile.create(mass_total_kg=60)
    heavy = replace(light, mass_total_kg=160)
    arc = road(delta=15)
    engine.traverse(arc, light, ConstantPower(200))
    cached = engine.traverse(arc, heavy, ConstantPower(200))
    fresh = MotionEngine().traverse(arc, heavy, ConstantPower(200))
    assert cached == fresh


def test_custom_propulsion_works_without_inheritance_or_unsafe_cache():
    class Push:
        def __init__(self):
            self.calls = 0
        def evaluate(self, speed_mps, road, mobility, environment):
            self.calls += 1
            return PropulsionOutput(15, 15 * max(.5, speed_mps), 15 * max(.5, speed_mps), work_mode="force")

    custom = Push()
    model = MotionTraversalModel(MotionEngine(), MobilityProfile.create(), custom)
    arc = road(delta=0)
    first = model.traverse(arc, 0)
    calls = custom.calls
    second = model.traverse(arc, 0)
    assert custom.calls == 2 * calls
    assert second == first
    assert model.traversal_cache_hits == 0
    assert first.mechanical_energy_j > 0


def test_unknown_source_efficiency_never_invents_battery_or_fuel_consumption():
    arc = road(delta=0)
    for kind in ("electric", "combustion", "manual"):
        profile = MobilityProfile.create("motorcar", kind)
        result = MotionEngine().traverse(arc, profile, ConstantPower(1000))
        assert result.source_energy_j is None
        assert result.battery_energy_j is None


def test_exact_transition_cache_invalidates_scenario_and_replaced_arc():
    model = MotionTraversalModel(MotionEngine(), MobilityProfile.create())
    arc = road(delta=0)
    original = model.traverse(arc, 4)
    assert model.traverse(arc, 4) is original
    changed = road(distance=200, delta=0)
    assert model.traverse(changed, 4).distance_m == 200
    model.environment = Environment(headwind_mps=5)
    assert model.traverse(arc, 4).time_s != original.time_s


def test_stateful_propulsion_is_not_silently_projected_to_speed_only():
    class BatteryState(ConstantPower):
        state_dependencies = ("speed_mps", "battery_j")
    with pytest.raises(ValueError, match="stateful"):
        MotionTraversalModel(MotionEngine(), MobilityProfile.create(), BatteryState(200))


def test_road_hooks_receive_immutable_observations_and_original_geometry():
    import copy
    arc = road(delta=0)
    segment = replace(arc.segments[0], tags={"smoothness": "bad", "width": "2.5"},
                      geometry=((0, 0), (0, .001)))
    arc = replace(arc, segments=(segment,))
    engine = MotionEngine()
    steps = engine.geometry.steps(arc)
    assert all(step.tag("smoothness") == "bad" for step in steps)
    assert all(step.tag("unknown") is None for step in steps)
    assert all(step.geometry == segment.geometry for step in steps)
    assert copy.deepcopy(engine.geometry).steps(arc) == steps


def test_generic_energy_serialization_does_not_label_motor_input_as_rider_work():
    result = MotionEngine().traverse(road(delta=0), MobilityProfile.create("motorcar"), ConstantPower(1000))
    payload = result.to_dict()
    assert "drive_input_energy_j" in payload
    assert "rider_energy_j" not in payload
    assert payload["source_energy_j"] is None
    legacy = PhysicalTraversal().traverse(road(delta=0), CyclistModel(75, 900, 220, .5), BicycleModel())
    assert "rider_energy_j" in legacy.to_dict()
