"""Reproduce global physical routing performance; no endpoint-specific solver.

python scripts/benchmark_physical_search.py --graph debug/.cache/graph_v4.json
    --origin=-17.804347455072403,-50.92253569751691
    --destination=-17.770388956200584,-50.920569302992085 --both-directions

--reference uses a zero heuristic, no incumbent pruning, and no result cache.
An explicit expansion budget marks an incomplete reference, never equivalence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from router.app.tooling import load_json
from router.core.data.snapping import build_query_graph, SnapOptions
from router.core.physics import PhysicalTraversal, CyclistModel, BicycleModel, Environment
from router.core.search.astar import MultiObjectiveAStar
from router.core.search.config import SearchConfig
from router.core.search.heuristic import Heuristic
from router.core.search.traversal_model import PhysicalTraversalModel
from router.core.search.circulation import validate_route


class ZeroHeuristic(Heuristic):
    def lower_bounds(self, node_id, goal_id, objectives):
        return (0.0,) * len(objectives)


def coord(value):
    lat, lon = value.split(",")
    return float(lat), float(lon)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--origin", required=True, type=coord)
    parser.add_argument("--destination", required=True, type=coord)
    parser.add_argument("--profile", default="flattest")
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--exact-speed", action="store_true")
    parser.add_argument("--both-directions", action="store_true")
    parser.add_argument("--max-expansions", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    graph = load_json(args.graph)
    load_s = time.perf_counter() - started
    directions = [(args.origin, args.destination)]
    if args.both_directions:
        directions.append((args.destination, args.origin))
    reports = []
    for origin, destination in directions:
        model = PhysicalTraversalModel(
            PhysicalTraversal(step_m=20, require_elevation=True),
            CyclistModel(mass_kg=75, max_power_w=900, sustainable_power_w=220, frontal_area_m2=.5),
            BicycleModel(), Environment(), cache_enabled=not args.reference,
        )
        started = time.perf_counter()
        query = build_query_graph(graph, origin, destination, SnapOptions(max_distance_m=150))
        snap_s = time.perf_counter() - started
        engine = MultiObjectiveAStar(
            query.graph, model, args.profile,
            heuristic=ZeroHeuristic(query.graph, max_speed_mps=None) if args.reference else None,
            goal_pruning=not args.reference,
            config=SearchConfig(exact_speed=args.exact_speed, max_expansions=args.max_expansions),
        )
        started = time.perf_counter()
        result = engine.search(query.origin_id, query.destination_id)
        search_s = time.perf_counter() - started
        values = [result.profile.values(label) for label in result.labels]
        frontier = [label for i, label in enumerate(result.labels) if not any(
            all(a <= b for a, b in zip(other, values[i]))
            and any(a < b for a, b in zip(other, values[i])) for other in values
        )]
        paths = result.arc_sequences()
        report = {
            "origin": origin, "destination": destination, "reference": args.reference,
            "load_s": load_s, "snap_s": snap_s, "search_s": search_s,
            "graph_nodes": graph.node_count, "graph_arcs": graph.arc_count,
            "expanded": result.expanded, "generated": result.generated,
            "goal_pruned": result.goal_pruned, "routes": result.count,
            "truncated": result.truncated, "state_approximated": result.state_approximated,
            "cache_hits": model.traversal_cache_hits, "physics_calls": model.traversal_cache_misses,
            "compliant": all(validate_route(query.graph, path).compliant for path in paths),
            "objective_front": sorted({result.profile.values(label) for label in frontier}),
        }
        reports.append(report)
        print(json.dumps(report), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
