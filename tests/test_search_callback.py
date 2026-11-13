import runpy
from pathlib import Path
import pytest

FIXTURE = Path(__file__).with_name('test_search_astar.py')
f = runpy.run_path(str(FIXTURE))


def test_candidate_callback_emits_before_search_finishes_without_changing_result():
    graph = f['make_graph']([('direct', 1, 3), ('b1', 1, 2), ('b2', 2, 3)])
    table = {'direct': {'time_s': 10, 'distance_m': 1000},
             'b1': {'time_s': 20, 'distance_m': 200},
             'b2': {'time_s': 20, 'distance_m': 200}}
    profile = f['custom_profile'](f['Objective'].TIME, f['Objective'].DISTANCE)
    base = f['MultiObjectiveAStar'](graph, f['StubTraversal'](table), profile).search(1, 3)
    traversal = f['StubTraversal'](table)
    seen = []
    result = f['MultiObjectiveAStar'](graph, traversal, profile).search(
        1, 3, on_candidate=lambda arcs, expanded, generated: seen.append((arcs, expanded, generated, len(traversal.calls))))
    assert seen and seen[0][0] == ('direct',)
    assert seen[0][3] < len(traversal.calls)
    assert result.arc_sequences() == base.arc_sequences()
    assert result.expanded == base.expanded and result.generated == base.generated


def test_callback_exception_is_propagated():
    graph = f['make_graph']([('e', 1, 2)], node_ids=(1, 2))
    astar = f['MultiObjectiveAStar'](graph, f['StubTraversal']({'e': {}}), 'fastest')
    def fail(*args):
        raise RuntimeError('callback failed')
    with pytest.raises(RuntimeError, match='callback failed'):
        astar.search(1, 2, on_candidate=fail)


def test_candidate_state_is_available_for_synchronous_evaluation():
    graph = f['make_graph']([('e', 1, 2)], node_ids=(1, 2))
    astar = f['MultiObjectiveAStar'](graph, f['StubTraversal']({'e': {}}), 'fastest')
    from router.core.search.astar import SearchResult
    from router.core.search.candidates import RouteCandidatePool
    captured = []
    def evaluate(state):
        partial = SearchResult(origin=1, destination=2, profile=astar.profile, labels=(state,))
        captured.extend(RouteCandidatePool.from_result(partial).candidates)
    final = astar.search(1, 2, on_candidate_state=evaluate)
    assert len(captured) == 1
    assert captured[0].arc_ids == ('e',)
    assert final.arc_sequences() == (('e',),)
