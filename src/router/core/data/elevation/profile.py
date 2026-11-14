"""Perfil de elevacao de uma geometria (ganho/perda acumulados)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from router.core.data.elevation.providers import ElevationProvider
from router.core.data.geo import Coord, sample_along
from router.core.data.graph.model import ElevationQuality, ElevationSample

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
