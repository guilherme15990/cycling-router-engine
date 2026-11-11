"""Comandos de ingestao, grafo, snapping e fisica (ADR-033, E6).

Reune os handlers que operam sobre o grafo salvo (``build``, ``stats``,
``geojson``, ``validate``, ``snap``, ``physical``) e o preparo offline de tiles
(``fetch-dem``). O comando ``plan`` vive em ``commands/plan.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from router.app.tooling import (
    DEFAULT_HIGHWAYS,
    BicycleModel,
    BuildOptions,
    ChainElevation,
    CyclistModel,
    Environment,
    OpenTopoDataElevation,
    OsmData,
    OsmTagElevation,
    PhysicalTraversal,
    RasterDemElevation,
    SimulationMode,
    SimulationState,
    SnapError,
    SnapOptions,
    SqliteElevationCache,
    TerrariumElevation,
    TerrariumTileError,
    attach_elevation,
    build_graph,
    build_query_graph,
    fetch_terrarium_tiles,
    load_json,
    parse_bbox,
    parse_coord,
    read_osm_pbf,
    save_geojson,
    save_json,
    simplify_graph,
    summarize,
    validate_graph,
)
from router.interfaces.cli.render import print_physical, print_snap, print_surface_table

__all__ = [
    "cmd_build",
    "cmd_fetch_dem",
    "cmd_geojson",
    "cmd_physical",
    "cmd_snap",
    "cmd_stats",
    "cmd_validate",
]


def cmd_build(args: argparse.Namespace) -> int:
    if not args.pbf:
        print(
            "erro: informe --pbf (arquivo OSM .pbf local); a ingestao e offline",
            file=sys.stderr,
        )
        return 2

    highways = (
        tuple(item.strip() for item in args.highways.split(",") if item.strip())
        if args.highways
        else DEFAULT_HIGHWAYS
    )

    print(f"OSM.PBF local: {args.pbf}")
    try:
        data = read_osm_pbf(args.pbf, highways=highways)
    except Exception as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    print(
        f"OSM: {len(data.nodes)} nos, {len(data.ways)} vias, "
        f"{len(data.relations)} relacoes de restricao"
    )

    options = BuildOptions(
        allow_footway=args.allow_footway,
        allow_dismount=not args.no_dismount,
    )
    graph, build_stats = build_graph(data, options)
    print(
        "vias selecionadas: "
        f"{build_stats.ways_selected}/{build_stats.ways_total} | "
        f"trechos: {build_stats.segments}"
    )
    if build_stats.skipped_missing_nodes:
        print(f"  ignorados por nos ausentes: {build_stats.skipped_missing_nodes}")
    if build_stats.skipped_short:
        print(f"  ignorados por comprimento minimo: {build_stats.skipped_short}")
    if build_stats.turn_restrictions:
        print(f"  restricoes de conversao: {build_stats.turn_restrictions}")

    provider = _make_provider(args, data)
    if provider is None:
        print("elevacao: desligada")
    else:
        print(f"elevacao: {getattr(provider, 'name', provider)} (espacamento {args.spacing} m)")
        attach_elevation(
            graph,
            provider,
            spacing_m=args.spacing,
            smoothing=args.smoothing,
        )
        if isinstance(provider, SqliteElevationCache):
            provider.close()

    if not args.no_simplify:
        simplified, simplify_stats = simplify_graph(graph)
        print(
            "simplificacao: "
            f"{simplify_stats.nodes_before} -> {simplify_stats.nodes_after} nos | "
            f"{simplify_stats.arcs_before} -> {simplify_stats.arcs_after} arcos | "
            f"{simplify_stats.contracted_nodes} nos contraidos | "
            f"{simplify_stats.multi_segment_arcs} arcos multi-segmento"
        )
        graph = simplified

    issues = validate_graph(graph)
    errors = sum(1 for issue in issues if issue.severity == "error")
    if errors:
        print(f"ATENCAO: {errors} erro(s) de consistencia; use 'roteamento validate'")
    else:
        print("consistencia: ok")

    destination = save_json(graph, args.out)
    summary = summarize(graph)
    print(
        f"grafo: {summary['nodes']} nos, {summary['arcs']} arcos, "
        f"{summary['total_length_km']} km"
    )
    print(f"salvo em: {destination}")

    if args.geojson_out:
        save_geojson(graph, args.geojson_out)
        print(f"geojson: {args.geojson_out}")

    print_surface_table(summary)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    graph = load_json(args.path)
    summary = summarize(graph)
    issues = validate_graph(graph)

    if args.json:
        print(json.dumps({**summary, "issues": [str(i) for i in issues]}, indent=2))
        return 0

    print(f"arquivo: {args.path}")
    print(
        f"nos: {summary['nodes']}  arcos: {summary['arcs']}  "
        f"segmentos: {summary['segments']}"
    )
    simplification = summary.get("simplification")
    if simplification:
        print(
            "simplificacao: "
            f"{simplification['contracted_nodes']} nos contraidos, "
            f"{simplification['multi_segment_arcs']} arcos multi-segmento"
        )
    print(
        f"comprimento: {summary['total_length_km']} km | "
        f"subida acumulada: {summary['total_ascent_m']} m | "
        f"descida acumulada: {summary['total_descent_m']} m"
    )
    print(
        f"elevacao por arco: {summary['elevation_coverage'] * 100:.1f}% "
        f"({summary['arcs_with_elevation']}/{summary['arcs']})"
    )
    if summary["elevation_sources"]:
        sources = ", ".join(
            f"{name} ({count})" for name, count in summary["elevation_sources"].items()
        )
        print(f"fonte de elevacao: {sources}")
    if summary["dismount_length_m"]:
        print(f"trechos com bicycle=dismount: {summary['dismount_length_m']} m")

    print_surface_table(summary)

    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    print(f"validacao: {len(errors)} erro(s), {len(warnings)} aviso(s)")
    if args.issues:
        for issue in issues:
            print(f"  {issue}")
    return 1 if errors else 0


def cmd_geojson(args: argparse.Namespace) -> int:
    graph = load_json(args.path)
    save_geojson(graph, args.out)
    print(f"salvo em: {args.out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    graph = load_json(args.path)
    issues = validate_graph(graph)
    for issue in issues:
        print(issue)

    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    print(f"total: {errors} erro(s), {warnings} aviso(s)")

    if errors or (args.strict and warnings):
        return 1
    return 0


def cmd_snap(args: argparse.Namespace) -> int:
    graph = load_json(args.path)
    options = SnapOptions(
        max_distance_m=args.max_distance,
        node_preference_m=args.node_preference,
        node_offset_tolerance_m=args.node_tolerance,
        min_split_distance_m=args.min_split,
        allow_dismount=not args.no_dismount,
    )

    try:
        query = build_query_graph(
            graph,
            parse_coord(args.origin),
            parse_coord(args.destination),
            options,
        )
    except SnapError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    summary = {
        "origin": query.origin.to_dict(),
        "destination": query.destination.to_dict(),
        "connected": query.is_connected(),
        "query": {
            "nodes": query.graph.node_count,
            "arcs": query.graph.arc_count,
            "segments": sum(
                arc.segment_count for arc in query.graph.arcs.values()
            ),
            "virtual_nodes": sorted(str(node_id) for node_id in query.virtual_nodes),
        },
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        print_snap(summary)

    if args.out_query:
        destination = save_json(query.graph, args.out_query)
        if not args.json:
            print(f"salvo em: {destination}")

    return 0


def cmd_physical(args: argparse.Namespace) -> int:
    graph = load_json(args.path)
    try:
        arc = graph.get_arc(args.arc)
    except Exception as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    max_power = max(args.max_power, args.power or 0.0)
    sustainable = (
        args.power if args.power is not None else min(200.0, max_power)
    )
    try:
        cyclist = CyclistModel(
            mass_kg=args.cyclist_mass,
            max_power_w=max_power,
            sustainable_power_w=sustainable,
            frontal_area_m2=args.frontal_area,
        )
        bicycle = BicycleModel(
            mass_kg=args.bike_mass,
            rolling_resistance=args.crr,
            drag_coefficient=args.cd,
            drivetrain_efficiency=args.efficiency,
        )
        environment = Environment(
            air_density_kg_m3=args.air_density,
            headwind_mps=args.headwind,
            safe_max_speed_mps=(
                args.safe_max_speed if args.safe_max_speed > 0 else None
            ),
        )
        traversal = PhysicalTraversal(
            step_m=args.step,
            dismount_speed_mps=args.dismount_speed,
            mode=SimulationMode(args.mode),
        )
        result = traversal.traverse(
            arc,
            cyclist,
            bicycle,
            initial_state=SimulationState.from_speed(args.initial_speed),
            environment=environment,
            power_w=args.power,
        )
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print_physical(arc, result, cyclist, bicycle)

    return 0


def cmd_fetch_dem(args: argparse.Namespace) -> int:
    """Baixa tiles Terrarium **localmente** (etapa de rede, fora da execucao)."""
    bbox = parse_bbox(args.bbox)

    def progress(done: int, total: int, path: Path) -> None:
        print(f"[{done}/{total}] {path}")

    print(f"Terrarium: zoom {args.zoom} | bbox {bbox} | destino {args.out}")
    try:
        paths = fetch_terrarium_tiles(
            bbox,
            args.zoom,
            args.out,
            timeout_s=args.timeout,
            progress=progress,
        )
    except TerrariumTileError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    print(
        f"tiles: {len(paths)} em {args.out} "
        "(leitura offline por 'build --elevation terrarium')"
    )
    return 0


def _make_provider(args: argparse.Namespace, data: object):
    if args.elevation == "none":
        return None

    if args.elevation == "dem":
        if not args.dem:
            raise SystemExit("--elevation dem exige --dem caminho/para/dem.tif")
        provider = RasterDemElevation(args.dem)
    elif args.elevation == "terrarium":
        if not args.tiles_dir:
            raise SystemExit(
                "--elevation terrarium exige --tiles-dir diretorio/dos/tiles "
                "(veja 'roteamento fetch-dem')"
            )
        provider = TerrariumElevation(args.tiles_dir, zoom=args.tiles_zoom)
    elif args.elevation == "opentopodata":
        assert isinstance(data, OsmData)
        provider = ChainElevation(
            OsmTagElevation.from_nodes(data.nodes.values()),
            OpenTopoDataElevation(),
        )
    else:  # osm
        assert isinstance(data, OsmData)
        provider = OsmTagElevation.from_nodes(data.nodes.values())

    if args.no_cache:
        return provider
    return SqliteElevationCache(provider, Path(args.cache_dir) / "elevation.sqlite")
