from dataclasses import replace
import json
import math

import pytest

from router.integration import MobilityEngine, MobilityPlan, NeuralRouteModel, ManualRiderProfile
from router.app.neural_routing import learned_step_cost
from router.core.prediction.route_network import FEATURE_NAMES, train_route_network, route_features
from router.core.search.learned import search_learned
from router.core.search import SearchConfig, MultiObjectiveAStar, profile_for, route_arcs
from router.integration import SearchCancelled
from router.core.data.graph.restrictions import AccessRule, TurnRestriction
from test_search_reference import make_graph, StubTraversal
from test_planning import _graph


def test_network_training_is_deterministic_and_validates_on_separate_arcs():
    samples = []
    for index in range(40):
        x = tuple((index * (feature + 3) % 37) / 37 for feature in range(len(FEATURE_NAMES)))
        samples.append((f'a{index}', x, (1 + .3 * x[0] + .7 * x[1],)))
    model = train_route_network('fastest', ('time_s',), samples)
    assert model == train_route_network('fastest', ('time_s',), samples)
    assert model.validation_samples == 8 and model.training_samples == 32
    assert model.validation_mse is not None and model.validation_mse < .15
    assert sum(model.feature_influence().values()) == pytest.approx(100)
    assert NeuralRouteModel.from_dict(json.loads(json.dumps(model.to_dict()))) == model
    assert NeuralRouteModel.from_dict(model.to_dict()).model_version == model.model_version
    assert any(abs(a) > .2 for row in model.hidden_weights for a in row)


class Traversal(StubTraversal):
    access_scopes = ('bicycle', 'vehicle', 'access')

    def allows_arc(self, arc):
        return True

    def traverse(self, arc, initial_speed_mps):
        part = super().traverse(arc, initial_speed_mps)
        return replace(part, aero_loss_j=self.table[arc.id].get('aero_loss_j', 0),
                       stalled=self.table[arc.id].get('stalled', False))


def selective_model():
    weights = [0.] * len(FEATURE_NAMES)
    weights[FEATURE_NAMES.index('aero_loss_j_per_m')] = 1.
    return NeuralRouteModel('fastest', ('time_s', 'ascent_m'), (0.,) * len(FEATURE_NAMES),
        (1.,) * len(FEATURE_NAMES), (1., 0.), (1., 0.), (tuple(weights),), (0.,),
        ((2.,), (0.,)), (0., 0.), 1, 0, None)


def test_neural_search_can_find_path_outside_physical_pareto_and_weights_affect_it():
    graph = make_graph([('a', 1, 2, 100), ('b', 1, 2, 100)], [1, 2])
    traversal = Traversal({'a': {'time_s': 1., 'energy_j': 100., 'aero_loss_j': 100.},
                           'b': {'time_s': 10., 'energy_j': 1000.}})
    pareto = MultiObjectiveAStar(graph, traversal, profile_for('fastest')).search(1, 2)
    assert [route_arcs(state) for state in pareto.labels] == [['a']]
    model = selective_model()
    learned = search_learned(graph, traversal, 1, 2, lambda part: learned_step_cost(model, part), config=SearchConfig(exact_speed=True))
    assert route_arcs(learned.state) == ['b']
    assert learned.cost == pytest.approx(learned_step_cost(model, traversal.traverse(graph.get_arc('b'), 0)))
    ablated = replace(model, output_weights=((0.,), (0.,)))
    changed = search_learned(graph, traversal, 1, 2, lambda part: learned_step_cost(ablated, part))
    assert route_arcs(changed.state) == ['a']


def test_learned_search_enforces_access_and_physical_feasibility():
    graph = make_graph([('a', 1, 2, 100), ('b', 1, 2, 100), ('c', 1, 2, 100)], [1, 2])
    arc = graph.get_arc('b')
    graph.arcs['b'] = replace(arc, segments=(replace(arc.segments[0], restrictions=(AccessRule('bicycle', 'no', None, 'no'),)),))
    traversal = Traversal({'a': {}, 'b': {'aero_loss_j': 0}, 'c': {'stalled': True}})
    result = search_learned(graph, traversal, 1, 2, lambda part: (10. if part.arc_id == 'a' else 1.,))
    assert route_arcs(result.state) == ['a']


def test_learned_search_preserves_arrival_context_for_turn_restrictions():
    graph = make_graph([('a', 1, 2), ('b', 1, 3), ('c', 3, 2), ('d', 2, 4)], [1, 2, 3, 4])
    for index, arc in enumerate(list(graph.arcs.values())):
        graph.arcs[arc.id] = replace(arc, segments=(replace(arc.segments[0], osm_way_id=index + 10),))
    graph.add_turn_restriction(TurnRestriction('no', 10, 13, 'no_right_turn', via_node_id=2))
    traversal = Traversal({name: {} for name in graph.arcs})
    result = search_learned(graph, traversal, 1, 4, lambda part: (1.,))
    assert route_arcs(result.state) == ['b', 'c', 'd']


def test_learned_search_reports_limits_cancellation_and_invalid_costs():
    graph = make_graph([('a', 1, 2), ('b', 2, 3)], [1, 2, 3])
    traversal = Traversal({'a': {}, 'b': {}})
    result = search_learned(graph, traversal, 1, 3, lambda part: (1.,), config=SearchConfig(max_expansions=1))
    assert result.state is None and result.status == 'expansion_limit'
    with pytest.raises(SearchCancelled):
        search_learned(graph, traversal, 1, 3, lambda part: (1.,), should_cancel=lambda: True)
    for value in (-1., math.nan, math.inf, 0.):
        with pytest.raises(ValueError):
            search_learned(graph, traversal, 1, 3, lambda part: (value,))


@pytest.mark.parametrize('profile', ['fastest', 'shortest', 'flattest', 'balanced', 'electric'])
def test_api_ml_targets_selected_profile_and_keeps_physical_metrics(profile):
    engine = MobilityEngine(_graph())
    previews = []
    plan = engine.plan((0, 0), (.002, .002), profile=profile, rider=ManualRiderProfile(),
                       ml_route=True, max_routes=1, on_plan=previews.append)
    info = plan.diagnostics['neural_routing']
    assert info['profile'] == profile and info['objectives'] == list(profile_for(profile).names)
    assert info['source'] == 'simulation_surrogate' and not info['calibrated']
    assert info['status'] == 'found'
    assert any('ml' in route.origins for route in plan.routes)
    assert 'ml' in previews[0].routes[0].origins  # ML published before Pareto starts.
    assert previews[0].pareto_route_count == 0
    assert all(route.effort_estimate is not None for route in plan.routes)
    assert len({route.arc_ids for route in plan.routes}) == len(plan.routes)
    assert all(route.generation_model_version == info['model_version'] for route in plan.routes if 'ml' in route.origins)
    assert plan.diagnostics['evaluated_candidates'] >= plan.pareto_route_count
    assert not plan.optimality_proven
    assert MobilityPlan.from_dict(json.loads(plan.to_json())) == plan
    loaded = NeuralRouteModel.from_dict(json.loads(json.dumps(info['model'])))
    reused = engine.plan((0, 0), (.002, .002), profile=profile, rider=ManualRiderProfile(), ml_model=loaded)
    assert reused.diagnostics['neural_routing']['model_reused']
    assert reused.diagnostics['neural_routing']['model_version'] == info['model_version']
    assert next(route.arc_ids for route in reused.routes if 'ml' in route.origins) == next(route.arc_ids for route in plan.routes if 'ml' in route.origins)


def test_ml_model_must_match_selected_route_profile():
    with pytest.raises(ValueError, match='incompatível'):
        MobilityEngine(_graph()).plan((0, 0), (.002, .002), profile='shortest', ml_model=selective_model())


def test_origins_merge_when_neural_and_pareto_paths_coincide():
    plan = MobilityEngine(_graph()).plan((0, 0), (.002, .002), profile='shortest', ml_route=True)
    assert any(set(route.origins) == {'pareto', 'ml'} for route in plan.routes)


def test_neural_data_is_not_invented_without_feasible_samples():
    from router.core.physics.propulsion import ConstantPower
    plan = MobilityEngine(_graph()).plan((0, 0), (.002, .002), propulsion=ConstantPower(0), ml_route=True,
                                         access_filter=lambda arc, mobility: False)
    assert not plan.routes


def test_debug_identifies_generated_route_and_displays_influence(tmp_path):
    from test_stream_progress import app_for_graph
    app = app_for_graph(tmp_path)
    plan = MobilityEngine(app.session_state['graph']).plan((0, 0), (.002, .002), rider=ManualRiderProfile(), ml_route=True)
    app.session_state['last_plan'] = plan
    app.run()
    assert not app.exception
    assert app.checkbox(key='ml_route_enabled').value
    assert any('Gerada por ML' in option for option in app.selectbox(key='selected_route').options)
    assert any('Influência (%)' in item.value for item in app.markdown)
