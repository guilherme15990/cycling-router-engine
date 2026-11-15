"""Interface Dash para testar a API de roteamento offline, camada a camada."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dash import Dash, Input, Output, State, dcc, html, no_update, ctx
import plotly.graph_objects as go

from mobility_debug import execute, VEHICLES, PROPULSIONS
from router.integration import Environment, MotionEngine, SnapOptions


DEFAULT_GRAPH = ROOT / "data" / "graph.json"
if not DEFAULT_GRAPH.exists():
    candidates = list((ROOT / "debug" / ".cache").glob("**/graph*.json"))
    DEFAULT_GRAPH = candidates[0] if candidates else ROOT / "debug" / ".cache" / "graph_v4.json"

JOB: dict[str, object] = {"status": "idle", "plan": None, "message": "Aguardando consulta."}
LOCK = threading.RLock()

def _figure(plan):
    fig = go.Figure()
    pts = plan.routes[0].geometry if plan and plan.routes else ()
    if pts:
        fig.add_trace(go.Scattermap(lat=[p[0] for p in pts], lon=[p[1] for p in pts], mode="lines",
                                   line={"color": "#2563eb", "width": 7}, showlegend=False))
    center = {"lat": pts[0][0], "lon": pts[0][1]} if pts else {"lat": -17.78, "lon": -50.91}
    fig.update_layout(map={"style": "open-street-map", "zoom": 12, "center": center},
                      margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=620)
    return fig


def _route_rows(plan):
    return [{"id": r.route_id, "distância_km": round(r.metrics.distance_m / 1000, 2),
             "tempo_min": round(r.metrics.time_s / 60, 1), "subida_m": round(r.metrics.ascent_m, 1),
             "trabalho_kJ": round(r.metrics.mechanical_energy_j / 1000, 1),
             "fonte_Wh": "desconhecida" if r.metrics.source_energy_j is None else round(r.metrics.source_energy_j / 3600, 1)}
            for r in plan.routes] if plan else []


def _zone_figure(plan):
    route = plan.routes[0] if plan and plan.routes else None
    if not route:
        return go.Figure().update_layout(title="Energia indisponível")
    return go.Figure(go.Bar(x=["Roda", "Entrada da transmissão"],
        y=[route.metrics.mechanical_energy_j / 1000, route.metrics.drive_input_energy_j / 1000])).update_layout(
            title="Trabalho mecânico", yaxis_title="kJ")


def _run(origin, destination, graph_path, profile, mass, power, vehicle_mass, vehicle, propulsion, pbf):
    try:
        env = Environment()
        settings = dict(physics_step=5., require_elevation=False, wind_mps=0., air_density=env.air_density_kg_m3,
                        gravity=env.gravity_m_s2, speed_cap_kmh=env.safe_max_speed_mps * 3.6,
                        initial_kmh=0., snap_radius=SnapOptions().max_distance_m,
                        pbf_path=pbf, dem_dir=str(ROOT / "data/dem"))
        def event(plan):
            with LOCK:
                JOB.update(status="route_found", plan=plan, message="Rota parcial validada; a busca continua.")
        def stage(name, details):
            with LOCK:
                JOB.update(status=name, message=name)
        plan = execute(dict(origin=origin, destination=destination, vehicle=vehicle, propulsion_type=propulsion,
            profile=profile, refinements={"rider_mass_kg": mass or None, "vehicle_mass_kg": vehicle_mass or None,
                                        "power_w": power or None}), settings, graph_path,
            on_plan=event, on_stage=stage, should_cancel=lambda: bool(JOB.get('cancel')))
        with LOCK:
            JOB.update(status="completed" if plan.routes else "no_route", plan=plan, message="Consulta concluída.")
    except Exception as exc:
        with LOCK:
            JOB.update(status="interrupted" if JOB.get('cancel') else "error", message=f"{type(exc).__name__}: {exc}")

app = Dash(__name__, title="Roteamento offline · API E2E")
app.layout = html.Div([
    html.Div([html.Span("TOPO / LAB", style={"color": "#5eead4", "letterSpacing": "0.18em", "fontWeight": "bold"}), html.H2("Bancada de roteamento", style={"margin": "8px 0"}), html.Div("MobilityEngine · rede por categoria, física e contrato nativo", style={"color": "#94a3b8"})], style={"background": "#0f172a", "color": "white", "padding": "24px 32px", "borderRadius": "12px"}),
    html.Div([html.Div([html.H4("Entrada"), dcc.Input(id="origin", value="-17.7818345,-50.9034942", style={"width": "100%"}),
              dcc.Input(id="destination", value="-17.7711026,-50.9197426", style={"width": "100%"}),
              dcc.Input(id="graph", value=str(DEFAULT_GRAPH), style={"width": "100%"}),
              html.Label("Perfil de busca"), dcc.Dropdown(id="profile", options=[{"label": x, "value": x} for x in ("fastest", "shortest", "flattest", "balanced", "electric")], value="flattest", clearable=False),
              html.Label("Veículo"), dcc.Dropdown(id="vehicle", options=[{"label": v, "value": k} for k, v in VEHICLES.items()], value="bicycle", clearable=False),
              html.Label("Propulsão"), dcc.Dropdown(id="propulsion", options=[{"label": v, "value": k} for k, v in PROPULSIONS.items()], value="manual", clearable=False),
              html.Label("PBF para preparar outra categoria"), dcc.Input(id="pbf", value=str(ROOT / "data/sources/planet_-51.73_-17.379_ff78d815.osm.pbf")),
              html.Label("Peso do usuário (kg; 0 = estimado)"), dcc.Input(id="mass", type="number", value=0, min=0),
              html.Label("Potência (W; 0 = estimada)"), dcc.Input(id="power", type="number", value=0, min=0),
              html.Label("Peso do veículo (kg; 0 = estimado)"), dcc.Input(id="bike_mass", type="number", value=0, min=0),
              html.Button("Calcular rota", id="run", n_clicks=0, style={"width": "100%"}), html.Button("Cancelar", id="cancel", n_clicks=0)], style={"width": "290px", "display": "grid", "gap": "8px"}),
             html.Div([html.H4("Camadas"), html.Ul([html.Li("01 · dados e snapping"), html.Li("02 · DEM e altimetria"), html.Li("03 · perfis e A*"), html.Li("04 · predição opcional (não executada)"), html.Li("05 · validação de circulação"), html.Li("06 · contrato")])], style={"paddingLeft": "24px"})], style={"display": "flex", "gap": "32px", "padding": "12px"}),
    html.Div([
        html.Div(id="status", children="Aguardando consulta.", style={"fontWeight": "600"}),
        html.Div(id="summary", style={"display": "flex", "gap": "24px", "padding": "8px 0"}),
    ], style={"padding": "8px"}),
    dcc.Interval(id="poll", interval=1000, n_intervals=0),
    dcc.Tabs([dcc.Tab(label="Mapa e segmentos", children=[dcc.Graph(id="map", figure=_figure(None))]),
              dcc.Tab(label="Rotas", children=[html.Div(id="route_table")]),
              dcc.Tab(label="Energia", children=[dcc.Graph(id="zone_chart", figure=_zone_figure(None))]),
              dcc.Tab(label="Contrato / diagnóstico", children=[html.Pre(id="details", style={"whiteSpace": "pre-wrap", "padding": "12px"})])]),
], style={"fontFamily": "Segoe UI, sans-serif", "background": "#f1f5f9", "color": "#0f172a", "padding": "20px", "maxWidth": "1600px", "margin": "auto"})

@app.callback(Output("status", "children"), Output("map", "figure"), Output("details", "children"), Output("route_table", "children"), Output("zone_chart", "figure"), Output("summary", "children"),
              Input("run", "n_clicks"), Input("poll", "n_intervals"), Input("cancel", "n_clicks"),
              State("origin", "value"), State("destination", "value"), State("graph", "value"), State("profile", "value"), State("mass", "value"), State("power", "value"), State("bike_mass", "value"), State("vehicle", "value"), State("propulsion", "value"), State("pbf", "value"), prevent_initial_call=True)
def update(_, __, cancel, origin_text, destination_text, graph_path, profile, mass, power, bike_mass, vehicle, propulsion, pbf):
    if ctx.triggered_id == 'cancel':
        with LOCK: JOB['cancel'] = True
    if not JOB.get("thread") or not getattr(JOB.get("thread"), "is_alive", lambda: False)():
        if ctx.triggered_id == 'run':
            try:
                origin = tuple(map(float, origin_text.split(",")))
                destination = tuple(map(float, destination_text.split(",")))
                with LOCK: JOB.update(status="searching", message="Iniciando busca.", plan=None, cancel=False)
                thread = threading.Thread(target=_run, args=(origin, destination, graph_path, profile, mass, power, bike_mass, vehicle, propulsion, pbf), daemon=True)
                with LOCK: JOB["thread"] = thread
                thread.start()
            except Exception as exc:
                with LOCK: JOB.update(status="error", message=str(exc))
    with LOCK:
        plan, status, message = JOB.get("plan"), JOB.get("status", "idle"), JOB.get("message", "")
    details = ""
    rows = _route_rows(plan)
    if plan:
        details = plan.to_json(indent=2)
    table = html.Table([html.Thead(html.Tr([html.Th(k) for k in (rows[0].keys() if rows else ["Nenhuma rota"])])),
                        html.Tbody([html.Tr([html.Td(v) for v in row.values()]) for row in rows])], style={"width": "100%", "padding": "12px"}) if rows else html.Div("Nenhuma rota elegível.")
    summary = [html.Div(f"Rotas: {len(rows)} · acesso: {plan.diagnostics.get('access_mode')}")] if rows else []
    return message, _figure(plan), details, table, _zone_figure(plan), summary

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8501, debug=False)
