from dataclasses import replace
import pytest

from router.core.data.graph.model import Arc, Segment, ElevationSample
from router.core.data.mobility import MobilityProfile
from router.core.physics.environment import Environment
from router.core.physics.motion import MotionEngine
from router.core.physics.propulsion import ReferenceSpeedPower


@pytest.mark.parametrize("points,expected", [
    (((0, 0), (.001, 0)), 5),
    (((0, 0), (-.001, 0)), -5),
    (((0, 0), (0, .001)), 0),
    (((0, 0), (0, -.001)), 0),
])
def test_meteorological_direction_is_projected_on_directed_arc(points, expected):
    env = Environment(wind_speed_mps=5, wind_from_deg=0)
    assert env.for_geometry(points).headwind_mps == pytest.approx(expected, abs=1e-12)
    assert env.wind_speed_mps == 5  # resolving one arc never mutates another


def test_turning_changes_wind_and_matches_scalar_simulations():
    engine = MotionEngine()
    mobility = MobilityProfile.create(cda_m2=.55, rolling_resistance=.007)
    propulsion = ReferenceSpeedPower(150, road_load=engine.road_load)
    vector = Environment(safe_max_speed_mps=None, wind_speed_mps=5, wind_from_deg=0)
    results = []
    for geometry, scalar in [(((0, 0), (.001, 0)), 5), (((.001, 0), (0, 0)), -5)]:
        arc = Arc(str(scalar), 1, 2, (Segment('s', 100, geometry=geometry,
            elevation_profile=(ElevationSample(0, 0), ElevationSample(100, 0))),))
        actual = engine.traverse(arc, mobility, propulsion, environment=vector)
        expected = engine.traverse(arc, mobility, propulsion,
            environment=Environment(safe_max_speed_mps=None, headwind_mps=scalar))
        assert actual == expected
        results.append(actual)
    assert results[0].rider_energy_j > results[1].rider_energy_j


def test_scalar_contract_and_invalid_vectors():
    scalar = Environment(headwind_mps=-3)
    assert scalar.for_geometry(()) is scalar
    assert replace(scalar, wind_speed_mps=0).for_geometry(()).headwind_mps == 0
    with pytest.raises(ValueError, match='geometry'):
        Environment(wind_speed_mps=5).for_geometry(())
    for kwargs in ({'wind_speed_mps': -1}, {'wind_from_deg': float('nan')}):
        with pytest.raises(ValueError):
            Environment(**kwargs)
