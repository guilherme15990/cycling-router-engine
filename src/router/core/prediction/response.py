"""Resposta fisiológica provisória (RPE e FC) a partir da intensidade mecânica.

A resposta é uma camada **separada** e explicitamente provisória: não preenche
``PhysiologicalPrediction`` e não é uma calibração individual. Ela existe para
dar ao produto e ao ML um número utilizável e auditável enquanto não há modelo
calibrado com observações reais.

Modelos:

* **RPE** — interpolação monótona de âncoras em ``P / P_ref`` (Borg 6–20).
  A fadiga só entra quando há CP e W' medidos (modelo CP/W'); sem eles, não há
  termo de fadiga arbitrário.
* **FC** — resposta de primeira ordem (constante de tempo declarada). A FC
  absoluta em bpm só existe quando **FC máxima e FC de repouso** existem. A
  FC máxima por Tanaka é uma equação populacional, não uma medida individual.
* **Zonas** — quando há FC máxima, a distribuição por ``%HRmax`` é sempre
  calculada. Com FC de repouso, a distribuição de Karvonen (``%HRR``) também é
  exposta como camada adicional.

A inclinação e o tráfego não entram nesta camada: a primeira já está na
potência física; o segundo é um eixo de exposição separado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    BorgScale,
    HeartRateZoneModel,
    RiderModel,
)
from router.core.physiology.zones import validate_distribution

from ._validation import finite, non_negative, positive, unit_interval
from .mechanical import MechanicalIntensity


class FatigueModel(str, Enum):
    """Modelo de fadiga usado na resposta provisória."""

    #: Sem modelo de fadiga (default quando não há CP/W' medidos).
    NONE = "none"
    #: Saldo de trabalho acima da potência crítica (CP/W').
    CP_W_PRIME = "cp_w_prime"


@dataclass(frozen=True, slots=True)
class ResponseConfig:
    """Parâmetros explícitos da resposta provisória.

    As âncoras de RPE são uma escolha de engenharia documentada, não uma
    calibração. A constante de tempo da FC e as frações de HRR/HRmax são
    parâmetros de literatura/engenharia; todas são declaradas aqui.
    """

    rpe_anchors: tuple[tuple[float, float], ...] = (
        (0.0, 6.0),
        (0.5, 11.0),
        (0.7, 13.0),
        (0.85, 15.0),
        (1.0, 17.0),
        (1.2, 19.0),
        (1.5, 20.0),
    )
    fatigue_rpe_gain: float = 1.0
    cp_w_recovery_factor: float = 0.5
    rpe_peak_window_s: float = 60.0
    hr_time_constant_s: float = 45.0
    hr_peak_window_s: float = 60.0
    hr_threshold_fraction_of_reserve: float = 0.85
    hr_above_gain: float = 2.0
    hr_rest_fraction_of_max: float = 0.55
    hr_threshold_fraction_of_max: float = 0.95

    def __post_init__(self) -> None:
        anchors = tuple(self.rpe_anchors)
        if len(anchors) < 2:
            raise ValueError("rpe_anchors exige ao menos duas ancoras")
        previous_intensity = -math.inf
        for index, (intensity, rpe) in enumerate(anchors):
            finite(f"rpe_anchors[{index}].intensity", intensity)
            finite(f"rpe_anchors[{index}].rpe", rpe)
            if intensity <= previous_intensity:
                raise ValueError(
                    "rpe_anchors deve ter intensidades estritamente crescentes"
                )
            previous_intensity = intensity
        object.__setattr__(self, "rpe_anchors", anchors)

        non_negative("fatigue_rpe_gain", self.fatigue_rpe_gain)
        unit_interval("cp_w_recovery_factor", self.cp_w_recovery_factor)
        positive("rpe_peak_window_s", self.rpe_peak_window_s)
        positive("hr_time_constant_s", self.hr_time_constant_s)
        positive("hr_peak_window_s", self.hr_peak_window_s)
        unit_interval(
            "hr_threshold_fraction_of_reserve",
            self.hr_threshold_fraction_of_reserve,
        )
        non_negative("hr_above_gain", self.hr_above_gain)
        unit_interval(
            "hr_rest_fraction_of_max", self.hr_rest_fraction_of_max
        )
        unit_interval(
            "hr_threshold_fraction_of_max",
            self.hr_threshold_fraction_of_max,
        )
        if self.hr_rest_fraction_of_max >= self.hr_threshold_fraction_of_max:
            raise ValueError(
                "hr_rest_fraction_of_max deve ser menor que "
                "hr_threshold_fraction_of_max"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "rpe_anchors": [
                [round(intensity, 4), round(rpe, 3)]
                for intensity, rpe in self.rpe_anchors
            ],
            "fatigue_rpe_gain": self.fatigue_rpe_gain,
            "cp_w_recovery_factor": self.cp_w_recovery_factor,
            "rpe_peak_window_s": self.rpe_peak_window_s,
            "hr_time_constant_s": self.hr_time_constant_s,
            "hr_peak_window_s": self.hr_peak_window_s,
            "hr_threshold_fraction_of_reserve": (
                self.hr_threshold_fraction_of_reserve
            ),
            "hr_above_gain": self.hr_above_gain,
            "hr_rest_fraction_of_max": self.hr_rest_fraction_of_max,
            "hr_threshold_fraction_of_max": self.hr_threshold_fraction_of_max,
        }


DEFAULT_RESPONSE_CONFIG = ResponseConfig()

#: Zonas por percentual de FC máxima (bordas explícitas).
_HRMAX_ZONE_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("z1", 0.50, 0.60),
    ("z2", 0.60, 0.70),
    ("z3", 0.70, 0.80),
    ("z4", 0.80, 0.90),
    ("z5", 0.90, 1.00),
)

#: Zonas de Karvonen por percentual da reserva de FC.
_HRR_ZONE_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("z1", 0.50, 0.60),
    ("z2", 0.60, 0.70),
    ("z3", 0.70, 0.80),
    ("z4", 0.80, 0.90),
    ("z5", 0.90, 1.00),
)


@dataclass(frozen=True, slots=True)
class RiderResponseEstimate:
    """Resposta provisória de RPE e FC para uma rota.

    ``heart_rate_max_bpm`` é a **FC máxima fisiológica** (medida ou Tanaka).
    ``peak_heart_rate_bpm`` é o **pico previsto da rota** — conceitos
    diferentes, com nomes diferentes.
    """

    borg_scale: BorgScale
    mean_rpe: float
    mean_rpe_time_weighted: float
    mean_rpe_distance_weighted: float
    peak_rpe: float
    segment_rpes: tuple[float, ...]
    segment_relative_intensity: tuple[float, ...]
    segment_heart_rate_bpm: tuple[float | None, ...]
    segment_fraction_of_max_hr: tuple[float | None, ...]
    mean_heart_rate_bpm: float | None
    peak_heart_rate_bpm: float | None
    heart_rate_zone_distribution: dict[str, float] | None
    heart_rate_zone_definition: str | None
    heart_rate_zone_distribution_karvonen: dict[str, float] | None = None
    heart_rate_zone_definition_karvonen: str | None = None
    heart_rate_max_bpm: float | None = None
    heart_rate_rest_bpm: float | None = None
    heart_rate_reserve_bpm: float | None = None
    fatigue_model: FatigueModel = FatigueModel.NONE
    fatigue_fraction_end: float = 0.0
    config: ResponseConfig = field(
        default_factory=lambda: DEFAULT_RESPONSE_CONFIG
    )

    def __post_init__(self) -> None:
        if not isinstance(self.borg_scale, BorgScale):
            raise ValueError("borg_scale deve ser um BorgScale")
        for name in (
            "mean_rpe",
            "mean_rpe_time_weighted",
            "mean_rpe_distance_weighted",
            "peak_rpe",
        ):
            self.borg_scale.require_valid(getattr(self, name))
        for rpe in self.segment_rpes:
            self.borg_scale.require_valid(rpe)

        size = len(self.segment_rpes)
        for name in (
            "segment_relative_intensity",
            "segment_heart_rate_bpm",
            "segment_fraction_of_max_hr",
        ):
            if len(getattr(self, name)) != size:
                raise ValueError(f"{name} deve ter o mesmo tamanho dos segmentos")

        for name in ("heart_rate_max_bpm", "heart_rate_rest_bpm"):
            value = getattr(self, name)
            if value is not None:
                positive(name, value)
        if self.mean_heart_rate_bpm is not None:
            positive("mean_heart_rate_bpm", self.mean_heart_rate_bpm)
        if self.peak_heart_rate_bpm is not None:
            positive("peak_heart_rate_bpm", self.peak_heart_rate_bpm)
        if self.heart_rate_reserve_bpm is not None:
            positive("heart_rate_reserve_bpm", self.heart_rate_reserve_bpm)

        if self.heart_rate_zone_distribution is not None:
            if not isinstance(self.heart_rate_zone_definition, str) or not (
                self.heart_rate_zone_definition
            ):
                raise ValueError(
                    "heart_rate_zone_distribution exige definicao explicita"
                )
            object.__setattr__(
                self,
                "heart_rate_zone_distribution",
                validate_distribution(
                    "heart_rate_zone_distribution",
                    self.heart_rate_zone_distribution,
                ),
            )
        if self.heart_rate_zone_distribution_karvonen is not None:
            if not isinstance(self.heart_rate_zone_definition_karvonen, str) or not (
                self.heart_rate_zone_definition_karvonen
            ):
                raise ValueError(
                    "heart_rate_zone_distribution_karvonen exige definicao"
                )
            object.__setattr__(
                self,
                "heart_rate_zone_distribution_karvonen",
                validate_distribution(
                    "heart_rate_zone_distribution_karvonen",
                    self.heart_rate_zone_distribution_karvonen,
                ),
            )
        unit_interval("fatigue_fraction_end", self.fatigue_fraction_end)
        if not isinstance(self.fatigue_model, FatigueModel):
            raise ValueError("fatigue_model deve ser um FatigueModel")
        if not isinstance(self.config, ResponseConfig):
            raise ValueError("config deve ser um ResponseConfig")

    # Aliases explicitos para a distincao entre FC maxima e pico da rota.
    @property
    def physiological_max_heart_rate_bpm(self) -> float | None:
        return self.heart_rate_max_bpm

    @property
    def resting_heart_rate_bpm(self) -> float | None:
        return self.heart_rate_rest_bpm

    @property
    def estimated_mean_heart_rate_bpm(self) -> float | None:
        return self.mean_heart_rate_bpm

    @property
    def estimated_peak_heart_rate_bpm(self) -> float | None:
        return self.peak_heart_rate_bpm

    @property
    def estimated_heart_rate_bpm(self) -> tuple[float | None, ...]:
        return self.segment_heart_rate_bpm

    def to_dict(self) -> dict[str, object]:
        return {
            "borg_scale": self.borg_scale.value,
            "mean_rpe": round(self.mean_rpe, 2),
            "mean_rpe_time_weighted": round(self.mean_rpe_time_weighted, 2),
            "mean_rpe_distance_weighted": round(
                self.mean_rpe_distance_weighted, 2
            ),
            "peak_rpe": round(self.peak_rpe, 2),
            "segment_rpes": [round(value, 2) for value in self.segment_rpes],
            "segment_relative_intensity": [
                round(value, 4) for value in self.segment_relative_intensity
            ],
            "segment_heart_rate_bpm": [
                None if value is None else round(value, 2)
                for value in self.segment_heart_rate_bpm
            ],
            "segment_fraction_of_max_hr": [
                None if value is None else round(value, 4)
                for value in self.segment_fraction_of_max_hr
            ],
            "mean_heart_rate_bpm": (
                None
                if self.mean_heart_rate_bpm is None
                else round(self.mean_heart_rate_bpm, 2)
            ),
            "peak_heart_rate_bpm": (
                None
                if self.peak_heart_rate_bpm is None
                else round(self.peak_heart_rate_bpm, 2)
            ),
            "heart_rate_max_bpm": (
                None
                if self.heart_rate_max_bpm is None
                else round(self.heart_rate_max_bpm, 2)
            ),
            "heart_rate_rest_bpm": (
                None
                if self.heart_rate_rest_bpm is None
                else round(self.heart_rate_rest_bpm, 2)
            ),
            "heart_rate_reserve_bpm": (
                None
                if self.heart_rate_reserve_bpm is None
                else round(self.heart_rate_reserve_bpm, 2)
            ),
            "heart_rate_zone_distribution": (
                None
                if self.heart_rate_zone_distribution is None
                else {
                    name: round(value, 4)
                    for name, value in self.heart_rate_zone_distribution.items()
                }
            ),
            "heart_rate_zone_definition": self.heart_rate_zone_definition,
            "heart_rate_zone_distribution_karvonen": (
                None
                if self.heart_rate_zone_distribution_karvonen is None
                else {
                    name: round(value, 4)
                    for name, value in self.heart_rate_zone_distribution_karvonen.items()
                }
            ),
            "heart_rate_zone_definition_karvonen": (
                self.heart_rate_zone_definition_karvonen
            ),
            "fatigue_model": self.fatigue_model.value,
            "fatigue_fraction_end": round(self.fatigue_fraction_end, 4),
            "config": self.config.to_dict(),
        }

    def to_feature_row(self) -> dict[str, float | None]:
        """Features estáveis para ML (sem substituir o artefato principal)."""
        row: dict[str, float | None] = {
            "rpe_mean": self.mean_rpe_time_weighted,
            "rpe_peak": self.peak_rpe,
            "estimated_hr_mean_bpm": self.mean_heart_rate_bpm,
            "estimated_hr_peak_bpm": self.peak_heart_rate_bpm,
            "heart_rate_max_bpm": self.heart_rate_max_bpm,
            "fatigue_fraction_end": self.fatigue_fraction_end,
        }
        if self.heart_rate_zone_distribution is not None:
            for zone, value in self.heart_rate_zone_distribution.items():
                row[f"hr_zone_{zone}_fraction"] = value
        return row


def _rpe_for_intensity(
    relative_intensity: float,
    *,
    borg_scale: BorgScale,
    config: ResponseConfig,
) -> float:
    anchors = config.rpe_anchors
    if relative_intensity <= anchors[0][0]:
        borg = anchors[0][1]
    elif relative_intensity >= anchors[-1][0]:
        borg = anchors[-1][1]
    else:
        borg = anchors[-1][1]
        for (low_i, low_rpe), (high_i, high_rpe) in zip(anchors, anchors[1:]):
            if low_i <= relative_intensity <= high_i:
                fraction = (relative_intensity - low_i) / (high_i - low_i)
                borg = low_rpe + fraction * (high_rpe - low_rpe)
                break
    scale_min = borg_scale.min_score
    scale_max = borg_scale.max_score
    mapped = scale_min + (borg - 6.0) / 14.0 * (scale_max - scale_min)
    return borg_scale.clamp(mapped)


def _hr_reserve_fraction(
    relative_intensity: float, config: ResponseConfig
) -> float:
    if relative_intensity <= 0.0:
        return 0.0
    threshold = config.hr_threshold_fraction_of_reserve
    if relative_intensity <= 1.0:
        return min(1.0, threshold * relative_intensity)
    return min(
        0.98,
        threshold + (1.0 - threshold)
        * (1.0 - math.exp(-config.hr_above_gain * (relative_intensity - 1.0))),
    )


def _hr_max_fraction(
    relative_intensity: float, config: ResponseConfig
) -> float:
    rest = config.hr_rest_fraction_of_max
    threshold = config.hr_threshold_fraction_of_max
    if relative_intensity <= 0.0:
        return rest
    if relative_intensity <= 1.0:
        return rest + (threshold - rest) * relative_intensity
    return min(
        0.99,
        threshold + (1.0 - threshold)
        * (1.0 - math.exp(-config.hr_above_gain * (relative_intensity - 1.0))),
    )


def _time_weighted_mean(
    values: tuple[float, ...], durations: tuple[float, ...]
) -> float:
    total = sum(durations)
    if total <= 0.0:
        return sum(values) / len(values) if values else 0.0
    return sum(value * duration for value, duration in zip(values, durations)) / total


def _distance_weighted_mean(
    values: tuple[float, ...], distances: tuple[float, ...]
) -> float:
    total = sum(distances)
    if total <= 0.0:
        return sum(values) / len(values) if values else 0.0
    return sum(
        value * distance for value, distance in zip(values, distances)
    ) / total


def _window_mean(
    values: tuple[float, ...],
    durations: tuple[float, ...],
    start_s: float,
    end_s: float,
) -> float | None:
    length = end_s - start_s
    if length <= 0.0:
        return None
    total = 0.0
    elapsed = 0.0
    for value, duration in zip(values, durations):
        segment_start = elapsed
        segment_end = elapsed + duration
        overlap = max(0.0, min(end_s, segment_end) - max(start_s, segment_start))
        if overlap > 0.0:
            total += value * overlap
        elapsed = segment_end
    return total / length


def _rolling_peak(
    values: tuple[float, ...],
    durations: tuple[float, ...],
    window_s: float,
) -> float | None:
    """Pico de uma média móvel de janela fixa, robusto à segmentação."""
    if not values:
        return None
    total_duration = sum(durations)
    if total_duration <= 0.0:
        return max(values)
    if total_duration <= window_s:
        return _window_mean(values, durations, 0.0, total_duration)

    starts = {0.0}
    elapsed = 0.0
    for duration in durations:
        starts.add(elapsed)
        starts.add(max(0.0, elapsed - window_s))
        elapsed += duration
    starts.add(max(0.0, total_duration - window_s))

    peak: float | None = None
    for start in starts:
        end = min(total_duration, start + window_s)
        value = _window_mean(values, durations, start, end)
        if value is not None and (peak is None or value > peak):
            peak = value
    return peak


def _rolling_peak_optional(
    values: tuple[float | None, ...],
    durations: tuple[float, ...],
    window_s: float,
) -> float | None:
    pairs = [
        (value, duration)
        for value, duration in zip(values, durations)
        if value is not None and duration > 0.0
    ]
    if not pairs:
        return None
    filtered_values = tuple(value for value, _ in pairs)
    filtered_durations = tuple(duration for _, duration in pairs)
    return _rolling_peak(filtered_values, filtered_durations, window_s)


def _zone_distribution(
    values: tuple[float | None, ...],
    durations: tuple[float, ...],
    model: HeartRateZoneModel,
) -> dict[str, float] | None:
    if not values:
        return None
    total_duration = sum(durations)
    if total_duration <= 0.0:
        return None
    by_zone: dict[str, float] = {name: 0.0 for name in model.zone_names}
    zones = model.zones
    for value, duration in zip(values, durations):
        if value is None or duration <= 0.0:
            continue
        if value <= zones[0].lower_bpm:
            by_zone[zones[0].name] += duration
            continue
        if value >= zones[-1].upper_bpm:
            by_zone[zones[-1].name] += duration
            continue
        for zone in zones:
            if zone.lower_bpm <= value < zone.upper_bpm:
                by_zone[zone.name] += duration
                break
    return model.distribution(by_zone)


def estimate_rider_response(
    mechanical: MechanicalIntensity,
    *,
    rider: RiderModel,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    config: ResponseConfig = DEFAULT_RESPONSE_CONFIG,
) -> RiderResponseEstimate:
    """Estima RPE, FC e zonas provisórias a partir da intensidade mecânica."""
    if not isinstance(mechanical, MechanicalIntensity):
        raise ValueError("mechanical deve ser um MechanicalIntensity")
    if not isinstance(rider, RiderModel):
        raise ValueError("rider deve ser um RiderModel")

    relative = mechanical.segment_relative_intensity
    durations = mechanical.segment_duration_s
    distances = mechanical.segment_distance_m
    segment_count = len(relative)

    use_cp = rider.has_critical_power_model
    fatigue_model = FatigueModel.CP_W_PRIME if use_cp else FatigueModel.NONE
    w_balance = rider.w_prime_j if use_cp else None
    segment_rpes: list[float] = []
    for index in range(segment_count):
        base_rpe = _rpe_for_intensity(
            relative[index], borg_scale=borg_scale, config=config
        )
        fatigue_fraction = 0.0
        if use_cp:
            assert w_balance is not None
            assert rider.critical_power_w is not None
            assert rider.w_prime_j is not None
            start_fraction = 1.0 - w_balance / rider.w_prime_j
            power = mechanical.segment_power_w[index]
            duration = durations[index]
            if power > rider.critical_power_w:
                w_balance -= (power - rider.critical_power_w) * duration
            else:
                w_balance = min(
                    rider.w_prime_j,
                    w_balance
                    + config.cp_w_recovery_factor
                    * (rider.critical_power_w - power)
                    * duration,
                )
            w_balance = max(0.0, min(rider.w_prime_j, w_balance))
            end_fraction = 1.0 - w_balance / rider.w_prime_j
            fatigue_fraction = max(0.0, 0.5 * (start_fraction + end_fraction))
            base_rpe += config.fatigue_rpe_gain * fatigue_fraction
        segment_rpes.append(borg_scale.clamp(base_rpe))

    rpe_values = tuple(segment_rpes)
    if rpe_values:
        mean_rpe_time = _time_weighted_mean(rpe_values, durations)
        mean_rpe_distance = _distance_weighted_mean(rpe_values, distances)
    else:
        mean_rpe_time = borg_scale.min_score
        mean_rpe_distance = borg_scale.min_score
    peak_rpe = _rolling_peak(rpe_values, durations, config.rpe_peak_window_s)
    if peak_rpe is None:
        peak_rpe = borg_scale.min_score
    peak_rpe = max(borg_scale.clamp(peak_rpe), mean_rpe_time)

    heart_rate = rider.heart_rate
    max_bpm = heart_rate.max_heart_rate_bpm
    rest_bpm = heart_rate.resting_heart_rate_bpm
    reserve_bpm = heart_rate.heart_rate_reserve_bpm
    has_max = max_bpm is not None
    has_absolute = max_bpm is not None and rest_bpm is not None

    segment_hr_bpm: list[float | None] = []
    segment_fraction_max: list[float | None] = []
    segment_fraction_reserve: list[float | None] = []
    previous_bpm = rest_bpm if has_absolute else None
    previous_fraction_max = (
        config.hr_rest_fraction_of_max if has_max else None
    )
    previous_fraction_reserve = 0.0 if reserve_bpm is not None else None

    for index in range(segment_count):
        intensity = relative[index]
        target_reserve = (
            _hr_reserve_fraction(intensity, config)
            if reserve_bpm is not None
            else None
        )
        target_max = (
            _hr_max_fraction(intensity, config) if has_max else None
        )
        duration = durations[index]
        decay = (
            math.exp(-duration / config.hr_time_constant_s)
            if duration > 0.0
            else 1.0
        )

        if (
            has_absolute
            and rest_bpm is not None
            and reserve_bpm is not None
            and max_bpm is not None
            and target_reserve is not None
        ):
            target_bpm = rest_bpm + reserve_bpm * target_reserve
            current_bpm = (
                target_bpm + (previous_bpm - target_bpm) * decay
                if previous_bpm is not None
                else target_bpm
            )
            segment_hr_bpm.append(current_bpm)
            segment_fraction_max.append(current_bpm / max_bpm)
            segment_fraction_reserve.append(
                (current_bpm - rest_bpm) / reserve_bpm
            )
            previous_bpm = current_bpm
            previous_fraction_max = current_bpm / max_bpm
            previous_fraction_reserve = (current_bpm - rest_bpm) / reserve_bpm
        elif has_max and max_bpm is not None and target_max is not None:
            current_fraction = (
                target_max + (previous_fraction_max - target_max) * decay
                if previous_fraction_max is not None
                else target_max
            )
            segment_hr_bpm.append(None)
            segment_fraction_max.append(current_fraction)
            segment_fraction_reserve.append(None)
            previous_fraction_max = current_fraction
        else:
            segment_hr_bpm.append(None)
            segment_fraction_max.append(None)
            segment_fraction_reserve.append(None)

    hr_values = tuple(segment_hr_bpm)
    peak_hr = _rolling_peak_optional(hr_values, durations, config.hr_peak_window_s)
    hr_duration = sum(
        duration
        for value, duration in zip(hr_values, durations)
        if value is not None and duration > 0.0
    )
    mean_hr = (
        sum(
            value * duration
            for value, duration in zip(hr_values, durations)
            if value is not None and duration > 0.0
        )
        / hr_duration
        if hr_duration > 0.0
        else None
    )
    if peak_hr is not None and mean_hr is not None:
        # O pico da rota nunca pode ficar abaixo da media observada.
        peak_hr = max(peak_hr, mean_hr)

    zone_distribution = None
    zone_definition = None
    karvonen_distribution = None
    karvonen_definition = None
    if has_max and max_bpm is not None:
        hrmax_model = HeartRateZoneModel.from_bounds(
            "hr-zones-hrmax",
            "1",
            tuple(
                (
                    name,
                    low * max_bpm,
                    high * max_bpm,
                )
                for name, low, high in _HRMAX_ZONE_BOUNDS
            ),
        )
        hrmax_values = tuple(
            None if value is None else value * max_bpm
            for value in segment_fraction_max
        )
        zone_distribution = _zone_distribution(
            hrmax_values, durations, hrmax_model
        )
        zone_definition = hrmax_model.identifier

    if (
        reserve_bpm is not None
        and rest_bpm is not None
        and any(value is not None for value in segment_fraction_reserve)
    ):
        hrr_model = HeartRateZoneModel.from_bounds(
            "hr-zones-karvonen",
            "1",
            tuple(
                (
                    name,
                    rest_bpm + low * reserve_bpm,
                    rest_bpm + high * reserve_bpm,
                )
                for name, low, high in _HRR_ZONE_BOUNDS
            ),
        )
        hrr_values = tuple(
            None
            if value is None
            else rest_bpm + value * reserve_bpm
            for value in segment_fraction_reserve
        )
        karvonen_distribution = _zone_distribution(
            hrr_values, durations, hrr_model
        )
        karvonen_definition = hrr_model.identifier

    fatigue_fraction_end = 0.0
    if use_cp and rider.w_prime_j is not None and w_balance is not None:
        fatigue_fraction_end = max(
            0.0, min(1.0, 1.0 - w_balance / rider.w_prime_j)
        )

    return RiderResponseEstimate(
        borg_scale=borg_scale,
        mean_rpe=mean_rpe_time,
        mean_rpe_time_weighted=mean_rpe_time,
        mean_rpe_distance_weighted=mean_rpe_distance,
        peak_rpe=peak_rpe,
        segment_rpes=rpe_values,
        segment_relative_intensity=relative,
        segment_heart_rate_bpm=tuple(segment_hr_bpm),
        segment_fraction_of_max_hr=tuple(segment_fraction_max),
        mean_heart_rate_bpm=mean_hr,
        peak_heart_rate_bpm=peak_hr,
        heart_rate_zone_distribution=zone_distribution,
        heart_rate_zone_definition=zone_definition,
        heart_rate_zone_distribution_karvonen=karvonen_distribution,
        heart_rate_zone_definition_karvonen=karvonen_definition,
        heart_rate_max_bpm=max_bpm,
        heart_rate_rest_bpm=rest_bpm,
        heart_rate_reserve_bpm=reserve_bpm,
        fatigue_model=fatigue_model,
        fatigue_fraction_end=fatigue_fraction_end,
        config=config,
    )


__all__ = [
    "FatigueModel",
    "ResponseConfig",
    "DEFAULT_RESPONSE_CONFIG",
    "RiderResponseEstimate",
    "estimate_rider_response",
]
