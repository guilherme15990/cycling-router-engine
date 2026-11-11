"""Linha de comando do motor de roteamento.

Exemplos:

    roteamento build --bbox -23.56,-46.66,-23.54,-46.62 --out centro.json
    roteamento build --pbf dados.osm.pbf --elevation terrarium --tiles-dir data/dem
    roteamento fetch-dem --bbox -18.35,-51.73,-17.09,-50.36 --zoom 12 --out data/dem
    roteamento stats centro.json
    roteamento geojson centro.json centro.geojson

O caminho **offline** usa um arquivo ``.osm.pbf`` local (``build --pbf``) e
tiles de elevacao Terrarium locais (``build --elevation terrarium``). O comando
``fetch-dem`` e a unica etapa que acessa a rede (preparo dos tiles).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from elevation import (
    ChainElevation,
    OpenTopoDataElevation,
    OsmTagElevation,
    RasterDemElevation,
    SqliteElevationCache,
    TerrariumElevation,
)
from elevation_tiles import TerrariumTileError, fetch_terrarium_tiles
from geo import bbox_size_km, parse_bbox, parse_coord
from graph.model import Arc
from graph.serialize import load_json, save_geojson, save_json, summarize
from graph.simplify import simplify_graph
from graph.validate import validate_graph
from osm.build import BuildOptions, attach_elevation, build_graph
from osm.overpass import DEFAULT_ENDPOINTS, DEFAULT_HIGHWAYS, OverpassClient
from osm.pbf import read_osm_pbf
from physical import (
    BicycleModel,
    CyclistModel,
    Environment,
    MIN_SPEED_MPS,
    PhysicalTraversal,
    SimulationMode,
    SimulationState,
    SimulationStatus,
    TraversalResult,
)
from snapping import SnapError, SnapOptions, build_query_graph
from intelligence import (
    EffortPreference,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    load_predictor,
)
from intelligence.intensity import INTENSITY_BAND_NAMES
from planning import RoutePlan, plan_offline_routes

DEFAULT_CACHE_DIR = Path(".cache") / "roteamento"
#: Diretorio padrao dos tiles Terrarium baixados por ``fetch-dem`` (uso offline).
DEFAULT_DEM_DIR = Path("data") / "dem"


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roteamento",
        description=(
            "Grafo ciclavel, snapping e modelo fisico a partir do OpenStreetMap."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser(
        "build", help="baixa dados do OSM e monta o grafo ciclavel"
    )
    build.add_argument(
        "--bbox",
        default=None,
        help=(
            "sul,oeste,norte,leste em graus decimais (formato Overpass); "
            "opcional (e ignorado) quando --pbf e informado"
        ),
    )
    build.add_argument(
        "--pbf",
        default=None,
        help=(
            "arquivo OpenStreetMap .osm.pbf local — fonte geografica offline "
            "(dispensa Overpass e --bbox)"
        ),
    )
    build.add_argument("--out", default="graph.json", help="arquivo JSON de saida")
    build.add_argument(
        "--geojson", dest="geojson_out", default=None, help="arquivo GeoJSON opcional"
    )
    build.add_argument(
        "--elevation",
        choices=("osm", "opentopodata", "dem", "terrarium", "none"),
        default="opentopodata",
        help=(
            "fonte de elevacao: 'opentopodata' combina ele=* do OSM com SRTM 30m "
            "(padrao, com cache); 'osm' usa so as tags (rapido, cobertura baixa); "
            "'dem' le um GeoTIFF local; 'terrarium' le tiles PNG locais "
            "(offline; veja 'fetch-dem'); 'none' desliga"
        ),
    )
    build.add_argument("--dem", default=None, help="caminho do GeoTIFF (com --elevation dem)")
    build.add_argument(
        "--tiles-dir",
        default=None,
        help="raiz dos tiles Terrarium {z}/{x}/{y}.png (com --elevation terrarium)",
    )
    build.add_argument(
        "--tiles-zoom",
        type=int,
        default=None,
        help=(
            "zoom fixo dos tiles Terrarium; se omitido, usa o nivel mais fino "
            "disponivel (com fallback aos mais grosseiros)"
        ),
    )
    build.add_argument(
        "--spacing",
        type=float,
        default=25.0,
        help="espacamento em metros das amostras de elevacao (padrao: 25)",
    )
    build.add_argument(
        "--smoothing",
        type=int,
        default=3,
        help=(
            "janela (amostras) da media movel no perfil de elevacao; "
            "1 desliga (padrao: 3)"
        ),
    )
    build.add_argument(
        "--overpass",
        default=DEFAULT_ENDPOINTS[0],
        help=f"endpoint Overpass (padrao: {DEFAULT_ENDPOINTS[0]})",
    )
    build.add_argument(
        "--highways",
        default=None,
        help="lista de highway=* separada por virgula (padrao: rede ciclavel urbana)",
    )
    build.add_argument("--allow-footway", action="store_true")
    build.add_argument("--no-dismount", action="store_true")
    build.add_argument(
        "--no-simplify",
        action="store_true",
        help="nao contrai nos de grau 2 (mantem a topologia crua do OSM)",
    )
    build.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    build.add_argument("--no-cache", action="store_true")
    build.add_argument("--timeout", type=int, default=180, help="timeout do Overpass (s)")
    build.set_defaults(handler=cmd_build)

    fetch_dem = subparsers.add_parser(
        "fetch-dem",
        help="baixa tiles de elevacao Terrarium para uso offline (etapa de rede)",
    )
    fetch_dem.add_argument(
        "--bbox",
        required=True,
        help="sul,oeste,norte,leste em graus decimais (formato Overpass)",
    )
    fetch_dem.add_argument(
        "--zoom",
        type=int,
        default=12,
        help=(
            "nivel de zoom XYZ; cada nivel dobra a resolucao e quadruplica o "
            "numero de tiles (padrao: 12)"
        ),
    )
    fetch_dem.add_argument(
        "--out",
        default=str(DEFAULT_DEM_DIR),
        help=f"diretorio de destino dos tiles (padrao: {DEFAULT_DEM_DIR})",
    )
    fetch_dem.add_argument(
        "--timeout", type=float, default=60.0, help="timeout por requisicao HTTP (s)"
    )
    fetch_dem.set_defaults(handler=cmd_fetch_dem)

    stats = subparsers.add_parser("stats", help="resumo e validacao de um grafo salvo")
    stats.add_argument("path")
    stats.add_argument("--json", action="store_true", help="imprime o resumo em JSON")
    stats.add_argument("--issues", action="store_true", help="lista todos os problemas")
    stats.set_defaults(handler=cmd_stats)

    geojson = subparsers.add_parser("geojson", help="converte o grafo para GeoJSON")
    geojson.add_argument("path")
    geojson.add_argument("out")
    geojson.set_defaults(handler=cmd_geojson)

    validate = subparsers.add_parser("validate", help="valida a consistencia do grafo")
    validate.add_argument("path")
    validate.add_argument(
        "--strict",
        action="store_true",
        help="retorna codigo 1 se houver qualquer problema (inclusive avisos)",
    )
    validate.set_defaults(handler=cmd_validate)

    snap = subparsers.add_parser(
        "snap",
        help="conecta origem/destino ao grafo e monta o grafo de consulta",
    )
    snap.add_argument("path", help="grafo salvo em JSON")
    snap.add_argument("--origin", required=True, help="origem no formato lat,lon")
    snap.add_argument(
        "--destination", required=True, help="destino no formato lat,lon"
    )
    snap.add_argument(
        "--max-distance",
        type=float,
        default=100.0,
        help="desvio maximo aceito em metros (padrao: 100)",
    )
    snap.add_argument(
        "--node-preference",
        type=float,
        default=10.0,
        help="distancia maxima para preferir um no existente (padrao: 10 m)",
    )
    snap.add_argument(
        "--node-tolerance",
        type=float,
        default=2.0,
        help="folga do no em relacao ao desvio do arco (padrao: 2 m)",
    )
    snap.add_argument(
        "--min-split",
        type=float,
        default=1.0,
        help="distancia minima das pontas para dividir o arco (padrao: 1 m)",
    )
    snap.add_argument(
        "--no-dismount",
        action="store_true",
        help="nao conecta em trechos bicycle=dismount",
    )
    snap.add_argument(
        "--out-query",
        default=None,
        help="salva o grafo de consulta (JSON) no caminho indicado",
    )
    snap.add_argument(
        "--json", action="store_true", help="imprime o resultado em JSON"
    )
    snap.set_defaults(handler=cmd_snap)

    physical = subparsers.add_parser(
        "physical",
        help="estima tempo e energia fisica de um arco",
    )
    physical.add_argument("path", help="grafo salvo em JSON")
    physical.add_argument("--arc", required=True, help="id do arco")
    physical.add_argument(
        "--initial-speed",
        type=float,
        default=0.0,
        help="velocidade inicial em m/s (padrao: 0)",
    )
    physical.add_argument(
        "--power",
        type=float,
        default=None,
        help="potencia no pedivela em W (padrao: 200 W ou --max-power)",
    )
    physical.add_argument(
        "--max-power",
        type=float,
        default=600.0,
        help="potencia maxima do ciclista em W (padrao: 600)",
    )
    physical.add_argument(
        "--cyclist-mass", type=float, default=75.0, help="massa do ciclista (kg)"
    )
    physical.add_argument(
        "--frontal-area", type=float, default=0.5, help="area frontal (m2)"
    )
    physical.add_argument(
        "--bike-mass", type=float, default=12.0, help="massa da bicicleta (kg)"
    )
    physical.add_argument(
        "--crr",
        type=float,
        default=0.005,
        help="coeficiente de resistencia ao rolamento (padrao: 0.005)",
    )
    physical.add_argument(
        "--cd",
        type=float,
        default=0.9,
        help="coeficiente aerodinamico (padrao: 0.9)",
    )
    physical.add_argument(
        "--efficiency",
        type=float,
        default=0.97,
        help="eficiencia da transmissao (padrao: 0.97)",
    )
    physical.add_argument(
        "--step",
        type=float,
        default=None,
        help=(
            "passo espacial da simulacao em metros "
            "(padrao: depende do modo — search 5, validation 1)"
        ),
    )
    physical.add_argument(
        "--mode",
        choices=[mode.value for mode in SimulationMode],
        default=SimulationMode.SEARCH.value,
        help=(
            "modo de execucao (search prioriza desempenho; "
            "validation refina o passo)"
        ),
    )
    physical.add_argument(
        "--safe-max-speed",
        type=float,
        default=22.0,
        help="limite global de velocidade em m/s (padrao: 22; 0 desliga)",
    )
    physical.add_argument(
        "--air-density", type=float, default=1.225, help="densidade do ar (kg/m3)"
    )
    physical.add_argument(
        "--headwind",
        type=float,
        default=0.0,
        help="vento de frente em m/s (negativo = cauda; padrao: 0)",
    )
    physical.add_argument(
        "--dismount-speed",
        type=float,
        default=1.4,
        help="velocidade a pe em trechos dismount (m/s; padrao: 1.4)",
    )
    physical.add_argument(
        "--json", action="store_true", help="imprime o resultado em JSON"
    )
    physical.set_defaults(handler=cmd_physical)

    plan = subparsers.add_parser(
        "plan",
        help=(
            "consulta offline completa: zona dinamica + busca + fisica completa "
            "+ previsao (ML) + classificacao"
        ),
    )
    plan.add_argument("path", help="grafo salvo em JSON (a cidade inteira)")
    plan.add_argument("--origin", required=True, help="origem no formato lat,lon")
    plan.add_argument(
        "--destination", required=True, help="destino no formato lat,lon"
    )
    plan.add_argument(
        "--profile",
        default="fastest",
        help="perfil de rota (padrao: fastest)",
    )
    plan.add_argument(
        "--max-routes",
        type=int,
        default=None,
        help="limita as rotas as N melhores pela ordem do perfil (padrao: todas)",
    )
    plan.add_argument(
        "--max-distance",
        type=float,
        default=100.0,
        help="desvio maximo do snapping em metros (padrao: 100)",
    )
    plan.add_argument(
        "--node-preference",
        type=float,
        default=10.0,
        help="distancia maxima para preferir um no existente (padrao: 10 m)",
    )
    plan.add_argument(
        "--node-tolerance",
        type=float,
        default=2.0,
        help="folga do no em relacao ao desvio do arco (padrao: 2 m)",
    )
    plan.add_argument(
        "--min-split",
        type=float,
        default=1.0,
        help="distancia minima das pontas para dividir o arco (padrao: 1 m)",
    )
    plan.add_argument(
        "--no-dismount",
        action="store_true",
        help="nao conecta em trechos bicycle=dismount",
    )
    plan.add_argument(
        "--cyclist-mass", type=float, default=75.0, help="massa do ciclista (kg)"
    )
    plan.add_argument(
        "--max-power",
        type=float,
        default=600.0,
        help="potencia maxima do ciclista em W (padrao: 600)",
    )
    plan.add_argument(
        "--power",
        type=float,
        default=None,
        help="potencia no pedivela em W (padrao: 200 W ou --max-power)",
    )
    plan.add_argument(
        "--frontal-area", type=float, default=0.5, help="area frontal (m2)"
    )
    plan.add_argument(
        "--bike-mass", type=float, default=12.0, help="massa da bicicleta (kg)"
    )
    plan.add_argument(
        "--crr",
        type=float,
        default=0.005,
        help="coeficiente de resistencia ao rolamento (padrao: 0.005)",
    )
    plan.add_argument(
        "--cd",
        type=float,
        default=0.9,
        help="coeficiente aerodinamico (padrao: 0.9)",
    )
    plan.add_argument(
        "--efficiency",
        type=float,
        default=0.97,
        help="eficiencia da transmissao (padrao: 0.97)",
    )
    plan.add_argument(
        "--step",
        type=float,
        default=None,
        help="passo espacial da simulacao em metros (padrao: depende do modo)",
    )
    plan.add_argument(
        "--mode",
        choices=[mode.value for mode in SimulationMode],
        default=SimulationMode.SEARCH.value,
        help="modo de execucao (padrao: search)",
    )
    plan.add_argument(
        "--safe-max-speed",
        type=float,
        default=22.0,
        help="limite global de velocidade em m/s (padrao: 22; 0 desliga)",
    )
    plan.add_argument(
        "--air-density", type=float, default=1.225, help="densidade do ar (kg/m3)"
    )
    plan.add_argument(
        "--headwind",
        type=float,
        default=0.0,
        help="vento de frente em m/s (negativo = cauda; padrao: 0)",
    )
    plan.add_argument(
        "--dismount-speed",
        type=float,
        default=1.4,
        help="velocidade a pe em trechos dismount (m/s; padrao: 1.4)",
    )
    plan.add_argument(
        "--model-store",
        default=None,
        help="diretorio do armazenamento local de modelos (offline)",
    )
    plan.add_argument(
        "--model-version",
        default=None,
        help="versao do modelo calibrado a carregar do --model-store",
    )
    plan.add_argument(
        "--rpe-min", type=float, default=None, help="RPE aceitavel minimo (preferencia)"
    )
    plan.add_argument(
        "--rpe-max", type=float, default=None, help="RPE aceitavel maximo (preferencia)"
    )
    plan.add_argument(
        "--prefer-bands",
        default=None,
        help=(
            "faixas de intensidade preferidas, separadas por virgula (i1..i5; "
            "estimativa nao calibrada)"
        ),
    )
    plan.add_argument(
        "--min-band-fraction",
        type=float,
        default=None,
        help="fracao minima (na --band-basis) que deve estar em --prefer-bands",
    )
    plan.add_argument(
        "--band-basis",
        choices=("distance", "duration"),
        default="distance",
        help="base da distribuicao de intensidade (padrao: distance)",
    )
    plan.add_argument(
        "--json", action="store_true", help="imprime o resultado em JSON"
    )
    plan.set_defaults(handler=cmd_plan)

    return parser


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    if not args.pbf and not args.bbox:
        print(
            "erro: informe --bbox (Overpass) ou --pbf (arquivo OSM .pbf local)",
            file=sys.stderr,
        )
        return 2

    highways = (
        tuple(item.strip() for item in args.highways.split(",") if item.strip())
        if args.highways
        else DEFAULT_HIGHWAYS
    )

    if args.pbf:
        print(f"OSM.PBF local: {args.pbf}")
        try:
            data = read_osm_pbf(args.pbf, highways=highways)
        except Exception as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 1
    else:
        bbox = parse_bbox(args.bbox)
        cache_dir = None if args.no_cache else Path(args.cache_dir)
        client = OverpassClient(
            endpoint=args.overpass,
            cache_dir=cache_dir,
            timeout_s=float(args.timeout),
        )
        print(f"Overpass: {args.overpass}")
        print(f"bbox: {bbox}")
        data = client.fetch(bbox, highways=highways, timeout_s=args.timeout)

    print(f"OSM: {len(data.nodes)} nos, {len(data.ways)} vias")

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

    _print_surface_table(summary)
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

    _print_surface_table(summary)

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
        _print_snap(summary)

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
        _print_physical(arc, result, cyclist, bicycle)

    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Consulta offline: zona dinamica + busca + fisica completa + ML."""
    graph = load_json(args.path)

    if (args.rpe_min is None) != (args.rpe_max is None):
        print(
            "erro: --rpe-min e --rpe-max devem ser informados juntos",
            file=sys.stderr,
        )
        return 2

    predictor = None
    if args.model_store or args.model_version:
        if not (args.model_store and args.model_version):
            print(
                "erro: --model-store e --model-version devem ser informados juntos",
                file=sys.stderr,
            )
            return 2
        try:
            predictor = load_predictor(
                ModelStore(args.model_store), args.model_version
            )
        except (ModelNotFoundError, ModelIncompatibleError) as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 1

    preference = (
        EffortPreference(acceptable_rpe=(args.rpe_min, args.rpe_max))
        if args.rpe_min is not None
        else None
    )

    if args.prefer_bands is not None:
        bands = tuple(
            item.strip() for item in args.prefer_bands.split(",") if item.strip()
        )
        if not bands:
            print("erro: --prefer-bands nao pode ser vazio", file=sys.stderr)
            return 2
        if args.min_band_fraction is None:
            print(
                "erro: --prefer-bands exige --min-band-fraction",
                file=sys.stderr,
            )
            return 2
        base = preference or EffortPreference()
        preference = EffortPreference(
            borg_scale=base.borg_scale,
            acceptable_rpe=base.acceptable_rpe,
            preferred_intensity_bands=bands,
            min_intensity_band_fraction=args.min_band_fraction,
            intensity_band_basis=args.band_basis,
        )

    max_power = max(args.max_power, args.power or 0.0)
    sustainable = args.power if args.power is not None else min(200.0, max_power)
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
        snap_options = SnapOptions(
            max_distance_m=args.max_distance,
            node_preference_m=args.node_preference,
            node_offset_tolerance_m=args.node_tolerance,
            min_split_distance_m=args.min_split,
            allow_dismount=not args.no_dismount,
        )
        plan = plan_offline_routes(
            graph,
            parse_coord(args.origin),
            parse_coord(args.destination),
            cyclist=cyclist,
            bicycle=bicycle,
            profile=args.profile,
            snap_options=snap_options,
            traversal=traversal,
            environment=environment,
            power_w=args.power,
            predictor=predictor,
            preference=preference,
            max_routes=args.max_routes,
        )
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        _print_plan(plan)
    return 0 if plan.reached else 1


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


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------
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
        from osm.parse import OverpassData

        assert isinstance(data, OverpassData)
        provider = ChainElevation(
            OsmTagElevation.from_nodes(data.nodes.values()),
            OpenTopoDataElevation(),
        )
    else:  # osm
        from osm.parse import OverpassData

        assert isinstance(data, OverpassData)
        provider = OsmTagElevation.from_nodes(data.nodes.values())

    if args.no_cache:
        return provider
    return SqliteElevationCache(provider, Path(args.cache_dir) / "elevation.sqlite")


def _print_snap(summary: dict) -> None:
    for key, label in (("origin", "origem"), ("destination", "destino")):
        item = summary[key]
        if item["kind"] == "node":
            print(
                f"{label}: no {item['node_id']} "
                f"(offset {item['offset_m']:.1f} m)"
            )
        else:
            fraction = item["fraction"] or 0.0
            along = item["along_m"] or 0.0
            print(
                f"{label}: arco {item['arc_id']} "
                f"(offset {item['offset_m']:.1f} m, fracao {fraction:.3f}, "
                f"{along:.1f} m)"
            )

    print(f"conectado: {'sim' if summary['connected'] else 'nao'}")
    query = summary["query"]
    print(
        f"grafo de consulta: {query['nodes']} nos, {query['arcs']} arcos, "
        f"{query['segments']} segmentos"
    )


def _print_plan(plan: RoutePlan) -> None:
    """Resumo textual do plano: zona, box e rotas descobertas."""
    width_km, height_km = bbox_size_km(plan.bbox)
    search_w, search_h = bbox_size_km(plan.search_bbox)
    share = 100.0 * plan.zone_arcs / plan.graph_arcs if plan.graph_arcs else 0.0
    print(f"perfil: {plan.profile_id}")
    print(
        f"grafo: {plan.graph_nodes} nos, {plan.graph_arcs} arcos (cidade inteira)"
    )
    print(
        f"zona de busca: {plan.zone_nodes} nos, {plan.zone_arcs} arcos "
        f"({share:.1f}% do grafo) em {plan.iterations} iteracao(oes)"
    )
    print(f"box de busca (dinamico): {search_w:.2f} x {search_h:.2f} km")
    print(
        f"box final (pinos + rotas): {width_km:.2f} x {height_km:.2f} km "
        "(limita a altimetria)"
    )

    if not plan.reached:
        print("nenhuma rota conectada entre origem e destino")
        return

    trunc = " (truncada)" if plan.truncated else ""
    total_routes = plan.count + plan.rejected_count
    print(
        f"rotas descobertas: {total_routes} "
        f"(fronteira Pareto={plan.pareto_route_count}{trunc})"
    )
    if plan.rejected_count:
        print(
            f"  viaveis: {plan.count} | excluidas: {plan.rejected_count} "
            "(nao recomendaveis)"
        )
    for index, route in enumerate(plan.routes, start=1):
        metrics = route.assessment.metrics
        marker = " [recomendada]" if route.recommended else ""
        print(
            f"[{index}]{marker} {route.route_id}: "
            f"{metrics.distance_m / 1000.0:.2f} km | "
            f"{metrics.time_s / 60.0:.1f} min | "
            f"subida {metrics.ascent_m:.0f} m | "
            f"descida {metrics.descent_m:.0f} m"
        )
        print(f"    seguranca: {route.safety_state.value}")
        estimate = route.intensity
        bands = " ".join(
            f"{name}={estimate.band_distance_fraction.get(name, 0.0):.0%}"
            for name in INTENSITY_BAND_NAMES
        )
        print(
            f"    intensidade (nao calibrada): RPE medio {estimate.mean_rpe:.1f} "
            f"| faixas(dist): {bands}"
        )
        rpe = route.predicted_rpe
        if rpe is None:
            print(
                f"    esforco: {route.outcome.value} | RPE fisiologico indisponivel "
                f"(status={route.prediction_status.value})"
            )
        else:
            print(
                f"    esforco: {route.outcome.value} | "
                f"RPE previsto {rpe:.1f} | "
                f"preferencia: {route.classification.verdict.value}"
            )
        if route.assessment.limitations:
            print("    limitacoes: " + ", ".join(route.assessment.limitations))

    if plan.rejected:
        print(f"rotas excluidas (nao recomendaveis): {plan.rejected_count}")
        for route in plan.rejected:
            metrics = route.assessment.metrics
            print(
                f"[x] {route.route_id} | seguranca=rejected | "
                f"{metrics.distance_m / 1000.0:.2f} km"
            )
            for reason in route.safety.reasons:
                arc = f" (arco {reason.arc_id})" if reason.arc_id else ""
                print(f"    motivo: {reason.kind.value}: {reason.detail}{arc}")

    for warning in plan.warnings:
        print(f"aviso: {warning}")


def _print_physical(
    arc: Arc,
    result: TraversalResult,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
) -> None:
    print(
        f"arco: {arc.id} | {arc.distance_m:.1f} m | "
        f"{arc.segment_count} segmento(s)"
    )
    print(
        f"perfil: subida {result.ascent_m:.1f} m | "
        f"descida {result.descent_m:.1f} m"
    )
    print(
        f"tempo: {result.time_s:.1f} s | "
        f"velocidade media: {result.average_speed_kmh:.1f} km/h"
    )
    print(
        f"velocidade: {result.initial_speed_mps:.2f} -> "
        f"{result.final_speed_mps:.2f} m/s"
    )
    print(
        f"energia: mecanica {result.mechanical_energy_j / 1000.0:.1f} kJ | "
        f"ciclista {result.rider_energy_j / 1000.0:.1f} kJ | "
        f"frenagem {result.braking_energy_j / 1000.0:.1f} kJ"
    )
    print(
        f"massa total: {cyclist.mass_kg + bicycle.mass_kg:.1f} kg | "
        f"potencia: {cyclist.sustainable_power_w:.0f} W | "
        f"eficiencia: {bicycle.drivetrain_efficiency:.2f}"
    )
    if result.stalled:
        print("aviso: o modelo estagnou em baixa velocidade")
    if result.used_min_speed_regularization:
        print(
            "aviso: passo usou o piso de velocidade "
            f"({MIN_SPEED_MPS} m/s) — precisao nao validada no regime de "
            "baixa velocidade"
        )
    if result.result_status is not SimulationStatus.VALID:
        uncertainty = result.elevation_uncertainty
        missing = (
            uncertainty.missing_distance_m if uncertainty is not None else 0.0
        )
        print(
            f"atencao: resultado {result.result_status.value} — "
            f"elevacao ausente em {missing:.1f} m "
            "(hipotese explicita de terreno plano)"
        )


def _print_surface_table(summary: dict) -> None:
    surface_lengths: dict[str, float] = summary["length_by_surface_m"]
    total = sum(surface_lengths.values()) or 1.0
    print("superficie:")
    for surface, length in sorted(
        surface_lengths.items(), key=lambda item: item[1], reverse=True
    ):
        share = 100.0 * length / total
        print(f"  {surface:<16} {length / 1000.0:8.3f} km  {share:5.1f}%")

    status_lengths: dict[str, float] = summary.get("length_by_surface_status_m") or {}
    if status_lengths:
        total_status = sum(status_lengths.values()) or 1.0
        print("proveniencia da superficie:")
        for status, length in sorted(
            status_lengths.items(), key=lambda item: item[1], reverse=True
        ):
            share = 100.0 * length / total_status
            print(f"  {status:<16} {length / 1000.0:8.3f} km  {share:5.1f}%")


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
