"""Uma consulta isolada; encerrar este processo nunca encerra o Streamlit."""
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    input_path, output_path = map(Path, sys.argv[1:3])
    payload = json.loads(input_path.read_text(encoding='utf-8'))
    timer = None
    if payload['timeout'] > 0:
        # Fallback caso a busca não alcance um ponto de cancelamento cooperativo.
        timer = threading.Timer(payload['timeout'] + 5, lambda: os._exit(124))
        timer.daemon = True
        timer.start()
    started = time.monotonic()
    state = SimpleNamespace(**payload['settings'], layer_report=None)
    result = {}
    try:
        from router.integration import plan_routes_progressive, load_graph, CyclistModel, BicycleModel, dumps
        from layer_lab import _execute_with_state
        request = payload['request']
        request['cyclist'] = CyclistModel(**request['cyclist'])
        request['bicycle'] = BicycleModel(**request['bicycle'])
        request['origin'] = tuple(request['origin'])
        request['destination'] = tuple(request['destination'])
        request['stops'] = tuple(map(tuple, request['stops']))
        request['graph'] = load_graph(payload['graph_path'])
        progress_path = input_path.with_name('progress.json')
        cancel_path = input_path.with_name('cancel')
        heartbeat_path = input_path.with_name('heartbeat')
        last_write = [0.0]
        latest_event = [None]
        def publish(event):
            latest_event[0] = event
            now = time.monotonic()
            terminal = event.status in {'completed', 'no_route', 'interrupted'}
            first_route = event.status == 'route_found' and not getattr(publish, 'sent_route', False)
            if not terminal and not first_route and now - last_write[0] < 1:
                return
            if first_route:
                publish.sent_route = True
            data = {'status': event.status, 'plan': event.plan.to_dict() if event.plan else None,
                    'leg': event.leg, 'legs': event.legs, 'details': event.details}
            temporary = progress_path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                temporary.replace(progress_path)
            except OSError:
                # O leitor pode estar acessando o arquivo no Windows. A busca
                # segue; o próximo evento tenta publicar novamente.
                return
            last_write[0] = now
        last_check = [0.0, False, None]
        def cancelled():
            now = time.monotonic()
            if now - last_check[0] >= .2:
                inactive = heartbeat_path.exists() and time.time() - heartbeat_path.stat().st_mtime > 30
                reason = ('Cancelada pelo usuário' if cancel_path.exists() else
                          'Tempo limite atingido' if payload['timeout'] > 0 and now - started >= payload['timeout'] else
                          'Sessão inativa por mais de 30 segundos' if inactive else None)
                last_check[:] = [now, reason is not None, reason]
            return last_check[1]
        def progressive_api(**kwargs):
            event = plan_routes_progressive(**kwargs, on_event=publish, should_cancel=cancelled)
            result['status'] = event.status
            result['details'] = ({'reason': last_check[2]} if event.status == 'interrupted' and last_check[2]
                                 else event.details)
            return event.plan
        plan = _execute_with_state(state, progressive_api, **request)
        if plan is not None:
            result['plan'] = json.loads(dumps(plan))
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        result.update(report=state.layer_report, elapsed=time.monotonic() - started)
        temp = output_path.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temp.replace(output_path)
        if timer is not None:
            timer.cancel()


if __name__ == '__main__':
    main()
