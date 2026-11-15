"""Framework-free output of the road-centric toolkit; independent of physiology."""
from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class MobilityMetrics:
    time_s: float
    distance_m: float
    ascent_m: float
    descent_m: float
    mechanical_energy_j: float
    drive_input_energy_j: float
    source_energy_j: float | None
    battery_energy_j: float | None

    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MobilityRoute:
    route_id: str
    arc_ids: tuple[str, ...]
    geometry: tuple[tuple[float, float], ...]
    metrics: MobilityMetrics
    recommended: bool
    simulation_status: str
    used_min_speed_regularization: bool
    stalled: bool
    circulation_unresolved: bool
    elevation_uncertainty: dict | None
    elevation_profile: tuple[tuple[float, float | None], ...] = ()

    def to_dict(self):
        return dict(route_id=self.route_id, arc_ids=list(self.arc_ids),
                    geometry=[list(coord) for coord in self.geometry], metrics=self.metrics.to_dict(),
                    recommended=self.recommended, simulation_status=self.simulation_status,
                    used_min_speed_regularization=self.used_min_speed_regularization, stalled=self.stalled,
                    circulation_unresolved=self.circulation_unresolved, elevation_uncertainty=self.elevation_uncertainty,
                    elevation_profile=[list(point) for point in self.elevation_profile])


@dataclass(frozen=True, slots=True)
class MobilityPlan:
    origin: tuple[float, float]
    destination: tuple[float, float]
    profile: str
    objectives: tuple[str, ...]
    mobility: dict
    routes: tuple[MobilityRoute, ...]
    bbox: tuple[float, float, float, float]
    pareto_route_count: int
    search_complete: bool
    state_approximated: bool
    optimality_proven: bool
    diagnostics: dict
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload):
        """Read the native toolkit envelope without importing the engine."""
        if payload.get("format") != "roteamento.mobility-route-plan" or payload.get("version") != 1:
            raise ValueError("unsupported mobility plan format/version")
        routes = tuple(MobilityRoute(
            route_id=row["route_id"], arc_ids=tuple(row["arc_ids"]),
            geometry=tuple(tuple(point) for point in row["geometry"]),
            metrics=MobilityMetrics(**row["metrics"]), recommended=row["recommended"],
            simulation_status=row["simulation_status"],
            used_min_speed_regularization=row["used_min_speed_regularization"],
            stalled=row["stalled"], circulation_unresolved=row["circulation_unresolved"],
            elevation_uncertainty=row["elevation_uncertainty"],
            elevation_profile=tuple(tuple(point) for point in row.get("elevation_profile", ())),
        ) for row in payload["routes"])
        return cls(
            origin=tuple(payload["origin"]), destination=tuple(payload["destination"]),
            profile=payload["profile"], objectives=tuple(payload["objectives"]),
            mobility=dict(payload["mobility"]), routes=routes, bbox=tuple(payload["bbox"]),
            pareto_route_count=payload["pareto_route_count"], search_complete=payload["search_complete"],
            state_approximated=payload["state_approximated"], optimality_proven=payload["optimality_proven"],
            diagnostics=dict(payload["diagnostics"]), warnings=tuple(payload.get("warnings", ())),
        )

    @property
    def reached(self):
        return bool(self.routes)

    @property
    def recommended_route(self):
        return next((route for route in self.routes if route.recommended), None)

    def to_dict(self):
        return dict(format="roteamento.mobility-route-plan", version=1,
                    origin=list(self.origin), destination=list(self.destination), profile=self.profile,
                    objectives=list(self.objectives), mobility=dict(self.mobility), reached=self.reached,
                    routes=[route.to_dict() for route in self.routes], bbox=list(self.bbox),
                    pareto_route_count=self.pareto_route_count, search_complete=self.search_complete,
                    state_approximated=self.state_approximated, optimality_proven=self.optimality_proven,
                    diagnostics=dict(self.diagnostics), warnings=list(self.warnings))

    def to_json(self, *, indent: int | None = None):
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, indent=indent)
