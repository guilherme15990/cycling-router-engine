"""Comando ``plan``: consulta offline completa (ADR-033, E6).

Zona dinamica + busca + fisica completa + ML (estimativa de intensidade) +
classificacao. O preditor, quando informado, e sempre **local** (artefato em
disco); o comando nunca toca a rede.
"""

from __future__ import annotations

import argparse
import sys

from router.app.tooling import (
    BicycleModel,
    CyclistModel,
    EffortPreference,
    Environment,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelStore,
    PhysicalTraversal,
    SimulationMode,
    SnapOptions,
    dumps,
    load_json,
    load_predictor,
    parse_coord,
    plan_offline_routes,
)
from router.interfaces.cli.render import print_plan

__all__ = ["cmd_plan"]


def cmd_plan(args: argparse.Namespace) -> int:
    """Consulta offline: zona dinamica + busca + fisica completa + ML."""
    graph = load_json(args.path)

    if (args.rpe_min is None) != (args.rpe_max is None):
        print(
            "erro: --rpe-min e --rpe-max devem ser informados juntos",
            file=sys.stderr,
        )
        return 2

    predictor = None
    if args.model_store or args.model_version:
        if not (args.model_store and args.model_version):
            print(
                "erro: --model-store e --model-version devem ser informados juntos",
                file=sys.stderr,
            )
            return 2
        try:
            predictor = load_predictor(
                ModelStore(args.model_store), args.model_version
            )
        except (ModelNotFoundError, ModelIncompatibleError) as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 1

    preference = (
        EffortPreference(acceptable_rpe=(args.rpe_min, args.rpe_max))
        if args.rpe_min is not None
        else None
    )

    if args.prefer_bands is not None:
        bands = tuple(
            item.strip() for item in args.prefer_bands.split(",") if item.strip()
        )
        if not bands:
            print("erro: --prefer-bands nao pode ser vazio", file=sys.stderr)
            return 2
        if args.min_band_fraction is None:
            print(
                "erro: --prefer-bands exige --min-band-fraction",
                file=sys.stderr,
            )
            return 2
        base = preference or EffortPreference()
        preference = EffortPreference(
            borg_scale=base.borg_scale,
            acceptable_rpe=base.acceptable_rpe,
            preferred_intensity_bands=bands,
            min_intensity_band_fraction=args.min_band_fraction,
            intensity_band_basis=args.band_basis,
        )

    max_power = max(args.max_power, args.power or 0.0)
    sustainable = args.power if args.power is not None else min(200.0, max_power)
    try:
        cyclist = CyclistModel(
            mass_kg=args.cyclist_mass,
            max_power_w=max_power,
            sustainable_power_w=sustainable,
            frontal_area_m2=args.frontal_area,
        )
        bicycle = BicycleModel(
            mass_kg=args.bike_mass,
            rolling_resistance=args.crr,
            drag_coefficient=args.cd,
            drivetrain_efficiency=args.efficiency,
        )
        environment = Environment(
            air_density_kg_m3=args.air_density,
            headwind_mps=args.headwind,
            safe_max_speed_mps=(
                args.safe_max_speed if args.safe_max_speed > 0 else None
            ),
        )
        traversal = PhysicalTraversal(
            step_m=args.step,
            dismount_speed_mps=args.dismount_speed,
            mode=SimulationMode(args.mode),
        )
        snap_options = SnapOptions(
            max_distance_m=args.max_distance,
            node_preference_m=args.node_preference,
            node_offset_tolerance_m=args.node_tolerance,
            min_split_distance_m=args.min_split,
            allow_dismount=not args.no_dismount,
        )
        plan = plan_offline_routes(
            graph,
            parse_coord(args.origin),
            parse_coord(args.destination),
            cyclist=cyclist,
            bicycle=bicycle,
            profile=args.profile,
            snap_options=snap_options,
            traversal=traversal,
            environment=environment,
            power_w=args.power,
            predictor=predictor,
            preference=preference,
            max_routes=args.max_routes,
        )
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(dumps(plan.to_contract(), indent=2))
    else:
        print_plan(plan)
    return 0 if plan.reached else 1
