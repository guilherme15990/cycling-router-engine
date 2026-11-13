"""Debug E2E do motor de roteamento, em Streamlit.

A aba **Consulta** e a interface principal: carrega um grafo v4, monta a
consulta, chama ``router.integration.plan_routes`` e mostra o resultado.
A aba **Logs** e separada e registra cada etapa da sessao.

Uso (a partir da raiz do repositorio)::

    python -m streamlit run debug/app.py

O modulo adiciona ``src/`` ao ``sys.path`` quando o pacote ``router`` ainda
nao estiver instalado, para funcionar direto do checkout.
"""

from __future__ import annotations

import csv
import inspect
import io
import json
import math
import random
import sys
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEBUG_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
for _path in (SRC_DIR, DEBUG_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import streamlit as st
import streamlit.components.v1 as components

import altimetry
import effort_colors
import layer_lab

from router.integration import (
    BicycleModel,
    CyclistModel,
    PROFILE_IDS,
    dumps,
    load_graph,
    plan_routes,
)

PAGE_TITLE = "Roteamento E2E — debug"
GRAPH_CACHE = Path(__file__).resolve().parent / ".cache" / "graph_v4.json"
DEFAULT_PBF = (
    PROJECT_ROOT
    / "data"
    / "sources"
    / "planet_-51.73_-17.379_ff78d815.osm.pbf"
)
DEFAULT_GRAPH = GRAPH_CACHE if GRAPH_CACHE.exists() else PROJECT_ROOT / "data" / "se.json"
#: Tiles Terrarium (DEM) locais — os mesmos usados pelo motor offline.
DEM_DIR = PROJECT_ROOT / "data" / "dem"
ALTIMETRY_CACHE = Path(__file__).resolve().parent / ".cache"
#: Centro/zoom usados quando ainda nao ha grafo carregado (area do PBF padrao).
FALLBACK_MAP_CENTER = (-17.379, -51.73)
FALLBACK_MAP_ZOOM = 12
MAX_LOG_ENTRIES = 2000
EARTH_RADIUS_M = 6_371_000.0

LOG_LEVELS = ("DEBUG", "INFO", "OK", "WARN", "ERROR")

#: Streamlit novo trocou ``use_container_width`` por ``width``. O app aceita
#: ambos: usa ``width`` quando o widget o suporta e cai para a opcao antiga
#: (Streamlit >= 1.30) caso contrario.
_WIDE: dict[str, Any] = (
    {"width": "stretch"}
    if "width" in inspect.signature(st.button).parameters
    else {"use_container_width": True}
)

try:  # componente bidirecional (Streamlit >= 1.5x)
    from streamlit.components.v2 import component as _v2_component
except Exception:  # pragma: no cover - versao antiga
    _v2_component = None


# ==========================================================================
# Mapa interativo (Leaflet, componente bidirecional)
# ==========================================================================
MAP_PICKER_HTML = """
<div class="rt-map-picker-canvas"></div>
<p class="rt-map-picker-status">Carregando mapa...</p>
"""

MAP_PICKER_CSS = """
.rt-map-picker-canvas {
    height: 430px;
    width: 100%;
    border-radius: 8px;
    cursor: crosshair;
    background: #eef1f4;
}
.rt-map-picker-status {
    margin: 4px 0 0;
    font-size: 12px;
    color: #888;
}
"""

MAP_PICKER_JS = """
const LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_JS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';

function ensureLeaflet() {
    if (window.L) return Promise.resolve(window.L);
    if (window.__rtLeafletPromise) return window.__rtLeafletPromise;
    window.__rtLeafletPromise = new Promise((resolve, reject) => {
        if (!document.querySelector('link[data-rt-leaflet]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = LEAFLET_CSS;
            link.dataset.rtLeaflet = '1';
            document.head.appendChild(link);
        }
        if (document.querySelector('script[data-rt-leaflet]')) {
            const check = setInterval(() => {
                if (window.L) { clearInterval(check); resolve(window.L); }
            }, 50);
            return;
        }
        const script = document.createElement('script');
        script.src = LEAFLET_JS;
        script.async = true;
        script.dataset.rtLeaflet = '1';
        script.onload = () => resolve(window.L);
        script.onerror = () => reject(new Error('Leaflet indisponivel (CDN)'));
        document.head.appendChild(script);
    });
    return window.__rtLeafletPromise;
}

function dotIcon(L, color) {
    return L.divIcon({
        className: '',
        html: '<span style="display:block;width:16px;height:16px;border-radius:50%;'
            + 'background:' + color + ';border:2px solid #fff;'
            + 'box-shadow:0 0 4px rgba(0,0,0,.55)"></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
    });
}

async function mount(component) {
    const { data, setTriggerValue, parentElement } = component;
    const canvas = parentElement.querySelector('.rt-map-picker-canvas');
    if (!canvas) return;

    // O estado vive no proprio elemento: sobrevive a re-renders do componente
    // mesmo que este modulo seja reavaliado.
    let state = canvas.__rtState;
    if (!state) {
        state = { map: null };
        canvas.__rtState = state;
    }
    state.data = data || {};
    state.setTriggerValue = setTriggerValue;

    if (state.map && !state.map.getContainer().isConnected) {
        state.map.remove();
        state.map = null;
    }

    // Aplica um ponto escolhido (clique no mapa ou arrasto do marcador).
    const applyPoint = (lat, lng, target) => {
        if (state.data && state.data.busy) return;
        if (state.pendingLayer) state.pendingLayer.clearLayers();
        const L = state.L;
        if (state.pendingLayer && L) {
            // Feedback imediato: o marcador provisorio some quando o Python
            // devolve o ponto confirmado no proximo render.
            L.marker([lat, lng], {
                icon: dotIcon(L, target === 'destination' ? '#e74c3c' : '#27ae60'),
            }).bindTooltip(target === 'destination' ? 'Destino (novo)' : 'Origem (nova)')
                .addTo(state.pendingLayer);
        }
        const status = parentElement.querySelector('.rt-map-picker-status');
        if (status) {
            status.textContent = (target === 'destination' ? 'Destino' : 'Origem')
                + ': ' + lat.toFixed(7) + ', ' + lng.toFixed(7) + ' — aplicando...';
        }
        state.setTriggerValue('clicked', {
            lat: lat,
            lng: lng,
            target: target,
            nonce: Date.now(),
        });
    };

    if (!state.map) {
        const L = await ensureLeaflet();
        state.L = L;
        state.map = L.map(canvas, { zoomControl: true, scrollWheelZoom: true });
        state.demRoadLayer = L.layerGroup().addTo(state.map);
        state.routeLayer = L.layerGroup().addTo(state.map);
        state.map.on('moveend', () => {
            clearTimeout(state.boundsTimer);
            state.boundsTimer = setTimeout(() => {
                const b = state.map.getBounds();
                const clamp = (v, limit) => Math.max(-limit, Math.min(limit, v));
                const bounds = [clamp(b.getSouth(), 90), clamp(b.getWest(), 180), clamp(b.getNorth(), 90), clamp(b.getEast(), 180)].map(v => Number(v.toFixed(5)));
                const signature = bounds.join(',');
                if (signature !== state.lastBounds) {
                    state.lastBounds = signature;
                    state.setTriggerValue('bounds', {bounds, nonce: Date.now()});
                }
            }, 350);
        });
        state.markerLayer = L.layerGroup().addTo(state.map);
        state.pendingLayer = L.layerGroup().addTo(state.map);
        state.tileLayer = null;
        state.lastFitKey = null;
        state.map.on('click', (event) => {
            const d = state.data || {};
            applyPoint(
                event.latlng.lat,
                event.latlng.lng,
                d.mode === 'destination' ? 'destination' : 'origin',
            );
        });
        const status = parentElement.querySelector('.rt-map-picker-status');
        if (status) status.textContent = 'Clique no mapa para definir o ponto.';
    }

    const L = state.L;
    const d = state.data;

    const status = parentElement.querySelector('.rt-map-picker-status');
    const wantTiles = d.tiles !== false;
    if (wantTiles && !state.tileLayer) {
        state.tileLayer = L.tileLayer(
            'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }
        ).addTo(state.map);
        state.tileLayer.on('tileerror', () => {
            if (status) {
                status.textContent = 'Tiles indisponiveis (sem rede?); '
                    + 'o tracado e os cliques continuam funcionando.';
            }
        });
    } else if (!wantTiles && state.tileLayer) {
        state.map.removeLayer(state.tileLayer);
        state.tileLayer = null;
    }

    if (d.altimetry && d.altimetry.url) {
        if (!state.altimetryLayer) {
            state.altimetryLayer = L.imageOverlay(
                d.altimetry.url,
                d.altimetry.bounds,
                { opacity: d.altimetry.opacity, interactive: false }
            ).addTo(state.map);
        } else {
            state.altimetryLayer.setUrl(d.altimetry.url);
            state.altimetryLayer.setBounds(d.altimetry.bounds);
            state.altimetryLayer.setOpacity(d.altimetry.opacity);
            if (!state.map.hasLayer(state.altimetryLayer)) {
                state.map.addLayer(state.altimetryLayer);
            }
        }
    } else if (state.altimetryLayer) {
        state.map.removeLayer(state.altimetryLayer);
        state.altimetryLayer = null;
    }

    if (!state.demRoadLayer) state.demRoadLayer = L.layerGroup().addTo(state.map);
    state.demRoadLayer.clearLayers();
    for (const edge of (d.dem_roads || [])) {
        const pts = [];
        for (let i = 0; i + 1 < edge.pts.length; i += 2) pts.push([edge.pts[i], edge.pts[i + 1]]);
        const colors = ['#22a45a', '#ed9a22', '#d93a3a'];
        L.polyline(pts, {color: edge.band == null ? '#929ca4' : colors[edge.band], weight: 3, opacity: 0.8, interactive: false}).addTo(state.demRoadLayer);
    }
    state.routeLayer.clearLayers();
    if (Array.isArray(d.route) && d.route.length > 1) {
        __RPE_COLOR_JS__
        const segments = Array.isArray(d.route_segments) ? d.route_segments : [];
        if (segments.length) {
            for (const seg of segments) {
                if (!Array.isArray(seg.pts) || seg.pts.length < 2) continue;
                L.polyline(seg.pts, { color: rpeColor(seg.rpe), weight: 6, opacity: 0.92 })
                    .bindTooltip(`RPE ${seg.rpe == null ? '—' : Number(seg.rpe).toFixed(1)} · inclinação ${Number(seg.grade_pct || 0).toFixed(1)}%`, {sticky: true})
                    .addTo(state.routeLayer);
            }
        } else {
            L.polyline(d.route, { color: '#dc3545', weight: 5, opacity: 0.9 }).addTo(state.routeLayer);
        }
    }

    state.markerLayer.clearLayers();
    state.pendingLayer.clearLayers();
    const addPoint = (point, color, label, target) => {
        const marker = L.marker(point, {
            icon: dotIcon(L, color),
            draggable: !(state.data && state.data.busy),
            autoPan: true,
        }).bindTooltip(label + ' (arraste para ajustar)').addTo(state.markerLayer);
        marker.on('dragend', (event) => {
            const p = event.target.getLatLng();
            applyPoint(p.lat, p.lng, target);
        });
    };
    if (Array.isArray(d.origin)) {
        addPoint(d.origin, '#27ae60', 'Origem', 'origin');
    }
    if (Array.isArray(d.destination)) {
        addPoint(d.destination, '#e74c3c', 'Destino', 'destination');
    }

    const fitKey = String(d.fit_key || '');
    if (fitKey !== state.lastFitKey) {
        state.lastFitKey = fitKey;
        const points = [];
        if (Array.isArray(d.route)) points.push(...d.route);
        if (Array.isArray(d.origin)) points.push(d.origin);
        if (Array.isArray(d.destination)) points.push(d.destination);
        if (points.length > 1) {
            state.map.fitBounds(L.latLngBounds(points), { padding: [26, 26] });
        } else if (points.length === 1) {
            state.map.setView(points[0], 14);
        } else if (Array.isArray(d.bounds) && d.bounds.length === 2) {
            state.map.fitBounds(d.bounds, { padding: [16, 16] });
        } else if (Array.isArray(d.center)) {
            state.map.setView(d.center, Number(d.zoom) || 12);
        } else {
            state.map.setView([0, 0], 2);
        }
    }

    if (status && status.textContent.indexOf('aplicando') >= 0) {
        status.textContent = 'Clique no mapa para definir o ponto.';
    }

    setTimeout(() => state.map.invalidateSize(), 60);
}

export default function (component) {
    mount(component).catch((error) => {
        const status = component.parentElement.querySelector('.rt-map-picker-status');
        if (status) status.textContent = 'Mapa indisponivel: ' + error.message;
    });
}
"""

# A cor do tracado por RPE vem da fonte unica em ``effort_colors``.
MAP_PICKER_JS = MAP_PICKER_JS.replace(
    "__RPE_COLOR_JS__", effort_colors.rpe_color_js()
)

_MAP_PICKER = (
    _v2_component(
        "roteamento_map_picker",
        html=MAP_PICKER_HTML,
        css=MAP_PICKER_CSS,
        js=MAP_PICKER_JS,
        isolate_styles=False,
    )
    if _v2_component is not None
    else None
)


# ==========================================================================
# Estado da sessao
# ==========================================================================
def _default_state() -> dict[str, Any]:
    return {
        "logs": [],
        "graph": None,
        "graph_path": None,
        "last_plan": None,
        "last_run": None,
        "last_error": None,
        "origin_text": "",
        "destination_text": "",
        "stops_text": "",
        "profile_select": "fastest" if "fastest" in PROFILE_IDS else PROFILE_IDS[0],
        "max_routes": 0,
        "sustained_power": 200.0,
        "cyclist_mass": 75.0,
        "max_power": 600.0,
        "frontal_area": 0.5,
        "bike_mass": 12.0,
        "crr": 0.005,
        "cd": 0.9,
        "efficiency": 0.97,
        "graph_path_input": str(DEFAULT_GRAPH),
        "pbf_path_input": str(DEFAULT_PBF),
        "graph_out_input": str(GRAPH_CACHE),
        "log_filter": "",
        "map_tiles": True,
        "map_altimetry": True,
        "map_mode": "Origem",
        "map_fit_key": "0",
        "graph_bounds": None,
        "selected_route": 0,
        "selected_plan_key": 0,
    }


def _init_state() -> None:
    for key, value in _default_state().items():
        st.session_state.setdefault(key, value)


def _apply_pending() -> None:
    """Aplica valores que precisam ser gravados antes dos widgets existirem."""
    pending = {
        "_pending_origin": "origin_text",
        "_pending_destination": "destination_text",
        "_pending_graph_path": "graph_path_input",
    }
    for source, target in pending.items():
        if source in st.session_state:
            st.session_state[target] = st.session_state.pop(source)


# ==========================================================================
# Logs
# ==========================================================================
def _log(level: str, message: str) -> None:
    """Registra um evento na aba de logs (sem poluir a interface principal)."""
    entry = {
        "hora": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "nivel": level.upper(),
        "mensagem": message,
    }
    logs = st.session_state.setdefault("logs", [])
    logs.append(entry)
    if len(logs) > MAX_LOG_ENTRIES:
        del logs[: len(logs) - MAX_LOG_ENTRIES]


def _log_exc(prefix: str, exc: BaseException) -> None:
    _log("ERROR", f"{prefix}: {type(exc).__name__}: {exc}")


# ==========================================================================
# Utilidades
# ==========================================================================
def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _resolve_path(text: str) -> Path:
    path = Path(text.strip()).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _format_coord(coord: tuple[float, float]) -> str:
    return f"{coord[0]:.7f}, {coord[1]:.7f}"


def _parse_coord(text: str, label: str) -> tuple[float, float]:
    raw = text.strip().replace(";", ",")
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"{label}: informe no formato 'lat, lon'")
    lat, lon = float(parts[0]), float(parts[1])
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(f"{label}: coordenada fora da faixa valida ({lat}, {lon})")
    return lat, lon


def _parse_stops(text: str) -> tuple[tuple[float, float], ...]:
    stops: list[tuple[float, float]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        stops.append(_parse_coord(line, f"parada {number}"))
    return tuple(stops)


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _suggest_pair(
    graph: Any, target_m: float = 5_000.0, max_nodes: int = 4_000
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Sugere origem/destino alcancaveis por arcos reais do grafo.

    A busca em largura parte de nos aleatorios e devolve o primeiro no que
    esteja a pelo menos ``target_m`` da origem (ou o mais distante visitado),
    garantindo que exista caminho direcionado entre o par sugerido.
    """
    starts = [node for node in graph.nodes.values() if graph.out_arcs(node.id)]
    if len(starts) < 2:
        return None

    rng = random.Random()
    for _ in range(10):
        origin = rng.choice(starts)
        origin_coord = (float(origin.lat), float(origin.lon))
        best: tuple[float, tuple[float, float]] | None = None
        seen = {origin.id}
        queue = deque([origin])
        while queue and len(seen) < max_nodes:
            current = queue.popleft()
            for arc in graph.out_arcs(current.id):
                if arc.target in seen:
                    continue
                node = graph.nodes.get(arc.target)
                if node is None:
                    continue
                seen.add(node.id)
                queue.append(node)
                coord = (float(node.lat), float(node.lon))
                distance = _haversine_m(origin_coord, coord)
                if distance >= target_m:
                    return origin_coord, coord
                if best is None or distance > best[0]:
                    best = (distance, coord)
        if best is not None and best[0] >= 750.0:
            return origin_coord, best[1]
    return None


# ==========================================================================
# Grafo
# ==========================================================================
def _graph_bounds(graph: Any) -> list[list[float]] | None:
    """Bounding box do grafo como ``[[sul, oeste], [norte, leste]]``."""
    nodes = list(graph.nodes.values())
    if not nodes:
        return None
    lats = [float(node.lat) for node in nodes]
    lons = [float(node.lon) for node in nodes]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


# ==========================================================================
# Altimetria (DEM Terrarium local)
# ==========================================================================
def _dem_zoom() -> int | None:
    """Zoom do acervo Terrarium local (``data/dem``), ou ``None``."""
    found = altimetry.discover_tiles(DEM_DIR)
    return found[0] if found else None


def _dem_provider() -> Any:
    """Provedor Terrarium offline reutilizado entre reruns."""
    provider = st.session_state.get("_dem_provider")
    if provider is None:
        from router.app.tooling import TerrariumElevation

        provider = TerrariumElevation(DEM_DIR, zoom=_dem_zoom())
        st.session_state["_dem_provider"] = provider
    return provider


def _point_elevation(coord: tuple[float, float] | None) -> float | None:
    """Altitude do ponto no DEM local, se houver cobertura."""
    if coord is None or _dem_zoom() is None:
        return None
    try:
        return _dem_provider().elevations([coord])[0]
    except Exception:
        return None


def _with_elevation(text: str, elevation: float | None) -> str:
    return f"{text} ({elevation:.0f} m)" if elevation is not None else text


def _altimetry_overlay() -> altimetry.AltimetryOverlay | None:
    """Imagem hipsometrica da camada (montada uma vez por sessao)."""
    if not st.session_state.get("map_altimetry", True):
        return None
    if "_altimetry_overlay" in st.session_state:
        return st.session_state["_altimetry_overlay"]
    overlay: altimetry.AltimetryOverlay | None = None
    try:
        overlay = altimetry.build_overlay(DEM_DIR, cache_dir=ALTIMETRY_CACHE)
    except Exception as exc:
        _log_exc("Falha ao montar a camada de altimetria", exc)
    if overlay is None:
        _log("WARN", f"Sem tiles de altimetria em {_display_path(DEM_DIR)}.")
    else:
        _log(
            "OK",
            "Camada de altimetria: "
            f"{overlay.width}x{overlay.height}px, {overlay.tiles} tiles z{overlay.zoom}, "
            f"{overlay.min_m:.0f}-{overlay.max_m:.0f} m",
        )
    st.session_state["_altimetry_overlay"] = overlay
    return overlay


def _graph_has_elevation(graph: Any) -> bool:
    return any(node.elevation_m is not None for node in graph.nodes.values())


def _apply_dem_elevation(graph: Any) -> None:
    """Preenche nos e segmentos do grafo com o DEM Terrarium local."""
    from router.app.tooling import TerrariumElevation, attach_elevation

    zoom = _dem_zoom()
    if zoom is None:
        raise FileNotFoundError(f"sem tiles Terrarium em {DEM_DIR}")
    provider = TerrariumElevation(DEM_DIR, zoom=zoom)
    st.session_state["_dem_provider"] = provider
    _log(
        "INFO",
        f"Amostrando altimetria do DEM local (z{zoom}) em "
        f"{graph.node_count} nos e {graph.arc_count} arcos...",
    )
    started = time.perf_counter()
    attach_elevation(graph, provider, spacing_m=25.0, smoothing=3)
    st.session_state.pop("_dem_roads_key", None)
    elapsed = time.perf_counter() - started
    segments = [segment for arc in graph.arcs.values() for segment in arc.segments]
    with_profile = sum(1 for segment in segments if segment.elevation_profile)
    nodes = sum(1 for node in graph.nodes.values() if node.elevation_m is not None)
    _log(
        "OK",
        f"Altimetria aplicada: {nodes}/{graph.node_count} nos e "
        f"{with_profile}/{len(segments)} segmentos com perfil em {elapsed:.1f}s "
        f"(DEM z{zoom}, passo 25 m)",
    )
    st.session_state.last_plan = None


def _save_graph(graph: Any, path: Path) -> None:
    from router.app.tooling import save_json

    save_json(graph, path)
    _log("OK", f"Grafo salvo em {_display_path(path)}")


def _set_graph(graph: Any, path: Path) -> None:
    st.session_state.graph = graph
    st.session_state.graph_path = str(path)
    st.session_state.graph_bounds = _graph_bounds(graph)
    st.session_state.last_plan = None
    st.session_state.last_run = None
    st.session_state.last_error = None
    st.session_state.map_fit_key = f"graph:{time.time()}"


def _load_and_set(path: Path) -> None:
    _log("INFO", f"Carregando grafo: {_display_path(path)}")
    started = time.perf_counter()
    graph = load_graph(path)
    elapsed = time.perf_counter() - started
    _set_graph(graph, path)
    _log(
        "OK",
        f"Grafo carregado: {graph.node_count} nos, {graph.arc_count} arcos "
        f"em {elapsed:.2f}s",
    )

    _suggest_if_empty(graph)


def _suggest_if_empty(graph: Any) -> None:
    """Preenche origem/destino quando o formulario ainda esta vazio."""
    origin_empty = not st.session_state.origin_text
    destination_empty = not st.session_state.destination_text
    if not origin_empty and not destination_empty:
        return
    pair = _suggest_pair(graph)
    if pair is not None:
        # Nunca sobrescreve um ponto ja escolhido (por exemplo, no mapa).
        if origin_empty:
            st.session_state["_pending_origin"] = _format_coord(pair[0])
        if destination_empty:
            st.session_state["_pending_destination"] = _format_coord(pair[1])
        _log("INFO", "Origem/destino sugeridos a partir de nos do grafo.")
    else:
        _log("WARN", "Nao foi possivel sugerir origem/destino automaticamente.")


def _build_graph(pbf: Path, out: Path) -> None:
    from router.app.tooling import (
        BuildOptions,
        build_graph,
        read_osm_pbf,
        save_json,
        simplify_graph,
    )

    _log("INFO", f"Lendo OSM.PBF: {_display_path(pbf)}")
    data = read_osm_pbf(pbf)
    _log("INFO", f"OSM: {len(data.nodes)} nos, {len(data.ways)} vias")

    graph, stats = build_graph(data, BuildOptions())
    _log(
        "INFO",
        f"Grafo bruto: {graph.node_count} nos, {graph.arc_count} arcos "
        f"({stats.segments} segmentos)",
    )

    simplified, simplify_stats = simplify_graph(graph)
    _log(
        "INFO",
        "Simplificacao: "
        f"{simplify_stats.nodes_before}->{simplify_stats.nodes_after} nos, "
        f"{simplify_stats.arcs_before}->{simplify_stats.arcs_after} arcos",
    )

    if _dem_zoom() is None:
        _log("WARN", f"Sem tiles de altimetria em {_display_path(DEM_DIR)}; grafo fica sem DEM.")
    else:
        try:
            _apply_dem_elevation(simplified)
        except Exception as exc:
            _log_exc("Falha ao aplicar altimetria do DEM", exc)

    out.parent.mkdir(parents=True, exist_ok=True)
    save_json(simplified, out)
    _set_graph(simplified, out)
    st.session_state["_pending_graph_path"] = str(out)
    _log("OK", f"Grafo v4 salvo em {_display_path(out)}")
    _suggest_if_empty(simplified)


# ==========================================================================
# Execucao E2E
# ==========================================================================
def _execute_plan(
    *,
    graph: Any,
    origin: tuple[float, float],
    destination: tuple[float, float],
    stops: tuple[tuple[float, float], ...],
    profile: str,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    max_routes: int,
) -> tuple[Any, float]:
    _log(
        "INFO",
        "plan_routes: "
        f"origem={_format_coord(origin)} | destino={_format_coord(destination)} | "
        f"paradas={len(stops)} | perfil={profile!r}",
    )
    started = time.perf_counter()
    plan = layer_lab.execute(
        plan_routes,
        graph=graph,
        origin=origin,
        destination=destination,
        stops=stops,
        cyclist=cyclist,
        bicycle=bicycle,
        profile=profile,
        max_routes=max_routes or None,
    )
    elapsed = time.perf_counter() - started

    if plan.reached and plan.routes:
        _log(
            "OK",
            f"Plano concluido em {elapsed:.2f}s: {len(plan.routes)} rota(s); "
            f"recomendada={plan.recommended_route_id!r}",
        )
    else:
        _log(
            "WARN",
            f"Plano sem rota em {elapsed:.2f}s (reached={plan.reached})",
        )

    for warning in plan.warnings:
        _log("WARN", warning)
    if plan.truncated:
        _log("WARN", "Busca truncada por max_expansions.")
    if plan.frontier_truncated:
        _log(
            "WARN",
            f"Fronteira podada: {plan.dropped_labels} label(s) nao dominada(s).",
        )
    return plan, elapsed


# ==========================================================================
# Renderizacao
# ==========================================================================
def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Fonte de dados", icon=":material/database:")
        st.text_input(
            "Grafo v4 (JSON)",
            key="graph_path_input",
            help="Formato roteamento.cycling-graph versao 4.",
        )
        if st.button("Carregar grafo", type="primary", disabled=st.session_state.get("route_job") is not None, **_WIDE):
            path = _resolve_path(st.session_state.graph_path_input)
            try:
                _load_and_set(path)
            except Exception as exc:
                st.session_state.last_error = traceback.format_exc()
                _log_exc("Falha ao carregar grafo", exc)
                st.error(f"Falha ao carregar: {exc}")
                if "versao nao suportada" in str(exc):
                    st.info(
                        "Esse arquivo e um grafo legado (v1/v2). Use o construtor "
                        "abaixo para gerar um grafo v4 a partir do PBF local."
                    )
            else:
                st.rerun()

        graph = st.session_state.graph
        if graph is None:
            st.warning("Nenhum grafo carregado.", icon=":material/database:")
        else:
            st.success(
                f"{graph.node_count:,} nós · {graph.arc_count:,} arcos",
                icon=":material/check_circle:",
            )
            if st.session_state.graph_path:
                st.caption(_display_path(Path(st.session_state.graph_path)))

            has_elevation = _graph_has_elevation(graph)
            if not has_elevation:
                st.warning("Grafo sem altimetria (perfil plano).", icon=":material/landscape:")
            if _dem_zoom() is None:
                st.caption(f"Sem tiles DEM em `{_display_path(DEM_DIR)}`.")
            elif st.button(
                "Aplicar altimetria do DEM local",
                disabled=st.session_state.get("route_job") is not None,
                **_WIDE,
                help=(
                    "Amostra os tiles Terrarium de data/dem nos nos e nos "
                    "segmentos do grafo (passo de 25 m) e regrava o JSON."
                ),
            ):
                try:
                    with st.spinner("Amostrando o DEM local..."):
                        _apply_dem_elevation(graph)
                        path = (
                            Path(st.session_state.graph_path)
                            if st.session_state.graph_path
                            else None
                        )
                        if path is not None and path.is_file():
                            _save_graph(graph, path)
                except Exception as exc:
                    st.session_state.last_error = traceback.format_exc()
                    _log_exc("Falha ao aplicar altimetria", exc)
                    st.error(f"Falha ao aplicar altimetria: {exc}")
                else:
                    st.rerun()

        st.divider()
        with st.expander("Construir grafo v4 a partir de PBF"):
            st.caption(
                "Preparacao local (opcional). O fluxo principal continua sendo "
                "a chamada E2E em `plan_routes`."
            )
            st.text_input("PBF local", key="pbf_path_input")
            st.text_input("Saida do grafo", key="graph_out_input")
            if st.button("Construir e carregar", disabled=st.session_state.get("route_job") is not None, **_WIDE):
                pbf = _resolve_path(st.session_state.pbf_path_input)
                out = _resolve_path(st.session_state.graph_out_input)
                if not pbf.exists():
                    st.error(f"PBF nao encontrado: {pbf}")
                else:
                    try:
                        with st.spinner("Construindo grafo v4..."):
                            _build_graph(pbf, out)
                    except Exception as exc:
                        st.session_state.last_error = traceback.format_exc()
                        _log_exc("Falha ao construir grafo", exc)
                        st.error(f"Falha ao construir: {exc}")
                    else:
                        st.rerun()

        st.divider()
        with st.expander("Sobre"):
            st.markdown(
                "A aba **Consulta** configura e executa a API. **Inspeção por camada** mostra entradas, cálculos e saídas reais. A aba **Logs** "
                "registra cada etapa da sessao.\n\n"
                "API E2E: `router.integration.plan_routes`.\n\n"
                "Clique no mapa para escolher origem/destino; a rota aparece "
                "no mesmo mapa depois do calculo."
            )


def _render_form() -> None:
    with st.form("route_form"):
        st.subheader("Parâmetros da rota", icon=":material/tune:")
        c1, c2 = st.columns(2)
        c1.text_input(
            "Origem (lat, lon)",
            key="origin_text",
            placeholder="-23.550000, -46.630000",
        )
        c2.text_input(
            "Destino (lat, lon)",
            key="destination_text",
            placeholder="-23.560000, -46.640000",
        )

        st.text_area(
            "Paradas intermediarias (uma por linha: lat, lon)",
            key="stops_text",
            height=72,
            placeholder="Opcional. Ex.: -23.555000, -46.635000",
        )

        c3, c4, c5 = st.columns(3)
        c3.selectbox("Perfil", list(PROFILE_IDS), key="profile_select")
        c4.number_input(
            "Max. rotas (0 = todas)",
            min_value=0,
            max_value=50,
            step=1,
            key="max_routes",
        )
        c5.number_input(
            "Potencia sustentada (W)",
            min_value=0.0,
            max_value=2_000.0,
            step=10.0,
            key="sustained_power",
            help="0 usa o padrao do motor: min(200, potencia maxima).",
        )

        with st.expander("Ciclista e bicicleta"):
            p1, p2, p3 = st.columns(3)
            p1.number_input(
                "Massa do ciclista (kg)",
                min_value=30.0,
                max_value=200.0,
                step=1.0,
                key="cyclist_mass",
            )
            p2.number_input(
                "Potencia maxima (W)",
                min_value=100.0,
                max_value=2_000.0,
                step=10.0,
                key="max_power",
            )
            p3.number_input(
                "Area frontal (m2)",
                min_value=0.2,
                max_value=1.2,
                step=0.01,
                key="frontal_area",
            )
            b1, b2, b3, b4 = st.columns(4)
            b1.number_input(
                "Massa da bike (kg)",
                min_value=5.0,
                max_value=40.0,
                step=0.5,
                key="bike_mass",
            )
            b2.number_input(
                "Crr",
                min_value=0.001,
                max_value=0.02,
                step=0.001,
                format="%.3f",
                key="crr",
            )
            b3.number_input(
                "Cd",
                min_value=0.3,
                max_value=1.5,
                step=0.05,
                key="cd",
            )
            b4.number_input(
                "Eficiencia",
                min_value=0.7,
                max_value=1.0,
                step=0.01,
                key="efficiency",
            )

        layer_lab.render_controls()
        submitted = st.form_submit_button(
            "Executar API e capturar camadas", type="primary", disabled=st.session_state.get("route_job") is not None, **_WIDE
        )

    if not submitted:
        return

    st.session_state.layer_report = None
    graph = st.session_state.graph
    if graph is None:
        st.error("Carregue um grafo antes de calcular.")
        return

    try:
        origin = _parse_coord(st.session_state.origin_text, "origem")
        destination = _parse_coord(st.session_state.destination_text, "destino")
        stops = _parse_stops(st.session_state.stops_text)

        max_power = float(st.session_state.max_power)
        sustained = float(st.session_state.sustained_power)
        if sustained <= 0.0:
            sustained = min(200.0, max_power)

        cyclist = CyclistModel(
            mass_kg=float(st.session_state.cyclist_mass),
            max_power_w=max_power,
            sustainable_power_w=sustained,
            frontal_area_m2=float(st.session_state.frontal_area),
        )
        bicycle = BicycleModel(
            mass_kg=float(st.session_state.bike_mass),
            rolling_resistance=float(st.session_state.crr),
            drag_coefficient=float(st.session_state.cd),
            drivetrain_efficiency=float(st.session_state.efficiency),
        )

        layer_lab.start_job(
            origin=origin, destination=destination, stops=stops,
            cyclist=cyclist, bicycle=bicycle,
            profile=st.session_state.profile_select,
            max_routes=int(st.session_state.max_routes) or None,
        )
    except Exception as exc:
        st.session_state.last_plan = None
        st.session_state.last_error = traceback.format_exc()
        _log_exc("Falha ao iniciar consulta", exc)
        st.error(str(exc))
        return
    st.rerun()


def _render_plan(plan: Any) -> None:
    run = st.session_state.last_run or {}
    st.divider()
    head_left, head_right = st.columns([4, 1])
    head_left.subheader("Resultado", icon=":material/route:")
    if head_right.button("Limpar resultado", disabled=st.session_state.get("route_job") is not None, **_WIDE):
        st.session_state.last_plan = None
        st.session_state.last_run = None
        st.session_state.layer_report = None
        st.rerun()
    status = st.session_state.get("progress_status")
    if status in ("searching", "route_found"):
        st.info("Rota validada parcial. A busca continua e as alternativas ou a recomendação podem mudar.")
    elif status == "interrupted":
        st.warning("Busca interrompida. O último resultado validado foi preservado e pode estar incompleto.")
    elif status == "completed":
        st.caption("Busca concluída na cobertura do grafo carregado; não há garantia de otimalidade fora dessa cobertura.")
    if run:
        st.caption(
            f"Execucao {run['timestamp']} em {run['elapsed']:.2f}s | "
            f"{run['profile']} | {run['sustained_power']:.0f} W | "
            f"paradas={run['stops']}"
        )

    if plan.truncated:
        st.warning("Busca truncada por `max_expansions`: a fronteira pode estar incompleta.")
    if plan.frontier_truncated:
        st.warning(
            "Fronteira podada: "
            f"{plan.dropped_labels} label(s) nao dominada(s) removida(s)."
        )
    for warning in plan.warnings:
        st.warning(warning)

    if not plan.reached or not plan.routes:
        st.warning("Nenhuma rota elegível foi retornada para a cobertura deste grafo. Consulte os avisos e as rotas rejeitadas no contrato.")
        _render_contract(plan)
        return

    routes = list(plan.routes)
    labels = [
        (
            f"{'[recomendada] ' if route.id == plan.recommended_route_id else ''}"
            f"{route.id} | {route.metrics.distance_m / 1000.0:.2f} km | "
            f"{route.metrics.time_s / 60.0:.1f} min"
        )
        for route in routes
    ]
    default_index = _recommended_route_index(plan)
    current = _selected_route_index(len(routes), default=default_index)
    if st.session_state.get("selected_route") != current:
        st.session_state.selected_route = current
    selected = st.selectbox(
        "Rota",
        options=list(range(len(routes))),
        format_func=lambda index: labels[index],
        key="selected_route",
    )
    route = routes[selected]

    metrics = route.metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Distancia", f"{metrics.distance_m / 1000.0:.2f} km")
    m2.metric("Tempo previsto", f"{metrics.time_s / 60.0:.1f} min")
    m3.metric("Subida", f"{metrics.ascent_m:.0f} m")
    m4.metric("Descida", f"{metrics.descent_m:.0f} m")

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("RPE medio", f"{route.ml.mean_rpe:.1f}")
    m6.metric("Pico de RPE", f"{route.ml.peak_rpe:.1f}")
    m7.metric("Energia", f"{metrics.energy_j / 1000.0:.0f} kJ")
    m8.metric("Grau maximo", f"{(metrics.max_grade_pct or 0.0):.1f}%")
    st.caption(
        "RPE = estimativa provisoria (Borg 6-20), nao calibrada. "
        "Pico = media movel de 60 s, nao o maximo de um arco."
    )
    st.caption(
        f"Seguranca: {route.safety.state} | elegivel={route.safety.eligible} | "
        f"fonte das metricas={metrics.source}"
    )

    _render_effort_card(route)

    left, right = st.columns([3, 2])
    with left:
        _render_elevation(route)
    with right:
        st.caption(
            "O mapa interativo fica no topo da página: clique nele para "
            "mover a origem ou o destino."
        )
        st.caption(
            f"Rota selecionada: `{route.id}` | {len(route.geometry)} pontos "
            "de geometria."
        )
        if st.button("Centralizar no mapa", **_WIDE):
            st.session_state.map_fit_key = f"route:{time.time()}"
            st.rerun()

    _render_bands(route)
    _render_geometry(route)
    _render_contract(plan)


def _maybe_coord(text: str) -> tuple[float, float] | None:
    try:
        return _parse_coord(text, "coordenada")
    except (TypeError, ValueError):
        return None


def _selected_route_index(route_count: int, default: int = 0) -> int:
    """Indice da rota selecionada, tolerante a estado defeituoso.

    ``selected_route`` e a chave do ``selectbox`` de rotas. Se o estado vier
    corrompido (por exemplo, um rotulo de uma sessao/versao antiga) ou fora da
    faixa, cai em ``default`` em vez de quebrar o render do mapa.
    """
    if route_count <= 0:
        return 0
    fallback = min(max(default, 0), route_count - 1)
    try:
        index = int(st.session_state.get("selected_route", fallback))
    except (TypeError, ValueError):
        return fallback
    if not 0 <= index < route_count:
        return fallback
    return index


def _recommended_route_index(plan: Any) -> int:
    routes = list(plan.routes)
    return next(
        (
            index
            for index, route in enumerate(routes)
            if route.id == plan.recommended_route_id
        ),
        0,
    )


def _normalize_selected_route() -> None:
    """Normaliza ``selected_route`` **antes** do mapa ler o estado.

    O mapa (``_render_picker``) e desenhado antes do ``selectbox`` de rotas, de
    modo que um valor invalido precisa ser corrigido aqui, e nao apenas quando
    o ``selectbox`` e renderizado.
    """
    plan = st.session_state.get("last_plan")
    if plan is None or not plan.routes:
        return
    routes = list(plan.routes)
    index = _selected_route_index(len(routes), default=_recommended_route_index(plan))
    if st.session_state.get("selected_route") != index:
        st.session_state.selected_route = index


def _selected_route_coords() -> list[list[float]]:
    plan = st.session_state.get("last_plan")
    if plan is None or not plan.routes:
        return []
    routes = list(plan.routes)
    index = _selected_route_index(len(routes))
    points = [
        [round(float(point.lat), 6), round(float(point.lon), 6)]
        for point in routes[index].geometry
    ]
    if len(points) > 2_000:
        step = max(1, len(points) // 2_000)
        points = [*points[::step], points[-1]]
    return points


def _selected_route_segments() -> list[dict[str, Any]]:
    plan = st.session_state.get("last_plan")
    if plan is None or not plan.routes:
        return []
    index = _selected_route_index(len(plan.routes))
    geometry = list(plan.routes[index].geometry)
    segments: list[dict[str, Any]] = []
    for previous, current in zip(geometry, geometry[1:]):
        segments.append({
            "pts": [[round(float(previous.lat), 6), round(float(previous.lon), 6)],
                    [round(float(current.lat), 6), round(float(current.lon), 6)]],
            "rpe": current.rpe if current.rpe is not None else previous.rpe,
            "grade_pct": current.grade_pct,
            "band": current.band,
        })
    return segments


def _map_click_to_pending(clicked: dict[str, Any]) -> tuple[str, str, str]:
    """Converte um clique do mapa em (alvo, chave pendente, texto).

    Funcao pura para permitir teste sem navegador.
    """
    target = "destination" if clicked.get("target") == "destination" else "origin"
    coord = f"{float(clicked['lat']):.7f}, {float(clicked['lng']):.7f}"
    key = "_pending_origin" if target == "origin" else "_pending_destination"
    return target, key, coord


@st.cache_resource
def _dem_executor():
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="dem-download")


@st.fragment(run_every=2)
def _dem_download_status() -> None:
    future = st.session_state.get("_dem_download")
    if future is None:
        return
    if not future.done():
        st.info("Baixando tiles DEM da área selecionada em segundo plano…")
        return
    st.session_state.pop("_dem_download", None)
    try:
        result = future.result()
    except Exception as exc:
        st.session_state["_dem_download_message"] = (False, str(exc))
    else:
        for key in ("_dem_provider", "_altimetry_overlay", "_dem_roads_key"):
            st.session_state.pop(key, None)
        st.session_state["_dem_download_message"] = (True, f"DEM preparado: {result['downloaded']} baixados, {result['reused']} reutilizados. Para atualizar as declividades do grafo, use Aplicar altimetria no painel lateral.")
    st.rerun(scope="app")


def _render_picker() -> None:
    st.subheader("Origem e destino", icon=":material/map:")
    st.caption(
        "Clique no mapa para definir o ponto escolhido abaixo; as coordenadas "
        "vao para os campos de origem/destino. Depois de escolhido, arraste o "
        "marcador para ajustar. Roda do mouse = zoom."
    )
    origin = _maybe_coord(st.session_state.origin_text)
    destination = _maybe_coord(st.session_state.destination_text)

    c_mode, c_tiles = st.columns([2, 1])
    with c_mode:
        if hasattr(st, "segmented_control"):
            mode_label = st.segmented_control(
                "Clique no mapa define",
                options=["Origem", "Destino"],
                key="map_mode",
                width="stretch",
            )
        else:
            mode_label = st.radio(
                "Clique no mapa define",
                options=["Origem", "Destino"],
                horizontal=True,
                key="map_mode",
            )
    with c_tiles:
        st.toggle(
            "Mapa com tiles (online)",
            key="map_tiles",
            help=(
                "Tiles do OpenStreetMap baixados pelo navegador. Desmarque "
                "para ver apenas o tracado offline."
            ),
        )
        st.toggle(
            "Vias por declividade (DEM local)",
            key="map_altimetry",
            help=(
                "Vias do grafo coloridas pela inclinação. Cinza indica altitude indisponível."
            ),
        )

    if _MAP_PICKER is None:
        st.info(
            "Mapa interativo indisponivel nesta versao do Streamlit "
            "(requer `components.v2`). Use os campos de coordenadas."
        )
        _render_static_map(origin, destination)
        return

    from router.integration import altimetry_edges, prepare_dem_tiles
    _dem_download_status()
    message = st.session_state.get("_dem_download_message")
    if message:
        (st.success if message[0] else st.error)(message[1])
    viewport = st.session_state.get("_visible_bounds")
    if st.button("Baixar DEM da área visível", disabled=not viewport or st.session_state.get("_dem_download") is not None,
                 help="Baixa até 40 tiles Terrarium em zoom 12; arquivos existentes são reutilizados."):
        st.session_state["_dem_download"] = _dem_executor().submit(prepare_dem_tiles, tuple(viewport), str(DEM_DIR), zoom=12, max_tiles=40)
        st.session_state.pop("_dem_download_message", None)
        st.rerun()
    graph = st.session_state.graph
    viewport = st.session_state.get("_visible_bounds")
    layer = None
    if graph is not None and viewport and st.session_state.map_altimetry:
        cache_key = (id(graph), tuple(viewport))
        if st.session_state.get("_dem_roads_key") != cache_key:
            st.session_state["_dem_roads"] = altimetry_edges(graph, tuple(viewport))
            st.session_state["_dem_roads_key"] = cache_key
        layer = st.session_state["_dem_roads"]
    overlay = None
    data = {
        "mode": "origin" if mode_label == "Origem" else "destination",
        "busy": st.session_state.get("route_job") is not None,
        "tiles": bool(st.session_state.map_tiles),
        "fit_key": (
            f"{st.session_state.map_fit_key}:"
            f"{st.session_state.get('selected_route', 0)}"
        ),
        "bounds": st.session_state.graph_bounds,
        "center": list(FALLBACK_MAP_CENTER),
        "zoom": FALLBACK_MAP_ZOOM,
        "origin": list(origin) if origin else None,
        "destination": list(destination) if destination else None,
        "route": _selected_route_coords(),
        "route_segments": _selected_route_segments(),
        "dem_roads": layer["edges"] if layer else [],
        "altimetry": (
            {
                "url": overlay.data_uri,
                "bounds": overlay.leaflet_bounds,
                "opacity": altimetry.DEFAULT_OPACITY,
            }
            if overlay is not None
            else None
        ),
    }
    result = _MAP_PICKER(
        key="map_picker",
        data=data,
        height=480,
        on_clicked_change=lambda: None,
        on_bounds_change=lambda: None,
    )
    visible = getattr(result, "bounds", None)
    if visible and visible.get("bounds"):
        bounds = visible["bounds"]
        if bounds != st.session_state.get("_visible_bounds"):
            st.session_state["_visible_bounds"] = bounds
            st.rerun()
    if layer:
        st.caption(f"Vias na área: {layer['total']} · desenhadas: {len(layer['edges'])} · sem altitude: {layer['missing']}" + (" · amostradas" if layer['truncated'] else ""))
        st.caption("Verde ≤ 4% · laranja ≤ 8% · vermelho > 8% · cinza sem altitude")
    elif st.session_state.map_altimetry:
        st.caption("Carregue um grafo com altimetria para visualizar as vias na área do mapa.")
    clicked = getattr(result, "clicked", None)
    if clicked:
        nonce = clicked.get("nonce")
        if nonce != st.session_state.get("_map_click_nonce"):
            st.session_state["_map_click_nonce"] = nonce
            target, key, coord = _map_click_to_pending(clicked)
            st.session_state[key] = coord
            elevation = _point_elevation(
                (float(clicked["lat"]), float(clicked["lng"]))
            )
            _log(
                "INFO",
                f"Ponto escolhido no mapa ({target}): {coord}"
                + (f" | {elevation:.0f} m" if elevation is not None else ""),
            )
            st.rerun()

    origin_elevation = _point_elevation(origin)
    destination_elevation = _point_elevation(destination)
    suffix_origin = (
        f" · {origin_elevation:.0f} m" if origin_elevation is not None else ""
    )
    suffix_destination = (
        f" · {destination_elevation:.0f} m"
        if destination_elevation is not None
        else ""
    )

    c1, c2, c3 = st.columns([3, 1, 1])
    c1.caption(
        f"Origem: `{st.session_state.origin_text or '—'}`{suffix_origin} | "
        f"Destino: `{st.session_state.destination_text or '—'}`{suffix_destination}"
    )
    if c2.button("Inverter", disabled=st.session_state.get("route_job") is not None, **_WIDE):
        st.session_state["_pending_origin"] = st.session_state.destination_text
        st.session_state["_pending_destination"] = st.session_state.origin_text
        _log("INFO", "Origem e destino invertidos.")
        st.rerun()
    if c3.button("Limpar pontos", disabled=st.session_state.get("route_job") is not None, **_WIDE):
        st.session_state["_pending_origin"] = ""
        st.session_state["_pending_destination"] = ""
        _log("INFO", "Origem e destino limpos.")
        st.rerun()



def _render_static_map(
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
) -> None:
    """Mapa Leaflet estatico para Streamlit sem ``components.v2``."""
    route = _selected_route_coords()
    if not route and not origin and not destination:
        return
    html = _leaflet_map_html(
        route,
        origin,
        destination,
        height=420,
        tiles=bool(st.session_state.map_tiles),
    )
    _embed_html(html, height=440)


def _embed_html(html: str, *, height: int) -> None:
    """Embute HTML/JS: ``st.iframe`` quando existe, senao ``components.html``."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        components.html(html, height=height, scrolling=False)


def _leaflet_map_html(
    route: list[list[float]],
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    *,
    height: int,
    tiles: bool = True,
) -> str:
    """HTML autonomo com Leaflet + tiles do OSM (sem pandas/pyarrow)."""
    points = [
        [round(float(lat), 6), round(float(lon), 6)] for lat, lon in route
    ]
    if len(points) > 2_000:
        step = max(1, len(points) // 2_000)
        points = [*points[::step], points[-1]]
    route_json = json.dumps(points, separators=(",", ":"))
    origin_json = json.dumps(list(origin)) if origin else "null"
    destination_json = json.dumps(list(destination)) if destination else "null"
    tile_js = (
        "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {"
        " maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'"
        " }).addTo(map);"
        if tiles
        else ""
    )

    template = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { margin: 0; padding: 0; background: #f4f6f8; }
  #map { height: __HEIGHT__px; width: 100%; border-radius: 8px; }
  .leaflet-control-attribution { font-size: 10px; }
</style>
</head>
<body>
<div id="map"></div>
<script>
  var route = __ROUTE__;
  var origin = __ORIGIN__;
  var destination = __DESTINATION__;
  var map = L.map('map');
  __TILES__
  if (route.length > 1) {
    L.polyline(route, {
      color: '#dc3545', weight: 5, opacity: 0.9, lineJoin: 'round'
    }).addTo(map);
  }
  if (origin) {
    L.circleMarker(origin, {
      radius: 8, color: '#ffffff', weight: 2, fillColor: '#27ae60', fillOpacity: 1
    }).addTo(map).bindPopup('Origem');
  }
  if (destination) {
    L.circleMarker(destination, {
      radius: 8, color: '#ffffff', weight: 2, fillColor: '#e74c3c', fillOpacity: 1
    }).addTo(map).bindPopup('Destino');
  }
  var all = route.slice();
  if (origin) all.push(origin);
  if (destination) all.push(destination);
  if (all.length > 1) {
    map.fitBounds(all, { padding: [24, 24] });
  } else if (all.length === 1) {
    map.setView(all[0], 14);
  }
</script>
</body>
</html>"""
    return (
        template.replace("__ROUTE__", route_json)
        .replace("__ORIGIN__", origin_json)
        .replace("__DESTINATION__", destination_json)
        .replace("__TILES__", tile_js)
        .replace("__HEIGHT__", str(height))
    )


def _markdown_table(fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    header = "| " + " | ".join(fieldnames) + " |"
    separator = "|" + "|".join("---:" for _ in fieldnames) + "|"
    body = [
        "| "
        + " | ".join("" if row.get(name) is None else str(row[name]) for name in fieldnames)
        + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _render_chart(
    layers: list[dict[str, Any]], fallback: str, *, height: int = 240
) -> None:
    """Renderiza um grafico Vega-Lite; se falhar, cai para a tabela.

    Os dados ficam dentro de cada camada (``layer[].data.values``), nao no
    topo do spec: assim o Streamlit nao precisa converter o dataset em Arrow,
    o que depende de ``pyarrow``. Se ainda assim a renderizacao falhar, a
    tabela equivalente mantem a informacao visivel.
    """
    spec = {"layer": layers, "height": height, "config": {"view": {"stroke": None}}}
    try:
        st.vega_lite_chart(spec, **_WIDE)
    except Exception as exc:
        _log(
            "WARN",
            f"Grafico indisponivel ({type(exc).__name__}: {exc}); exibindo tabela.",
        )
        st.markdown(fallback)


def _render_elevation(route: Any) -> None:
    rows = [
        {"dist_km": float(point.dist_m) / 1000.0, "elevacao_m": float(point.ele)}
        for point in route.geometry
        if point.ele is not None
    ]
    if not rows:
        st.info("Sem altimetria no grafo para esta rota.")
        return

    if len(rows) > 800:
        step = max(1, len(rows) // 800)
        rows = rows[::step]

    layers = [
        {
            "data": {"values": rows},
            "mark": {
                "type": "area",
                "line": True,
                "color": "#2e86de",
                "opacity": 0.35,
            },
            "encoding": {
                "x": {
                    "field": "dist_km",
                    "type": "quantitative",
                    "title": "distancia (km)",
                },
                "y": {
                    "field": "elevacao_m",
                    "type": "quantitative",
                    "title": "elevacao (m)",
                },
                "tooltip": [
                    {"field": "dist_km", "type": "quantitative", "format": ".2f"},
                    {"field": "elevacao_m", "type": "quantitative", "format": ".1f"},
                ],
            },
        }
    ]

    sample_step = max(1, len(rows) // 15)
    fallback = _markdown_table(
        ["dist_km", "elevacao_m"],
        [
            {"dist_km": f"{row['dist_km']:.2f}", "elevacao_m": f"{row['elevacao_m']:.1f}"}
            for row in rows[::sample_step]
        ],
    )
    _render_chart(layers, fallback, height=270)


#: Ordem de confianca dos parametros de ritmo (menor = mais fraco).
_PROVENANCE_RANK: dict[str, int] = {
    "measured": 3,
    "calibrated_model_estimated": 2,
    "profile_estimated": 1,
    "provisional_default": 0,
}

#: Rotulos de apresentacao dos parametros de ritmo.
_PROVENANCE_LABELS: dict[str, str] = {
    "measured": "medido",
    "calibrated_model_estimated": "estimado por modelo calibrado",
    "profile_estimated": "estimado do perfil",
    "provisional_default": "padrao provisorio",
}


def _provenance_chip_color(source: str) -> str:
    if source == "measured":
        return "green"
    if source in ("calibrated_model_estimated", "profile_estimated"):
        return "blue"
    return "orange"


def _render_demand_bars(demand: Any, scenario: Any) -> None:
    """Barras da demanda do terreno (``d1``..``d5``) com fallback em tabela."""
    from router.core.prediction.intensity import (
        DEMAND_BAND_LABELS,
        DEMAND_BAND_NAMES,
    )

    rows = [
        {
            "faixa": name,
            "rotulo": DEMAND_BAND_LABELS.get(name, name),
            "percentual": 100.0
            * demand.distance_fraction_by_band.get(name, 0.0),
        }
        for name in DEMAND_BAND_NAMES
    ]
    layers = [
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "faixa",
                    "type": "nominal",
                    "title": "faixa de demanda",
                    "sort": list(DEMAND_BAND_NAMES),
                },
                "y": {
                    "field": "percentual",
                    "type": "quantitative",
                    "title": "% da distancia",
                },
                "tooltip": [
                    {"field": "faixa", "type": "nominal"},
                    {"field": "rotulo", "type": "nominal"},
                    {
                        "field": "percentual",
                        "type": "quantitative",
                        "format": ".1f",
                    },
                ],
            },
        }
    ]
    fallback = _markdown_table(
        ["faixa", "rotulo", "distancia_%"],
        [
            {
                "faixa": row["faixa"],
                "rotulo": row["rotulo"],
                "distancia_%": f"{row['percentual']:.1f}",
            }
            for row in rows
        ],
    )
    st.markdown("**Demanda do terreno** · `P_req / P_ref`")
    _render_chart(layers, fallback, height=220)
    st.caption(
        f"P_ref = {scenario.reference_power_w:.0f} W · "
        f"demanda media = {demand.mean_demand_power_w:.0f} W. "
        "Custo do terreno no ritmo declarado; nao e o esforco do ciclista."
    )


def _render_grade_bars(demand: Any) -> None:
    """Barras da distribuicao de declividade (3 baldes) com fallback em tabela."""
    from router.core.physics.demand import GRADE_BIN_LABELS

    total = sum(demand.distance_by_grade_bin_m)
    rows = [
        {
            "balde": label,
            "percentual": 100.0 * value / total if total > 0.0 else 0.0,
        }
        for label, value in zip(GRADE_BIN_LABELS, demand.distance_by_grade_bin_m)
    ]
    layers = [
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "balde",
                    "type": "nominal",
                    "title": "declividade",
                    "sort": list(GRADE_BIN_LABELS),
                },
                "y": {
                    "field": "percentual",
                    "type": "quantitative",
                    "title": "% da distancia",
                },
                "tooltip": [
                    {"field": "balde", "type": "nominal"},
                    {
                        "field": "percentual",
                        "type": "quantitative",
                        "format": ".1f",
                    },
                ],
            },
        }
    ]
    fallback = _markdown_table(
        ["declividade", "distancia_%"],
        [
            {"declividade": row["balde"], "distancia_%": f"{row['percentual']:.1f}"}
            for row in rows
        ],
    )
    st.markdown("**Distribuicao de declividade**")
    _render_chart(layers, fallback, height=220)
    st.caption("Descidas contam no primeiro balde (`<=4%`).")


def _render_effort_card(route: Any) -> None:
    """Cartao "Esforco e terreno" (estilo TopoRide), honesto quanto a fonte.

    O RPE e o ritmo vem de ``route.telemetry``; a demanda do terreno, de
    ``route.demand``. Sem a politica de ritmo, o cartao nao e inventado: apenas
    se informa que a potencia e constante. Zonas cardiacas nunca aparecem —
    nao ha metodo fisiologico individual, e uma estimativa nao e uma medicao.
    """
    telemetry = getattr(route, "telemetry", None)
    demand = getattr(route, "demand", None)
    st.subheader("Esforco e terreno", icon=":material/bolt:")
    if telemetry is None:
        st.caption(
            "Sem dados de esforco para esta rota: o resultado nao passou pela "
            "avaliacao fisica."
        )
        return
    if demand is None and telemetry.policy == "constant-power":
        st.caption(
            "Politica de ritmo inativa: potencia constante e sem demanda do "
            "terreno. Ative o ritmo planejado para ver o cartao completo."
        )

    scenario = None if demand is None else demand.scenario
    if scenario is not None:
        sources = (scenario.critical_power_source, scenario.w_prime_source)
        weakest = min(
            sources, key=lambda source: _PROVENANCE_RANK.get(source, 0)
        )
        st.badge(
            f"Fonte: {_PROVENANCE_LABELS.get(weakest, weakest)}",
            icon=":material/fingerprint:",
            color=_provenance_chip_color(weakest),
        )
        st.caption(
            "CP: "
            f"{_PROVENANCE_LABELS.get(scenario.critical_power_source, scenario.critical_power_source)} · "
            "W′: "
            f"{_PROVENANCE_LABELS.get(scenario.w_prime_source, scenario.w_prime_source)}. "
            "Estimativas nao sao medicoes."
        )

    card = st.columns(4)
    card[0].metric("RPE medio", f"{telemetry.mean_rpe:.1f}")
    card[1].metric("Pico de RPE", f"{telemetry.peak_rpe:.1f}")
    card[2].metric("Inercia", f"{100.0 * telemetry.inertia_fraction:.0f}%")
    card[3].metric("Entradas em arterial", str(telemetry.arterial_entries))
    st.caption(
        f"Borg: **{effort_colors.rpe_band_label(telemetry.rpe_band)}** "
        f"(faixa `{telemetry.rpe_band}`, i1..i5) · ancora verbal: "
        f"{telemetry.rpe_label}. "
        "Inercia = fracao da distancia terminada sem perder velocidade. "
        "Arterial = entradas em via arterial pela classe OSM, nao estresse de trafego."
    )

    if demand is not None:
        left, right = st.columns(2)
        with left:
            _render_demand_bars(demand, scenario)
        with right:
            _render_grade_bars(demand)

    if scenario is not None:
        st.caption(
            f"Ritmo planejado {scenario.reference_speed_kmh:.1f} km/h · "
            f"CP {scenario.critical_power_w:.0f} W · "
            f"W′ {scenario.w_prime_j / 1000.0:.1f} kJ · politica `{scenario.policy}`"
        )
    if telemetry.w_prime_start_j is not None and telemetry.w_prime_end_j is not None:
        spent = max(0.0, telemetry.w_prime_start_j - telemetry.w_prime_end_j)
        tail = (
            " — W′ provisorio, nao medido."
            if telemetry.w_prime_is_provisional
            else "."
        )
        st.caption(
            f"Saldo anaerobio: {telemetry.w_prime_end_j / 1000.0:.1f} kJ de "
            f"{telemetry.w_prime_start_j / 1000.0:.1f} kJ "
            f"({spent / 1000.0:.1f} kJ consumidos){tail}"
        )


def _render_bands(route: Any) -> None:
    distance = route.ml.band_distance_fraction or {}
    duration = route.ml.band_duration_fraction or {}
    names = sorted(set(distance) | set(duration))
    if not names:
        return

    rows = []
    for name in names:
        rows.append(
            {
                "faixa": name,
                "serie": "distancia",
                "percentual": 100.0 * distance.get(name, 0.0),
            }
        )
        rows.append(
            {
                "faixa": name,
                "serie": "tempo",
                "percentual": 100.0 * duration.get(name, 0.0),
            }
        )

    layers = [
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {
                "x": {"field": "faixa", "type": "nominal", "title": "faixa"},
                "xOffset": {"field": "serie", "type": "nominal"},
                "y": {"field": "percentual", "type": "quantitative", "title": "%"},
                "color": {"field": "serie", "type": "nominal", "title": None},
                "tooltip": [
                    {"field": "faixa", "type": "nominal"},
                    {"field": "serie", "type": "nominal"},
                    {"field": "percentual", "type": "quantitative", "format": ".1f"},
                ],
            },
        }
    ]

    fallback = _markdown_table(
        ["faixa", "distancia_%", "tempo_%"],
        [
            {
                "faixa": name,
                "distancia_%": f"{100.0 * distance.get(name, 0.0):.1f}",
                "tempo_%": f"{100.0 * duration.get(name, 0.0):.1f}",
            }
            for name in names
        ],
    )

    st.subheader("Distribuicao de intensidade")
    _render_chart(layers, fallback, height=240)
    st.caption("Faixas i1..i5 derivadas do Borg; nao sao zonas cardiacas.")


def _render_geometry(route: Any) -> None:
    fieldnames = [
        "dist_m",
        "lat",
        "lon",
        "ele_m",
        "vel_kmh",
        "grade_pct",
        "rpe",
        "band",
        "watts",
    ]
    rows = [
        {
            "dist_m": round(float(point.dist_m), 1),
            "lat": round(float(point.lat), 7),
            "lon": round(float(point.lon), 7),
            "ele_m": None if point.ele is None else round(float(point.ele), 1),
            "vel_kmh": round(float(point.speed_kmh), 2),
            "grade_pct": round(float(point.grade_pct), 2),
            "rpe": point.rpe,
            "band": point.band,
            "watts": None if point.watts is None else round(float(point.watts), 1),
        }
        for point in route.geometry
    ]
    with st.expander(f"Geometria ({len(rows)} pontos)"):
        preview = rows[:200]
        st.markdown(_markdown_table(fieldnames, preview))
        if len(rows) > len(preview):
            st.caption(
                f"Mostrando os primeiros {len(preview)} de {len(rows)} pontos. "
                "Baixe o CSV para a geometria completa."
            )

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        st.download_button(
            "Baixar geometria (CSV)",
            data=buffer.getvalue(),
            file_name="route-geometry.csv",
            mime="text/csv",
        )


def _render_contract(plan: Any) -> None:
    with st.expander("Contrato publico (JSON)"):
        text = dumps(plan, indent=2)
        st.download_button(
            "Baixar route-plan.json",
            data=text,
            file_name="route-plan.json",
            mime="application/json",
        )
        st.caption(f"{len(text):,} caracteres")
        st.json(json.loads(text), expanded=False)


def _render_logs() -> None:
    logs = st.session_state.get("logs", [])
    st.subheader("Logs da sessao")
    st.caption(
        "Eventos da interface e da chamada E2E. Esta aba e separada da "
        "interface principal."
    )

    if not logs:
        st.info("Nenhum log ainda. Carregue um grafo e calcule uma rota.")
        return

    counts = {level: 0 for level in LOG_LEVELS}
    for entry in logs:
        counts[entry["nivel"]] = counts.get(entry["nivel"], 0) + 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", len(logs))
    c2.metric("OK", counts.get("OK", 0))
    c3.metric("WARN", counts.get("WARN", 0))
    c4.metric("ERROR", counts.get("ERROR", 0))

    f1, f2 = st.columns([2, 3])
    levels = f1.multiselect(
        "Niveis",
        options=list(LOG_LEVELS),
        default=list(LOG_LEVELS),
        key="log_levels",
    )
    query = f2.text_input("Filtrar mensagem", key="log_filter")

    filtered = [
        entry
        for entry in logs
        if entry["nivel"] in levels
        and (not query or query.lower() in entry["mensagem"].lower())
    ]

    if filtered:
        preview = filtered[-500:]
        text_preview = "\n".join(
            f"{entry['hora']} {entry['nivel']:5} {entry['mensagem']}"
            for entry in preview
        )
        st.code(text_preview, language="text")
        if len(filtered) > len(preview):
            st.caption(
                f"Mostrando as ultimas {len(preview)} de {len(filtered)} entradas. "
                "O download inclui todas."
            )
    else:
        st.info("Nenhum log corresponde ao filtro.")

    text = "\n".join(
        f"{entry['hora']} {entry['nivel']:5} {entry['mensagem']}" for entry in logs
    )
    c5, c6 = st.columns(2)
    c5.download_button(
        "Baixar log",
        data=text,
        file_name="roteamento-debug.log",
        mime="text/plain",
        **_WIDE,
    )
    if c6.button("Limpar logs", **_WIDE):
        st.session_state.logs = []
        st.rerun()


def _render_pipeline() -> None:
    layer_lab.render()


def _render_system() -> None:
    layer_lab.render_job()
    st.title("Depuração da engine", icon=":material/directions_bike:")
    graph = st.session_state.graph
    if graph is None:
        st.badge("Grafo não carregado", icon=":material/pending:", color="orange")
    else:
        st.badge(
            f"{graph.node_count:,} nós · {graph.arc_count:,} arcos",
            icon=":material/check_circle:",
            color="green",
        )
    st.caption(
        "Consulta E2E via "
        "`router.integration.plan_routes` (grafo -> snapping -> fisica -> "
        "intensidade -> contrato)."
    )

    # O seletor de pontos fica sempre no topo, mesmo sem grafo carregado:
    # os cliques ja ficam guardados nos campos de origem/destino.
    _normalize_selected_route()
    _render_picker()

    if st.session_state.graph is None:
        st.warning(
            "Carregue um grafo v4 no painel lateral para calcular rotas. "
            "Os pontos escolhidos no mapa acima ja ficam salvos."
        )
        if GRAPH_CACHE.exists():
            if st.button(
                "Carregar grafo em cache",
                type="primary",
                help=_display_path(GRAPH_CACHE),
                **_WIDE,
            ):
                try:
                    with st.spinner("Carregando grafo v4 do cache..."):
                        _load_and_set(GRAPH_CACHE)
                except Exception as exc:
                    st.session_state.last_error = traceback.format_exc()
                    _log_exc("Falha ao carregar grafo do cache", exc)
                    st.error(f"Falha ao carregar: {exc}")
                else:
                    st.rerun()
        if st.session_state.last_error:
            with st.expander("Ultimo erro"):
                st.code(st.session_state.last_error, language="text")
        return

    graph = st.session_state.graph
    if not _graph_has_elevation(graph):
        st.info(
            "Este grafo nao tem altimetria (o perfil da rota fica plano). "
            "Use **Aplicar altimetria do DEM local** no painel lateral, ou "
            "reconstrua o grafo a partir do PBF (o build ja aplica o DEM)."
        )

    _render_form()

    plan = st.session_state.last_plan
    if plan is not None:
        _render_plan(plan)
    elif st.session_state.last_error:
        st.error("A ultima execucao falhou. Veja os detalhes abaixo.")
        with st.expander("Traceback"):
            st.code(st.session_state.last_error, language="text")


def main() -> None:
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=":material/directions_bike:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_state()
    layer_lab.init_state()
    _apply_pending()
    _render_sidebar()

    system_tab, pipeline_tab, logs_tab = st.tabs(["Consulta", "Inspeção por camada", "Logs"])
    with system_tab:
        _render_system()
    with pipeline_tab:
        _render_pipeline()
    with logs_tab:
        _render_logs()


if __name__ == "__main__":
    main()








