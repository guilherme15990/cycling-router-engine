"""Cliente Overpass com cache em disco.

O mapa de origem e o OpenStreetMap: baixamos apenas as vias com
``highway=*`` que podem ser percorridas de bicicleta dentro de uma bbox.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .parse import OverpassData

DEFAULT_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

DEFAULT_HIGHWAYS = (
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
    "track",
    "path",
    "cycleway",
    "footway",
    "pedestrian",
    "bridleway",
)

USER_AGENT = "roteamento-engine/0.1 (cycling graph; OSM)"


class OverpassError(RuntimeError):
    """Falha ao consultar o Overpass."""


def build_query(
    bbox: tuple[float, float, float, float],
    highways: tuple[str, ...] = DEFAULT_HIGHWAYS,
    timeout_s: int = 180,
) -> str:
    south, west, north, east = bbox
    pattern = "|".join(highways)
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        "(\n"
        f'  way["highway"~"^({pattern})$"]["area"!="yes"]'
        f"({south:.7f},{west:.7f},{north:.7f},{east:.7f});\n"
        ");\n"
        "(._;>;);\n"
        "out body;\n"
    )


class OverpassClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINTS[0],
        cache_dir: str | Path | None = None,
        timeout_s: float = 180.0,
        max_retries: int = 3,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.endpoint = endpoint
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.user_agent = user_agent

    def query(self, ql: str, use_cache: bool = True) -> dict:
        cache_path = self._cache_path(ql) if (self.cache_dir and use_cache) else None
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._request(ql)

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def fetch(
        self,
        bbox: tuple[float, float, float, float],
        highways: tuple[str, ...] = DEFAULT_HIGHWAYS,
        timeout_s: int = 180,
        use_cache: bool = True,
    ) -> OverpassData:
        ql = build_query(bbox, highways=highways, timeout_s=timeout_s)
        return OverpassData.from_payload(self.query(ql, use_cache=use_cache))

    # ------------------------------------------------------------------
    def _cache_path(self, ql: str) -> Path:
        digest = hashlib.sha1(ql.encode("utf-8")).hexdigest()
        assert self.cache_dir is not None
        return self.cache_dir / "overpass" / f"{digest}.json"

    def _request(self, ql: str) -> dict:
        body = urllib.parse.urlencode({"data": ql}).encode("utf-8")
        delay = 2.0
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                self.endpoint,
                data=body,
                headers={
                    "User-Agent": self.user_agent,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise OverpassError(f"Overpass HTTP {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise OverpassError(f"falha ao consultar Overpass: {exc}") from exc

        raise OverpassError(f"falha ao consultar Overpass: {last_error}")
