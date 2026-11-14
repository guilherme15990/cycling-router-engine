"""Preparo de tiles Terrarium para uso offline.

Este modulo faz a parte de **rede** do provedor de elevacao Terrarium: baixa os
tiles PNG ``{z}/{x}/{y}.png`` do acervo ``elevation-tiles-prod`` (Mapzen/AWS)
para um diretorio local que, depois, e lido offline por
``elevation.TerrariumElevation``. Nada aqui e importado pelo caminho de leitura
offline; usa apenas a biblioteca padrao (``urllib.request``).

O formato Terrarium codifica a altitude em metros num pixel RGB(A) como

    h = (R * 256 + G + B / 256) - 32768
"""

from __future__ import annotations

import math
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

BASE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium"
USER_AGENT = "roteamento-engine/0.1 (cycling graph; terrarium)"
TILE_SIZE = 256

#: Assinatura de um arquivo PNG; usada para nao gravar respostas invalidas.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

ProgressCallback = Callable[[int, int, Path], None]


class TerrariumTileError(RuntimeError):
    """Falha ao baixar um tile Terrarium."""


def lonlat_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Converte ``(lat, lon)`` no tile XYZ ``(x, y)`` do web-mercator (OSM).

    Args:
        lat: Latitude em graus decimais.
        lon: Longitude em graus decimais.
        zoom: Nivel de zoom XYZ.

    Returns:
        Par ``(x, y)`` do tile que contem a coordenada.

    Example:
        >>> lonlat_to_tile(-17.79, -50.92, 12)
        (1468, 2253)
    """
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


def tiles_for_bbox(
    bbox: tuple[float, float, float, float], zoom: int
) -> list[tuple[int, int]]:
    """Tiles XYZ ``(x, y)`` que cobrem ``bbox`` no ``zoom`` dado.

    Args:
        bbox: ``(sul, oeste, norte, leste)`` em graus decimais.
        zoom: Nivel de zoom XYZ.

    Returns:
        Lista em ordem deterministica: ``y`` crescente (norte -> sul) no laco
        externo e ``x`` crescente (oeste -> leste) no interno.
    """
    south, west, north, east = bbox
    x_min, y_north = lonlat_to_tile(north, west, zoom)
    x_max, y_south = lonlat_to_tile(south, east, zoom)
    x_min, x_max = min(x_min, x_max), max(x_min, x_max)
    y_min, y_max = min(y_north, y_south), max(y_north, y_south)
    return [
        (x, y) for y in range(y_min, y_max + 1) for x in range(x_min, x_max + 1)
    ]


def fetch_terrarium_tiles(
    bbox: tuple[float, float, float, float],
    zoom: int,
    dest_dir: str | Path,
    *,
    timeout_s: float = 60.0,
    user_agent: str = USER_AGENT,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Baixa os tiles de ``bbox``/``zoom`` para ``dest_dir`` (idempotente).

    Cada tile e gravado em ``dest_dir/{z}/{x}/{y}.png``. Arquivos ja presentes
    sao mantidos (nao ha novo download) e os diretorios sao criados conforme a
    necessidade. A gravacao e atomica (arquivo temporario + ``replace``).

    Args:
        bbox: ``(sul, oeste, norte, leste)`` em graus decimais.
        zoom: Nivel de zoom XYZ.
        dest_dir: Raiz de destino dos tiles.
        timeout_s: Timeout por requisicao HTTP, em segundos.
        user_agent: User-Agent educado enviado nas requisicoes.
        progress: Callback opcional ``progress(done, total, path)``, chamado
            uma vez por tile (inclusive os ja existentes).

    Returns:
        Caminhos locais dos tiles, na mesma ordem de :func:`tiles_for_bbox`.

    Raises:
        TerrariumTileError: Em falha de rede, HTTP ou resposta corrompida.
    """
    tiles = tiles_for_bbox(bbox, zoom)
    dest = Path(dest_dir)
    total = len(tiles)
    paths: list[Path] = []

    for index, (x, y) in enumerate(tiles):
        path = dest / str(zoom) / str(x) / f"{y}.png"
        paths.append(path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"{BASE_URL}/{zoom}/{x}/{y}.png"
            data = _download(url, timeout_s=timeout_s, user_agent=user_agent)
            _write_atomic(path, data)
        if progress is not None:
            progress(index + 1, total, path)

    return paths


def _download(url: str, *, timeout_s: float, user_agent: str) -> bytes:
    """Baixa ``url`` e devolve os bytes, validando a assinatura PNG."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise TerrariumTileError(f"HTTP {exc.code} ao baixar {url}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TerrariumTileError(f"falha ao baixar {url}: {exc}") from exc
    if not data.startswith(PNG_SIGNATURE):
        raise TerrariumTileError(f"resposta nao e um PNG: {url}")
    return data


def _write_atomic(path: Path, data: bytes) -> None:
    """Grava ``data`` em ``path`` de forma atomica (tmp + replace)."""
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)
