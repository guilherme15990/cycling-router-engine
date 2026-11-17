"""Run Python on TopoRide's exact hydrated and snapped saved graph."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from router.core.data.graph.model import RoutingGraph,Node,Arc,Segment,ElevationSample
from router.core.data.mobility import MobilityProfile
from router.core.physics.environment import Environment
from router.core.physics.motion import MotionEngine,MotionTraversalModel
from router.core.physics.propulsion import ReferenceSpeedPower
from router.core.search.astar import MultiObjectiveAStar
from router.core.search.state import route_arcs
from router.core.search.config import SearchConfig

data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
graph=RoutingGraph()
for id,point in data['nodes']:
    graph.add_node(Node(id,point['lat'],point['lon']))
for a in data['arcs']:
    if a.get('allowed') is False:
        continue
    samples=tuple(ElevationSample(p['distance'],p['elevation']) for p in a['samples'])
    gain=sum(max(0,b.elevation_m-a.elevation_m) for a,b in zip(samples,samples[1:]) if a.elevation_m is not None and b.elevation_m is not None)
    loss=sum(max(0,a.elevation_m-b.elevation_m) for a,b in zip(samples,samples[1:]) if a.elevation_m is not None and b.elevation_m is not None)
    graph.add_arc(Arc(a['id'],a['from'],a['to'],(Segment(a['id'],a['meters'],
        elevation_gain_m=gain,elevation_loss_m=loss,elevation_profile=samples,
        geometry=tuple((p['lat'],p['lon']) for p in a['points']),
        max_speed_kmh=a['maxSpeedMps']*3.6 if a.get('maxSpeedMps') is not None else None),)))
motion=MotionEngine()
mobility=MobilityProfile.create(rider_mass_kg=75,vehicle_mass_kg=12,power_w=150,cda_m2=.55,rolling_resistance=.007,drive_efficiency=.97)
traversal=MotionTraversalModel(motion,mobility,ReferenceSpeedPower(150,road_load=motion.road_load),Environment(safe_max_speed_mps=None))
result=MultiObjectiveAStar(graph,traversal,data['profile'],config=SearchConfig(max_expansions=60000),goal_pruning=True).search(data['start'],data['end'])
routes=[dict(arcs=route_arcs(l),time=l.time_s,distance=l.distance_m,ascent=l.ascent_m,energy=l.energy_j) for l in result.labels]
ts_paths={tuple(r['arcs']) for r in data['ts']['routes']}
py_paths={tuple(r['arcs']) for r in routes}
report=dict(python_count=len(routes),ts_count=len(ts_paths),common_paths=len(py_paths&ts_paths),
    python_only=len(py_paths-ts_paths),ts_only=len(ts_paths-py_paths),python_truncated=result.truncated,
    ts_truncated=data['ts']['truncated'],best_python=min(routes,key=lambda r:(r['ascent'],r['distance']),default=None),
    best_ts=min(data['ts']['routes'],key=lambda r:(r['ascent'],r['distance']),default=None),routes=routes)
metric_errors=[]
ts_by_path={tuple(r['arcs']):r for r in data['ts']['routes']}
for route in routes:
    other=ts_by_path.get(tuple(route['arcs']))
    if other:
        for name in ('time','distance','ascent','energy'):
            # Python rounds the distance of each arc to millimetres.
            tolerance=.00051*len(route['arcs']) if name=='distance' else 1e-7+1e-9*abs(other[name])
            if abs(route[name]-other[name])>tolerance:
                metric_errors.append(dict(path=route['arcs'],metric=name,python=route[name],typescript=other[name]))
report['metric_errors']=metric_errors
Path(sys.argv[2]).write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:({field:value for field,value in v.items() if field!='arcs'} if k.startswith('best_') and v else v)
    for k,v in report.items() if k!='routes'},indent=2))
sys.exit(1 if py_paths!=ts_paths or metric_errors or result.truncated or data['ts']['truncated'] else 0)
