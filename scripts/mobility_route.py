"""Minimal offline client of the public mobility toolkit (no routing logic)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from router.interfaces.cli.coordinates import normalize_coordinate_args
from router.integration import (
    MobilityEngine, MobilityProfile, VehicleCategory, PropulsionType, ConstantPower,
    MotionEngine, SearchConfig, load_graph, PROFILE_IDS,
)


def coordinate(value):
    try:
        lat, lon = value.split(",")
        return float(lat), float(lon)
    except ValueError:
        raise argparse.ArgumentTypeError("use latitude,longitude") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--graph", type=Path)
    source.add_argument("--pbf", type=Path)
    parser.add_argument("--dem-dir", type=Path)
    parser.add_argument("--origin", required=True, type=coordinate)
    parser.add_argument("--destination", required=True, type=coordinate)
    parser.add_argument("--vehicle", choices=[v.value for v in VehicleCategory], default="bicycle")
    parser.add_argument("--propulsion", choices=[v.value for v in PropulsionType])
    parser.add_argument("--profile", choices=PROFILE_IDS, default="flattest")
    parser.add_argument("--rider-kg", type=float)
    power = parser.add_mutually_exclusive_group()
    power.add_argument("--power-w", type=float)
    power.add_argument("--power-cv", type=float)
    parser.add_argument("--source-efficiency", type=float)
    parser.add_argument("--step-m", type=float, default=5)
    parser.add_argument("--require-elevation", action="store_true")
    parser.add_argument("--exact-speed", action="store_true")
    parser.add_argument("--max-routes", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(normalize_coordinate_args())
    profile = MobilityProfile.create(args.vehicle, args.propulsion, rider_mass_kg=args.rider_kg,
                                     power_w=args.power_w, power_cv=args.power_cv)
    motion = MotionEngine(step_m=args.step_m, require_elevation=args.require_elevation)
    start = time.perf_counter()
    if args.pbf:
        engine = MobilityEngine.from_pbf(args.pbf, motion=motion, elevation_tiles=args.dem_dir)
    else:
        if args.dem_dir:
            parser.error("--dem-dir is applied during PBF import; --graph expects prepared elevation")
        engine = MobilityEngine(load_graph(args.graph), motion=motion)
    setup_s = time.perf_counter() - start
    start = time.perf_counter()
    engine.prepare(mobility=profile)
    preparation_s = time.perf_counter() - start
    start = time.perf_counter()
    result = engine.plan(args.origin, args.destination, mobility=profile,
                         propulsion=ConstantPower(profile.nominal_power_w, source_efficiency=args.source_efficiency),
                         profile=args.profile, max_routes=args.max_routes,
                         search_config=SearchConfig(exact_speed=args.exact_speed))
    planning_s = time.perf_counter() - start
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.to_json(indent=2), encoding="utf-8")
        print(json.dumps({"output": str(args.output), "setup_s": setup_s, "preparation_s": preparation_s, "planning_s": planning_s,
                          "routes": len(result.routes), "search_complete": result.search_complete,
                          "state_approximated": result.state_approximated,
                          "recommended": None if result.recommended_route is None else result.recommended_route.metrics.to_dict(),
                          "diagnostics": result.diagnostics}))
    else:
        print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
