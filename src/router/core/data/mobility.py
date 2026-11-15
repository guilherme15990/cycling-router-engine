"""Small immutable descriptions of rolling mobility, independent of routing."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class PropulsionType(str, Enum):
    MANUAL = "manual"
    ELECTRIC = "electric"
    COMBUSTION = "combustion"


class VehicleCategory(str, Enum):
    BICYCLE = "bicycle"
    CARGO_BIKE = "cargo_bike"
    KICK_SCOOTER = "kick_scooter"
    SMALL_ELECTRIC_VEHICLE = "small_electric_vehicle"
    MOPED = "moped"
    MOTORCYCLE = "motorcycle"
    MOTORCAR = "motorcar"
    GOODS = "goods"
    HGV = "hgv"
    BUS = "bus"


CV_TO_W = 735.49875  # metric horsepower (cv), not imperial horsepower (hp)


def power_in_watts(value: float, unit: str = "W") -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("power must be finite and nonnegative")
    factors = {"w": 1.0, "kw": 1000.0, "cv": CV_TO_W}
    try:
        return float(value) * factors[unit.strip().lower()]
    except KeyError:
        raise ValueError("power unit must be W, kW or cv") from None


# These are bootstrap modelling assumptions, not manufacturer specifications
# or calibrated vehicle performance. Every inferred value is reported.
# vehicle mass, CdA, baseline Crr, mechanical drive efficiency, input watts
_PRESETS = {
    "bicycle": (12.0, .45, .005, .97, 200.0),
    "cargo_bike": (30.0, .65, .007, .95, 200.0),
    "kick_scooter": (5.0, .55, .015, 1.0, 120.0),
    "small_electric_vehicle": (18.0, .55, .015, .90, 350.0),
    "moped": (90.0, .60, .012, .90, 2000.0),
    "motorcycle": (180.0, .65, .015, .90, 15000.0),
    "motorcar": (1400.0, .70, .012, .90, 55000.0),
    "goods": (2200.0, 1.3, .012, .90, 70000.0),
    "hgv": (10000.0, 5.0, .010, .90, 180000.0),
    "bus": (11000.0, 5.0, .010, .90, 180000.0),
}


@dataclass(frozen=True, slots=True)
class MobilityProfile:
    category: VehicleCategory
    propulsion_type: PropulsionType
    mass_total_kg: float
    cda_m2: float
    rolling_resistance: float
    drive_efficiency: float = 1.0
    nominal_power_w: float = 0.0
    max_speed_mps: float | None = None
    acceleration_limit_mps2: float = 2.0
    access_mode: str | None = None
    parameter_sources: tuple[tuple[str, str], ...] = ()
    assumptions: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "category", VehicleCategory(self.category))
        object.__setattr__(self, "propulsion_type", PropulsionType(self.propulsion_type))
        for name in ("mass_total_kg", "drive_efficiency"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("cda_m2", "rolling_resistance", "nominal_power_w", "acceleration_limit_mps2"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.drive_efficiency > 1:
            raise ValueError("drive_efficiency must not exceed 1")
        if self.max_speed_mps is not None and (not math.isfinite(self.max_speed_mps) or self.max_speed_mps <= 0):
            raise ValueError("max_speed_mps must be positive or None")

    @property
    def transport_mode(self) -> str:
        if self.access_mode is not None:
            return self.access_mode
        if self.propulsion_type is PropulsionType.ELECTRIC:
            if self.category is VehicleCategory.BICYCLE:
                return "electric_bicycle"
            if self.category is VehicleCategory.CARGO_BIKE:
                return "electric_cargo_bike"
        return self.category.value

    @classmethod
    def create(cls, vehicle: str | VehicleCategory = "bicycle", propulsion: str | PropulsionType | None = None,
               *, rider_mass_kg: float | None = None, vehicle_mass_kg: float | None = None,
               load_mass_kg: float = 0.0, power_w: float | None = None,
               power_cv: float | None = None, **overrides) -> "MobilityProfile":
        category = VehicleCategory(vehicle)
        kind = PropulsionType(propulsion) if propulsion is not None else (
            PropulsionType.MANUAL if category.value in ("bicycle", "cargo_bike", "kick_scooter") else
            PropulsionType.ELECTRIC if category is VehicleCategory.SMALL_ELECTRIC_VEHICLE else PropulsionType.COMBUSTION
        )
        vehicle_mass, cda, crr, efficiency, watts = _PRESETS[category.value]
        sources = {name: "preset_estimate" for name in (
            "vehicle_mass_kg", "rider_mass_kg", "cda_m2", "rolling_resistance", "drive_efficiency", "nominal_power_w"
        )}
        if power_w is not None and power_cv is not None:
            raise ValueError("provide power_w or power_cv, not both")
        for name, value in (("rider_mass_kg", rider_mass_kg), ("vehicle_mass_kg", vehicle_mass_kg)):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive")
            if value is not None:
                sources[name] = "provided"
        if not math.isfinite(load_mass_kg) or load_mass_kg < 0:
            raise ValueError("load_mass_kg must be nonnegative")
        if kind is PropulsionType.ELECTRIC and category.value in ("bicycle", "cargo_bike"):
            watts = 250.0  # mechanical input assumption; no battery efficiency inferred
        if power_w is not None or power_cv is not None:
            watts = power_in_watts(power_w if power_w is not None else power_cv, "W" if power_w is not None else "cv")
            sources["nominal_power_w"] = "provided"
        values = dict(category=category, propulsion_type=kind,
                      mass_total_kg=(rider_mass_kg if rider_mass_kg is not None else 75.0)
                      + (vehicle_mass_kg if vehicle_mass_kg is not None else vehicle_mass) + load_mass_kg,
                      cda_m2=cda, rolling_resistance=crr, drive_efficiency=efficiency, nominal_power_w=watts)
        for name in overrides:
            sources[name] = "provided"
        values.update(overrides)
        if "mass_total_kg" not in overrides:
            sources["mass_total_kg"] = "provided" if rider_mass_kg is not None and vehicle_mass_kg is not None else "partly_estimated"
        assumptions = ["generic_vehicle_preset@1", "constant_power_is_not_a_driving_or_effort_prediction"]
        if kind is PropulsionType.ELECTRIC and category in (VehicleCategory.BICYCLE, VehicleCategory.CARGO_BIKE) and "access_mode" not in overrides:
            assumptions.append("pedal_assist_access_class_assumed")
        return cls(**values, parameter_sources=tuple(sorted(sources.items())), assumptions=tuple(assumptions))

    @classmethod
    def from_bicycle(cls, cyclist, bicycle, **overrides) -> "MobilityProfile":
        values = dict(category=VehicleCategory.BICYCLE, propulsion_type=PropulsionType.MANUAL,
                      mass_total_kg=cyclist.mass_kg + bicycle.mass_kg,
                      cda_m2=cyclist.frontal_area_m2 * bicycle.drag_coefficient,
                      rolling_resistance=bicycle.rolling_resistance,
                      drive_efficiency=bicycle.drivetrain_efficiency,
                      nominal_power_w=cyclist.default_power_w)
        values.update(overrides)
        return cls(**values)

    def to_dict(self) -> dict:
        return {"vehicle": self.category.value, "propulsion": self.propulsion_type.value,
                "mass_total_kg": self.mass_total_kg, "cda_m2": self.cda_m2,
                "rolling_resistance": self.rolling_resistance, "drive_efficiency": self.drive_efficiency,
                "nominal_power_w": self.nominal_power_w, "max_speed_mps": self.max_speed_mps,
                "acceleration_limit_mps2": self.acceleration_limit_mps2,
                "transport_mode": self.transport_mode, "parameter_sources": dict(self.parameter_sources),
                "assumptions": list(self.assumptions)}
