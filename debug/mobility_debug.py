"""Debug adapters: public toolkit calls and native contract transport only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from router.integration import (
    MobilityEngine, MobilityProfile, MobilityPlan, MotionEngine, Environment,
    SnapOptions, SearchConfig, SearchCancelled, load_graph, save_graph,
)

ROOT = Path(__file__).resolve().parents[1]

VEHICLES = {
    "bicycle": "Bicicleta", "cargo_bike": "Bicicleta de carga",
    "kick_scooter": "Patinete", "small_electric_vehicle": "Veículo elétrico leve",
    "moped": "Ciclomotor", "motorcycle": "Motocicleta", "motorcar": "Automóvel",
    "goods": "Veículo de carga leve", "hgv": "Caminhão", "bus": "Ônibus",
}
PROPULSIONS = {"manual": "Manual", "electric": "Elétrica", "combustion": "Combustão"}


def read_plan(payload):
    if payload.get("format") == "roteamento.mobility-route-plan":
        return MobilityPlan.from_dict(payload)
    # Display an older saved/debug preview; never execute the older planner.
    from router.integration import loads
    return loads(json.dumps(payload))


def profile_for_request(request):
    refinements = {key: value for key, value in request.get("refinements", {}).items() if value is not None}
    return MobilityProfile.create(request.get("vehicle", "bicycle"), request.get("propulsion_type"), **refinements)


def execute(request, settings, graph_path, *, on_plan=None, should_cancel=None, on_stage=None, inspect=None):
    """Prepare once per source/category, then call MobilityEngine.plan directly."""
    started = time.monotonic()
    mobility = profile_for_request(request)
    def check():
        if should_cancel is not None and should_cancel():
            # Let the application facade own the public cancellation exception.
            raise SearchCancelled("Busca cancelada pelo usuário")
    def stage(name, **details):
        check()
        if on_stage is not None:
            on_stage(name, details)
    stage("preparing", access_mode=mobility.transport_mode)
    graph = load_graph(graph_path)
    mode = graph.metadata.get("transport_mode")
    rebuild = mode != mobility.transport_mode and not (mode is None and mobility.transport_mode == "bicycle")
    source = Path(graph.metadata.get("source_pbf") or settings.get("pbf_path") or "")
    if rebuild and not source.is_file():
        raise ValueError(f"O grafo não foi preparado para {mobility.transport_mode}. Informe o PBF local para reconstruir a rede dessa categoria.")
    cache_path = None
    if rebuild:
        dem = Path(settings.get("dem_dir") or ROOT / "data/dem")
        files = sorted(dem.rglob("*.png")) if dem.is_dir() else []
        stamp = lambda path: (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
        identity = dict(schema=1, source=stamp(source), mode=mobility.transport_mode,
                        dem=[stamp(path) for path in files])
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        cache = ROOT / "debug/.cache/mobility"
        cache.mkdir(parents=True, exist_ok=True)
        cache_path = cache / f"{mobility.transport_mode}-{key}.json"
        if cache_path.is_file():
            graph = load_graph(cache_path)
        else:
            builder = MobilityEngine.from_pbf(source, elevation_tiles=dem if files else None)
            graph = builder.prepare(mobility=mobility)
            graph.metadata["source_pbf"] = str(source.resolve())
            # Keep concurrent sessions from exposing a partially written graph.
            import os
            temporary = cache_path.with_suffix(f".{os.getpid()}.tmp")
            try:
                save_graph(graph, temporary)
                temporary.replace(cache_path)
            finally:
                temporary.unlink(missing_ok=True)
    motion = MotionEngine(step_m=float(settings["physics_step"]), require_elevation=bool(settings["require_elevation"]))
    engine = MobilityEngine(graph, motion=motion, environment=Environment(
        headwind_mps=float(settings["wind_mps"]), air_density_kg_m3=float(settings["air_density"]),
        gravity_m_s2=float(settings["gravity"]), safe_max_speed_mps=float(settings["speed_cap_kmh"]) / 3.6,
    ))
    engine.prepare(mobility=mobility)
    preparation_s = time.monotonic() - started
    if inspect is not None:
        inspect.update(engine=engine, prepared_graph_path=str(cache_path or graph_path), preparation_s=preparation_s)
    stage("searching", graph_nodes=graph.node_count, graph_arcs=graph.arc_count, preparation_s=preparation_s)
    return engine.plan(
        tuple(request["origin"]), tuple(request["destination"]), mobility=mobility,
        profile=request.get("profile", "flattest"), max_routes=request.get("max_routes"),
        collapse_similar=bool(request.get("collapse_similar", False)),
        initial_speed_mps=float(settings["initial_kmh"]) / 3.6,
        snap_options=SnapOptions(max_distance_m=float(settings["snap_radius"])),
        search_config=SearchConfig(exact_speed=bool(settings.get("exact_speed", False)),
                                   max_expansions=settings.get("max_expansions") or None),
        should_cancel=should_cancel, on_plan=on_plan,
        rider=request.get("rider"),
        progress_event_every=int(settings.get("progress_event_every", 4)),
    )
