"""Camada fisica: tempo e energia de travessia de um arco.

A camada nao conhece perfis de rota. Ela transforma atributos do grafo
(distancia, elevacao, superficie e limites) em metricas fisicas que a busca
Pareto pode acumular.
"""

from .bicycle import BicycleModel
from .cyclist import CyclistModel
from .arc_profile import ArcProfile, ProfileStep, SegmentProfile
from .energy import (
    battery_energy_from_wheel_j,
    kinetic_energy_j,
    potential_energy_j,
    rider_energy_from_wheel_j,
    wheel_energy_from_rider_j,
)
from .energy_model import (
    CALIBRATED_LEVEL,
    GENERIC_LEVEL,
    JOULES_PER_WH,
    PERSONALIZED_LEVEL,
    BatteryConfig,
    BatteryFeasibility,
    EnergyEstimate,
    EnergyModel,
    GenericEnergyConfig,
    assess_battery,
)
from .environment import Environment
from .effort import (
    EFFORT_FEATURES_VERSION,
    EffortFeatures,
    extract_effort_features,
)
from .forces import (
    ForceBreakdown,
    aerodynamic_force_n,
    equilibrium_speed_mps,
    forces_at,
    gravity_force_n,
    required_rider_power_w,
    rolling_force_n,
    wheel_power_w,
)
from .models import (
    ElevationUncertainty,
    EnergyBalance,
    PhysicsRouteMetrics,
    SimulationState,
    SimulationStatus,
    TraversalPath,
    TraversalResult,
    classify_status,
    worst_status,
)
from .speed import MIN_SPEED_MPS, SpeedStep, advance_speed
from .traversal import (
    DEFAULT_ACCELERATION_LIMIT_MPS2,
    DEFAULT_DISMOUNT_SPEED_MPS,
    DEFAULT_STEP_M,
    DEFAULT_STEP_M_BY_MODE,
    PhysicalTraversal,
    SimulationMode,
    traverse_arc,
)

__all__ = [
    "BicycleModel",
    "BatteryConfig",
    "BatteryFeasibility",
    "CALIBRATED_LEVEL",
    "CyclistModel",
    "DEFAULT_ACCELERATION_LIMIT_MPS2",
    "DEFAULT_DISMOUNT_SPEED_MPS",
    "DEFAULT_STEP_M",
    "DEFAULT_STEP_M_BY_MODE",
    "ArcProfile",
    "EFFORT_FEATURES_VERSION",
    "EffortFeatures",
    "ElevationUncertainty",
    "EnergyBalance",
    "EnergyEstimate",
    "EnergyModel",
    "Environment",
    "ForceBreakdown",
    "GENERIC_LEVEL",
    "GenericEnergyConfig",
    "JOULES_PER_WH",
    "MIN_SPEED_MPS",
    "PERSONALIZED_LEVEL",
    "PhysicalTraversal",
    "ProfileStep",
    "PhysicsRouteMetrics",
    "SegmentProfile",
    "SimulationMode",
    "SimulationState",
    "SimulationStatus",
    "SpeedStep",
    "TraversalPath",
    "TraversalResult",
    "advance_speed",
    "aerodynamic_force_n",
    "assess_battery",
    "battery_energy_from_wheel_j",
    "classify_status",
    "equilibrium_speed_mps",
    "extract_effort_features",
    "forces_at",
    "gravity_force_n",
    "kinetic_energy_j",
    "potential_energy_j",
    "required_rider_power_w",
    "rider_energy_from_wheel_j",
    "rolling_force_n",
    "traverse_arc",
    "wheel_energy_from_rider_j",
    "wheel_power_w",
    "worst_status",
]
