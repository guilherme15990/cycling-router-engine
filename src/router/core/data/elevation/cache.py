"""Cache em disco/memoria de altitudes (ADR-031, core.data.elevation.cache)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from router.core.data.elevation.providers import ElevationProvider, _round_coord
from router.core.data.geo import Coord
from router.core.data.graph.model import ElevationQuality

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
