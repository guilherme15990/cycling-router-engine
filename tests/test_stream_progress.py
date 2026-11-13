import json
import os
import subprocess
import sys
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest
from router.integration import save_graph
from test_planning import _graph, _plan

ROOT = Path(__file__).resolve().parents[1]


def app_for_graph(tmp_path):
    graph = _graph()
    path = tmp_path / 'graph.json'
    save_graph(graph, path)
    app = AppTest.from_file(str(ROOT / 'debug/app.py'), default_timeout=30)
    app.session_state['map_altimetry'] = False
    app.session_state['graph'] = graph
    app.session_state['graph_path'] = str(path)
    app.session_state['origin_text'] = '0, 0'
    app.session_state['destination_text'] = '0.002, 0.002'
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    return app


def test_real_worker_finishes_and_returns_public_plan(tmp_path):
    app = app_for_graph(tmp_path)
    submit = next(b for b in app.button if b.label == 'Executar API e capturar camadas')
    submit.click().run()
    assert not app.exception, [e.message for e in app.exception]
    job = app.session_state['route_job']
    try:
        deadline = time.monotonic() + 45
        while job['process'].poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        assert app.session_state['progress_status'] == 'completed'
        assert app.session_state['last_plan'].routes
        assert app.session_state['layer_report'] is not None
    finally:
        if job['process'].poll() is None:
            job['process'].kill()
            job['process'].wait()


def test_partial_route_is_shown_and_survives_forced_cancel(tmp_path):
    app = app_for_graph(tmp_path)
    plan = _plan().to_contract()
    output = tmp_path / 'result.json'
    (tmp_path / 'progress.json').write_text(json.dumps({
        'status': 'route_found', 'plan': plan.to_dict(), 'leg': 1, 'legs': 1,
        'details': {'iteration': 1},
    }), encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        app.session_state['route_job'] = dict(process=proc, folder=str(tmp_path), output=str(output),
            started=time.monotonic(), timeout=0, progress_version=0, cancel_started=None,
            request={'profile': 'fastest', 'cyclist': {'sustainable_power_w': 200}, 'stops': []})
        app.session_state['progress_message'] = 'Buscando'
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        assert app.session_state['last_plan'].routes
        app.button(key='cancel_route_job').click().run()
        assert (tmp_path / 'cancel').exists()
        app.session_state['route_job']['cancel_started'] = time.monotonic() - 6
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        assert app.session_state['route_job'] is None
        assert app.session_state['progress_status'] == 'interrupted'
        assert app.session_state['last_plan'].routes
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_worker_stops_when_session_heartbeat_is_stale(tmp_path):
    graph_path = tmp_path / 'graph.json'
    save_graph(_graph(), graph_path)
    payload = {
        'graph_path': str(graph_path), 'timeout': 0,
        'request': {'origin': [0, 0], 'destination': [0.002, 0.002], 'stops': [],
            'cyclist': {'mass_kg': 75, 'max_power_w': 600, 'sustainable_power_w': 200, 'frontal_area_m2': .5},
            'bicycle': {'mass_kg': 12, 'rolling_resistance': .005, 'drag_coefficient': .9, 'drivetrain_efficiency': .97},
            'profile': 'fastest', 'max_routes': None},
        'settings': {'capture_layers': False, 'trace_samples': 1, 'wind_mps': 0., 'air_density': 1.225,
            'gravity': 9.80665, 'speed_cap_kmh': 79.2, 'initial_kmh': 0., 'physics_step': 5.,
            'require_elevation': False, 'snap_radius': 100., 'bbox_growth': 1.6,
            'bbox_padding': 80., 'predictor_enabled': False, 'predictor_store': '', 'predictor_version': '',
            'rider_enabled': False, 'rider_ftp': 0., 'rider_hr_max': 0., 'rider_hr_rest': 0.},
    }
    source, result, heartbeat = tmp_path/'input.json', tmp_path/'result.json', tmp_path/'heartbeat'
    source.write_text(json.dumps(payload), encoding='utf-8')
    heartbeat.touch()
    os.utime(heartbeat, (time.time()-40, time.time()-40))
    proc = subprocess.run([sys.executable, str(ROOT/'debug/layer_worker.py'), str(source), str(result)],
        cwd=ROOT, capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert proc.returncode == 0, proc.stderr
    outcome = json.loads(result.read_text(encoding='utf-8'))
    assert outcome['status'] == 'interrupted'
    assert 'Sessão inativa' in outcome['details']['reason']
