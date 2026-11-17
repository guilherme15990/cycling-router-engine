"""Generate current Python physics oracle for the independent TypeScript port.

Run this first, then audit_toporide_physics.mts with TopoRide's tsx runtime.
All cases share geometry, mass, propulsion, wind and speed limits.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from router.core.data.graph.model import Arc, Segment, ElevationSample
from router.core.data.mobility import MobilityProfile
from router.core.physics.environment import Environment
from router.core.physics.models import SimulationState
from router.core.physics.motion import MotionEngine
from router.core.physics.propulsion import ConstantPower, ReferenceSpeedPower


def generate():
    mobility = MobilityProfile.create(
        rider_mass_kg=75, vehicle_mass_kg=12, power_w=150,
        cda_m2=.55, rolling_resistance=.007, drive_efficiency=.97,
    )
    cases = []
    for rise, speed, wind, cap, constant in itertools.product(
        (-20, -5, 0, 5, 20), (0, 5, 15), (-12, -8, 0, 5), (None, 12.5), (False, True),
    ):
        engine = MotionEngine(step_m=5)
        arc = Arc("road", 1, 2, (Segment(
            "s", 100, elevation_gain_m=max(0, rise), elevation_loss_m=max(0, -rise),
            elevation_profile=(ElevationSample(0, 0), ElevationSample(100, rise)),
        ),))
        propulsion = ConstantPower(150) if constant else ReferenceSpeedPower(150, road_load=engine.road_load)
        result = engine.traverse(arc, mobility, propulsion,
            initial_state=SimulationState.from_speed(speed),
            environment=Environment(headwind_mps=wind, safe_max_speed_mps=cap))
        cases.append(dict(rise=rise, speed=speed, wind=wind, cap=cap, constant=constant,
                          expected={name: getattr(result, name) for name in (
                              "time_s", "final_speed_mps", "rider_energy_j", "mechanical_energy_j",
                              "aero_loss_j", "rolling_loss_j", "braking_energy_j",
                              "kinetic_assist_j", "climb_entry_kinetic_j")}))
    for direction, speed, rise in itertools.product((0, 90, 180, 270), (0, 5), (-5, 0, 5)):
        engine = MotionEngine()
        arc = Arc('vector',1,2,(Segment('s',100,geometry=((0,0),(.001,0)),
            elevation_gain_m=max(0,rise),elevation_loss_m=max(0,-rise),
            elevation_profile=(ElevationSample(0,0),ElevationSample(100,rise))),))
        result = engine.traverse(arc,mobility,ReferenceSpeedPower(150,road_load=engine.road_load),
            initial_state=SimulationState.from_speed(speed),
            environment=Environment(safe_max_speed_mps=None,wind_speed_mps=5,wind_from_deg=direction))
        cases.append(dict(rise=rise,speed=speed,wind=0,cap=None,constant=False,
            windSpeed=5,windFrom=direction,expected={name:getattr(result,name) for name in (
                'time_s','final_speed_mps','rider_energy_j','mechanical_energy_j',
                'aero_loss_j','rolling_loss_j','braking_energy_j','kinetic_assist_j','climb_entry_kinetic_j')}))
    return dict(schema="physics-parity@1", mobility=asdict(mobility), cases=cases)


def route_cases():
    from router.core.data.graph.model import RoutingGraph, Node
    from router.core.search.astar import MultiObjectiveAStar
    from router.core.search.state import route_arcs
    from router.core.search.config import SearchConfig
    from router.core.physics.motion import MotionTraversalModel
    scenarios = []
    # A direct hill, a longer level bypass and a descent before a climb.
    for wind, exact in itertools.product((0, -5, 5), (False, True)):
        for profile in ('fastest', 'shortest', 'flattest', 'balanced', 'electric'):
            graph = RoutingGraph()
            for i in range(5):
                graph.add_node(Node(i, 0, i*.001))
            arcs = []
            for name, source, target, length, low, high in (
                ('a',0,1,100,0,10),('b',1,4,100,10,0),
                ('c',0,2,160,0,0),('d',2,4,160,0,0),
                ('e',0,3,120,0,-8),('f',3,4,140,-8,0),
            ):
                graph.add_arc(Arc(name,source,target,(Segment(name,length,
                    elevation_gain_m=max(0,high-low), elevation_loss_m=max(0,low-high),
                    elevation_profile=(ElevationSample(0,low),ElevationSample(length,high))),)))
                arcs.append(dict(id=name,fromNode=source,to=target,meters=length,
                    samples=[dict(distance=0,elevation=low),dict(distance=length,elevation=high)]))
            mobility = MobilityProfile.create(rider_mass_kg=75,vehicle_mass_kg=12,power_w=150,
                cda_m2=.55,rolling_resistance=.007,drive_efficiency=.97)
            engine = MotionEngine()
            traversal = MotionTraversalModel(engine,mobility,
                ReferenceSpeedPower(150,road_load=engine.road_load),
                Environment(headwind_mps=wind,safe_max_speed_mps=None))
            result = MultiObjectiveAStar(graph,traversal,profile,
                config=SearchConfig(exact_speed=exact),goal_pruning=True).search(0,4)
            scenarios.append(dict(profile=profile,wind=wind,exact=exact,arcs=arcs,
                routes=[dict(arcs=route_arcs(l),time=l.time_s,distance=l.distance_m,
                             ascent=l.ascent_m,energy=l.energy_j) for l in result.labels]))
    return scenarios


def network_cases(cases):
    from router.core.prediction.route_network import train_route_network
    from router.core.search.objectives import objectives_for
    import math
    parts=[]
    for i,c in enumerate(cases[:240:4]):
        p=c['expected']
        parts.append(dict(arcId=str(i//2),distance=100,time=p['time_s'],
            ascent=max(0,c['rise']),descent=max(0,-c['rise']),initialSpeed=c['speed'],
            rolling=p['rolling_loss_j'],aero=p['aero_loss_j'],coverage=1,wheelWork=p['mechanical_energy_j']))
    models=[]
    for profile in ('fastest','shortest','flattest','balanced','electric'):
        objectives=objectives_for(profile).objectives
        samples=[]
        for p in parts:
            values=dict(time_s=p['time'],distance_m=p['distance'],ascent_m=p['ascent'],energy_j=p['wheelWork'])
            features=(math.log1p(p['distance']),p['ascent']/100,p['descent']/100,
                p['initialSpeed'],p['rolling']/100,p['aero']/100,1)
            samples.append((p['arcId'],features,tuple(values[o]/100 for o in objectives)))
        model=train_route_network(profile,objectives,samples)
        models.append(dict(profile=profile,model=model.to_dict(),influence=model.feature_influence()))
    return dict(parts=parts,models=models)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = generate()
    payload['routeCases'] = route_cases()
    payload['network'] = network_cases(payload['cases'])
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Generated {len(payload['cases'])} physics and {len(payload['routeCases'])} route cases: {args.output}")
