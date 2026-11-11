"""Elevacao: provedores de altitude e perfil ao longo do arco.

O documento recomenda guardar um perfil de amostras ao longo do arco em vez
de apenas a altura inicial/final. E isso que fazemos: a cada ``spacing_m``
metros amostramos a altitude e acumulamos subida/descida com

    H+ = sum(max(0, h_i - h_{i-1}))
    H- = sum(max(0, h_{i-1} - h_i))
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from geo import Coord, sample_along
from graph.model import ElevationQuality, ElevationSample

TryElevations = Sequence[float | None]

DEFAULT_TIMEOUT_S = 30.0
OPENTOPODATA_CHUNK = 100


class ElevationError(RuntimeError):
    """Falha ao obter elevacao de um provedor."""


class ElevationProvider(Protocol):
    name: str

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        """Altitudes na mesma ordem das coordenadas; ``None`` quando ausente."""


# --------------------------------------------------------------------------
# Provedores
# --------------------------------------------------------------------------
class ConstantElevation:
    """Altitude fixa. Util para testes e para grafos sem DEM."""

    name = "constant"
    resolution_m: float | None = None
    quality = ElevationQuality.ESTIMATED

    def __init__(self, elevation_m: float) -> None:
        self.elevation_m = elevation_m

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        return [self.elevation_m for _ in coords]


class OsmTagElevation:
    """Usa ``ele=*`` das próprias tags do OSM (quando existir)."""

    name = "osm-ele"
    resolution_m: float | None = None
    quality = ElevationQuality.SOURCE_REPORTED

    def __init__(self, elevation_by_coord: dict[tuple[float, float], float]) -> None:
        self._by_coord = elevation_by_coord

    @classmethod
    def from_nodes(cls, nodes: Iterable[object]) -> "OsmTagElevation":
        table: dict[tuple[float, float], float] = {}
        for node in nodes:
            tags = getattr(node, "tags", {}) or {}
            raw = tags.get("ele")
            if raw is None:
                continue
            try:
                table[_round_coord((node.lat, node.lon))] = float(str(raw).replace(",", "."))
            except (TypeError, ValueError):
                continue
        return cls(table)

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        return [self._by_coord.get(_round_coord(c)) for c in coords]


class CallableElevation:
    """Adapta uma funcao ``(lat, lon) -> altura`` a interface de provedor."""

    name = "callable"
    quality = ElevationQuality.ESTIMATED

    def __init__(
        self,
        fn: Callable[[float, float], float],
        name: str = "callable",
        resolution_m: float | None = None,
    ) -> None:
        self._fn = fn
        self.name = name
        self.resolution_m = resolution_m

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        return [float(self._fn(lat, lon)) for lat, lon in coords]


class ChainElevation:
    """Consulta provedores em ordem e usa o primeiro valor nao nulo."""

    name = "chain"

    def __init__(self, *providers: ElevationProvider) -> None:
        self._providers = providers
        self.name = "+".join(getattr(p, "name", "provider") for p in providers)
        resolutions = [
            getattr(p, "resolution_m", None) for p in providers
        ]
        resolutions = [value for value in resolutions if value is not None]
        self.resolution_m = max(resolutions) if resolutions else None

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        if not self._providers:
            return [None for _ in coords]
        result: list[float | None] = self._providers[0].elevations(coords)
        for provider in self._providers[1:]:
            missing = [i for i, value in enumerate(result) if value is None]
            if not missing:
                break
            extra = provider.elevations([coords[i] for i in missing])
            for index, value in zip(missing, extra):
                if value is not None:
                    result[index] = value
        return result


class OpenTopoDataElevation:
    """Cliente do OpenTopoData (SRTM 30 m por padrao).

    Limites publicos: 100 localidades por requisicao e ~1 req/s. O cliente
    fatia as coordenadas, respeita o intervalo e faz retry com backoff.
    """

    name = "opentopodata"
    quality = ElevationQuality.DEM_SAMPLED

    RESOLUTION_BY_DATASET = {
        "srtm30m": 30.0,
        "srtm90m": 90.0,
        "aster30m": 30.0,
        "eudem25m": 25.0,
        "mapzen": 30.0,
        "ned10m": 10.0,
        "nzdem8m": 8.0,
    }

    def __init__(
        self,
        dataset: str = "srtm30m",
        endpoint: str = "https://api.opentopodata.org/v1",
        min_interval_s: float = 1.1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = 4,
        user_agent: str = "roteamento-engine/0.1 (cycling graph)",
    ) -> None:
        self.dataset = dataset
        self.endpoint = endpoint.rstrip("/")
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.resolution_m = self.RESOLUTION_BY_DATASET.get(dataset)
        self._last_request = 0.0

    @property
    def url(self) -> str:
        return f"{self.endpoint}/{self.dataset}"

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        if not coords:
            return []
        result: list[float | None] = []
        for start in range(0, len(coords), OPENTOPODATA_CHUNK):
            chunk = coords[start : start + OPENTOPODATA_CHUNK]
            result.extend(self._fetch(chunk))
        return result

    def _fetch(self, coords: Sequence[Coord]) -> list[float | None]:
        locations = "|".join(f"{lat:.7f},{lon:.7f}" for lat, lon in coords)
        params = urllib.parse.urlencode({"locations": locations})
        url = f"{self.url}?{params}"
        payload = self._request_with_retry(url)

        results = payload.get("results", [])
        if len(results) != len(coords):
            raise ElevationError(
                f"OpenTopoData retornou {len(results)} resultados para {len(coords)} pontos"
            )

        values: list[float | None] = []
        for item in results:
            elevation = item.get("elevation") if isinstance(item, dict) else None
            values.append(float(elevation) if isinstance(elevation, (int, float)) else None)
        return values

    def _request_with_retry(self, url: str) -> dict:
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit()
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                status = payload.get("status")
                if status not in (None, "OK"):
                    raise ElevationError(f"OpenTopoData respondeu status={status!r}")
                return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ElevationError(f"OpenTopoData HTTP {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ElevationError(f"falha ao consultar OpenTopoData: {exc}") from exc
        raise ElevationError(f"falha ao consultar OpenTopoData: {last_error}")

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.min_interval_s - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


class RasterDemElevation:
    """Le altitudes de um GeoTIFF local (requer ``rasterio``)."""

    name = "dem"
    quality = ElevationQuality.DEM_SAMPLED

    def __init__(self, path: str | Path) -> None:
        try:
            import rasterio  # type: ignore
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise ElevationError(
                "rasterio nao instalado; use: pip install '.[dem]'"
            ) from exc
        self._rasterio = rasterio
        self._path = str(path)
        self._dataset = rasterio.open(self._path)
        self._nodata = self._dataset.nodata
        try:
            transform = self._dataset.transform
            self.resolution_m = abs(transform.a) if self._dataset.crs.is_projected else None
        except Exception:  # pragma: no cover - metadados incompletos
            self.resolution_m = None

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        if not coords:
            return []
        values: list[float | None] = []
        for value in self._dataset.sample([(lon, lat) for lat, lon in coords]):
            height = float(value[0])
            if self._nodata is not None and height == float(self._nodata):
                values.append(None)
            elif math.isnan(height):
                values.append(None)
            else:
                values.append(height)
        return values


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


class SqliteElevationCache:
    """Cache em disco para provedores de elevacao (chave: coordenada + provedor)."""

    name = "cached"

    def __init__(self, provider: ElevationProvider, path: str | Path) -> None:
        self._provider = provider
        self.name = getattr(provider, "name", "cached")
        self.resolution_m = getattr(provider, "resolution_m", None)
        self.quality = getattr(provider, "quality", ElevationQuality.DEM_SAMPLED)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS elevation (
                provider TEXT NOT NULL,
                lat INTEGER NOT NULL,
                lon INTEGER NOT NULL,
                elevation_m REAL,
                PRIMARY KEY (provider, lat, lon)
            )
            """
        )
        self._connection.commit()

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        if not coords:
            return []

        keys = [(_round_coord(c, 4), c) for c in coords]
        found: dict[tuple[int, int], float | None] = {}
        for rounded, _ in keys:
            if rounded in found:
                continue
            row = self._connection.execute(
                "SELECT elevation_m FROM elevation WHERE provider=? AND lat=? AND lon=?",
                (self.name, rounded[0], rounded[1]),
            ).fetchone()
            if row is not None:
                found[rounded] = row[0]

        missing_coords: list[Coord] = []
        missing_keys: list[tuple[int, int]] = []
        for rounded, coord in keys:
            if rounded not in found:
                missing_coords.append(coord)
                missing_keys.append(rounded)

        if missing_coords:
            fetched = self._provider.elevations(missing_coords)
            rows = [
                (self.name, key[0], key[1], value)
                for key, value in zip(missing_keys, fetched)
            ]
            self._connection.executemany(
                "INSERT OR REPLACE INTO elevation (provider, lat, lon, elevation_m) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._connection.commit()
            for key, value in zip(missing_keys, fetched):
                found[key] = value

        return [found[_round_coord(c, 4)] for c in coords]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqliteElevationCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class MemoElevation:
    """Memoiza em memoria coordenadas ja consultadas (evita chamadas repetidas)."""

    name = "memo"

    def __init__(self, provider: ElevationProvider) -> None:
        self._provider = provider
        self.name = getattr(provider, "name", "memo")
        self.resolution_m = getattr(provider, "resolution_m", None)
        self.quality = getattr(provider, "quality", ElevationQuality.DEM_SAMPLED)
        self._cache: dict[tuple[int, int], float | None] = {}

    def elevations(self, coords: Sequence[Coord]) -> list[float | None]:
        result: list[float | None] = [None] * len(coords)
        pending: list[Coord] = []
        pending_index: list[int] = []
        pending_keys: list[tuple[int, int]] = []

        for index, coord in enumerate(coords):
            key = _round_coord(coord)
            if key in self._cache:
                result[index] = self._cache[key]
            else:
                pending.append(coord)
                pending_index.append(index)
                pending_keys.append(key)

        if pending:
            fetched = self._provider.elevations(pending)
            for index, key, value in zip(pending_index, pending_keys, fetched):
                if key not in self._cache:
                    self._cache[key] = value
                result[index] = self._cache[key]
        return result


def _round_coord(coord: Coord, digits: int = 5) -> tuple[int, int]:
    scale = 10**digits
    return (int(round(coord[0] * scale)), int(round(coord[1] * scale)))


# --------------------------------------------------------------------------
# Perfil de elevacao do arco
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ElevationStats:
    """Perfil de elevacao de uma geometria, com ganho e perda acumulados.

    Attributes:
        profile: Amostras ``ElevationSample`` ao longo do perfil.
        gain_m: Subida acumulada (``H+``), em metros.
        loss_m: Descida acumulada (``H-``), em metros.
        min_elevation_m: Altitude minima observada, ou ``None`` sem dados.
        max_elevation_m: Altitude maxima observada, ou ``None`` sem dados.
    """

    profile: tuple[ElevationSample, ...]
    gain_m: float
    loss_m: float
    min_elevation_m: float | None
    max_elevation_m: float | None

    @property
    def delta_m(self) -> float | None:
        if len(self.profile) < 2:
            return None
        start = self.profile[0].elevation_m
        end = self.profile[-1].elevation_m
        if start is None or end is None:
            return None
        return end - start


def fill_missing(elevations: Sequence[float | None]) -> list[float | None]:
    """Interpola linearmente lacunas entre amostras conhecidas.

    As lacunas antes do primeiro valor conhecido e apos o ultimo sao
    preenchidas por extrapolacao constante (repetindo a borda).

    Args:
        elevations: Altitudes amostradas; ``None`` marca lacunas.

    Returns:
        Nova lista com as lacunas preenchidas (toda ``None`` se nao houver
        nenhum valor conhecido).
    """
    known = [i for i, value in enumerate(elevations) if value is not None]
    if not known:
        return [None for _ in elevations]

    filled: list[float | None] = list(elevations)
    first, last = known[0], known[-1]
    for index in range(0, first):
        filled[index] = filled[first]
    for index in range(last + 1, len(filled)):
        filled[index] = filled[last]

    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        left_value = float(filled[left])  # type: ignore[arg-type]
        right_value = float(filled[right])  # type: ignore[arg-type]
        for offset in range(1, right - left):
            ratio = offset / (right - left)
            filled[left + offset] = left_value + (right_value - left_value) * ratio
    return filled


def smooth_elevations(values: Sequence[float], window: int = 3) -> list[float]:
    """Suaviza o perfil com media movel centrada, preservando os extremos.

    Ruido de DEM em terreno plano infla H+ e H- (cada ondulacao de 1 m vira
    subida e descida). A janela padrao de 3 amostras remove parte desse ruido
    sem alterar rampas monotonas. ``window=1`` desliga o filtro.
    """
    if window <= 1 or len(values) <= window:
        return list(values)

    half = window // 2
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - half)
        end = min(len(values), index + half + 1)
        chunk = values[start:end]
        smoothed.append(sum(chunk) / len(chunk))

    # Pontas precisam continuar batendo com a elevacao dos nos.
    smoothed[0] = values[0]
    smoothed[-1] = values[-1]
    return smoothed


def build_elevation_profile(
    geometry: Sequence[Coord],
    provider: ElevationProvider,
    spacing_m: float = 25.0,
    smoothing: int = 3,
) -> ElevationStats:
    """Amostra a geometria e acumula ganho/perda de elevacao.

    Amostra a polilinha a cada ``spacing_m`` metros, consulta o provedor,
    interpola lacunas (:func:`fill_missing`) e suaviza o perfil
    (:func:`smooth_elevations`) antes de somar ``H+`` e ``H-``.

    Args:
        geometry: Polilinha ``(lat, lon)`` do arco ou segmento.
        provider: Provedor com ``elevations(coords)``.
        spacing_m: Espacamento da amostragem, em metros.
        smoothing: Janela da media movel (``1`` desliga o filtro).

    Returns:
        ``ElevationStats``. Sem geometria ou sem altitude disponivel, devolve
        perfil vazio e ganho/perda zero.

    Example:
        >>> from elevation import CallableElevation
        >>> stats = build_elevation_profile(
        ...     [(-23.55, -46.63), (-23.54, -46.62)],
        ...     CallableElevation(lambda lat, lon: 760.0),
        ...     spacing_m=50.0,
        ... )
        >>> stats.gain_m, stats.loss_m
        (0.0, 0.0)
    """
    samples = sample_along(geometry, spacing_m)
    if not samples:
        return ElevationStats((), 0.0, 0.0, None, None)

    distances = [distance for distance, _ in samples]
    coords = [coord for _, coord in samples]
    raw = provider.elevations(coords)
    elevations = fill_missing(raw)

    if not elevations or all(value is None for value in elevations):
        return ElevationStats((), 0.0, 0.0, None, None)

    heights = smooth_elevations(
        [float(value) for value in elevations if value is not None],
        window=smoothing,
    )

    source = getattr(provider, "name", "") or ""
    resolution_m = getattr(provider, "resolution_m", None)
    provider_quality = getattr(provider, "quality", ElevationQuality.DEM_SAMPLED)

    profile = tuple(
        ElevationSample(
            distance_m=distance,
            elevation_m=elevation,
            source_id=source,
            resolution_m=resolution_m,
            quality=(
                ElevationQuality.INTERPOLATED
                if index < len(raw) and raw[index] is None
                else provider_quality
            ),
        )
        for index, (distance, elevation) in enumerate(zip(distances, heights))
    )

    gain = 0.0
    loss = 0.0
    for previous, current in zip(profile, profile[1:]):
        delta = current.elevation_m - previous.elevation_m
        if delta > 0:
            gain += delta
        else:
            loss -= delta

    values = [sample.elevation_m for sample in profile]
    return ElevationStats(
        profile=profile,
        gain_m=round(gain, 3),
        loss_m=round(loss, 3),
        min_elevation_m=min(values),
        max_elevation_m=max(values),
    )
