import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'debug'))
from effort_colors import RPE_COLOR_NONE, rpe_color
from route_overlay import mobility_segments


def test_map_uses_each_arcs_geometry_and_rpe_in_order():
    route = SimpleNamespace(geometry=((0, 0), (0, .001), (0, .002), (0, .003)),
        effort_estimate={'mean_rpe': 13., 'segments': [
            {'arc_id': 'snap:a', 'rpe': 8., 'geometry': [[0, 0], [0, .001], [0, .002]]},
            {'arc_id': 'snap:b', 'rpe': 19., 'geometry': [[0, .002], [0, .003]]},
        ]})
    segments = mobility_segments(route)
    assert [row['rpe'] for row in segments] == [8., 19.]
    assert segments[0]['pts'] == [[0, 0], [0, .001], [0, .002]]
    assert segments[1]['pts'] == [[0, .002], [0, .003]]
    assert all(row['rpe_source'] == 'segment' for row in segments)
    assert [row['band'] for row in segments] == ['i1', 'i5']
    assert rpe_color(segments[0]['rpe']) != rpe_color(segments[1]['rpe'])
    assert all(rpe_color(row['rpe']) != RPE_COLOR_NONE for row in segments)


def test_old_plan_uses_labelled_route_mean_when_arc_geometry_is_missing():
    route = SimpleNamespace(geometry=((0, 0), (0, .001), (0, .002)),
        effort_estimate={'mean_rpe': 13., 'segments': [{'arc_id': 'a', 'rpe': 19.}]})
    segments = mobility_segments(route)
    assert [row['rpe'] for row in segments] == [13., 13.]
    assert all(row['rpe_source'] == 'route_mean' for row in segments)
    assert all(row['grade_pct'] is None for row in segments)


def test_routes_without_rpe_remain_grey():
    route = SimpleNamespace(geometry=((0, 0), (0, .001)), effort_estimate=None)
    segments = mobility_segments(route)
    assert segments[0]['rpe'] is None
    assert segments[0]['rpe_source'] == 'unavailable'
    assert rpe_color(segments[0]['rpe']) == RPE_COLOR_NONE


def test_map_preserves_signed_grade_per_arc():
    grades = [-3., 3.5, -2.]
    route = SimpleNamespace(geometry=(), effort_estimate={'segments': [
        {'rpe': 12., 'grade_pct': grade,
         'geometry': [[0, index * .001], [0, (index + 1) * .001]]}
        for index, grade in enumerate(grades)]})
    segments = mobility_segments(route)
    assert [row['grade_pct'] for row in segments] == grades
