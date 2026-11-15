"""Application bridge from existing rider presets to the mobility toolkit.

Physics receives scalar mobility parameters; capacity/activity and their
precedence remain owned by the existing physiology/prediction domain.
"""
from dataclasses import replace
import math

from router.core.data.mobility import MobilityProfile, PropulsionType, VehicleCategory
from router.core.physics.bicycle import BicycleModel
from router.core.physics.cyclist import CyclistModel
from router.core.physiology import ActivityLevel, CapacityProfile, RiderProfile
from router.core.physiology.rider import default_capacity_for_activity
from router.core.prediction.rider import rider_model_from_cyclist


class RiderPresets:
    @staticmethod
    def catalogue():
        bike = BicycleModel()
        mobility = MobilityProfile.create("bicycle", "manual")
        return {
            "capacity_profiles": [{"id": p.value, "description": p.description,
                                   "reference_w_per_kg": p.reference_w_per_kg} for p in CapacityProfile],
            "activity_levels": [{"id": a.value, "description": a.description,
                                 "weekly_active_minutes": list(a.weekly_active_minutes),
                                 "capacity_profile": default_capacity_for_activity(a).value} for a in ActivityLevel],
            "defaults": {"rider_mass_kg": 75.0, "vehicle_mass_kg": bike.mass_kg,
                         "power_w": mobility.nominal_power_w, "max_power_w": 600.0,
                         "frontal_area_m2": 0.5, "drag_coefficient": bike.drag_coefficient,
                         "rolling_resistance": bike.rolling_resistance,
                         "drive_efficiency": bike.drivetrain_efficiency},
        }

    @staticmethod
    def resolve(mobility, settings):
        if not settings:
            return mobility, None
        declared = dict(settings)
        capacity = CapacityProfile(declared["capacity_profile"]) if declared.get("capacity_profile") else None
        activity = ActivityLevel(declared["activity_level"]) if declared.get("activity_level") else None
        mass = float(declared.get("weight_kg") or 75.0)
        maximum = float(declared.get("max_power_w") or 600.0)
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("A potência máxima do ciclista deve ser positiva e finita.")
        profile = RiderProfile(weight_kg=mass, capacity_profile=capacity, activity_level=activity,
            **{name: declared.get(name) or None for name in (
                "ftp_w", "critical_power_w", "w_prime_j", "age_years",
                "max_heart_rate_bpm", "resting_heart_rate_bpm")})
        # This adapter reuses the existing precedence and reference provenance.
        cyclist = CyclistModel(mass, max(maximum, mobility.nominal_power_w),
                               mobility.nominal_power_w, 0.5)
        model = rider_model_from_cyclist(cyclist, profile)
        applicable = mobility.category in {VehicleCategory.BICYCLE, VehicleCategory.CARGO_BIKE} and mobility.propulsion_type is PropulsionType.MANUAL
        sources = dict(mobility.parameter_sources)
        explicit_power = sources.get("nominal_power_w") == "provided"
        if applicable and not explicit_power and any((capacity, activity, profile.ftp_w, profile.critical_power_w)):
            sources["nominal_power_w"] = "rider_reference_assumption"
            mobility = replace(mobility, nominal_power_w=model.reference_power_w,
                parameter_sources=tuple(sorted(sources.items())),
                assumptions=tuple(dict.fromkeys((*mobility.assumptions, "rider_reference_used_as_constant_simulation_power"))))
        if applicable and mobility.nominal_power_w > maximum:
            raise ValueError("A potência da simulação excede a potência máxima informada para o ciclista.")
        context = model.to_dict()
        context.update(applies_to_propulsion=applicable, max_power_w=maximum,
                       simulation_power_w=mobility.nominal_power_w,
                       simulation_power_source="provided" if explicit_power else sources.get("nominal_power_w"),
                       declared_capacity_profile=capacity.value if capacity else None,
                       declared_activity_level=activity.value if activity else None)
        return mobility, context
