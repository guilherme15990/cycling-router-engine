"""Camada de vias com declividade para consumidores da API de integração."""
from __future__ import annotations

import math
from typing import Any


def prepare_dem_tiles(bbox: tuple[float, float, float, float], tiles_dir: str, *, zoom: int = 12, max_tiles: int = 400, timeout_s: float = 10.0) -> dict[str, Any]:
    """Baixa os tiles ausentes do bbox e reutiliza os existentes no disco.

    Faz rede explicitamente. Os dados existentes permanecem offline e podem
    ser usados por TerrariumElevation. O limite impede downloads acidentais.
    """
    from pathlib import Path
    from router.core.data.elevation.tiles import fetch_terrarium_tiles, lonlat_to_tile
    if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
        raise ValueError('bbox deve conter quatro coordenadas finitas')
    south, west, north, east = bbox
    if not (-85.05112878 <= south <= north <= 85.05112878 and -180 <= west <= east <= 180):
        raise ValueError('bbox inválido para Web Mercator')
    if isinstance(zoom, bool) or not isinstance(zoom, int) or not 0 <= zoom <= 18:
        raise ValueError('zoom deve ser inteiro entre 0 e 18')
    if isinstance(max_tiles, bool) or not isinstance(max_tiles, int) or max_tiles < 1:
        raise ValueError('max_tiles deve ser inteiro positivo')
    x0, y0 = lonlat_to_tile(north, west, zoom)
    x1, y1 = lonlat_to_tile(south, east, zoom)
    total = (abs(x1 - x0) + 1) * (abs(y1 - y0) + 1)
    if total > max_tiles:
        raise ValueError(f'área requer {total} tiles; limite é {max_tiles}')
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s deve ser positivo e finito")
    root = Path(tiles_dir)
    existing = sum((root / str(zoom) / str(x) / f'{y}.png').is_file()
                   for x in range(min(x0, x1), max(x0, x1)+1)
                   for y in range(min(y0, y1), max(y0, y1)+1))
    paths = fetch_terrarium_tiles(bbox, zoom, root, timeout_s=timeout_s)
    return dict(zoom=zoom, total=len(paths), reused=existing, downloaded=len(paths)-existing,
                paths=[str(path) for path in paths])


def altimetry_edges(graph: Any, bbox: tuple[float, float, float, float], *, max_edges: int = 2000) -> dict[str, Any]:
    """Vias no bbox (sul, oeste, norte, leste), com faixas TopoRide.

    A faixa é 0 (até 4%), 1 (até 8%) ou 2; altitude indisponível é None.
    O resultado é amostrado uniformemente se exceder max_edges. Usa os perfis
    do grafo já enriquecido; não baixa DEM nem altera o grafo.
    """
    if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
        raise ValueError('bbox deve conter quatro coordenadas finitas')
    south, west, north, east = bbox
    if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
        raise ValueError('bbox inválido ou cruzando o antimeridiano')
    if isinstance(max_edges, bool) or not isinstance(max_edges, int) or max_edges < 1:
        raise ValueError('max_edges deve ser inteiro positivo')
    found = []
    missing = 0
    for link in graph.links.values():
        for segment in link.segments:
            pts = segment.geometry
            if len(pts) < 2:
                continue
            lat, lon = zip(*pts)
            if max(lat) < south or min(lat) > north or max(lon) < west or min(lon) > east:
                continue
            slopes = []
            for a, b in zip(segment.elevation_profile, segment.elevation_profile[1:]):
                delta = b.distance_m - a.distance_m
                if delta > 0 and a.elevation_m is not None and b.elevation_m is not None:
                    slopes.append(100.0 * (b.elevation_m - a.elevation_m) / delta)
            up = max([0.0, *slopes]) if slopes else None
            down = max([0.0, *(-v for v in slopes)]) if slopes else None
            complete = len(slopes) == len(segment.elevation_profile) - 1 and len(segment.elevation_profile) >= 2
            if not complete:
                missing += 1
            worst = (max(up, down) if link.is_bidirectional else up) if complete else None
            band = None if worst is None else (0 if worst <= 4 else 1 if worst <= 8 else 2)
            found.append(dict(pts=[v for point in pts for v in point], band=band,
                              name=segment.name or '', hwClass=segment.highway or '',
                              oneWay=not link.is_bidirectional, meters=segment.distance_m,
                              upPct=up, downPct=down))
    total = len(found)
    edges = found if total <= max_edges else [found[(i * total) // max_edges] for i in range(max_edges)]
    return dict(edges=edges, total=total, truncated=total > max_edges, missing=missing)
