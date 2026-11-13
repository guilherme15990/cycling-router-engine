"""Camada de altimetria do app de debug.

Le os tiles Terrarium locais (``{z}/{x}/{y}.png``, o mesmo acervo consumido por
``router.core.data.elevation.TerrariumElevation``) e monta **uma unica imagem**
com hipsometria + sombreamento, pronta para ser usada como ``L.imageOverlay``
no mapa do navegador.

O mosaico permanece em Web Mercator, como os tiles e o mapa Leaflet.
Espacos entre tiles ausentes permanecem transparentes.

A imagem final fica em cache no disco (``.cache/altimetria.*``) e so e
regerada quando o conjunto de tiles muda.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

#: Rampa hipsometrica: altitude (m) -> cor RGB.
HYPSOMETRIC_RAMP: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (56, 142, 60)),
    (200.0, (124, 168, 70)),
    (450.0, (190, 196, 100)),
    (700.0, (208, 164, 100)),
    (950.0, (176, 120, 84)),
    (1200.0, (150, 128, 118)),
    (1700.0, (236, 236, 236)),
)

#: Opacidade padrao da camada sobre os tiles do OSM.
DEFAULT_OPACITY = 0.45
#: Largura/altura maxima (px) da imagem gerada.
DEFAULT_MAX_PX = 2048
#: Versao do algoritmo; entra no cache para invalidar imagens antigas.
RENDER_VERSION = 3


@dataclass(frozen=True)
class AltimetryOverlay:
    """Imagem da camada e metadados para montar o overlay no mapa."""

    png: bytes
    suffix: str
    mime: str
    bounds: tuple[float, float, float, float]
    zoom: int
    tile_size_px: int
    tiles: int
    min_m: float
    max_m: float
    width: int
    height: int
    resolution_m: float

    @property
    def data_uri(self) -> str:
        import base64

        return f"data:{self.mime};base64,{base64.b64encode(self.png).decode('ascii')}"

    @property
    def leaflet_bounds(self) -> list[list[float]]:
        south, west, north, east = self.bounds
        return [[south, west], [north, east]]


def _lat_of_tile(y: float, zoom: int) -> float:
    """Latitude (graus) da borda superior do tile ``y``."""
    n = 2**zoom
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))


def _lon_of_tile(x: float, zoom: int) -> float:
    """Longitude (graus) da borda esquerda do tile ``x``."""
    return x / 2**zoom * 360.0 - 180.0


def discover_tiles(tiles_dir: Path) -> tuple[int, list[int], list[int]] | None:
    """Zoom mais fino e faixas de ``x``/``y`` disponiveis no diretorio."""
    if not tiles_dir.is_dir():
        return None
    zooms: list[int] = []
    for child in tiles_dir.iterdir():
        if child.is_dir() and child.name.isdigit():
            zooms.append(int(child.name))
    for zoom in sorted(zooms, reverse=True):
        xs = sorted(int(p.name) for p in (tiles_dir / str(zoom)).iterdir() if p.is_dir() and p.name.isdigit())
        ys: set[int] = set()
        for x in xs:
            for png in (tiles_dir / str(zoom) / str(x)).glob("*.png"):
                if png.stem.isdigit():
                    ys.add(int(png.stem))
        if xs and ys:
            return zoom, xs, sorted(ys)
    return None


def _fingerprint(
    tiles_dir: Path, zoom: int, xs: list[int], ys: list[int], max_px: int
) -> str:
    digest = hashlib.sha1()
    digest.update(
        f"v{RENDER_VERSION}:{max_px}:{zoom}:{xs[0]}:{xs[-1]}:{ys[0]}:{ys[-1]}".encode()
    )
    for x in xs:
        for y in ys:
            path = tiles_dir / str(zoom) / str(x) / f"{y}.png"
            if path.is_file():
                stat = path.stat()
                digest.update(f"{x},{y},{stat.st_size},{stat.st_mtime_ns}".encode())
            else:
                digest.update(f"{x},{y},-".encode())
    return digest.hexdigest()[:16]


def _colorize(elevation: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Aplica a rampa hipsometrica; pixels sem dados ficam transparentes."""
    stops = np.array([stop for stop, _ in HYPSOMETRIC_RAMP], dtype=np.float64)
    colors = np.array([color for _, color in HYPSOMETRIC_RAMP], dtype=np.float64)
    # Normaliza a rampa na faixa coberta para dar contraste ao relevo local.
    valid = elevation[mask]
    low = float(valid.min())
    high = float(valid.max())
    if high - low < 1.0:
        low, high = float(stops[0]), float(stops[-1])
    scaled = stops / stops[-1] * (high - low) + low
    out = np.zeros((*elevation.shape, 3), dtype=np.float64)
    for channel in range(3):
        out[..., channel] = np.interp(
            elevation, scaled, colors[:, channel], left=colors[0, channel],
            right=colors[-1, channel],
        )
    return out


def _hillshade(elevation: np.ndarray, mask: np.ndarray, cell_m: float) -> np.ndarray:
    """Sombreamento simples (luz a noroeste), em 0..1."""
    filled = np.where(mask, elevation, np.nan)
    # Preenche vazios com o vizinho valido mais proximo via media local simples.
    for _ in range(3):
        padded = np.pad(filled, 1, mode="edge")
        stack = np.stack(
            [
                padded[:-2, 1:-1],
                padded[2:, 1:-1],
                padded[1:-1, :-2],
                padded[1:-1, 2:],
            ]
        )
        counts = np.isfinite(stack).sum(axis=0)
        neighbour = np.divide(
            np.nansum(stack, axis=0), counts,
            out=np.full_like(filled, np.nan), where=counts > 0,
        )
        filled = np.where(np.isnan(filled), neighbour, filled)
    filled = np.where(np.isnan(filled), 0.0, filled)

    dzdx = np.gradient(filled, axis=1) / max(cell_m, 1e-6)
    dzdy = np.gradient(filled, axis=0) / max(cell_m, 1e-6)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, dzdy)
    azimuth = math.radians(315.0)
    altitude = math.radians(45.0)
    shade = math.sin(altitude) * np.cos(slope) + math.cos(altitude) * np.sin(
        slope
    ) * np.cos(azimuth - aspect)
    return np.clip(shade, 0.0, 1.0)


def _reproject_rows(array: np.ndarray, south: float, north: float) -> np.ndarray:
    """Reamostra linhas de Mercator para latitude linear (como o Leaflet)."""
    height = array.shape[0]
    north_merc = math.log(math.tan(math.pi / 4 + math.radians(north) / 2))
    south_merc = math.log(math.tan(math.pi / 4 + math.radians(south) / 2))
    lats = np.linspace(north, south, height)
    merc = np.log(np.tan(np.pi / 4 + np.radians(lats) / 2))
    rows = (north_merc - merc) / max(north_merc - south_merc, 1e-12) * (height - 1)
    rows = np.clip(np.rint(rows).astype(int), 0, height - 1)
    return array[rows]


def _encode(image: Image.Image) -> tuple[bytes, str, str]:
    """Codifica em WebP (menor) com fallback para PNG."""
    buffer = io.BytesIO()
    try:
        image.save(buffer, format="WEBP", quality=82, method=4)
        return buffer.getvalue(), "webp", "image/webp"
    except Exception:  # pragma: no cover - Pillow sem suporte a WebP
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "png", "image/png"


def build_overlay(
    tiles_dir: str | Path,
    *,
    max_px: int = DEFAULT_MAX_PX,
    cache_dir: str | Path | None = None,
) -> AltimetryOverlay | None:
    """Monta (ou reusa do cache) a imagem hipsometrica dos tiles locais.

    Args:
        tiles_dir: raiz com ``{z}/{x}/{y}.png`` (Terrarium).
        max_px: limite de pixels do maior lado da imagem final.
        cache_dir: diretorio do cache; ``None`` desliga o cache em disco.

    Returns:
        O overlay pronto para o mapa, ou ``None`` se nao houver tiles.
    """
    tiles_dir = Path(tiles_dir)
    found = discover_tiles(tiles_dir)
    if found is None:
        return None
    zoom, xs, ys = found

    cache_root = Path(cache_dir) if cache_dir is not None else None
    fingerprint = _fingerprint(tiles_dir, zoom, xs, ys, max_px)
    if cache_root is not None:
        meta_path = cache_root / "altimetria.json"
        image_path = cache_root / f"altimetria_{fingerprint}"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = None
            if meta and meta.get("fingerprint") == fingerprint:
                for suffix, mime in (("webp", "image/webp"), ("png", "image/png")):
                    candidate = image_path.with_suffix(f".{suffix}")
                    if candidate.is_file():
                        return AltimetryOverlay(
                            png=candidate.read_bytes(),
                            suffix=suffix,
                            mime=mime,
                            bounds=tuple(meta["bounds"]),  # type: ignore[arg-type]
                            zoom=zoom,
                            tile_size_px=int(meta["tile_size_px"]),
                            tiles=int(meta["tiles"]),
                            min_m=float(meta["min_m"]),
                            max_m=float(meta["max_m"]),
                            width=int(meta["width"]),
                            height=int(meta["height"]),
                            resolution_m=float(
                                meta.get("resolution_m")
                                or 156543.03392
                                * math.cos(
                                    math.radians(
                                        (meta["bounds"][0] + meta["bounds"][2]) / 2.0
                                    )
                                )
                                / 2**zoom
                            ),
                        )

    xs = list(range(xs[0], xs[-1] + 1))
    ys = list(range(ys[0], ys[-1] + 1))
    nx, ny = len(xs), len(ys)
    tile_px = max(16, min(128, max_px // max(nx, ny)))
    mosaic = np.zeros((ny * tile_px, nx * tile_px, 3), dtype=np.uint8)
    mask = np.zeros((ny * tile_px, nx * tile_px), dtype=bool)

    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            path = tiles_dir / str(zoom) / str(x) / f"{y}.png"
            if not path.is_file():
                continue
            with Image.open(path) as tile:
                tile = tile.convert("RGB").resize((tile_px, tile_px), Image.NEAREST)
                block = np.asarray(tile, dtype=np.uint8)
            top = row * tile_px
            left = col * tile_px
            mosaic[top : top + tile_px, left : left + tile_px] = block
            mask[top : top + tile_px, left : left + tile_px] = True

    if not mask.any():
        return None

    rgb = mosaic.astype(np.float64)
    elevation = (rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0) - 32768.0
    valid = elevation[mask]
    min_m = float(valid.min())
    max_m = float(valid.max())

    # Celula em metros (aproximacao local; latitude media da area).
    south = _lat_of_tile(ys[-1] + 1, zoom)
    north = _lat_of_tile(ys[0], zoom)
    west = _lon_of_tile(xs[0], zoom)
    east = _lon_of_tile(xs[-1] + 1, zoom)
    lat_mid = math.radians((south + north) / 2.0)
    cell_m = (
        156543.03392 * math.cos(lat_mid) / 2**zoom * (256.0 / tile_px)
    )
    resolution_m = 156543.03392 * math.cos(lat_mid) / 2**zoom

    color = _colorize(elevation, mask)
    shade = _hillshade(elevation, mask, cell_m)
    lit = color * (0.55 + 0.45 * shade[..., None])
    alpha = np.where(mask, 235.0, 0.0)

    rgba = np.dstack([np.clip(lit, 0, 255), alpha]).astype(np.uint8)

    image = Image.fromarray(rgba, mode="RGBA")
    png, suffix, mime = _encode(image)

    overlay = AltimetryOverlay(
        png=png,
        suffix=suffix,
        mime=mime,
        bounds=(south, west, north, east),
        zoom=zoom,
        tile_size_px=tile_px,
        tiles=int(mask.sum() // (tile_px * tile_px)),
        min_m=min_m,
        max_m=max_m,
        width=image.width,
        height=image.height,
        resolution_m=resolution_m,
    )
    if cache_root is not None:
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            image_path.with_suffix(f".{suffix}").write_bytes(png)
            (cache_root / "altimetria.json").write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "bounds": list(overlay.bounds),
                        "tile_size_px": overlay.tile_size_px,
                        "tiles": overlay.tiles,
                        "min_m": overlay.min_m,
                        "max_m": overlay.max_m,
                        "width": overlay.width,
                        "height": overlay.height,
                        "resolution_m": overlay.resolution_m,
                        "suffix": suffix,
                        "mime": mime,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
    return overlay
