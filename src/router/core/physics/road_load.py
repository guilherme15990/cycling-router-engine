"""Road/mobility interaction; vehicle-dependent forces never live on the graph."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from router.core.data.mobility import MobilityProfile
from router.core.data.graph.surface import Surface
from .arc_profile import ProfileStep
from .environment import Environment
from .forces import ForceBreakdown, gravity_force_n, rolling_force_n, aerodynamic_force_n


@dataclass(frozen=True, slots=True)
class MotionParameters:
    """Scalar parameters required by the existing longitudinal integrator."""
    rolling_resistance: float
    drag_coefficient: float  # CdA when frontal_area_m2=1
    drivetrain_efficiency: float


class RoadLoadModel(Protocol):
    """Deterministic, memoryless road load. Stateful models need a stateful router."""
    def evaluate(self, road: ProfileStep, mobility: MobilityProfile,
                 environment: Environment, speed_mps: float) -> ForceBreakdown: ...


@dataclass(frozen=True, slots=True)
class StandardRoadLoad:
    """Gravity, rolling resistance and signed relative-wind drag.

    Optional surface coefficients are supplied/calibrated by the integrator.
    Without them, the mobility baseline is used explicitly; no coefficient is
    invented from a surface name. Roughness/curvature remain observations until
    a model knows how to interpret them.
    """
    surface_coefficients: tuple[tuple[Surface, float], ...] = ()

    def __post_init__(self):
        import math
        if any(not math.isfinite(value) or value < 0 for _, value in self.surface_coefficients):
            raise ValueError("surface rolling coefficients must be finite and nonnegative")

    @property
    def cache_key(self):
        return (type(self), self.surface_coefficients)

    def parameters(self, road: ProfileStep, mobility: MobilityProfile) -> MotionParameters:
        crr = dict(self.surface_coefficients).get(road.surface, mobility.rolling_resistance)
        return MotionParameters(crr, mobility.cda_m2, mobility.drive_efficiency)

    def evaluate(self, road: ProfileStep, mobility: MobilityProfile,
                 environment: Environment, speed_mps: float) -> ForceBreakdown:
        parameters = self.parameters(road, mobility)
        return ForceBreakdown(
            gravity_force_n(mobility.mass_total_kg, road.slope_rad, environment.gravity_m_s2),
            rolling_force_n(mobility.mass_total_kg, road.slope_rad, parameters.rolling_resistance, environment.gravity_m_s2),
            aerodynamic_force_n(speed_mps, mobility.cda_m2, 1.0,
                                environment.air_density_kg_m3, environment.headwind_mps),
        )
