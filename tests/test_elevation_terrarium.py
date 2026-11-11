"""Testes do provedor Terrarium offline e do preparo de tiles.

Totalmente auto-contidos e **sem rede**: um encoder PNG minimo e embutido aqui
para produzir tiles com altitudes conhecidas, escritos em ``tmp_path``.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from router.core.data.elevation import ElevationError, TerrariumElevation
from router.core.data.elevation.tiles import lonlat_to_tile, tiles_for_bbox

ZOOM = 12
TILE_X, TILE_Y = 1468, 2253
# Coordenadas que caem no pixel (0, 0) do tile (1468, 2253) acima.
LAT0, LON0 = -17.7278, -50.9765625
# Coordenada que cai no pixel (1, 2) do mesmo tile.
LAT1, LON1 = -17.7285, -50.9760
# Coordenada num tile vizinho a oeste: (1467, 2253) em z12.
LAT_W, LON_W = -17.78, -51.0


# --------------------------------------------------------------------------
# Encoder PNG minimo (filter 0 no caminho simples)
# --------------------------------------------------------------------------
def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _apply_filter(
    filter_type: int, row: bytes, previous: bytes, bpp: int
) -> bytes:
    out = bytearray(len(row))
    for index, value in enumerate(row):
        left = row[index - bpp] if index >= bpp else 0
        up = previous[index]
        up_left = previous[index - bpp] if index >= bpp else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = up
        elif filter_type == 3:
            predictor = (left + up) >> 1
        elif filter_type == 4:
            predictor = _paeth(left, up, up_left)
        else:  # pragma: no cover - erro de teste
            raise ValueError(f"filtro invalido: {filter_type}")
        out[index] = (value - predictor) & 0xFF
    return bytes(out)


def _encode_png(
    width: int,
    height: int,
    rows: list[bytes],
    *,
    color_type: int = 2,
    bit_depth: int = 8,
    filters: list[int] | None = None,
    interlace: int = 0,
) -> bytes:
    """Encoda um PNG minimo a partir de linhas de pixels ja montadas."""
    bpp = 4 if color_type == 6 else 3
    raw = bytearray()
    previous = bytes(width * bpp)
    for index, row in enumerate(rows):
        filter_type = filters[index] if filters else 0
        raw.append(filter_type)
        raw.extend(_apply_filter(filter_type, bytes(row), previous, bpp))
        previous = bytes(row)
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _chunk(b"IEND", b"")
    )


def _uniform_rgb(value: tuple[int, int, int], size: int = 256) -> list[bytes]:
    return [bytes(value) * size for _ in range(size)]


def _tile_with_pixels(
    points: dict[tuple[int, int], tuple[int, int, int]],
    *,
    size: int = 256,
    color_type: int = 2,
) -> bytes:
    """Monta um tile uniforme (0) com alguns pixels sobrescritos."""
    bpp = 4 if color_type == 6 else 3
    buffer = bytearray(size * size * bpp)
    for (pixel_x, pixel_y), (red, green, blue) in points.items():
        offset = (pixel_y * size + pixel_x) * bpp
        buffer[offset : offset + 3] = bytes((red, green, blue))
        if bpp == 4:
            buffer[offset + 3] = 255
    rows = [
        bytes(buffer[row * size * bpp : (row + 1) * size * bpp])
        for row in range(size)
    ]
    return _encode_png(size, size, rows, color_type=color_type)


def _write_tile(root: Path, zoom: int, x: int, y: int, data: bytes) -> Path:
    path = Path(root) / str(zoom) / str(x) / f"{y}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _terrarium_bytes(elevation_m: float) -> tuple[int, int, int]:
    """Bytes RGB que codificam ``elevation_m`` (B=0 por simplicidade)."""
    value = elevation_m + 32768.0
    red = int(value // 256)
    green = int(value % 256)
    return red, green, 0


# --------------------------------------------------------------------------
# Formula Terrarium
# --------------------------------------------------------------------------
def test_terrarium_formula_zero(tmp_path: Path) -> None:
    data = _encode_png(1, 1, [bytes((128, 0, 0))])
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    assert provider.elevations([(LAT0, LON0)]) == [0.0]


def test_terrarium_formula_fraction(tmp_path: Path) -> None:
    # 2x1: primeiro pixel +0.5 m, segundo -256.0 m.
    rows = [bytes((128, 0, 128)) + bytes((127, 0, 0))]
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, _encode_png(2, 1, rows))

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    values = provider.elevations([(LAT0, LON0)])
    assert values == [0.5]


def test_terrarium_negative_value(tmp_path: Path) -> None:
    data = _encode_png(1, 1, [bytes((127, 0, 0))])
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    assert provider.elevations([(LAT0, LON0)]) == [-256.0]


def test_terrarium_rgba_ignores_alpha(tmp_path: Path) -> None:
    rows = [bytes((128, 0, 128, 0))]  # alfa=0 deve ser ignorado
    data = _encode_png(1, 1, rows, color_type=6)
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    assert provider.elevations([(LAT0, LON0)]) == [0.5]


# --------------------------------------------------------------------------
# Matematica web-mercator e mapeamento de pixel
# --------------------------------------------------------------------------
def test_lonlat_to_tile_known_point() -> None:
    assert lonlat_to_tile(-17.79, -50.92, 12) == (1468, 2253)


def test_pixel_mapping_uses_correct_pixel(tmp_path: Path) -> None:
    points = {
        (0, 0): _terrarium_bytes(100.0),
        (1, 2): _terrarium_bytes(200.0),
    }
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, _tile_with_pixels(points))

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    values = provider.elevations([(LAT0, LON0), (LAT1, LON1)])

    assert values == [100.0, 200.0]


# --------------------------------------------------------------------------
# Dados ausentes e multiplas coordenadas
# --------------------------------------------------------------------------
def test_missing_tile_returns_none(tmp_path: Path) -> None:
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    assert provider.elevations([(LAT0, LON0)]) == [None]


def test_missing_and_present_tile(tmp_path: Path) -> None:
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, _encode_png(1, 1, [bytes((128, 0, 0))]))
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    values = provider.elevations([(LAT0, LON0), (LAT_W, LON_W)])

    assert values == [0.0, None]


def test_multiple_coords_same_tile(tmp_path: Path) -> None:
    points = {
        (0, 0): _terrarium_bytes(100.0),
        (1, 2): _terrarium_bytes(200.0),
    }
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, _tile_with_pixels(points))
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    values = provider.elevations([(LAT0, LON0), (LAT1, LON1), (LAT0, LON0)])

    assert values == [100.0, 200.0, 100.0]


def test_multiple_coords_across_tiles(tmp_path: Path) -> None:
    _write_tile(
        tmp_path, ZOOM, TILE_X, TILE_Y, _encode_png(256, 256, _uniform_rgb(_terrarium_bytes(100.0)))
    )
    _write_tile(
        tmp_path, ZOOM, 1467, TILE_Y, _encode_png(256, 256, _uniform_rgb(_terrarium_bytes(300.0)))
    )
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    # (LAT_W, LON_W) = (-17.78, -51.0) cai em (1467, 2253); as demais em (1468, 2253).
    values = provider.elevations([(LAT0, LON0), (LAT_W, LON_W)])

    assert values == [100.0, 300.0]


def test_empty_coords_returns_empty(tmp_path: Path) -> None:
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    assert provider.elevations([]) == []


# --------------------------------------------------------------------------
# Filtros PNG
# --------------------------------------------------------------------------
def test_png_filter_roundtrip_all_types(tmp_path: Path) -> None:
    size = 256
    rows: list[bytes] = []
    filters: list[int] = []
    expected: dict[tuple[int, int], float] = {}
    for pixel_y in range(size):
        filters.append(pixel_y % 5)  # 0..4, todos os filtros
        row = bytearray()
        for pixel_x in range(size):
            red = (pixel_x + pixel_y) % 256
            green = (2 * pixel_x + pixel_y) % 256
            blue = (3 * pixel_x) % 256
            row += bytes((red, green, blue))
            expected[(pixel_x, pixel_y)] = (
                red * 256.0 + green + blue / 256.0
            ) - 32768.0
        rows.append(bytes(row))

    data = _encode_png(size, size, rows, filters=filters)
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    coords = [(LAT0, LON0), (LAT1, LON1), (-17.79, -50.889), (-17.8, -50.93)]
    pixels = [(0, 0), (1, 2), (255, 190), (135, 220)]
    values = provider.elevations(coords)

    for value, pixel in zip(values, pixels):
        assert value == pytest.approx(expected[pixel])


# --------------------------------------------------------------------------
# PNGs invalidos
# --------------------------------------------------------------------------
def test_unsupported_color_type_raises(tmp_path: Path) -> None:
    # color_type=0 (cinza) nao e suportado.
    raw = b"\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)

    provider = TerrariumElevation(tmp_path, zoom=ZOOM)
    with pytest.raises(ElevationError):
        provider.elevations([(LAT0, LON0)])


def test_bad_signature_raises(tmp_path: Path) -> None:
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, b"isto nao e um png")
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    with pytest.raises(ElevationError):
        provider.elevations([(LAT0, LON0)])


def test_truncated_png_raises(tmp_path: Path) -> None:
    data = _encode_png(1, 1, [bytes((128, 0, 0))])[:16]
    _write_tile(tmp_path, ZOOM, TILE_X, TILE_Y, data)
    provider = TerrariumElevation(tmp_path, zoom=ZOOM)

    with pytest.raises(ElevationError):
        provider.elevations([(LAT0, LON0)])


# --------------------------------------------------------------------------
# Configuracao do provedor
# --------------------------------------------------------------------------
def test_resolution_and_quality_with_fixed_zoom(tmp_path: Path) -> None:
    from router.core.data.graph.model import ElevationQuality

    provider = TerrariumElevation(tmp_path, zoom=12)

    assert provider.resolution_m == pytest.approx(156543.03392 / 2**12)
    assert provider.quality is ElevationQuality.DEM_SAMPLED
    assert provider.name == "terrarium"


def test_resolution_none_when_zoom_unknown(tmp_path: Path) -> None:
    provider = TerrariumElevation(tmp_path)
    assert provider.resolution_m is None
    assert provider.zoom is None


def test_discovered_zoom_uses_finest_available(tmp_path: Path) -> None:
    # Tile grosseiro (z11) e fino (z12), valores diferentes.
    _write_tile(tmp_path, 11, 734, 1126, _encode_png(256, 256, _uniform_rgb((129, 244, 0))))
    _write_tile(tmp_path, 12, TILE_X, TILE_Y, _encode_png(1, 1, [bytes(_terrarium_bytes(100.0))]))

    provider = TerrariumElevation(tmp_path)
    # Deve preferir o zoom mais fino (12).
    assert provider.zoom is None
    assert provider.elevations([(LAT0, LON0)]) == [100.0]


def test_discovered_zoom_falls_back_to_coarser(tmp_path: Path) -> None:
    _write_tile(tmp_path, 11, 734, 1126, _encode_png(256, 256, _uniform_rgb((129, 244, 0))))

    provider = TerrariumElevation(tmp_path)
    values = provider.elevations([(-17.79, -50.92)])

    assert values == [500.0]


# --------------------------------------------------------------------------
# tiles_for_bbox (preparo de rede, mas logica pura)
# --------------------------------------------------------------------------
def test_tiles_for_bbox_single_tile() -> None:
    bbox = (-17.78, -50.96, -17.75, -50.93)
    assert tiles_for_bbox(bbox, 12) == [(1468, 2253)]


def test_tiles_for_bbox_spans_two_tiles() -> None:
    bbox = (-17.78, -51.0, -17.75, -50.93)
    assert tiles_for_bbox(bbox, 12) == [(1467, 2253), (1468, 2253)]


def test_tiles_for_bbox_spans_rows() -> None:
    # Bbox que cruza a fronteira entre y=2253 e y=2254 (lat ~ -17.811).
    bbox = (-17.85, -50.96, -17.75, -50.93)
    assert tiles_for_bbox(bbox, 12) == [(1468, 2253), (1468, 2254)]
