"""Road-centric toolkit facade over the shared physics/search/network engines."""
from __future__ import annotations

from dataclasses import asdict
from threading import RLock
import math

from router.contracts.mobility import MobilityPlan, MobilityRoute, MobilityMetrics
from router.offline import offline
from router.core.data.mobility import MobilityProfile, VehicleCategory, PropulsionType, power_in_watts
from router.core.data.geo import query_bbox
from router.core.data.snapping import build_query_graph, SnapOptions, SnapError
from router.core.data.elevation.providers import ElevationProvider, CallableElevation
from router.core.data.elevation.offline import TerrariumElevation
from router.core.physics.environment import Environment
from router.core.physics.motion import MotionEngine, MotionTraversalModel
from router.core.physics.propulsion import PropulsionModel, PropulsionOutput, ConstantPower, ConstantForce
from router.core.physics.road_load import RoadLoadModel, StandardRoadLoad
from router.core.physics.arc_profile import ArcProfile as RoadProfile, ProfileStep as RoadStep
from router.core.physics.forces import ForceBreakdown
from router.core.physics.models import SimulationState as MotionState
from router.core.search.config import SearchConfig
from router.core.physics.models import SimulationStatus
from router.core.search.astar import SearchCancelled, SearchResult, order_labels
from router.core.search.candidates import RouteCandidatePool
from router.core.search.candidate_search import search_candidates
from router.core.search.circulation import DEFAULT_POLICY, arc_decision, validate_route
from router.core.search.objectives import profile_for
from router.core.search.route_set import select_distinct_routes


class MobilityEngine:
    """Configure a network once; users can start with category/propulsion only.

    Existing category-filtered graphs cannot recover excluded roads. Factory
    engines retain raw OSM and build/cache the correct directional network for
    each requested access class. Raw graph coverage without class metadata is
    explicitly reported as unverified.
    """
    def __init__(self, network, *, motion: MotionEngine | None = None,
                 environment: Environment | None = None, elevation_provider=None,
                 elevation_spacing_m: float = 25.0, network_mode: str | None = None, elevation_tiles=None):
        self.network = network
        self.network_mode = network_mode
        self.motion = motion if motion is not None else MotionEngine()
        self.environment = environment if environment is not None else Environment()
        if elevation_tiles is not None and elevation_provider is not None:
            raise ValueError("provide elevation_tiles or elevation_provider, not both")
        self.elevation_provider = TerrariumElevation(elevation_tiles) if elevation_tiles is not None else elevation_provider
        self.elevation_spacing_m = elevation_spacing_m
        self._networks = {}
        self._lock = RLock()

    @classmethod
    def from_osm(cls, data, **options):
        from router.core.data.osm.parse import OsmData
        if isinstance(data, dict):
            data = OsmData.from_payload(data)
        if not isinstance(data, OsmData):
            raise TypeError("from_osm expects OsmData or an OSM payload mapping")
        return cls(data, **options)

    @classmethod
    def from_pbf(cls, path, **options):
        from router.core.data.osm.pbf import read_osm_pbf
        from router.core.data.access import ALL_ROAD_HIGHWAYS
        # The legacy parser defaults to cycling road classes. Keep the raw
        # road network here so a later car request retains motorways/trunks.
        return cls.from_osm(read_osm_pbf(path, highways=ALL_ROAD_HIGHWAYS), **options)

    def _network_for(self, mode):
        from router.core.data.graph.model import RoutingGraph
        if isinstance(self.network, RoutingGraph):
            known = self.network.metadata.get("transport_mode", self.network_mode)
            if known is None and mode not in {"bicycle", "electric_bicycle", "cargo_bike"}:
                raise ValueError("unclassified graph coverage: use from_osm/from_pbf or declare network_mode")
            if known is not None and known != mode:
                raise ValueError(f"network was built for {known}; rebuild from OSM for {mode}")
            if self.elevation_provider is None:
                return self.network
            from router.core.data.osm.build import attach_elevation
            with self._lock:
                if mode not in self._networks:
                    prepared = self.network.copy()
                    attach_elevation(prepared, self.elevation_provider, spacing_m=self.elevation_spacing_m)
                    self._networks[mode] = prepared
                return self._networks[mode]
        from router.core.data.osm.build import BuildOptions, build_graph, attach_elevation
        with self._lock:
            if mode not in self._networks:
                graph, _ = build_graph(self.network, BuildOptions(travel_mode=mode))
                if self.elevation_provider is not None:
                    attach_elevation(graph, self.elevation_provider, spacing_m=self.elevation_spacing_m)
                self._networks[mode] = graph
            return self._networks[mode]

    @offline()
    def prepare(self, *, mobility: MobilityProfile | None = None,
                vehicle: str | VehicleCategory = "bicycle", propulsion_type: str | PropulsionType | None = None):
        """Prepare/cache ingestion at service startup, outside route latency.

        The returned graph can be persisted with the existing save_graph API.
        """
        profile = mobility if mobility is not None else MobilityProfile.create(vehicle, propulsion_type)
        return self._network_for(profile.transport_mode)

    @offline()
    def plan(self, origin, destination, *, mobility: MobilityProfile | None = None,
             vehicle: str | VehicleCategory = "bicycle", propulsion_type: str | PropulsionType | None = None,
             propulsion=None, profile: str = "flattest", initial_speed_mps: float = 0.0,
             max_routes: int | None = None, collapse_similar: bool = False,
             snap_options: SnapOptions | None = None, search_config=None,
             condition_evaluator=None, circulation_policy=DEFAULT_POLICY, should_cancel=None,
             access_filter=None, on_plan=None, progress_event_every: int = 4) -> MobilityPlan:
        def check():
            if should_cancel is not None and should_cancel():
                raise SearchCancelled("Busca cancelada pelo usuário")
        check()
        if isinstance(progress_event_every, bool) or not isinstance(progress_event_every, int) or progress_event_every < 1:
            raise ValueError("progress_event_every must be a positive integer")
        for coord in (origin, destination):
            if len(coord) != 2 or not all(math.isfinite(v) for v in coord) or not -90 <= coord[0] <= 90 or not -180 <= coord[1] <= 180:
                raise ValueError("origin and destination must be valid (latitude, longitude)")
        origin, destination = tuple(origin), tuple(destination)
        if not math.isfinite(initial_speed_mps) or initial_speed_mps < 0:
            raise ValueError("initial_speed_mps must be finite and nonnegative")
        if max_routes is not None and (isinstance(max_routes, bool) or not isinstance(max_routes, int) or max_routes <= 0):
            raise ValueError("max_routes must be a positive integer or None")
        mobility = mobility if mobility is not None else MobilityProfile.create(vehicle, propulsion_type)
        objective_profile = profile_for(profile)
        model = MotionTraversalModel(self.motion, mobility, propulsion, self.environment, access_filter)
        graph = self._network_for(mobility.transport_mode)
        check()
        scenario = {"environment": asdict(self.environment), "step_m": self.motion.geometry.step_m,
                    "require_elevation": self.motion.geometry.require_elevation,
                    "propulsion_model": type(model.propulsion).__module__ + "." + type(model.propulsion).__qualname__,
                    "road_load_model": type(self.motion.road_load).__module__ + "." + type(self.motion.road_load).__qualname__}
        if type(model.propulsion) in (ConstantPower, ConstantForce):
            scenario["propulsion_parameters"] = asdict(model.propulsion)
        warnings = list(mobility.assumptions)
        if "access_defaults" in graph.metadata:
            warnings.append(graph.metadata["access_defaults"])
        if graph.metadata.get("transport_mode") is None:
            warnings.append("network_access_class_coverage_unverified")
        if getattr(self.motion.road_load, "surface_coefficients", ()) == ():
            warnings.append("surface_response_uses_mobility_baseline")
        options = snap_options if snap_options is not None else SnapOptions()
        def allowed(arc):
            return model.allows_arc(arc) and not arc_decision(arc, evaluator=condition_evaluator, policy=circulation_policy,
                                    access_scopes=model.access_scopes, access_from_tags=True).forbids
        try:
            query = build_query_graph(graph, origin, destination, options, arc_filter=allowed)
        except SnapError:
            box = query_bbox((origin, destination))
            return MobilityPlan(origin, destination, objective_profile.id, objective_profile.names,
                                mobility.to_dict(), (), box, 0, True, False, False,
                                {"reason": "no_accessible_snap", "graph_nodes": graph.node_count, "graph_arcs": graph.arc_count}, tuple(warnings))
        check()
        def present(result, pool):
            if collapse_similar:
                indices = select_distinct_routes(pool.routes, max_routes=max_routes).indices
                candidates = tuple(pool.candidates[index] for index in indices)
            else:
                candidates = pool.candidates if max_routes is None else pool.candidates[:max_routes]
            routes = []
            rejected = []
            for candidate in candidates:
                check()
                path = tuple(candidate.route.arc_ids)
                validation = validate_route(query.graph, path, access_scopes=model.access_scopes, access_from_tags=True,
                                            evaluator=condition_evaluator, policy=circulation_policy)
                if not validation.compliant or not all(model.allows_arc(query.graph.get_arc(arc_id)) for arc_id in path):
                    rejected.append(candidate.route_id)
                    continue
                speed = initial_speed_mps
                parts = []
                geometry = []
                elevation_profile = []
                offset_m = 0.0
                for arc_id in path:
                    arc = query.graph.get_arc(arc_id)
                    part = model.traverse(arc, speed)
                    parts.append(part)
                    for step in self.motion.geometry.steps(arc):
                        if not elevation_profile or elevation_profile[-1] != (offset_m + step.start_m, step.elevation_start_m):
                            elevation_profile.append((offset_m + step.start_m, step.elevation_start_m))
                        elevation_profile.append((offset_m + step.start_m + step.distance_m, step.elevation_end_m))
                    offset_m += part.distance_m
                    speed = part.final_speed_mps
                    points = arc.geometry or (query.graph.get_node(arc.source).coord, query.graph.get_node(arc.target).coord)
                    for point in points:
                        if not geometry or geometry[-1] != point:
                            geometry.append(point)
                if self.motion.geometry.require_elevation and any(part.result_status is SimulationStatus.UNAVAILABLE for part in parts):
                    rejected.append(candidate.route_id)
                    continue
                def optional_sum(name):
                    values = [getattr(part, name) for part in parts]
                    return sum(values) if values and all(value is not None for value in values) else None
                metrics = MobilityMetrics(
                    time_s=sum(part.time_s for part in parts), distance_m=sum(part.distance_m for part in parts),
                    ascent_m=sum(part.ascent_m for part in parts), descent_m=sum(part.descent_m for part in parts),
                    mechanical_energy_j=sum(part.mechanical_energy_j for part in parts),
                    drive_input_energy_j=sum(part.rider_energy_j for part in parts),
                    source_energy_j=optional_sum("source_energy_j"), battery_energy_j=optional_sum("battery_energy_j"),
                )
                route = candidate.route
                if route.elevation_uncertainty is not None and route.elevation_uncertainty.has_gap:
                    if "missing_elevation_assumed_flat" not in warnings:
                        warnings.append("missing_elevation_assumed_flat")
                routes.append(MobilityRoute(candidate.route_id, path, tuple(geometry), metrics, not routes,
                                            route.result_status.value, route.used_min_speed_regularization, route.stalled,
                                            bool(validation.unresolved),
                                            None if route.elevation_uncertainty is None else route.elevation_uncertainty.to_dict(),
                                            tuple(elevation_profile)))
            box = query_bbox((origin, destination), [route.geometry for route in routes])
            complete = not result.truncated and not result.stopped_early
            return MobilityPlan(
                origin, destination, objective_profile.id, objective_profile.names, mobility.to_dict(), tuple(routes), box,
                len(pool.candidates), complete, result.state_approximated,
                complete and not result.frontier_truncated and not result.state_approximated and not rejected,
                dict(graph_nodes=graph.node_count, graph_arcs=graph.arc_count, expanded=result.expanded,
                     generated=result.generated, goal_pruned=result.goal_pruned, truncated=result.truncated,
                     frontier_truncated=result.frontier_truncated, rejected_routes=rejected,
                     physics_calls=model.traversal_cache_misses, cache_hits=model.traversal_cache_hits,
                     coverage="provided_graph", access_mode=mobility.transport_mode, scenario=scenario), tuple(warnings),
            )
        published = [0]
        def candidate_found(state, astar):
            published[0] += 1
            if published[0] != 1 and published[0] % progress_event_every:
                return
            # Project the live destination frontier through the SAME validation
            # and metrics path as the final result. Physics cache is shared.
            partial = SearchResult(
                query.origin_id, query.destination_id, objective_profile,
                order_labels(objective_profile, astar.frontier.labels_at(query.destination_id)),
                stopped_early=True, frontier_truncated=astar.frontier.frontier_truncated,
                state_approximated=not astar.config.exact_speed,
            )
            plan = present(partial, RouteCandidatePool.from_result(partial))
            if plan.routes:
                on_plan(plan)

        searched = search_candidates(query.graph, model, objective_profile, query.origin_id, query.destination_id,
                                     initial_speed_mps=initial_speed_mps, search_config=search_config,
                                     should_cancel=should_cancel, condition_evaluator=condition_evaluator,
                                     circulation_policy=circulation_policy,
                                     on_candidate_state=candidate_found if on_plan is not None else None)
        return present(searched.result, searched.pool)



__all__ = [
    "MobilityEngine", "MobilityProfile", "VehicleCategory", "PropulsionType", "SearchCancelled",
    "MotionEngine", "PropulsionModel", "ConstantPower", "ConstantForce", "RoadLoadModel",
    "StandardRoadLoad", "MobilityPlan", "MobilityRoute", "MobilityMetrics", "power_in_watts",
    "Environment", "SnapOptions", "SearchConfig", "PropulsionOutput", "ForceBreakdown", "RoadProfile", "RoadStep", "MotionState",
    "ElevationProvider", "CallableElevation", "TerrariumElevation",
]
