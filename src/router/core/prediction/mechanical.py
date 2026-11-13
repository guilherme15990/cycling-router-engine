"""Intensidade mecânica relativa — nível 1 da separação do ADR-034.

O artefato primário é ``I_mech = P / P_ref``, com ``P_ref`` declarado e sem
massa de referência artificial. A inclinação **não** entra como termo aditivo:
a gravidade já está no trabalho de pedivela calculado pela física. O tráfego
também não entra: ele é um eixo separado (``traffic.py``).

A potência usada aqui é a potência no **pedivela** do ciclista. A assistência
de motor, quando modelada, é explicitada por ``PropulsionModel`` e não é
confundida com esforço do ciclista.

``rms_power_w`` é a média quadrática da potência por segmento. Ela é um
descritor matemático e **não** é Normalized Power (Coggan).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from router.core.physiology import PowerReference, RiderModel

from ._validation import non_negative, positive, unit_interval


@dataclass(frozen=True, slots=True)
class RouteSegment:
    """Trecho de rota com grandezas físicas explícitas.

    ``rider_energy_j`` é o trabalho no **pedivela**; ``mechanical_energy_j`` é
    o trabalho na **roda**. ``ascent_m``/``descent_m`` são descritores físicos;
    a inclinação não é usada para penalizar a intensidade. ``traffic_stress`` é
    um descritor separado e nunca entra em ``I_mech``.
    """

    distance_m: float
    duration_s: float
    rider_energy_j: float
    mechanical_energy_j: float
    ascent_m: float = 0.0
    descent_m: float = 0.0
    traffic_stress: float = 0.0
    peak_grade_pct: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "distance_m",
            "duration_s",
            "rider_energy_j",
            "mechanical_energy_j",
            "ascent_m",
            "descent_m",
            "traffic_stress",
            "peak_grade_pct",
        ):
            non_negative(name, getattr(self, name))

    @property
    def mean_rider_power_w(self) -> float:
        if self.duration_s <= 0.0:
            return 0.0
        return self.rider_energy_j / self.duration_s


#: Nome histórico do trecho; o modelo novo usa ``RouteSegment``.
IntensitySegment = RouteSegment


class PropulsionMode(str, Enum):
    """Como a assistência de motor (se houver) é modelada."""

    #: Bicicleta convencional: todo o trabalho útil vem do ciclista.
    RIDER_ONLY = "rider_only"
    #: O motor cobre uma fração declarada do trabalho na roda.
    ASSIST_FRACTION = "assist_fraction"
    #: O motor complementa até uma potência-alvo no pedivela do ciclista.
    ASSIST_TARGET_RIDER_POWER = "assist_target_rider_power"


@dataclass(frozen=True, slots=True)
class PropulsionSplit:
    """Separação do trabalho entre ciclista e motor."""

    rider_crank_work_j: float
    rider_wheel_work_j: float
    motor_wheel_work_j: float
    wheel_work_j: float

    def __post_init__(self) -> None:
        for name in (
            "rider_crank_work_j",
            "rider_wheel_work_j",
            "motor_wheel_work_j",
            "wheel_work_j",
        ):
            non_negative(name, getattr(self, name))
        if (
            self.rider_wheel_work_j + self.motor_wheel_work_j
            > self.wheel_work_j + 1e-6
        ):
            raise ValueError(
                "trabalho da roda deve ser a soma de ciclista e motor"
            )

    @property
    def motor_fraction_of_wheel_energy(self) -> float | None:
        if self.wheel_work_j <= 0.0:
            return None
        return self.motor_wheel_work_j / self.wheel_work_j

    def to_dict(self) -> dict[str, object]:
        return {
            "rider_crank_work_j": round(self.rider_crank_work_j, 3),
            "rider_wheel_work_j": round(self.rider_wheel_work_j, 3),
            "motor_wheel_work_j": round(self.motor_wheel_work_j, 3),
            "wheel_work_j": round(self.wheel_work_j, 3),
            "motor_fraction_of_wheel_energy": (
                None
                if self.motor_fraction_of_wheel_energy is None
                else round(self.motor_fraction_of_wheel_energy, 4)
            ),
        }


@dataclass(frozen=True, slots=True)
class PropulsionModel:
    """Modelo de propulsão; por padrão **sem assistência**.

    A assistência é opcional e explícita. O motor atua na roda; o trabalho do
    ciclista é convertido de roda para pedivela pela mesma razão de eficiência
    observada no trecho. Não há assistência implícita de e-bike.
    """

    mode: PropulsionMode = PropulsionMode.RIDER_ONLY
    assist_fraction: float = 0.0
    target_rider_power_w: float | None = None
    max_motor_power_w: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PropulsionMode):
            raise ValueError("mode deve ser um PropulsionMode")
        unit_interval("assist_fraction", self.assist_fraction)
        if self.target_rider_power_w is not None:
            positive("target_rider_power_w", self.target_rider_power_w)
        if self.max_motor_power_w is not None:
            positive("max_motor_power_w", self.max_motor_power_w)
        if self.mode is PropulsionMode.ASSIST_FRACTION and self.assist_fraction <= 0.0:
            raise ValueError(
                "ASSIST_FRACTION exige assist_fraction positiva"
            )
        if self.mode is PropulsionMode.ASSIST_TARGET_RIDER_POWER:
            if self.target_rider_power_w is None:
                raise ValueError(
                    "ASSIST_TARGET_RIDER_POWER exige target_rider_power_w"
                )
            if self.max_motor_power_w is None:
                raise ValueError(
                    "ASSIST_TARGET_RIDER_POWER exige max_motor_power_w"
                )

    def split(
        self,
        *,
        rider_energy_j: float,
        mechanical_energy_j: float,
        duration_s: float,
    ) -> PropulsionSplit:
        """Separa o trabalho da roda em ciclista e motor.

        A razão ``trabalho_no_pedivela / trabalho_na_roda`` do próprio trecho é
        usada para converter o trabalho restante do ciclista de volta ao
        pedivela. Sem assistência, o resultado é exatamente o trabalho físico
        já calculado.
        """
        rider_energy = non_negative("rider_energy_j", rider_energy_j)
        wheel = non_negative("mechanical_energy_j", mechanical_energy_j)
        duration = non_negative("duration_s", duration_s)

        crank_per_wheel = rider_energy / wheel if wheel > 0.0 else 1.0
        max_motor = wheel
        if self.max_motor_power_w is not None and duration > 0.0:
            max_motor = min(wheel, self.max_motor_power_w * duration)

        motor = 0.0
        if self.mode is PropulsionMode.ASSIST_FRACTION:
            motor = min(max_motor, self.assist_fraction * wheel)
        elif self.mode is PropulsionMode.ASSIST_TARGET_RIDER_POWER:
            assert self.target_rider_power_w is not None
            if duration > 0.0:
                rider_crank_target = self.target_rider_power_w * duration
                rider_wheel_target = (
                    rider_crank_target / crank_per_wheel
                    if crank_per_wheel > 0.0
                    else rider_crank_target
                )
                motor = min(max_motor, max(0.0, wheel - rider_wheel_target))

        rider_wheel = max(0.0, wheel - motor)
        rider_crank = (
            rider_wheel * crank_per_wheel if wheel > 0.0 else rider_energy
        )
        return PropulsionSplit(
            rider_crank_work_j=rider_crank,
            rider_wheel_work_j=rider_wheel,
            motor_wheel_work_j=motor,
            wheel_work_j=wheel,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "assist_fraction": round(self.assist_fraction, 4),
            "target_rider_power_w": self.target_rider_power_w,
            "max_motor_power_w": self.max_motor_power_w,
        }


DEFAULT_PROPULSION = PropulsionModel()


@dataclass(frozen=True, slots=True)
class RoutePhysics:
    """Grandezas físicas agregadas da rota (sem fisiologia)."""

    distance_m: float
    duration_s: float
    ascent_m: float
    descent_m: float
    wheel_work_j: float
    rider_wheel_work_j: float
    rider_crank_work_j: float
    motor_wheel_work_j: float

    def __post_init__(self) -> None:
        for name in (
            "distance_m",
            "duration_s",
            "ascent_m",
            "descent_m",
            "wheel_work_j",
            "rider_wheel_work_j",
            "rider_crank_work_j",
            "motor_wheel_work_j",
        ):
            non_negative(name, getattr(self, name))

    @property
    def average_speed_mps(self) -> float:
        if self.duration_s <= 0.0:
            return 0.0
        return self.distance_m / self.duration_s

    @property
    def average_speed_kmh(self) -> float:
        return 3.6 * self.average_speed_mps

    @property
    def mean_grade_percent(self) -> float:
        if self.distance_m <= 0.0:
            return 0.0
        return 100.0 * self.ascent_m / self.distance_m

    @property
    def mean_rider_power_w(self) -> float:
        if self.duration_s <= 0.0:
            return 0.0
        return self.rider_crank_work_j / self.duration_s

    @property
    def mean_wheel_power_w(self) -> float:
        if self.duration_s <= 0.0:
            return 0.0
        return self.wheel_work_j / self.duration_s

    def to_dict(self) -> dict[str, object]:
        return {
            "distance_m": round(self.distance_m, 3),
            "duration_s": round(self.duration_s, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "wheel_work_j": round(self.wheel_work_j, 3),
            "rider_wheel_work_j": round(self.rider_wheel_work_j, 3),
            "rider_crank_work_j": round(self.rider_crank_work_j, 3),
            "motor_wheel_work_j": round(self.motor_wheel_work_j, 3),
            "average_speed_mps": round(self.average_speed_mps, 3),
            "average_speed_kmh": round(self.average_speed_kmh, 3),
            "mean_grade_percent": round(self.mean_grade_percent, 3),
            "mean_rider_power_w": round(self.mean_rider_power_w, 3),
            "mean_wheel_power_w": round(self.mean_wheel_power_w, 3),
            "motor_fraction_of_wheel_energy": (
                None
                if self.wheel_work_j <= 0.0
                else round(self.motor_wheel_work_j / self.wheel_work_j, 4)
            ),
        }


@dataclass(frozen=True, slots=True)
class MechanicalIntensity:
    """Intensidade mecânica relativa e descritores por segmento.

    ``relative_intensity`` é ``P_media / P_ref``; não depende da massa do
    sistema. ``rms_power_w`` é a média quadrática (não é Normalized Power).
    """

    physics: RoutePhysics
    reference: PowerReference
    segment_power_w: tuple[float, ...]
    segment_relative_intensity: tuple[float, ...]
    segment_duration_s: tuple[float, ...]
    segment_distance_m: tuple[float, ...]
    rms_power_w: float
    max_segment_mean_power_w: float
    variability_index: float | None
    segment_peak_grade_pct: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.physics, RoutePhysics):
            raise ValueError("physics deve ser um RoutePhysics")
        if not isinstance(self.reference, PowerReference):
            raise ValueError("reference deve ser um PowerReference")
        size = len(self.segment_power_w)
        for name in (
            "segment_relative_intensity",
            "segment_duration_s",
            "segment_distance_m",
        ):
            if len(getattr(self, name)) != size:
                raise ValueError(f"{name} deve ter o mesmo tamanho dos segmentos")
        if self.segment_peak_grade_pct and len(self.segment_peak_grade_pct) != size:
            raise ValueError("segment_peak_grade_pct deve ter o mesmo tamanho dos segmentos")
        for name in (
            "rms_power_w",
            "max_segment_mean_power_w",
        ):
            non_negative(name, getattr(self, name))
        if self.variability_index is not None:
            non_negative("variability_index", self.variability_index)

    @property
    def mean_power_w(self) -> float:
        return self.physics.mean_rider_power_w

    @property
    def relative_intensity(self) -> float:
        return self.mean_power_w / self.reference.power_w

    @property
    def rms_relative_intensity(self) -> float:
        return self.rms_power_w / self.reference.power_w

    @property
    def total_work_j(self) -> float:
        return self.physics.rider_crank_work_j

    @property
    def specific_power_w_per_kg(self) -> float:
        return self.mean_power_w / self.reference.rider_mass_kg

    @property
    def specific_reference_w_per_kg(self) -> float:
        return self.reference.w_per_kg

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_intensity": round(self.relative_intensity, 4),
            "rms_relative_intensity": round(self.rms_relative_intensity, 4),
            "mean_power_w": round(self.mean_power_w, 3),
            "rms_power_w": round(self.rms_power_w, 3),
            "max_segment_mean_power_w": round(self.max_segment_mean_power_w, 3),
            "variability_index": (
                None
                if self.variability_index is None
                else round(self.variability_index, 4)
            ),
            "specific_power_w_per_kg": round(self.specific_power_w_per_kg, 4),
            "specific_reference_w_per_kg": round(
                self.specific_reference_w_per_kg, 4
            ),
            "total_work_j": round(self.total_work_j, 3),
            "reference": self.reference.to_dict(),
            "physics": self.physics.to_dict(),
            "segment_power_w": [round(value, 3) for value in self.segment_power_w],
            "segment_relative_intensity": [
                round(value, 4) for value in self.segment_relative_intensity
            ],
            "segment_peak_grade_pct": [round(value, 3) for value in self.segment_peak_grade_pct],
        }


def estimate_mechanical_intensity(
    segments: tuple[RouteSegment, ...] | list[RouteSegment],
    *,
    rider: RiderModel,
    propulsion: PropulsionModel = DEFAULT_PROPULSION,
) -> MechanicalIntensity:
    """Calcula a intensidade mecânica relativa de uma sequência de trechos.

    A função é determinística e não conhece ML. A massa total do sistema não
    entra: ``P_ref`` usa apenas a massa do ciclista e a referência declarada.
    """
    if not isinstance(rider, RiderModel):
        raise ValueError("rider deve ser um RiderModel")
    if not isinstance(propulsion, PropulsionModel):
        raise ValueError("propulsion deve ser um PropulsionModel")

    segment_powers: list[float] = []
    segment_relative: list[float] = []
    segment_durations: list[float] = []
    segment_distances: list[float] = []
    segment_peak_grades: list[float] = []
    rider_crank_total = 0.0
    rider_wheel_total = 0.0
    motor_total = 0.0
    wheel_total = 0.0
    distance_total = 0.0
    duration_total = 0.0
    ascent_total = 0.0
    descent_total = 0.0

    reference_power = rider.reference_power_w
    if reference_power <= 0.0:  # pragma: no cover - RiderModel ja valida
        raise ValueError("P_ref deve ser positivo")

    for segment in segments:
        if not isinstance(segment, RouteSegment):
            raise TypeError("segments deve conter RouteSegment")
        split = propulsion.split(
            rider_energy_j=segment.rider_energy_j,
            mechanical_energy_j=segment.mechanical_energy_j,
            duration_s=segment.duration_s,
        )
        power = (
            split.rider_crank_work_j / segment.duration_s
            if segment.duration_s > 0.0
            else 0.0
        )
        segment_powers.append(power)
        segment_relative.append(power / reference_power)
        segment_durations.append(segment.duration_s)
        segment_distances.append(segment.distance_m)
        segment_peak_grades.append(max(0.0, float(segment.peak_grade_pct)))

        rider_crank_total += split.rider_crank_work_j
        rider_wheel_total += split.rider_wheel_work_j
        motor_total += split.motor_wheel_work_j
        wheel_total += split.wheel_work_j
        distance_total += segment.distance_m
        duration_total += segment.duration_s
        ascent_total += segment.ascent_m
        descent_total += segment.descent_m

    if duration_total > 0.0:
        rms = math.sqrt(
            sum(power * power * duration for power, duration in zip(
                segment_powers, segment_durations
            ))
            / duration_total
        )
    else:
        rms = 0.0

    mean_power = (
        rider_crank_total / duration_total if duration_total > 0.0 else 0.0
    )
    variability = rms / mean_power if mean_power > 0.0 else None

    physics = RoutePhysics(
        distance_m=distance_total,
        duration_s=duration_total,
        ascent_m=ascent_total,
        descent_m=descent_total,
        wheel_work_j=wheel_total,
        rider_wheel_work_j=rider_wheel_total,
        rider_crank_work_j=rider_crank_total,
        motor_wheel_work_j=motor_total,
    )
    return MechanicalIntensity(
        physics=physics,
        reference=rider.reference,
        segment_power_w=tuple(segment_powers),
        segment_relative_intensity=tuple(segment_relative),
        segment_duration_s=tuple(segment_durations),
        segment_distance_m=tuple(segment_distances),
        rms_power_w=rms,
        max_segment_mean_power_w=max(segment_powers, default=0.0),
        variability_index=variability,
        segment_peak_grade_pct=tuple(segment_peak_grades),
    )


__all__ = [
    "RouteSegment",
    "IntensitySegment",
    "PropulsionMode",
    "PropulsionSplit",
    "PropulsionModel",
    "DEFAULT_PROPULSION",
    "RoutePhysics",
    "MechanicalIntensity",
    "estimate_mechanical_intensity",
]
