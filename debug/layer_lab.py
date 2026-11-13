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
    from router.core.physics.environment import Environment
    from router.core.physics.traversal import PhysicalTraversal
    from router.core.data.snapping import SnapOptions
    env, traversal, snap = Environment(), PhysicalTraversal(), SnapOptions()
    defaults = dict(job_timeout=0.0, capture_layers=True, trace_samples=1, wind_mps=env.headwind_mps,
                    air_density=env.air_density_kg_m3, gravity=env.gravity_m_s2,
                    speed_cap_kmh=env.safe_max_speed_mps * 3.6, initial_kmh=0.0,
                    physics_step=traversal.step_m, require_elevation=False,
                    snap_radius=snap.max_distance_m,
                    bbox_padding=80.0, use_full_graph=False,
                    progress_event_every=4, max_workers=4,
                    predictor_enabled=False, predictor_store="debug/.cache/models", predictor_version="",
                    rider_enabled=False, rider_ftp=0.0, rider_hr_max=0.0, rider_hr_rest=0.0,
                    pacing_enabled=True, reference_speed_kmh=15.0)
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_controls():
    st.number_input("Limite de tempo da consulta (s; 0 = sem limite)", min_value=0.0, max_value=3600.0, step=5.0, key="job_timeout")
    st.caption("A busca apresenta rotas validadas enquanto continua. O limite opcional inclui carregar o grafo; cancelar preserva o último resultado parcial.")
    with st.expander("Captura das camadas", expanded=True):
        st.checkbox("Capturar entradas, cálculos e saídas das camadas", key="capture_layers")
        st.number_input("Amostras por função", min_value=1, max_value=10, step=1, key="trace_samples")
        st.caption("A captura acompanha a API real na thread desta consulta e acrescenta custo de depuração. As primeiras chamadas são observadas; entradas e saídas são amostradas para limitar o volume.")
    with st.expander("Ambiente, simulação e snapping"):
        a, b, c = st.columns(3)
        a.number_input("Vento frontal (m/s; negativo = cauda)", min_value=-50.0, max_value=50.0, key="wind_mps")
        b.number_input("Densidade do ar (kg/m³)", min_value=0.0, max_value=3.0, key="air_density")
        c.number_input("Gravidade (m/s²)", min_value=0.1, max_value=30.0, key="gravity", format="%.5f")
        a, b, c = st.columns(3)
        a.number_input("Teto de velocidade (km/h)", min_value=1.0, max_value=200.0, key="speed_cap_kmh")
        b.number_input("Velocidade inicial (km/h)", min_value=0.0, max_value=200.0, key="initial_kmh")
        c.number_input("Passo da simulação na busca (m)", min_value=0.5, max_value=100.0, key="physics_step")
        st.caption("A avaliação completa usa o modo definido pelo próprio motor; a captura mostra o simulador efetivamente utilizado em cada chamada.")
        st.checkbox("Exigir altimetria na simulação", key="require_elevation")
        st.number_input("Distância máxima de snapping (m)", min_value=1.0, max_value=5000.0, key="snap_radius")
    with st.expander("Envelope de busca e bbox"):
        st.number_input("Margem do bbox (m)", min_value=0.0, max_value=10000.0, key="bbox_padding")
        st.checkbox(
            "Buscar no grafo inteiro (expansão explícita)",
            key="use_full_graph",
            help="Desligado: usa apenas o corredor dos pontos e a margem informada. Ligado: o usuário autoriza a cobertura do grafo carregado.",
        )
        st.caption("A busca ocorre uma única vez. Por padrão, o corredor é definido pelos pontos e pela margem; o índice espacial persistente evita varrer o grafo inteiro ao recortar a BBox. O box final de exibição/altimetria cobre o traçado devolvido.")
    with st.expander("Busca e publicação progressiva"):
        st.number_input(
            "Publicar preview a cada N candidatas",
            min_value=1,
            max_value=100,
            step=1,
            key="progress_event_every",
            help="A primeira candidata é publicada imediatamente; depois, o preview é atualizado a cada N candidatas. Isso reduz avaliações e eventos repetidos.",
        )
        st.number_input(
            "Máximo de pernas simultâneas",
            min_value=1,
            max_value=16,
            step=1,
            key="max_workers",
            help="Aplicado apenas quando há paradas. Cada perna mantém seus caches mutáveis isolados.",
        )
        st.caption("A captura do debug assina os eventos porque precisa mostrar o progresso. Um consumidor sem assinante não executa preview.")
    with st.expander("Preditor ML local e dados fisiológicos"):
        st.checkbox("Carregar preditor de um artefato local", key="predictor_enabled")
        st.text_input("Diretório do ModelStore", key="predictor_store")
        st.text_input("Versão do modelo registrado", key="predictor_version")
        st.caption("Sem artefato, a API mantém a estimativa de intensidade e declara a indisponibilidade da predição calibrada. Não há treino automático nesta chamada.")
        st.checkbox("Enviar perfil fisiológico", key="rider_enabled")
        a, b, c = st.columns(3)
        a.number_input("FTP medida (W; 0 = ausente)", min_value=0.0, max_value=2000.0, key="rider_ftp")
        b.number_input("FC máxima informada (bpm; 0 = ausente)", min_value=0.0, max_value=250.0, key="rider_hr_max")
        c.number_input("FC repouso informada (bpm; 0 = ausente)", min_value=0.0, max_value=200.0, key="rider_hr_rest")
    with st.expander("Ritmo planejado (cartão de esforço)"):
        st.checkbox("Aplicar política de ritmo `hold-reference-speed@2`", key="pacing_enabled")
        st.number_input("Velocidade de referência (km/h)", min_value=1.0, max_value=60.0, step=0.5, key="reference_speed_kmh")
        st.caption("A velocidade de referência é o ritmo planejado declarado — nunca derivada da potência. Com a política ativa, a potência varia por arco e o orçamento anaeróbio (W′) é contabilizado no próprio ritmo; sem ela, mantém-se a potência constante.")


def _execute_with_state(s, api, **request):
    from pathlib import Path
    from router.core.physics.environment import Environment
    from router.core.physics.traversal import PhysicalTraversal
    from router.core.data.snapping import SnapOptions
    from router.core.physiology.prediction import RiderProfile
    from router.core.prediction.model_store import ModelStore
    from router.core.prediction.predictor import load_predictor
    from router.integration import dumps
    from contextlib import nullcontext
    trace = LayerTrace(samples=int(s.trace_samples)) if s.capture_layers else None
    s.layer_report = None
    options = {}
    plan = None
    try:
        with trace if trace is not None else nullcontext():
            options = dict(environment=Environment(headwind_mps=s.wind_mps,
                           air_density_kg_m3=s.air_density, gravity_m_s2=s.gravity,
                           safe_max_speed_mps=s.speed_cap_kmh / 3.6),
                           traversal=PhysicalTraversal(step_m=s.physics_step, require_elevation=s.require_elevation),
                           snap_options=SnapOptions(max_distance_m=s.snap_radius),
                           initial_speed_mps=s.initial_kmh / 3.6,
                           pad_m=float(getattr(s, "bbox_padding", 80.0)),
                           use_full_graph=bool(getattr(s, "use_full_graph", False)),
                           progress_event_every=int(getattr(s, "progress_event_every", 4)),
                           max_workers=int(getattr(s, "max_workers", 4)))
            if s.predictor_enabled:
                if not s.predictor_version.strip():
                    raise ValueError("Informe a versão registrada do preditor local.")
                store_path = Path(s.predictor_store).expanduser()
                if not store_path.is_absolute():
                    store_path = Path(__file__).resolve().parents[1] / store_path
                options["predictor"] = load_predictor(ModelStore(store_path), s.predictor_version.strip())
            if s.rider_enabled:
                options["rider"] = RiderProfile(weight_kg=request["cyclist"].mass_kg,
                    ftp_w=s.rider_ftp or None, max_heart_rate_bpm=s.rider_hr_max or None,
                    resting_heart_rate_bpm=s.rider_hr_rest or None)
            if getattr(s, "pacing_enabled", False):
                from router.app.planning import reference_pacing_for
                options["pacing"] = reference_pacing_for(
                    request["cyclist"], options.get("rider"),
                    reference_speed_kmh=float(s.reference_speed_kmh))
            plan = api(**request, **options)
            if plan is not None:
                dumps(plan)  # Exercita também a serialização pública na captura.
            return plan
    except Exception as exc:
        if trace is not None and not getattr(trace, "failure", None):
            trace.failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if trace is not None:
            report = trace.report()
            graph = request.get("graph")
            traversal = options.get("traversal")
            spatial_index = None
            if graph is not None and hasattr(graph, "has_spatial_index"):
                spatial_index = {
                    "persistent_grid": bool(graph.has_spatial_index()),
                    "node_index": type(getattr(graph, "_node_spatial_index", None)).__name__
                    if getattr(graph, "_node_spatial_index", None) is not None else None,
                    "arc_index": type(getattr(graph, "_arc_spatial_index", None)).__name__
                    if getattr(graph, "_arc_spatial_index", None) is not None else None,
                }
            report["performance"] = {
                "search_orchestration": "router.core.search.candidate_search.search_candidates",
                "search_scope": "provided_graph" if bool(getattr(s, "use_full_graph", False)) else "corridor_bbox",
                "spatial_index": spatial_index,
                "physics_cache": {
                    "profiles": len(getattr(traversal, "_profiles", {})),
                    "steps": len(getattr(traversal, "_steps", {})),
                    "equilibrium_entries": len(getattr(traversal, "_equilibrium", {})),
                } if traversal is not None else None,
                "preview": {
                    "subscribed": True,
                    "progress_event_every": int(getattr(s, "progress_event_every", 4)),
                    "max_workers": int(getattr(s, "max_workers", 4)),
                    "geometry_reused": True,
                    "preview_assessments_reused": True,
                },
                "result": {
                    "routes": len(getattr(plan, "routes", ()) or ()) if plan is not None else 0,
                    "search_mode": getattr(plan, "search_mode", None) if plan is not None else None,
                    "search_complete": getattr(plan, "search_complete", None) if plan is not None else None,
                    "optimality_proven": getattr(plan, "optimality_proven", None) if plan is not None else None,
                },
            }
            report["request"] = {k: snapshot(v) for k, v in {**request, **options}.items()}
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


def execute(api, **request):
    return _execute_with_state(st.session_state, api, **request)


def start_job(**request):
    import dataclasses
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
        'bbox_padding', 'use_full_graph', 'progress_event_every', 'max_workers', 'predictor_enabled',
        'predictor_store', 'predictor_version', 'rider_enabled', 'rider_ftp', 'rider_hr_max', 'rider_hr_rest',
        'pacing_enabled', 'reference_speed_kmh')
    settings = {key: s[key] for key in settings_keys}
    cache = Path(__file__).resolve().parent / '.cache' / 'jobs'
    cache.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='consulta-', dir=cache))
    request['cyclist'] = dataclasses.asdict(request['cyclist'])
    request['bicycle'] = dataclasses.asdict(request['bicycle'])
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
    from router.integration import loads
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
                if event['status'] in ('searching', 'route_found'):
                    zone = ""
                    if details.get('zone_nodes') is not None:
                        zone = f" · zona {details['zone_nodes']:,} nós/{details.get('zone_arcs', 0):,} arcos"
                    s.progress_message = (
                        f"Busca em andamento · perna {event.get('leg') or 1}/{event.get('legs') or 1}"
                        f" · busca {details.get('iteration', '—')}{zone}"
                    )
                if event.get('plan'):
                    candidate = loads(json.dumps(event['plan']))
                    signature = tuple((route.id, len(route.geometry)) for route in candidate.routes)
                    if signature != job.get('plan_signature'):
                        job['plan_signature'] = signature
                        s.last_plan = candidate
                        req = job['request']
                        s.last_run = dict(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            elapsed=elapsed, profile=req['profile'],
                            sustained_power=req['cyclist']['sustainable_power_w'], stops=len(req['stops']))
                        s.selected_plan_key += 1
                        s.selected_route = next((i for i, route in enumerate(candidate.routes) if route.id == candidate.recommended_route_id), 0)
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
        plan = loads(json.dumps(result['plan']))
        s.last_plan = plan
        req = job['request']
        s.last_run = dict(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         elapsed=result['elapsed'], profile=req['profile'],
                         sustained_power=req['cyclist']['sustainable_power_w'], stops=len(req['stops']))
        s.selected_plan_key += 1
        s.selected_route = next((i for i, route in enumerate(plan.routes) if route.id == plan.recommended_route_id), 0)
        s.map_fit_key = f'plan:{time.time()}'
    except Exception as exc:
        s.last_error = str(exc)
        s.progress_status = 'interrupted'
        s.progress_message = str(exc)
    st.rerun(scope='app')
