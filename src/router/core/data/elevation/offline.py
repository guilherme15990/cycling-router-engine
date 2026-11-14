"""Leitura offline de tiles Terrarium (ADR-031, D4: sem rede no caminho padrao)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from router.core.data.elevation.providers import ElevationError
from router.core.data.geo import Coord
from router.core.data.graph.model import ElevationQuality

# Assinatura fixa de todo arquivo PNG.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    """Previsor Paeth do PNG (filtro 4)."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_scanline(
    filter_type: int, line: bytearray, previous: bytearray, bpp: int
) -> None:
    """Reconstroi uma scanline PNG in-place (filtros None/Sub/Up/Average/Paeth)."""
    if filter_type == 0:  # None
        return
    if filter_type == 1:  # Sub
        for index in range(bpp, len(line)):
            line[index] = (line[index] + line[index - bpp]) & 0xFF
        return
    if filter_type == 2:  # Up
        for index in range(len(line)):
            line[index] = (line[index] + previous[index]) & 0xFF
        return
    if filter_type == 3:  # Average
        for index in range(len(line)):
            left = line[index - bpp] if index >= bpp else 0
            line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        return
    if filter_type == 4:  # Paeth
        for index in range(len(line)):
            left = line[index - bpp] if index >= bpp else 0
            up = previous[index]
            up_left = previous[index - bpp] if index >= bpp else 0
            line[index] = (line[index] + _paeth(left, up, up_left)) & 0xFF
        return
    raise ElevationError(f"filtro PNG desconhecido: {filter_type}")


def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Decodifica um PNG 8-bit RGB/RGBA para ``(largura, altura, pixels)``.

    Apenas ``color_type`` 2 (RGB) e 6 (RGBA) com 8 bits por canal sao aceitos;
    qualquer outro formato levanta :class:`ElevationError` com uma mensagem
    clara. Os filtros de linha do PNG sao aplicados aqui. O canal alfa e
    preservado nos bytes, mas ignorado por quem interpreta o pixel.
    """
    import zlib  # stdlib; importado aqui para nao alterar os imports do modulo

    if data[:8] != _PNG_SIGNATURE:
        raise ElevationError("PNG de elevacao sem assinatura valida")

    offset = 8
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    idat = bytearray()

    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        tag = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            raise ElevationError("PNG de elevacao truncado")
        payload = data[start:end]
        if tag == b"IHDR":
            if length != 13:
                raise ElevationError("IHDR de elevacao invalido")
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            bit_depth = payload[8]
            color_type = payload[9]
            if payload[12] != 0:
                raise ElevationError("PNG de elevacao entrelacado nao e suportado")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
        offset = end + 4  # pula o CRC

    if width is None or height is None or color_type is None or bit_depth is None:
        raise ElevationError("PNG de elevacao sem cabecalho IHDR")
    if width <= 0 or height <= 0:
        raise ElevationError("PNG de elevacao com dimensoes invalidas")
    if bit_depth != 8 or color_type not in (2, 6):
        raise ElevationError(
            "PNG de elevacao nao suportado: "
            f"bit_depth={bit_depth} color_type={color_type} "
            "(esperado 8-bit RGB=2 ou RGBA=6)"
        )

    bpp = 4 if color_type == 6 else 3
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ElevationError(f"falha ao descomprimir IDAT: {exc}") from exc

    stride = width * bpp
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise ElevationError(
            f"PNG de elevacao com {len(raw)} bytes de scanline; esperava {expected}"
        )

    pixels = bytearray(height * stride)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1 : position + 1 + stride])
        position += stride + 1
        _unfilter_scanline(filter_type, line, previous, bpp)
        start_row = row * stride
        pixels[start_row : start_row + stride] = line
        previous = line

    return width, height, bytes(pixels)


def _lonlat_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Tile XYZ ``(x, y)`` do web-mercator (OSM) para ``(lat, lon)``."""
    n = 2**zoom
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat)
    y = int(
        math.floor(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
            / 2.0
            * n
        )
    )
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _pixel_in_tile(lat: float, lon: float, zoom: int, x: int, y: int) -> tuple[int, int]:
    """Pixel ``(coluna, linha)`` dentro do tile ``(x, y)``, limitado a 0..255."""
    n = 2**zoom
    lat_rad = math.radians(lat)
    pixel_x = int((lon + 180.0) / 360.0 * n * 256) - x * 256
    pixel_y = (
        int(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
            / 2.0
            * n
            * 256
        )
        - y * 256
    )
    return max(0, min(255, pixel_x)), max(0, min(255, pixel_y))


class TerrariumElevation:
    """Altitudes de tiles Terrarium locais (PNG ``{z}/{x}/{y}.png``).

    O Terrarium e um DEM codificado em PNG RGB(A) (Mapzen/``elevation-tiles-prod``).
    Cada pixel carrega a altitude em metros como

        h = (R * 256 + G + B / 256) - 32768

    Este provedor e **totalmente offline**: apenas le arquivos do disco. O
    download dos tiles e feito, separadamente, pelo modulo ``elevation_tiles``
    (etapa de preparo de rede). Um tile ausente ou ilegivel vira ``None``; um
    PNG presente mas corrompido levanta :class:`ElevationError`, porque dados
    explicitamente invalidos nao devem ser silenciados.

    Args:
        tiles_dir: Raiz que contem ``{z}/{x}/{y}.png``.
        zoom: Nivel de zoom fixo. Se ``None``, os diretorios ``{z}`` existentes
            sao descobertos e, para cada consulta, usa-se o nivel mais fino que
            tiver o tile (com fallback para niveis mais grosseiros).
        name: Identificador do provedor.

    Notes:
        A resolucao nominal e ``156543.03392 / 2**zoom`` metros por pixel no
        equador (latitudes maiores tem passo horizontal menor). Quando ``zoom``
        e ``None`` o nivel pode variar por consulta, entao ``resolution_m``
        fica ``None`` para nao inventar precisao.
    """

    quality = ElevationQuality.DEM_SAMPLED
    EQUATOR_M_PER_PX = 156543.03392
    TILE_SIZE = 256

    def __init__(
        self,
        tiles_dir: str | Path,
        *,
        zoom: int | None = None,
        name: str = "terrarium",
    ) -> None:
        self.name = name
        self.tiles_dir = Path(tiles_dir)
        self._cache: dict[tuple[int, int, int], tuple[int, int, bytes] | None] = {}
        if zoom is not None:
            self.zoom: int | None = int(zoom)
            self._zooms: list[int] = [self.zoom]
            self.resolution_m: float | None = (
                self.EQUATOR_M_PER_PX / 2**self.zoom
            )
        else:
            self.zoom = None
            self._zooms = self._discover_zooms()
            self.resolution_m = None

    def _discover_zooms(self) -> list[int]:
        """Diretorios ``{z}`` inteiros, do mais fino para o mais grosseiro."""
        zooms: list[int] = []
        if self.tiles_dir.is_dir():
            for child in self.tiles_dir.iterdir():
                if not child.is_dir():
                    continue
                try:
                    zooms.append(int(child.name))
                except ValueError:
                    continue
        return sorted(set(zooms), reverse=True)

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        return [self._sample(lat, lon) for lat, lon in coords]

    def _sample(self, lat: float, lon: float) -> float | None:
        for zoom in self._zooms:
            x, y = _lonlat_to_tile(lat, lon, zoom)
            tile = self._load_tile(zoom, x, y)
            if tile is None:
                continue
            width, height, pixels = tile
            pixel_x, pixel_y = _pixel_in_tile(lat, lon, zoom, x, y)
            if not (0 <= pixel_x < width and 0 <= pixel_y < height):
                continue
            bpp = len(pixels) // (width * height) if width and height else 0
            if bpp < 3:
                raise ElevationError("tile de elevacao sem canais RGB suficientes")
            offset = (pixel_y * width + pixel_x) * bpp
            red = pixels[offset]
            green = pixels[offset + 1]
            blue = pixels[offset + 2]
            return (red * 256.0 + green + blue / 256.0) - 32768.0
        return None

    def _load_tile(
        self, zoom: int, x: int, y: int
    ) -> tuple[int, int, bytes] | None:
        """Le e decodifica o tile, com cache; ``None`` se ausente/ilegivel."""
        key = (zoom, x, y)
        if key in self._cache:
            return self._cache[key]

        path = self.tiles_dir / str(zoom) / str(x) / f"{y}.png"
        if not path.is_file():
            self._cache[key] = None
            return None
        try:
            data = path.read_bytes()
        except OSError:
            self._cache[key] = None
            return None

        decoded = _decode_png(data)
        self._cache[key] = decoded
        return decoded
