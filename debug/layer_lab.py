"""Interface da bancada de inspeção das camadas."""
import json
import streamlit as st
from layer_trace import LAYERS, LayerTrace, snapshot


def _table(rows):
    """Tabela textual: este ambiente bloqueia a DLL do PyArrow."""
    if not rows:
        return
    columns = list(rows[0])
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(map(cell, columns)) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(cell(row.get(column, "")) for column in columns) + " |" for row in rows]
    st.markdown("\n".join(lines))


def init_state():
    from router.integration import Environment, MotionEngine, SnapOptions
    env, motion, snap = Environment(), MotionEngine(), SnapOptions()
    defaults = dict(job_timeout=0.0, capture_layers=True, trace_samples=1, wind_mps=env.headwind_mps,
                    air_density=env.air_density_kg_m3, gravity=env.gravity_m_s2,
                    speed_cap_kmh=env.safe_max_speed_mps * 3.6, initial_kmh=0.0,
                    physics_step=motion.geometry.step_m, require_elevation=False,
                    snap_radius=snap.max_distance_m, bbox_padding=80.0,
                    progress_event_every=4, exact_speed=False, max_expansions=0,
                    mobility_vehicle="bicycle", mobility_propulsion="manual",
                    mobility_rider_mass=0.0, mobility_vehicle_mass=0.0, mobility_load=0.0,
                    mobility_power=0.0, mobility_power_unit="W",
                    manual_fitness="recreational", manual_age=None, ml_route_enabled=True)
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_controls():
    st.number_input("Limite de tempo da consulta (s; 0 = sem limite)", min_value=0.0, max_value=3600.0, step=5.0, key="job_timeout")
    st.caption("A preparação e a busca são isoladas num worker. Cancelar preserva a última rota validada publicada.")
    with st.expander("Captura das camadas"):
        st.checkbox("Capturar entradas, cálculos e saídas das camadas", key="capture_layers")
        st.number_input("Amostras por função", min_value=1, max_value=10, step=1, key="trace_samples")
        st.caption("A instrumentação acompanha a toolkit real e acrescenta custo à consulta.")
    with st.expander("Ambiente, simulação e snapping"):
        a, b, c = st.columns(3)
        a.number_input("Vento frontal (m/s; negativo = cauda)", min_value=-50.0, max_value=50.0, key="wind_mps")
        b.number_input("Densidade do ar (kg/m³)", min_value=0.0, max_value=3.0, key="air_density")
        c.number_input("Gravidade (m/s²)", min_value=0.1, max_value=30.0, key="gravity", format="%.5f")
        a, b, c = st.columns(3)
        a.number_input("Teto de velocidade (km/h)", min_value=1.0, max_value=200.0, key="speed_cap_kmh")
        b.number_input("Velocidade inicial (km/h)", min_value=0.0, max_value=200.0, key="initial_kmh")
        c.number_input("Passo da simulação (m)", min_value=0.5, max_value=100.0, key="physics_step")
        st.checkbox("Exigir altimetria na simulação", key="require_elevation")
        st.number_input("Distância máxima de snapping (m)", min_value=1.0, max_value=5000.0, key="snap_radius")
    with st.expander("Busca e publicação progressiva"):
        st.checkbox("Comparar velocidades sem buckets (pode aumentar o custo)", key="exact_speed")
        st.number_input("Limite de expansões (0 = sem limite)", min_value=0, step=1000, key="max_expansions")
        st.number_input("Publicar preview a cada N candidatas", min_value=1, max_value=100, step=1, key="progress_event_every")
        st.caption("A busca usa toda a rede preparada para a categoria. Aproximações e interrupções ficam explícitas no contrato.")


def _execute_mobility_with_state(s, request, graph_path, *, on_plan=None, should_cancel=None, on_stage=None):
    from contextlib import nullcontext
    from mobility_debug import execute as execute_toolkit
    if request.get("rider"):
        # sys.monitoring registra somente códigos já carregados no início.
        from router.integration import ManualRiderProfile
    if request.get("ml_route"):
        from router.app import neural_routing
        from router.core.search import learned
    settings = vars(s) if not isinstance(s, dict) else s
    trace = LayerTrace(samples=int(settings["trace_samples"])) if settings["capture_layers"] else None
    inspect = {}
    plan = None
    s.layer_report = None
    try:
        with trace if trace is not None else nullcontext():
            plan = execute_toolkit(request, settings, graph_path, on_plan=on_plan,
                                   should_cancel=should_cancel, on_stage=on_stage, inspect=inspect)
            plan.to_json()  # Native toolkit serialization is part of the trace.
        return plan
    finally:
        s.prepared_graph_path = inspect.get("prepared_graph_path")
        if trace is not None:
            report = trace.report()
            engine = inspect.get("engine")
            motion = engine.motion if engine is not None else None
            report["performance"] = {
                "api": "router.integration.MobilityEngine.plan",
                "search_orchestration": "router.core.search.candidate_search.search_candidates",
                "search_scope": "provided_graph",
                "preparation_s": inspect.get("preparation_s"),
                "prepared_graph_path": inspect.get("prepared_graph_path"),
                "physics_cache": {
                    "profiles": len(motion.geometry._profiles), "steps": len(motion.geometry._steps),
                    "equilibrium_entries": len(motion._equilibrium),
                } if motion is not None else None,
                "result": plan.diagnostics if plan is not None else None,
                "optional_layers": {"04": "Baseline provisório de RPE por perfil." if request.get("rider") else "Nenhum RPE solicitado nesta consulta."},
            }
            report["request"] = snapshot(request)
            report["neural_routing"] = plan.diagnostics.get("neural_routing") if plan is not None else None
            s.layer_report = report


def render():
    st.title("Inspeção por camada", icon=":material/biotech:")
    st.caption("Configure o cenário em Consulta e execute a API com a captura habilitada. Depois selecione uma camada e uma chamada para conferir suas entradas, cálculos e retorno.")
    report = st.session_state.get("layer_report")
    if report is None:
        if st.session_state.get("route_job") is not None:
            st.info(
                "Captura em andamento. As rotas validadas aparecem em Consulta; "
                "os detalhes das camadas ficam disponíveis quando a execução terminar ou for cancelada."
            )
        else:
            st.info("Nenhuma execução capturada. Execute a consulta para iniciar a inspeção.")
        for number, title in LAYERS.items():
            st.markdown(f"**{number} · {title}**")
        from ml_debug import render as render_ml
        render_ml(None, _table)
        return
    if report.get("failure"):
        st.error(report["failure"])
    st.caption(f"Captura da última execução: {report['elapsed']:.3f} s. Durações incluem instrumentação e chamadas filhas; não devem ser somadas nem usadas como benchmark.")
    performance = report.get("performance")
    if performance:
        st.subheader("Diagnóstico das melhorias", icon=":material/speed:")
        spatial = performance.get("spatial_index") or {}
        cache = performance.get("physics_cache") or {}
        preview = performance.get("preview") or {}
        result = performance.get("result") or {}
        _table([
            {"Item": "Orquestração da busca", "Valor": performance.get("search_orchestration", "—")},
            {"Item": "Escopo", "Valor": performance.get("search_scope", "—")},
            {"Item": "Índice espacial persistente", "Valor": "sim" if spatial.get("persistent_grid") else "não"},
            {"Item": "Cache físico", "Valor": f"{cache.get('profiles', 0)} perfis · {cache.get('steps', 0)} passos · {cache.get('equilibrium_entries', 0)} equilíbrios"},
            {"Item": "Publicação do preview", "Valor": f"a cada {preview.get('progress_event_every', '—')} candidata(s)"},
            {"Item": "Geometria / avaliações do preview", "Valor": "reutilizadas" if preview.get("geometry_reused") and preview.get("preview_assessments_reused") else "não confirmado"},
            {"Item": "Resultado", "Valor": f"{result.get('routes', 0)} rota(s) · completo={result.get('search_complete', '—')} · ótimo={result.get('optimality_proven', '—')}"},
        ])
        st.caption("Esses indicadores descrevem o caminho executado e o estado dos caches; os tempos da captura continuam inadequados para benchmark.")
    with st.expander("Cenário enviado à API"):
        st.json(report.get("request", {}), expanded=False)
    functions = report["functions"]
    events = report["events"]
    overview = []
    for number, title in LAYERS.items():
        calls = sum(row["calls"] for row in functions if row["layer"] == number)
        overview.append({"Camada": f"{number} · {title}", "Chamadas amostradas": calls,
                         "Estado": "Observada" if calls else "Sem chamada observada"})
    _table(overview)
    layer = st.selectbox("Camada para inspecionar", list(LAYERS),
                         format_func=lambda key: f"{key} · {LAYERS[key]}", key="inspect_layer")
    if layer == "04":
        from ml_debug import render as render_ml
        render_ml(report, _table)
    layer_functions = [row for row in functions if row["layer"] == layer]
    if not layer_functions:
        st.info("Nenhum ponto instrumentado dessa camada foi chamado neste cenário. Isso não comprova que toda a camada está ausente; confira a falha e o fluxo anterior.")
        return
    _table([{"Função": row["function"], "Amostras": row["calls"], "Tempo inclusivo (s)": round(row["inclusive_seconds"], 5)} for row in layer_functions])
    candidates = [event for event in events if event["layer"] == layer]
    st.caption(f"Entradas e saídas são amostras limitadas às primeiras {report['samples_per_function']} chamadas por função. Coleções grandes aparecem truncadas, com contagem; o JSON final completo continua disponível em Consulta.")
    index = st.selectbox("Chamada capturada (ordem real de entrada)", range(len(candidates)),
                         format_func=lambda i: f"#{candidates[i]['order']} · {candidates[i]['function']}",
                         key=f"inspect_call_{layer}")
    event = candidates[index]
    st.caption(f"{event['file']}:{event['line']} · {event.get('seconds', 0):.6f} s")
    if event["exceptions"]:
        st.warning("Exceções observadas nesta chamada (podem ter sido tratadas pelo motor): " + "; ".join(event["exceptions"]))
    inputs, calculations, output, source = st.tabs(["Entrada", "Cálculos intermediários", "Saída", "Implementação executada"])
    with inputs:
        st.json(event["inputs"], expanded=False)
    with calculations:
        st.caption("Variáveis locais no retorno da função: valores efetivamente usados ou calculados nessa chamada.")
        st.json(event.get("calculations", {}), expanded=False)
    with output:
        st.json(event.get("output"), expanded=False)
    with source:
        st.code(report["sources"].get(event["function"], "Fonte indisponível."), language="python")
    with st.expander("Linha do tempo da captura"):
        _table([{"Ordem": item["order"], "Camada": item["layer"], "Função": item["function"], "Tempo inclusivo (s)": round(item.get("seconds", 0), 5)} for item in events])
    st.download_button("Baixar diagnóstico das camadas", json.dumps(report, ensure_ascii=False, indent=2),
                       file_name="camadas-e2e.json", mime="application/json", key="layer_report_download")


def start_job(**request):
    import os
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path
    s = st.session_state
    if s.get('route_job') is not None:
        raise ValueError('Já existe uma consulta em execução. Cancele ou aguarde o resultado.')
    graph_path = s.get('graph_path')
    if not graph_path or not Path(graph_path).is_file():
        raise ValueError('Carregue um grafo de arquivo antes de executar a consulta isolada.')
    settings_keys = ('capture_layers', 'trace_samples', 'wind_mps', 'air_density', 'gravity',
        'speed_cap_kmh', 'initial_kmh', 'physics_step', 'require_elevation', 'snap_radius',
        'progress_event_every', 'exact_speed', 'max_expansions')
    settings = {key: s[key] for key in settings_keys}
    source = Path(s.pbf_path_input).expanduser()
    if not source.is_absolute():
        source = Path(__file__).resolve().parents[1] / source
    settings.update(pbf_path=str(source), dem_dir=str(Path(__file__).resolve().parents[1] / 'data/dem'))
    cache = Path(__file__).resolve().parent / '.cache' / 'jobs'
    cache.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='consulta-', dir=cache))
    payload = dict(graph_path=str(graph_path), settings=settings, request=request, timeout=float(s.job_timeout))
    input_path, output_path = folder / 'input.json', folder / 'result.json'
    input_path.write_text(json.dumps(payload), encoding='utf-8')
    (folder / 'heartbeat').touch()
    with (folder / 'worker.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('layer_worker.py')),
            str(input_path), str(output_path)], cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    s.route_job = dict(process=process, folder=str(folder), output=str(output_path),
                       started=time.monotonic(), timeout=float(s.job_timeout), request=request,
                       progress_version=0, cancel_started=None)
    s.last_plan = None
    s.last_error = None
    s.layer_report = None
    s.progress_status = "searching"
    s.progress_message = "Buscando a primeira rota validada."


@st.fragment(run_every=1)
def render_job():
    import time
    from datetime import datetime
    from pathlib import Path
    from mobility_debug import read_plan
    s = st.session_state
    job = s.get('route_job')
    if job is None:
        return
    process = job['process']
    elapsed = time.monotonic() - job['started']
    Path(job['folder'], 'heartbeat').touch()
    cancel = st.button('Cancelar consulta', icon=':material/stop:', key='cancel_route_job',
                       disabled=job['cancel_started'] is not None)
    expired = job['timeout'] > 0 and elapsed >= job['timeout']
    if (cancel or expired) and job['cancel_started'] is None:
        Path(job['folder'], 'cancel').touch()
        job['cancel_started'] = time.monotonic()
        job['cancel_reason'] = 'Tempo limite atingido' if expired else 'Cancelada pelo usuário'
    if job['cancel_started'] is not None and time.monotonic() - job['cancel_started'] > 5 and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except Exception:
            process.kill()
            process.wait(timeout=2)
    progress = Path(job['folder'], 'progress.json')
    if progress.is_file():
        try:
            version = progress.stat().st_mtime_ns
            if version != job['progress_version']:
                event = json.loads(progress.read_text(encoding='utf-8'))
                job['progress_version'] = version
                s.progress_status = event['status']
                details = event.get('details') or {}
                if event['status'] in ('preparing', 'searching', 'route_found'):
                    zone = "Preparando rede da categoria" if event['status'] == 'preparing' else ""
                    if details.get('zone_nodes') is not None:
                        zone = f" · zona {details['zone_nodes']:,} nós/{details.get('zone_arcs', 0):,} arcos"
                    s.progress_message = (
                        f"Busca em andamento · perna {event.get('leg') or 1}/{event.get('legs') or 1}"
                        f" · busca {details.get('iteration', '—')}{zone}"
                    )
                if event.get('plan'):
                    candidate = read_plan(event['plan'])
                    signature = tuple((getattr(route, 'route_id', getattr(route, 'id', None)), len(route.geometry)) for route in candidate.routes)
                    if signature != job.get('plan_signature'):
                        job['plan_signature'] = signature
                        s.last_plan = candidate
                        req = job['request']
                        s.last_run = dict(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            elapsed=elapsed, profile=req['profile'],
                            vehicle=req.get('vehicle', 'bicycle'), propulsion=req.get('propulsion_type', 'manual'))
                        s.selected_plan_key += 1
                        s.selected_route = next((i for i, route in enumerate(candidate.routes) if getattr(route, 'recommended', False) or getattr(route, 'id', None) == getattr(candidate, 'recommended_route_id', '__none__')), 0)
                        s.map_fit_key = f'plan:{time.time()}'
                        st.rerun(scope='app')
        except (OSError, ValueError, KeyError):
            pass  # O próximo ciclo volta a ler o arquivo substituído atomicamente.
    code = process.poll()
    if code is None:
        suffix = f" / {job['timeout']:g} s" if job['timeout'] else ""
        state = 'Cancelando e preservando o último resultado…' if job['cancel_started'] is not None else s.progress_message
        st.info(f"{state} · {elapsed:.0f} s{suffix}")
        return
    s.route_job = None
    output = Path(job['output'])
    try:
        if not output.is_file():
            if code == 124 or job['cancel_started'] is not None:
                raise TimeoutError(job.get('cancel_reason', 'Consulta interrompida pelo tempo limite.'))
            tail = (Path(job['folder']) / 'worker.log').read_text(encoding='utf-8', errors='replace')[-5000:]
            raise RuntimeError(f'Processo da consulta terminou com código {code}. {tail}')
        result = json.loads(output.read_text(encoding='utf-8'))
        s.layer_report = result.get('report')
        prepared_path = result.get('prepared_graph_path')
        if prepared_path and prepared_path != s.graph_path:
            from router.integration import load_graph
            s.graph = load_graph(prepared_path)
            s.graph_path = prepared_path
            nodes = tuple(s.graph.nodes.values())
            if nodes:
                s.graph_bounds = [[min(n.lat for n in nodes), min(n.lon for n in nodes)], [max(n.lat for n in nodes), max(n.lon for n in nodes)]]
        if result.get('error'):
            raise RuntimeError(result['error'])
        status = result.get('status', 'interrupted')
        s.progress_status = status
        s.progress_message = (result.get('details') or {}).get('reason', '')
        if not result.get('plan'):
            if status == 'interrupted':
                s.last_error = s.progress_message or 'Busca interrompida antes da primeira rota.'
                st.rerun(scope='app')
            raise RuntimeError('A API não retornou um plano.')
        plan = read_plan(result['plan'])
        s.last_plan = plan
        req = job['request']
        s.last_run = dict(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         elapsed=result['elapsed'], profile=req['profile'],
                         vehicle=req.get('vehicle', 'bicycle'), propulsion=req.get('propulsion_type', 'manual'))
        s.selected_plan_key += 1
        s.selected_route = next((i for i, route in enumerate(plan.routes) if getattr(route, 'recommended', False) or getattr(route, 'id', None) == getattr(plan, 'recommended_route_id', '__none__')), 0)
        s.map_fit_key = f'plan:{time.time()}'
    except Exception as exc:
        s.last_error = str(exc)
        s.progress_status = 'interrupted'
        s.progress_message = str(exc)
    st.rerun(scope='app')
