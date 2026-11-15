"""Uma consulta isolada; encerrar este processo nunca encerra o Streamlit."""
import json
import os
import sys
import threading
import time
import traceback
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
        # Honor an abandoned/cancelled session before importing the UI stack
        # or loading the graph. A cold import must not delay cancellation.
        heartbeat = input_path.with_name('heartbeat')
        reason = (
            'Cancelada pelo usuário' if input_path.with_name('cancel').exists() else
            'Sessão inativa por mais de 30 segundos'
            if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime > 30 else None
        )
        if reason is not None:
            result.update(status='interrupted', details={'reason': reason})
            return
        from router.integration import SearchCancelled
        from layer_lab import _execute_mobility_with_state
        request = payload['request']
        progress_path = input_path.with_name('progress.json')
        cancel_path = input_path.with_name('cancel')
        heartbeat_path = input_path.with_name('heartbeat')
        last_write = [0.0]
        latest_event = [None]
        def publish(status, plan=None, details=None):
            if plan is not None:
                latest_event[0] = plan
            now = time.monotonic()
            terminal = status in {'completed', 'no_route', 'interrupted'}
            first_route = status == 'route_found' and not getattr(publish, 'sent_route', False)
            if not terminal and not first_route and now - last_write[0] < 1:
                return
            if first_route:
                publish.sent_route = True
            data = {'status': status, 'plan': plan.to_dict() if plan is not None else None, 'details': details}
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
        try:
            plan = _execute_mobility_with_state(
                state, request, payload['graph_path'],
                on_plan=lambda plan: publish('route_found', plan), should_cancel=cancelled,
                on_stage=lambda name, details: publish(name, details=details),
            )
            result['status'] = 'completed' if plan.routes else 'no_route'
            result['plan'] = plan.to_dict()
            publish(result['status'], plan)
        except (SearchCancelled, InterruptedError):
            result.update(status='interrupted', details={'reason': last_check[2] or 'Consulta interrompida'})
            if latest_event[0] is not None:
                result['plan'] = latest_event[0].to_dict()
            publish('interrupted', latest_event[0], result['details'])
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        result.update(report=state.layer_report, elapsed=time.monotonic() - started,
                      prepared_graph_path=getattr(state, "prepared_graph_path", None))
        temp = output_path.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temp.replace(output_path)
        if timer is not None:
            timer.cancel()


if __name__ == '__main__':
    main()
