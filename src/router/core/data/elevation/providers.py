"""Provedores de altitude (rede e locais) do ADR-031 (D4)."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from router.core.data.geo import Coord
from router.core.data.graph.model import ElevationQuality

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




def _round_coord(coord: Coord, digits: int = 5) -> tuple[int, int]:
    scale = 10**digits
    return (int(round(coord[0] * scale)), int(round(coord[1] * scale)))
