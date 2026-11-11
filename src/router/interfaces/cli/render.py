"""Renderizacao textual dos resultados da CLI (ADR-033, E6).

Funcoes sobre os DTOs/resultados ja calculados: nao constroem parser, nao
executam comandos e nao acessam a rede. A CLI (``router.interfaces.cli``)
concentra apenas a orquestracao em ``main``; o parser vive em
``router.interfaces.cli.parser`` e os comandos em
``router.interfaces.cli.commands``.
"""

from __future__ import annotations

import sys

from router.app.tooling import (
    INTENSITY_BAND_NAMES,
    MIN_SPEED_MPS,
    Arc,
    BicycleModel,
    CyclistModel,
    RoutePlanResult,
    SimulationStatus,
    TraversalResult,
    bbox_size_km,
)

__all__ = [
    "configure_stdout",
    "print_physical",
    "print_plan",
    "print_snap",
    "print_surface_table",
]


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def print_snap(summary: dict) -> None:
    for key, label in (("origin", "origem"), ("destination", "destino")):
        item = summary[key]
        if item["kind"] == "node":
            print(
                f"{label}: no {item['node_id']} "
                f"(offset {item['offset_m']:.1f} m)"
            )
        else:
            fraction = item["fraction"] or 0.0
            along = item["along_m"] or 0.0
            print(
                f"{label}: arco {item['arc_id']} "
                f"(offset {item['offset_m']:.1f} m, fracao {fraction:.3f}, "
                f"{along:.1f} m)"
            )

    print(f"conectado: {'sim' if summary['connected'] else 'nao'}")
    query = summary["query"]
    print(
        f"grafo de consulta: {query['nodes']} nos, {query['arcs']} arcos, "
        f"{query['segments']} segmentos"
    )


def print_plan(plan: RoutePlanResult) -> None:
    """Resumo textual do plano: zona, box e rotas descobertas."""
    width_km, height_km = bbox_size_km(plan.bbox)
    search_w, search_h = bbox_size_km(plan.search_bbox)
    share = 100.0 * plan.zone_arcs / plan.graph_arcs if plan.graph_arcs else 0.0
    print(f"perfil: {plan.profile_id}")
    print(
        f"grafo: {plan.graph_nodes} nos, {plan.graph_arcs} arcos (cidade inteira)"
    )
    print(
        f"zona de busca: {plan.zone_nodes} nos, {plan.zone_arcs} arcos "
        f"({share:.1f}% do grafo) em {plan.iterations} iteracao(oes)"
    )
    print(f"box de busca (dinamico): {search_w:.2f} x {search_h:.2f} km")
    print(
        f"box final (pinos + rotas): {width_km:.2f} x {height_km:.2f} km "
        "(limita a altimetria)"
    )

    if not plan.reached:
        print("nenhuma rota conectada entre origem e destino")
        return

    trunc = " (truncada)" if plan.truncated else ""
    total_routes = plan.count + plan.rejected_count
    print(
        f"rotas descobertas: {total_routes} "
        f"(fronteira Pareto={plan.pareto_route_count}{trunc})"
    )
    if plan.frontier_truncated:
        print(
            f"aviso: fronteira podada por max_labels_per_node "
            f"({plan.dropped_labels} label(s) nao dominada(s) removida(s)) — "
            "a completude da fronteira nao esta garantida"
        )
    if plan.truncated:
        print(
            "aviso: busca interrompida por max_expansions — "
            "a fronteira de destino pode estar incompleta"
        )
    if plan.rejected_count:
        print(
            f"  viaveis: {plan.count} | excluidas: {plan.rejected_count} "
            "(nao recomendaveis)"
        )
    for index, route in enumerate(plan.routes, start=1):
        metrics = route.assessment.metrics
        marker = " [recomendada]" if route.recommended else ""
        print(
            f"[{index}]{marker} {route.route_id}: "
            f"{metrics.distance_m / 1000.0:.2f} km | "
            f"{metrics.time_s / 60.0:.1f} min | "
            f"subida {metrics.ascent_m:.0f} m | "
            f"descida {metrics.descent_m:.0f} m"
        )
        print(f"    seguranca: {route.safety_state.value}")
        estimate = route.intensity
        bands = " ".join(
            f"{name}={estimate.band_distance_fraction.get(name, 0.0):.0%}"
            for name in INTENSITY_BAND_NAMES
        )
        print(
            f"    intensidade (nao calibrada): RPE medio {estimate.mean_rpe:.1f} "
            f"| faixas(dist): {bands}"
        )
        rpe = route.predicted_rpe
        if rpe is None:
            print(
                f"    esforco: {route.outcome.value} | RPE fisiologico indisponivel "
                f"(status={route.prediction_status.value})"
            )
        else:
            print(
                f"    esforco: {route.outcome.value} | "
                f"RPE previsto {rpe:.1f} | "
                f"preferencia: {route.classification.verdict.value}"
            )
        if route.assessment.limitations:
            print("    limitacoes: " + ", ".join(route.assessment.limitations))

    if plan.rejected:
        print(f"rotas excluidas (nao recomendaveis): {plan.rejected_count}")
        for route in plan.rejected:
            metrics = route.assessment.metrics
            print(
                f"[x] {route.route_id} | seguranca=rejected | "
                f"{metrics.distance_m / 1000.0:.2f} km"
            )
            for reason in route.safety.reasons:
                arc = f" (arco {reason.arc_id})" if reason.arc_id else ""
                print(f"    motivo: {reason.kind.value}: {reason.detail}{arc}")

    for warning in plan.warnings:
        print(f"aviso: {warning}")


def print_physical(
    arc: Arc,
    result: TraversalResult,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
) -> None:
    print(
        f"arco: {arc.id} | {arc.distance_m:.1f} m | "
        f"{arc.segment_count} segmento(s)"
    )
    print(
        f"perfil: subida {result.ascent_m:.1f} m | "
        f"descida {result.descent_m:.1f} m"
    )
    print(
        f"tempo: {result.time_s:.1f} s | "
        f"velocidade media: {result.average_speed_kmh:.1f} km/h"
    )
    print(
        f"velocidade: {result.initial_speed_mps:.2f} -> "
        f"{result.final_speed_mps:.2f} m/s"
    )
    print(
        f"energia: mecanica {result.mechanical_energy_j / 1000.0:.1f} kJ | "
        f"ciclista {result.rider_energy_j / 1000.0:.1f} kJ | "
        f"frenagem {result.braking_energy_j / 1000.0:.1f} kJ"
    )
    print(
        f"massa total: {cyclist.mass_kg + bicycle.mass_kg:.1f} kg | "
        f"potencia: {cyclist.sustainable_power_w:.0f} W | "
        f"eficiencia: {bicycle.drivetrain_efficiency:.2f}"
    )
    if result.stalled:
        print("aviso: o modelo estagnou em baixa velocidade")
    if result.used_min_speed_regularization:
        print(
            "aviso: passo usou o piso de velocidade "
            f"({MIN_SPEED_MPS} m/s) — precisao nao validada no regime de "
            "baixa velocidade"
        )
    if result.result_status is not SimulationStatus.VALID:
        uncertainty = result.elevation_uncertainty
        missing = (
            uncertainty.missing_distance_m if uncertainty is not None else 0.0
        )
        print(
            f"atencao: resultado {result.result_status.value} — "
            f"elevacao ausente em {missing:.1f} m "
            "(hipotese explicita de terreno plano)"
        )


def print_surface_table(summary: dict) -> None:
    surface_lengths: dict[str, float] = summary["length_by_surface_m"]
    total = sum(surface_lengths.values()) or 1.0
    print("superficie:")
    for surface, length in sorted(
        surface_lengths.items(), key=lambda item: item[1], reverse=True
    ):
        share = 100.0 * length / total
        print(f"  {surface:<16} {length / 1000.0:8.3f} km  {share:5.1f}%")

    status_lengths: dict[str, float] = summary.get("length_by_surface_status_m") or {}
    if status_lengths:
        total_status = sum(status_lengths.values()) or 1.0
        print("proveniencia da superficie:")
        for status, length in sorted(
            status_lengths.items(), key=lambda item: item[1], reverse=True
        ):
            share = 100.0 * length / total_status
            print(f"  {status:<16} {length / 1000.0:8.3f} km  {share:5.1f}%")
