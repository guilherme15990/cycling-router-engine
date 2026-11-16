"""Road-centric longitudinal motion using the shared spatial integrator."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math

from router.core.data.mobility import MobilityProfile, PropulsionType
from .environment import Environment
from .models import SimulationState, TraversalResult, classify_status
from .propulsion import PropulsionModel, ConstantPower, ReferenceSpeedPower
from .road_load import RoadLoadModel, StandardRoadLoad, MotionParameters
from .speed import advance_motion, MIN_SPEED_MPS
from .forces import equilibrium_speed_mps
from .arc_profile import RoadProfileCache


@dataclass(frozen=True, slots=True)
class MotionResult(TraversalResult):
    """The legacy rider field carries mechanical drive-input work internally.

    The toolkit exposes it as drive_input_energy_j. Source energy is distinct
    and unknown unless the propulsion model actually provides it.
    """
    source_energy_j: float | None = None

    @property
    def drive_input_energy_j(self):
        return self.rider_energy_j

    def to_dict(self):
        payload = TraversalResult.to_dict(self)
        payload["drive_input_energy_j"] = payload.pop("rider_energy_j")
        payload["source_energy_j"] = self.source_energy_j
        return payload


class MotionEngine:
    def __init__(self, *, step_m: float = 5.0, require_elevation: bool = False,
                 road_load: RoadLoadModel | None = None, geometry: RoadProfileCache | None = None):
        self.road_load = road_load if road_load is not None else StandardRoadLoad()
        self.geometry = geometry if geometry is not None else RoadProfileCache(step_m=step_m, require_elevation=require_elevation)
        self._equilibrium = {}

    @property
    def cache_key(self):
        load_key = getattr(self.road_load, "cache_key", None)
        if load_key is None:
            return None
        return (id(self), id(self.geometry), self.geometry.step_m, self.geometry.require_elevation, load_key)

    def traverse(self, arc, mobility: MobilityProfile, propulsion: PropulsionModel,
                 *, initial_state: SimulationState | None = None, environment: Environment | None = None) -> MotionResult:
        environment = environment if environment is not None else Environment()
        state = initial_state if initial_state is not None else SimulationState()
        profile = self.geometry.profile(arc)
        totals = dict(time_s=0.0, mechanical_energy_j=0.0, rider_energy_j=0.0,
                      braking_energy_j=0.0, aero_loss_j=0.0, rolling_loss_j=0.0)
        source_energy = 0.0
        source_known = True
        stalled = regularized = False
        speed = state.speed_mps
        for road in self.geometry.steps(arc):
            caps = [v for v in (road.max_speed_mps, mobility.max_speed_mps, environment.safe_max_speed_mps) if v is not None]
            cap = min(caps) if caps else None
            entry = min(speed, cap) if cap is not None else speed
            output = propulsion.evaluate(entry, road, mobility, environment)
            loads = self.road_load.evaluate(road, mobility, environment, max(entry, MIN_SPEED_MPS))
            if any(not math.isfinite(v) for v in (loads.gravity_n, loads.rolling_n, loads.aerodynamic_n)):
                raise ValueError("road load must return finite forces")
            parameters = MotionParameters(mobility.rolling_resistance, mobility.cda_m2, mobility.drive_efficiency)
            equilibrium = terminal = None
            # Only this known analytic combination promises the monotone
            # equilibrium law used by the original solver. Custom models still
            # integrate their own forces, without an invented equilibrium cap.
            analytic = type(self.road_load) is StandardRoadLoad and (
                (type(propulsion) is ConstantPower and propulsion.force_limit_n is None)
                or (type(propulsion) is ReferenceSpeedPower and propulsion.road_load is self.road_load))
            if analytic:
                parameters = self.road_load.parameters(road, mobility)
                key = (road.slope_rad, road.max_speed_mps, cap, mobility.mass_total_kg, parameters, environment, output.input_power_w)
                pair = self._equilibrium.get(key)
                if pair is None:
                    pair = tuple(equilibrium_speed_mps(
                        road.slope_rad, mobility.mass_total_kg, 1.0, parameters, environment, power,
                        max_speed_mps=cap,
                    ) for power in (output.input_power_w, 0.0))
                    self._equilibrium[key] = pair
                equilibrium, terminal = pair
            step = advance_motion(
                road.distance_m, road.slope_rad, speed, output.input_power_w,
                mobility.mass_total_kg, 1.0, parameters, environment,
                max_speed_mps=cap, acceleration_limit_mps2=mobility.acceleration_limit_mps2,
                equilibrium_speed_mps_value=equilibrium, terminal_speed_mps_value=terminal,
                road_forces=loads, propulsion_output=output, enforce_equilibrium=analytic,
            )
            totals["time_s"] += step.time_s
            totals["mechanical_energy_j"] += step.wheel_energy_j
            totals["rider_energy_j"] += step.rider_energy_j
            totals["braking_energy_j"] += step.braking_energy_j
            totals["aero_loss_j"] += step.aero_loss_j
            totals["rolling_loss_j"] += step.rolling_loss_j
            if step.pedaling:
                if output.source_power_w is None:
                    source_known = False
                else:
                    if output.work_mode == "force" and output.input_power_w > 0:
                        source_energy += step.rider_energy_j * output.source_power_w / output.input_power_w
                    else:
                        source_energy += output.source_power_w * step.time_s
            stalled |= step.stalled
            regularized |= step.regularized
            speed = step.final_speed_mps
        uncertainty = profile.elevation_uncertainty
        source = source_energy if source_known else None
        return MotionResult(
            **totals, distance_m=profile.distance_m, ascent_m=profile.ascent_m,
            descent_m=profile.descent_m, initial_speed_mps=state.speed_mps, final_speed_mps=speed,
            battery_energy_j=source if mobility.propulsion_type is PropulsionType.ELECTRIC else None,
            stalled=stalled, arc_id=arc.id,
            state=SimulationState(elapsed_s=state.elapsed_s + totals["time_s"], speed_mps=speed,
                                  wheel_work_j=state.wheel_work_j + totals["mechanical_energy_j"],
                                  rider_work_j=state.rider_work_j + totals["rider_energy_j"],
                                  braking_loss_j=state.braking_loss_j + totals["braking_energy_j"]),
            elevation_uncertainty=uncertainty,
            result_status=classify_status(uncertainty, require_elevation=self.geometry.require_elevation),
            used_min_speed_regularization=regularized, source_energy_j=source,
        )


class MotionTraversalModel:
    """Adapter for the existing multiobjective A*, with safe opt-in caching."""
    access_from_tags = True
    def __init__(self, engine: MotionEngine, mobility: MobilityProfile,
                 propulsion: PropulsionModel | None = None, environment: Environment | None = None,
                 access_filter=None):
        self.engine, self.mobility = engine, mobility
        self.propulsion = propulsion if propulsion is not None else ConstantPower(mobility.nominal_power_w)
        self.environment = environment if environment is not None else Environment()
        self._access_filter = access_filter
        for model in (self.propulsion, self.engine.road_load):
            if set(getattr(model, "state_dependencies", ("speed_mps",))) - {"speed_mps"}:
                raise ValueError("stateful models need a TraversalModel and dominance carrying their extra state")
        self._cache = OrderedDict()
        self._signature = None
        self.traversal_cache_hits = self.traversal_cache_misses = 0

    @property
    def mass_kg(self):
        return self.mobility.mass_total_kg

    @property
    def max_speed_mps(self):
        caps = [v for v in (self.mobility.max_speed_mps, self.environment.safe_max_speed_mps) if v is not None]
        return min(caps) if caps else None

    @property
    def access_scopes(self):
        from router.core.data.access import access_scopes
        return access_scopes(self.mobility.transport_mode)

    def allows_arc(self, arc) -> bool:
        if self._access_filter is None:
            return True
        answer = self._access_filter(arc, self.mobility)
        if not isinstance(answer, bool):
            raise TypeError("access_filter must return bool")
        return answer

    def arc_lower_bounds(self, arc):
        distance = arc.distance_m
        return {"distance_m": distance, "ascent_m": sum(s.elevation_gain_m for s in arc.segments),
                "time_s": distance / self.max_speed_mps if self.max_speed_mps is not None else 0.0,
                "energy_j": 0.0}

    def traverse(self, arc, initial_speed_mps):
        signature = (self.engine.cache_key, getattr(self.propulsion, "cache_key", None), self.mobility, self.environment)
        cacheable = signature[0] is not None and signature[1] is not None
        if signature != self._signature:
            self._cache.clear()
            self._signature = signature
        key = (arc.id, float(initial_speed_mps))
        cached = self._cache.get(key) if cacheable else None
        if cached is not None and cached[0] is arc:
            self._cache.move_to_end(key)
            self.traversal_cache_hits += 1
            return cached[1]
        self.traversal_cache_misses += 1
        result = self.engine.traverse(arc, self.mobility, self.propulsion,
                                     initial_state=SimulationState.from_speed(initial_speed_mps), environment=self.environment)
        if cacheable:
            self._cache[key] = (arc, result)
            if len(self._cache) > 100_000:
                self._cache.popitem(last=False)
        return result
