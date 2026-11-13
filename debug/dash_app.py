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

from router.integration import BicycleModel, CyclistModel, load_graph, plan_routes_progressive

import effort_colors

DEFAULT_GRAPH = ROOT / "data" / "graph.json"
if not DEFAULT_GRAPH.exists():
    candidates = list((ROOT / "debug" / ".cache").glob("**/graph*.json"))
    DEFAULT_GRAPH = candidates[0] if candidates else ROOT / "debug" / ".cache" / "graph_v4.json"

JOB: dict[str, object] = {"status": "idle", "plan": None, "message": "Aguardando consulta."}
LOCK = threading.RLock()

def _color(rpe):
    return effort_colors.rpe_color(rpe)

def _figure(plan):
    fig = go.Figure()
    if plan and plan.routes:
        route = plan.routes[0]
        pts = list(route.geometry)
        for a, b in zip(pts, pts[1:]):
            fig.add_trace(go.Scattermap(
                lat=[a.lat, b.lat], lon=[a.lon, b.lon], mode="lines",
                line={"color": _color(b.rpe if b.rpe is not None else a.rpe), "width": 7},
                customdata=[[b.rpe, b.grade_pct], [b.rpe, b.grade_pct]],
                hovertemplate="RPE %{customdata[0]:.1f}<br>Inclinação %{customdata[1]:.1f}%<extra></extra>",
                showlegend=False,
            ))
        fig.add_trace(go.Scattermap(lat=[pts[0].lat, pts[-1].lat], lon=[pts[0].lon, pts[-1].lon],
                                    mode="markers", marker={"size": 13, "color": ["#16a34a", "#dc2626"]},
                                    text=["Origem", "Destino"], showlegend=False))
    center = {"lat": pts[0].lat, "lon": pts[0].lon} if plan and plan.routes and pts else {"lat": -17.78, "lon": -50.91}
    fig.update_layout(map={"style": "open-street-map", "zoom": 12, "center": center}, margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=620)
    return fig

def _route_rows(plan):
    if not plan: return []
    rows = []
    for route in plan.routes:
        rows.append({"id": route.id, "perfil": route.profile, "distância_km": round(route.metrics.distance_m / 1000, 2),
                     "tempo_min": round(route.metrics.time_s / 60, 1), "RPE_médio": round(route.ml.mean_rpe, 1),
                     "RPE_pico": round(route.ml.peak_rpe, 1), "segura": "sim" if route.safety.eligible else "não"})
    return rows

def _zone_figure(plan):
    route = plan.routes[0] if plan and plan.routes else None
    fractions = route.ml.band_distance_fraction if route else {}
    values = [float(fractions.get(f'i{i}', 0.0)) for i in range(1, 6)] if fractions else []
    if not values: return go.Figure().update_layout(title="Distribuição de RPE indisponível")
    colors = [effort_colors.RPE_BAND_COLORS[f"i{i}"] for i in range(1, 6)]
    return go.Figure(go.Bar(x=[f'I{i}' for i in range(1, 6)], y=values, marker_color=colors)).update_layout(title="Faixas de intensidade por distância", xaxis_title="Faixa estimada", yaxis_title="Fração da distância")

def _run(origin, destination, graph_path, profile, mass, power, bike_mass):
    try:
        graph = load_graph(graph_path)
        def event(e):
            with LOCK:
                JOB["status"] = e.status
                JOB["plan"] = e.plan
                JOB["message"] = f"{e.status} · perna {e.leg or 1}/{e.legs}"
        result = plan_routes_progressive(graph, tuple(origin), tuple(destination),
            cyclist=CyclistModel(mass_kg=mass, sustainable_power_w=power), bicycle=BicycleModel(mass_kg=bike_mass), profile=profile, on_event=event, should_cancel=lambda: bool(JOB.get('cancel')))
        with LOCK:
            JOB.update(status=result.status, plan=result.plan, message=result.status)
    except Exception as exc:
        with LOCK:
            JOB.update(status="error", message=f"{type(exc).__name__}: {exc}")

app = Dash(__name__, title="Roteamento offline · API E2E")
app.layout = html.Div([
    html.Div([html.Span("TOPO / LAB", style={"color": "#5eead4", "letterSpacing": "0.18em", "fontWeight": "bold"}), html.H2("Bancada de roteamento", style={"margin": "8px 0"}), html.Div("Inspeção da API offline · grafo, física e intensidade", style={"color": "#94a3b8"})], style={"background": "#0f172a", "color": "white", "padding": "24px 32px", "borderRadius": "12px"}),
    html.Div([html.Div([html.H4("Entrada"), dcc.Input(id="origin", value="-17.7818345,-50.9034942", style={"width": "100%"}),
              dcc.Input(id="destination", value="-17.7711026,-50.9197426", style={"width": "100%"}),
              dcc.Input(id="graph", value=str(DEFAULT_GRAPH), style={"width": "100%"}),
              html.Label("Perfil de busca"), dcc.Dropdown(id="profile", options=[{"label": x, "value": x} for x in ("fastest", "shortest", "balanced")], value="fastest", clearable=False),
              html.Label("Ciclista · massa (kg)"), dcc.Input(id="mass", type="number", value=75, min=1),
              html.Label("Potência sustentável (W)"), dcc.Input(id="power", type="number", value=200, min=1),
              html.Label("Bicicleta · massa (kg)"), dcc.Input(id="bike_mass", type="number", value=12, min=1),
              html.Button("Calcular rota", id="run", n_clicks=0, style={"width": "100%"}), html.Button("Cancelar", id="cancel", n_clicks=0)], style={"width": "290px", "display": "grid", "gap": "8px"}),
             html.Div([html.H4("Camadas"), html.Ul([html.Li("01 · dados e snapping"), html.Li("02 · DEM e altimetria"), html.Li("03 · perfis e A*"), html.Li("04 · ML / RPE"), html.Li("05 · segurança"), html.Li("06 · contrato")])], style={"paddingLeft": "24px"})], style={"display": "flex", "gap": "32px", "padding": "12px"}),
    html.Div([
        html.Div(id="status", children="Aguardando consulta.", style={"fontWeight": "600"}),
        html.Div(id="summary", style={"display": "flex", "gap": "24px", "padding": "8px 0"}),
    ], style={"padding": "8px"}),
    dcc.Interval(id="poll", interval=1000, n_intervals=0),
    dcc.Tabs([dcc.Tab(label="Mapa e segmentos", children=[dcc.Graph(id="map", figure=_figure(None))]),
              dcc.Tab(label="Rotas", children=[html.Div(id="route_table")]),
              dcc.Tab(label="RPE e zonas", children=[dcc.Graph(id="zone_chart", figure=_zone_figure(None))]),
              dcc.Tab(label="Contrato / diagnóstico", children=[html.Pre(id="details", style={"whiteSpace": "pre-wrap", "padding": "12px"})])]),
], style={"fontFamily": "Segoe UI, sans-serif", "background": "#f1f5f9", "color": "#0f172a", "padding": "20px", "maxWidth": "1600px", "margin": "auto"})

@app.callback(Output("status", "children"), Output("map", "figure"), Output("details", "children"), Output("route_table", "children"), Output("zone_chart", "figure"), Output("summary", "children"),
              Input("run", "n_clicks"), Input("poll", "n_intervals"), Input("cancel", "n_clicks"),
              State("origin", "value"), State("destination", "value"), State("graph", "value"), State("profile", "value"), State("mass", "value"), State("power", "value"), State("bike_mass", "value"), prevent_initial_call=True)
def update(_, __, cancel, origin_text, destination_text, graph_path, profile, mass, power, bike_mass):
    if ctx.triggered_id == 'cancel':
        with LOCK: JOB['cancel'] = True
    if not JOB.get("thread") or not getattr(JOB.get("thread"), "is_alive", lambda: False)():
        if ctx.triggered_id == 'run':
            try:
                origin = tuple(map(float, origin_text.split(",")))
                destination = tuple(map(float, destination_text.split(",")))
                with LOCK: JOB.update(status="searching", message="Iniciando busca.", plan=None, cancel=False)
                thread = threading.Thread(target=_run, args=(origin, destination, graph_path, profile, mass, power, bike_mass), daemon=True)
                with LOCK: JOB["thread"] = thread
                thread.start()
            except Exception as exc:
                with LOCK: JOB.update(status="error", message=str(exc))
    with LOCK:
        plan, status, message = JOB.get("plan"), JOB.get("status", "idle"), JOB.get("message", "")
    details = ""
    rows = _route_rows(plan)
    if plan:
        route = plan.routes[0] if plan.routes else None
        details = json.dumps({"status": status, "routes": len(plan.routes), "mean_rpe": route.ml.mean_rpe if route else None,
                              "peak_rpe": route.ml.peak_rpe if route else None, "zone": plan.zone.to_dict()}, ensure_ascii=False, indent=2)
    table = html.Table([html.Thead(html.Tr([html.Th(k) for k in (rows[0].keys() if rows else ["Nenhuma rota"])])),
                        html.Tbody([html.Tr([html.Td(v) for v in row.values()]) for row in rows])], style={"width": "100%", "padding": "12px"}) if rows else html.Div("Nenhuma rota elegível.")
    summary = [html.Div(f"RPE médio: {rows[0]['RPE_médio']}") for _ in [0]] if rows else []
    return message, _figure(plan), details, table, _zone_figure(plan), summary

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8501, debug=False)
