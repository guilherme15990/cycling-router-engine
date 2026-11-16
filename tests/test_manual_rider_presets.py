import json
import sys
from pathlib import Path

import pytest

from router.integration import MANUAL_RIDER_PRESETS, ManualRiderProfile, MobilityEngine, MobilityPlan
from test_planning import _graph
from router.integration import ConstantPower


def test_public_presets_are_fixed_watts_independent_of_weight():
    assert [preset.reference_power_w for preset in MANUAL_RIDER_PRESETS] == [75., 112.5, 150., 225., 315., 412.5]
    for preset in MANUAL_RIDER_PRESETS:
        for weight in (60., 90.):
            rider = ManualRiderProfile(preset.profile, weight, 40)
            assert rider.reference_power_w == preset.reference_power_w
            assert rider.to_rider_model().reference.power_w == preset.reference_power_w
            assert not rider.to_rider_model().reference.is_measured


@pytest.mark.parametrize("kwargs", [{"profile": "invalid"}, {"mass_kg": 0}, {"mass_kg": float('nan')},
                                    {"age_years": 4}, {"age_years": 101}])
def test_invalid_rider_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ManualRiderProfile(**kwargs)


def test_profiles_change_rpe_without_changing_simulation_and_survive_json():
    engine = MobilityEngine(_graph())
    plain = engine.plan((0, 0), (.002, .002))
    estimates = []
    for profile in ("sedentary", "elite"):
        plan = engine.plan((0, 0), (.002, .002), rider=ManualRiderProfile(profile, 75, 35), propulsion=ConstantPower(200))
        assert [route.arc_ids for route in plan.routes] == [route.arc_ids for route in plain.routes]
        assert [route.metrics for route in plan.routes] == [route.metrics for route in plain.routes]
        estimate = plan.routes[0].effort_estimate
        assert estimate and 6 <= estimate['mean_rpe'] <= 20
        assert not estimate['is_ml'] and not estimate['calibrated']
        assert sum(segment['duration_s'] for segment in estimate['segments']) == pytest.approx(plan.routes[0].metrics.time_s)
        assert estimate['mean_power_w'] == pytest.approx(plan.routes[0].metrics.drive_input_energy_j / plan.routes[0].metrics.time_s)
        estimates.append(estimate['mean_rpe'])
        assert MobilityPlan.from_dict(json.loads(plan.to_json())) == plan
    assert estimates[0] > estimates[1]
    assert plain.routes[0].effort_estimate is None
    old_payload = plain.to_dict()
    for route in old_payload['routes']:
        route.pop('effort_estimate')
    assert MobilityPlan.from_dict(old_payload) == plain


def test_automatic_pacing_uses_profile_capacity_without_fixed_100_percent():
    engine = MobilityEngine(_graph())
    estimates = []
    for profile in ('sedentary', 'elite'):
        rider = ManualRiderProfile(profile)
        plan = engine.plan((0, 0), (.002, .002), rider=rider)
        assert plan.mobility['nominal_power_w'] == rider.reference_power_w
        estimate = plan.routes[0].effort_estimate
        assert estimate['pacing']['target_speed_kmh'] == 18
        assert 0 < estimate['relative_intensity'] < 1
        assert estimate['mean_power_w'] < rider.reference_power_w
        assert estimate['mean_power_w'] == pytest.approx(plan.routes[0].metrics.drive_input_energy_j / plan.routes[0].metrics.time_s)
        estimates.append(estimate['mean_rpe'])
    assert estimates[0] > estimates[1]


def test_rider_mass_is_used_by_physics_when_mobility_is_implicit():
    plan = MobilityEngine(_graph()).plan((0, 0), (.002, .002), rider=ManualRiderProfile(mass_kg=90))
    assert plan.mobility['mass_total_kg'] == 102


def test_rider_rejected_for_electric_bicycle():
    with pytest.raises(ValueError, match="manuais"):
        MobilityEngine(_graph()).plan((0, 0), (.002, .002), propulsion_type="electric", rider=ManualRiderProfile())


def test_effort_preserves_signed_grade_instead_of_accumulating_across_arcs():
    from router.app.manual_rider import estimate_manual_effort
    from router.core.physics.motion import MotionEngine
    from router.integration import MobilityProfile
    from test_mobility_physics import road
    motion = MotionEngine()
    parts = [motion.traverse(road(arc_id=str(index), distance=100., delta=delta),
                             MobilityProfile.create(), ConstantPower(150.))
             for index, delta in enumerate((-3., 3.5, -2.))]
    estimate = estimate_manual_effort(parts, ManualRiderProfile())
    assert [row['grade_pct'] for row in estimate['segments']] == pytest.approx([-3., 3.5, -2.])


def test_debug_profile_selection_populates_reference_and_submits_to_worker(tmp_path):
    from test_stream_progress import app_for_graph
    app = app_for_graph(tmp_path)
    assert app.number_input(key='manual_reference_recreational').value == 150
    assert not any(widget.key in ('mobility_power', 'mobility_power_unit') for widget in app.number_input)
    assert not any(widget.key == 'mobility_power_unit' for widget in app.selectbox)
    app.selectbox(key='manual_fitness').select('beginner').run()
    assert not app.exception
    assert app.number_input(key='manual_reference_beginner').value == 112.5
    app.number_input(key='mobility_rider_mass').set_value(90.).run()
    assert app.number_input(key='manual_reference_beginner').value == 112.5
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'debug'))
    from mobility_debug import execute
    from test_debug_mobility import settings
    plan = execute(dict(origin=(0, 0), destination=(.002, .002), vehicle='bicycle', propulsion_type='manual',
                        refinements={'rider_mass_kg': 90., 'power_w': 200.},
                        rider=dict(profile='beginner', mass_kg=90., age_years=35)),
                   settings(tmp_path), Path(app.session_state['graph_path']))
    app.session_state['last_plan'] = plan
    app.run()
    assert not app.exception
    assert any(metric.label == 'RPE médio estimado' for metric in app.metric)
    assert plan.routes[0].effort_estimate['rider']['reference_power_w'] == 112.5
    assert plan.mobility['nominal_power_w'] == 112.5  # stale 200 W ignored for manual bicycles
    component = app.get('bidi_component')[0]
    map_data = json.loads(component.proto.json)
    segments = map_data['route_segments']
    assert len(segments) == len(plan.routes[0].effort_estimate['segments'])
    assert all(row['rpe'] is not None and row['rpe_source'] == 'segment' for row in segments)
    assert [row['pts'] for row in segments] == [row['geometry'] for row in plan.routes[0].effort_estimate['segments']]


@pytest.mark.parametrize('vehicle,propulsion', [('motorcar', 'combustion'), ('motorcycle', 'electric'), ('bicycle', 'electric')])
def test_debug_other_vehicles_keep_editable_power_and_units(tmp_path, vehicle, propulsion):
    from test_stream_progress import app_for_graph
    app = app_for_graph(tmp_path, vehicle, propulsion)
    assert not any(widget.key == 'manual_fitness' for widget in app.selectbox)
    assert app.number_input(key='mobility_power') is not None
    assert app.selectbox(key='mobility_power_unit') is not None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'debug'))
    from mobility_debug import profile_for_request
    mobility = profile_for_request(dict(vehicle=vehicle, propulsion_type=propulsion, refinements={'power_cv': 2.}))
    assert mobility.nominal_power_w == pytest.approx(1470.9975)


def test_debug_keeps_vehicle_power_when_switching_to_manual_and_back(tmp_path):
    from test_stream_progress import app_for_graph
    app = app_for_graph(tmp_path, 'motorcar', 'combustion')
    # Form values are submitted together in the real UI; AppTest can set the
    # stored values without starting a route worker for this navigation check.
    app.session_state['mobility_power'] = 120.
    app.session_state['mobility_power_unit'] = 'cv'
    app.run()
    app.selectbox(key='mobility_vehicle').select('bicycle')
    app.selectbox(key='mobility_propulsion').select('manual').run()
    assert not app.exception
    assert not any(widget.key == 'mobility_power' for widget in app.number_input)
    app.selectbox(key='mobility_vehicle').select('motorcar')
    app.selectbox(key='mobility_propulsion').select('combustion').run()
    assert not app.exception
    assert app.number_input(key='mobility_power').value == 120
    assert app.selectbox(key='mobility_power_unit').value == 'cv'
