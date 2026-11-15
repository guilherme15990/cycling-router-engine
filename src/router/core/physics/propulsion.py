"""Memoryless propulsion contracts: input power, force at wheel, source energy."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

from router.core.data.mobility import MobilityProfile
from .arc_profile import ProfileStep
from .environment import Environment


@dataclass(frozen=True, slots=True)
class PropulsionOutput:
    wheel_force_n: float
    input_power_w: float
    wheel_power_w: float
    source_power_w: float | None = None
    work_mode: str = "power"

    def __post_init__(self):
        if self.work_mode not in {"power", "force"}:
            raise ValueError("work_mode must be power or force")
        for value in (self.wheel_force_n, self.input_power_w, self.wheel_power_w, self.source_power_w):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("propulsion outputs must be finite and nonnegative")


class PropulsionModel(Protocol):
    """Stateless for a fixed request. Hidden battery/fatigue state is unsupported.

    Return positive tractive force and mechanical powers in SI units. Source
    power is optional: shaft power alone does not identify fuel/battery use.
    Only models declaring an immutable cache_key opt into transition caching.
    """
    def evaluate(self, speed_mps: float, road: ProfileStep,
                 mobility: MobilityProfile, environment: Environment) -> PropulsionOutput: ...


@dataclass(frozen=True, slots=True)
class ConstantPower:
    power_w: float
    force_limit_n: float | None = None
    source_efficiency: float | None = None

    def __post_init__(self):
        if not math.isfinite(self.power_w) or self.power_w < 0:
            raise ValueError("power_w must be finite and nonnegative")
        if self.force_limit_n is not None and (not math.isfinite(self.force_limit_n) or self.force_limit_n <= 0):
            raise ValueError("force_limit_n must be positive")
        if self.source_efficiency is not None and (not math.isfinite(self.source_efficiency) or not 0 < self.source_efficiency <= 1):
            raise ValueError("source_efficiency must be in (0, 1]")

    @property
    def cache_key(self):
        return (type(self), self.power_w, self.force_limit_n, self.source_efficiency)

    def evaluate(self, speed_mps: float, road: ProfileStep,
                 mobility: MobilityProfile, environment: Environment) -> PropulsionOutput:
        # Preserve the declared low-speed regularization of the legacy model.
        from .speed import MIN_SPEED_MPS
        speed = max(float(speed_mps), MIN_SPEED_MPS)
        force = self.power_w * mobility.drive_efficiency / speed
        if self.force_limit_n is not None:
            force = min(force, self.force_limit_n)
        wheel_power = self.power_w * mobility.drive_efficiency if self.force_limit_n is None else force * speed
        input_power = self.power_w if self.force_limit_n is None else wheel_power / mobility.drive_efficiency
        source_power = None if self.source_efficiency is None else input_power / self.source_efficiency
        return PropulsionOutput(force, input_power, wheel_power, source_power)


@dataclass(frozen=True, slots=True)
class ConstantForce:
    """Force-limited propulsion, including a finite starting force at rest."""
    force_n: float
    max_power_w: float | None = None
    source_efficiency: float | None = None

    def __post_init__(self):
        if not math.isfinite(self.force_n) or self.force_n < 0:
            raise ValueError("force_n must be finite and nonnegative")
        if self.max_power_w is not None and (not math.isfinite(self.max_power_w) or self.max_power_w < 0):
            raise ValueError("max_power_w must be finite and nonnegative")
        if self.source_efficiency is not None and (not math.isfinite(self.source_efficiency) or not 0 < self.source_efficiency <= 1):
            raise ValueError("source_efficiency must be in (0, 1]")

    @property
    def cache_key(self):
        return (type(self), self.force_n, self.max_power_w, self.source_efficiency)

    def evaluate(self, speed_mps, road, mobility, environment):
        from .speed import MIN_SPEED_MPS
        speed = max(float(speed_mps), MIN_SPEED_MPS)
        force = self.force_n
        if self.max_power_w is not None:
            force = min(force, self.max_power_w * mobility.drive_efficiency / speed)
        wheel_power = force * speed
        input_power = wheel_power / mobility.drive_efficiency
        return PropulsionOutput(force, input_power, wheel_power,
                                None if self.source_efficiency is None else input_power / self.source_efficiency,
                                work_mode="force")
