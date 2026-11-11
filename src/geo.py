"""Geometria basica: distancias, interpolacao e amostragem ao longo de linhas.

Todas as coordenadas sao pares ``(lat, lon)`` em graus decimais (WGS84).
Distancias horizontais sao calculadas com a formula de haversine, porque a
distancia de um arco deve ser o comprimento real do caminho e nao a reta
entre os nos (ver ALGO CICLISTA.md).
"""

from __future__ import annotations

import bisect
import math
from typing import Sequence

Coord = tuple[float, float]

#: Caixa envolvente no formato ``(sul, oeste, norte, leste)`` em graus decimais.
BBox = tuple[float, float, float, float]

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(a: Coord, b: Coord) -> float:
    """Distancia em metros entre duas coordenadas."""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def cumulative_distances(coords: Sequence[Coord]) -> list[float]:
    """Distancias acumuladas ao longo da polilinha (comeca em 0.0)."""
    if not coords:
        return []
    out = [0.0]
    for previous, current in zip(coords, coords[1:]):
        out.append(out[-1] + haversine_m(previous, current))
    return out


def line_length_m(coords: Sequence[Coord]) -> float:
    if len(coords) < 2:
        return 0.0
    return cumulative_distances(coords)[-1]


def interpolate_along(
    coords: Sequence[Coord],
    cumulative: Sequence[float],
    target_m: float,
) -> Coord:
    """Ponto a ``target_m`` metros do inicio da polilinha."""
    if not coords:
        raise ValueError("polilinha vazia")
    if len(coords) == 1 or cumulative[-1] <= 0.0:
        return coords[0]

    target_m = min(max(target_m, 0.0), cumulative[-1])
    index = bisect.bisect_right(cumulative, target_m) - 1
    index = min(index, len(coords) - 2)

    start_d = cumulative[index]
    end_d = cumulative[index + 1]
    if end_d <= start_d:
        return coords[index]

    ratio = (target_m - start_d) / (end_d - start_d)
    lat1, lon1 = coords[index]
    lat2, lon2 = coords[index + 1]
    return (lat1 + (lat2 - lat1) * ratio, lon1 + (lon2 - lon1) * ratio)


def sample_along(
    coords: Sequence[Coord],
    spacing_m: float,
) -> list[tuple[float, Coord]]:
    """Amostra a polilinha a cada ``spacing_m`` metros, incluindo os extremos.

    Retorna pares ``(distancia_m, coordenada)``. O ultimo intervalo pode ser
    menor que ``spacing_m`` para garantir que o ponto final seja amostrado.
    """
    if not coords:
        return []
    if spacing_m <= 0:
        raise ValueError("spacing_m deve ser positivo")

    cumulative = cumulative_distances(coords)
    total = cumulative[-1]
    if len(coords) == 1 or total <= 0.0:
        return [(0.0, coords[0])]

    samples: list[tuple[float, Coord]] = []
    distance = 0.0
    while distance < total:
        samples.append((distance, interpolate_along(coords, cumulative, distance)))
        distance += spacing_m

    target = total
    last_distance = samples[-1][0] if samples else 0.0
    if target - last_distance < 1e-6:
        samples[-1] = (target, coords[-1])
    else:
        samples.append((target, coords[-1]))
    return samples


def point_at_fraction(coords: Sequence[Coord], fraction: float) -> Coord:
    """Ponto na fracao ``fraction`` (0..1) do comprimento da polilinha."""
    cumulative = cumulative_distances(coords)
    return interpolate_along(coords, cumulative, cumulative[-1] * fraction)


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """Aceita ``sul,oeste,norte,leste`` (formato Overpass)."""
    parts = [chunk.strip() for chunk in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox deve ter 4 valores: sul,oeste,norte,leste")
    south, west, north, east = (float(part) for part in parts)
    if south >= north or west >= east:
        raise ValueError("bbox invalida: sul < norte e oeste < leste")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("latitude fora do intervalo [-90, 90]")
    return south, west, north, east


def format_bbox(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    return f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"


def parse_coord(text: str) -> tuple[float, float]:
    """Aceita ``lat,lon`` em graus decimais."""
    parts = [chunk.strip() for chunk in text.split(",")]
    if len(parts) != 2:
        raise ValueError("coordenada deve ser 'lat,lon'")
    lat, lon = (float(part) for part in parts)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"coordenada fora do intervalo: {text!r}")
    return lat, lon


def bbox_of(coords: Sequence[Coord]) -> tuple[float, float, float, float]:
    if not coords:
        raise ValueError("sem coordenadas")
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return min(lats), min(lons), max(lats), max(lons)


# ---------------------------------------------------------------------------
# Envelope dinamico de consulta (box)
#
# Portado de ``toporide`` (``src/domain/geo.ts``). O box de uma consulta e o
# **envelope do tracado real**: parte do retangulo dos pinos (origem + paradas +
# destino), com margem **por eixo**, e cresce para cobrir **todas** as rotas
# calculadas, de modo que nenhum trecho percorrido fique sem altimetria. Como o
# aplicativo e **integralmente offline**, este box limita a **busca** e a
# **altimetria** (nao ha um segundo modo em que o grafo inteiro seja usado).
# ---------------------------------------------------------------------------

#: Comprimento medio de um grau de latitude em metros (mesma regua do toporide).
M_PER_DEG_LAT = 110_574.0
#: Comprimento de um grau de longitude no equador (metros).
M_PER_DEG_LON_EQ = 111_320.0


def m_per_deg_lon(lat: float) -> float:
    """Metros por grau de longitude na latitude dada."""
    return M_PER_DEG_LON_EQ * math.cos(math.radians(lat))


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _round_pad_km(value: float) -> float:
    return round(_clamp(value, 0.1, 0.4), 2)


def expand_bbox(bbox: BBox, pad_m: float, lat_ref: float) -> BBox:
    """Expande o box em ``pad_m`` metros, nos dois sentidos de cada eixo."""
    south, west, north, east = bbox
    d_lat = pad_m / M_PER_DEG_LAT
    d_lon = pad_m / m_per_deg_lon(lat_ref)
    return (south - d_lat, west - d_lon, north + d_lat, east + d_lon)


def corridor_bbox_of(
    points: Sequence[Coord], pad_x_km: float, pad_y_km: float | None = None
) -> BBox:
    """Box do corredorenvolvendo **todos** os pontos + margem por eixo (km)."""
    if not points:
        raise ValueError("sem pontos")
    if pad_y_km is None:
        pad_y_km = pad_x_km
    south, west, north, east = bbox_of(points)
    ref = sum(point[0] for point in points) / len(points)
    d_lat = (pad_y_km * 1000.0) / M_PER_DEG_LAT
    d_lon = (pad_x_km * 1000.0) / m_per_deg_lon(ref)
    return (south - d_lat, west - d_lon, north + d_lat, east + d_lon)


def auto_box_pads(points: Sequence[Coord]) -> tuple[float, float]:
    """Margens automaticas do box (km, por lado), uma por eixo.

    A base cresce com a **raiz** da diagonal do trajeto (crescimento lento, que
    satura) em vez de crescer linearmente — evita boxes gigantes em trajetos
    longos. A anisotropia e suave (+/-20%): o eixo em que os pontos ja se
    estendem recebe **menos** folga; o eixo curto recebe **mais**. O resultado
    fica sempre entre 0,1 e 0,4 km por lado.
    """
    if not points:
        return (0.15, 0.15)
    if len(points) == 1:
        return (0.2, 0.2)

    south, west, north, east = bbox_of(points)
    ref = sum(point[0] for point in points) / len(points)
    span_x_km = ((east - west) * m_per_deg_lon(ref)) / 1000.0
    span_y_km = ((north - south) * M_PER_DEG_LAT) / 1000.0
    diag_km = math.hypot(span_x_km, span_y_km)

    base = _clamp(0.1 + 0.06 * math.sqrt(diag_km), 0.12, 0.32)
    total = span_x_km + span_y_km
    r_x = span_x_km / total if total > 0 else 0.5
    r_y = 1.0 - r_x
    return (
        _round_pad_km(base * (1.0 + 0.2 * (r_y - r_x))),
        _round_pad_km(base * (1.0 + 0.2 * (r_x - r_y))),
    )


def bbox_size_km(bbox: BBox) -> tuple[float, float]:
    """Dimensoes ``(largura_x_km, altura_y_km)`` do box."""
    south, west, north, east = bbox
    ref = (north + south) / 2.0
    return (
        ((east - west) * m_per_deg_lon(ref)) / 1000.0,
        ((north - south) * M_PER_DEG_LAT) / 1000.0,
    )


def bbox_of_routes(
    routes: Sequence[Sequence[Coord]], pad_m: float = 0.0
) -> BBox | None:
    """Box de todas as rotas (tracado real); ``None`` se nao houver pontos."""
    coords = [point for route in routes for point in route]
    if not coords:
        return None
    bbox = bbox_of(coords)
    if pad_m > 0.0:
        return expand_bbox(bbox, pad_m, (bbox[2] + bbox[0]) / 2.0)
    return bbox


def union_bbox(a: BBox, b: BBox) -> BBox:
    """Menor box que contem ``a`` e ``b``."""
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def bbox_with_routes(
    base: BBox | None,
    routes: Sequence[Sequence[Coord]],
    pad_m: float = 80.0,
) -> BBox | None:
    """Box efetivo: une o box dos pinos ao envelope do tracado de TODAS as rotas.

    Se uma rota sai do box inicial, a area cresce para cobrir tambem esse
    trecho — nenhum trecho percorrido fica sem altimetria.
    """
    route_box = bbox_of_routes(routes, pad_m)
    if base is None:
        return route_box
    if route_box is None:
        return base
    return union_bbox(base, route_box)


def ensure_min_span(bbox: BBox, min_span_deg: float) -> BBox:
    """Garante extensao minima (graus) dos dois lados do box."""
    south, west, north, east = bbox
    if north - south < min_span_deg:
        center = (north + south) / 2.0
        south, north = center - min_span_deg / 2.0, center + min_span_deg / 2.0
    if east - west < min_span_deg:
        center = (east + west) / 2.0
        west, east = center - min_span_deg / 2.0, center + min_span_deg / 2.0
    return (south, west, north, east)


def query_bbox(
    waypoints: Sequence[Coord],
    routes: Sequence[Sequence[Coord]] = (),
    *,
    pad_m: float = 80.0,
) -> BBox | None:
    """Box dinamico de uma consulta: pinos (com margens) unido ao tracado real.

    ``waypoints`` sao a origem + paradas + destino. O box comeca no retangulo
    dos pinos com as margens automaticas por eixo e cresce para cobrir todas as
    ``routes`` calculadas. Sem pinos nem rotas devolve ``None``.
    """
    base: BBox | None = None
    if waypoints:
        pad_x_km, pad_y_km = auto_box_pads(waypoints)
        base = corridor_bbox_of(waypoints, pad_x_km, pad_y_km)
    return bbox_with_routes(base, routes, pad_m)


def bbox_contains(outer: BBox, inner: BBox, eps: float = 1e-9) -> bool:
    """``True`` quando ``outer`` cobre todo o ``inner`` (com tolerancia)."""
    return (
        inner[0] >= outer[0] - eps
        and inner[2] <= outer[2] + eps
        and inner[1] >= outer[1] - eps
        and inner[3] <= outer[3] + eps
    )


def bbox_intersects(a: BBox, b: BBox) -> bool:
    """``True`` quando dois boxes se sobrepoem."""
    return not (
        a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
    )

