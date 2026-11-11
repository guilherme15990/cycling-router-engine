"""Parser ``argparse`` da CLI (ADR-033, E6).

Constroi a arvore de subcomandos e associa a cada um o handler de
``router.interfaces.cli.commands``. Nao executa comandos nem renderiza nada.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from router.app.tooling import SimulationMode
from router.interfaces.cli.commands import (
    cmd_build,
    cmd_fetch_dem,
    cmd_geojson,
    cmd_physical,
    cmd_plan,
    cmd_snap,
    cmd_stats,
    cmd_validate,
)

DEFAULT_CACHE_DIR = Path(".cache") / "roteamento"
#: Diretorio padrao dos tiles Terrarium baixados por ``fetch-dem`` (uso offline).
DEFAULT_DEM_DIR = Path("data") / "dem"

__all__ = ["build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roteamento",
        description=(
            "Grafo ciclavel, snapping e modelo fisico a partir do OpenStreetMap."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser(
        "build", help="monta o grafo ciclavel a partir de um .osm.pbf local"
    )
    build.add_argument(
        "--pbf",
        default=None,
        help=(
            "arquivo OpenStreetMap .osm.pbf local — fonte geografica offline "
            "(obrigatorio)"
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
    build.set_defaults(handler=cmd_build)

    fetch_dem = subparsers.add_parser(
        "fetch-dem",
        help="baixa tiles de elevacao Terrarium para uso offline (etapa de rede)",
    )
    fetch_dem.add_argument(
        "--bbox",
        required=True,
        help="sul,oeste,norte,leste em graus decimais",
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
