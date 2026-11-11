"""Validacao da consistencia do grafo ciclavel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from geo import line_length_m
from .model import RoutingGraph
from .surface import Surface, SurfaceStatus

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    """Um problema encontrado na validacao do grafo.

    Attributes:
        severity: ``"error"`` (inconsistencia certa) ou ``"warning"``
            (suspeita; muitos avisos sao normais em bbox recortada).
        code: Identificador estavel do problema (ex.: ``"dead-end"``).
        message: Descricao legivel.
        arc_id: Arco relacionada, quando aplicavel.
        node_id: No relacionado, quando aplicavel.
    """

    severity: Severity
    code: str
    message: str
    arc_id: str | None = None
    node_id: object | None = None

    def __str__(self) -> str:
        location = self.arc_id or self.node_id
        prefix = f"[{self.severity}] {self.code}"
        if location is not None:
            prefix += f" ({location})"
        return f"{prefix}: {self.message}"


def validate_graph(
    graph: RoutingGraph,
    elevation_tolerance_m: float = 1.0,
    distance_tolerance_ratio: float = 0.02,
) -> list[Issue]:
    """Verifica a consistencia do grafo e devolve a lista de problemas.

    Checo arcos orfas, ausencia de segmentos, distancia nao positiva,
    elevacao negativa, geometria ausente, divergencia entre distancia e
    geometria, inconsistencia entre superficie e proveniencia, divergencia
    entre perfil e nos, nos isolados, becos sem saida e componentes desconexas.
    Nao lanca excecao: reporta tudo e deixa a decisao para quem chama.

    Args:
        graph: Grafo a validar.
        elevation_tolerance_m: Tolerancia (m) entre o delta do perfil e o delta
            dos nos das pontas antes de virar aviso.
        distance_tolerance_ratio: Tolerancia relativa entre ``distance_m`` do
            segmento e o comprimento medido da geometria.

    Returns:
        Lista de :class:`Issue` (vazia quando o grafo esta consistente).

    Example:
        >>> issues = validate_graph(graph)
        >>> [str(issue) for issue in issues][:1]  # doctest: +SKIP
        ['[warning] disconnected: grafo com 2 componentes desconexas ...']
    """
    issues: list[Issue] = []

    for arc in graph.arcs.values():
        if arc.source not in graph.nodes:
            issues.append(
                Issue("error", "arc-missing-source", "origem inexistente", arc_id=arc.id)
            )
        if arc.target not in graph.nodes:
            issues.append(
                Issue("error", "arc-missing-target", "destino inexistente", arc_id=arc.id)
            )
        if not arc.segments:
            issues.append(
                Issue("error", "arc-without-segments", "arco sem segmentos", arc_id=arc.id)
            )
        if len(arc.geometry) < 2:
            issues.append(
                Issue("warning", "arc-geometry", "geometria ausente", arc_id=arc.id)
            )

        for segment in arc.segments:
            _validate_segment(segment, arc.id, issues, distance_tolerance_ratio)

        # Elevacao do perfil x elevacao dos nos das pontas.
        if arc.has_elevation:
            source = graph.nodes.get(arc.source)
            target = graph.nodes.get(arc.target)
            if (
                source
                and target
                and source.elevation_m is not None
                and target.elevation_m is not None
            ):
                profile = arc.elevation_profile
                if profile[0].elevation_m is None or profile[-1].elevation_m is None:
                    continue
                expected = target.elevation_m - source.elevation_m
                actual = profile[-1].elevation_m - profile[0].elevation_m
                if abs(expected - actual) > elevation_tolerance_m:
                    issues.append(
                        Issue(
                            "warning",
                            "arc-elevation-mismatch",
                            f"perfil ({actual:.1f} m) difere dos nos ({expected:.1f} m)",
                            arc_id=arc.id,
                        )
                    )

    in_degree_map: dict[object, int] = {node_id: 0 for node_id in graph.nodes}
    out_degree_map: dict[object, int] = {node_id: 0 for node_id in graph.nodes}
    for arc in graph.arcs.values():
        out_degree_map[arc.source] = out_degree_map.get(arc.source, 0) + 1
        in_degree_map[arc.target] = in_degree_map.get(arc.target, 0) + 1

    for node_id in graph.nodes:
        out_degree = out_degree_map[node_id]
        in_degree = in_degree_map[node_id]
        if out_degree == 0 and in_degree == 0:
            issues.append(
                Issue("warning", "isolated-node", "no sem arcos", node_id=node_id)
            )
        elif out_degree == 0 or in_degree == 0:
            issues.append(
                Issue(
                    "warning",
                    "dead-end",
                    f"no com grau de entrada {in_degree} e saida {out_degree}",
                    node_id=node_id,
                )
            )

    components = _weak_components(graph)
    if components > 1:
        issues.append(
            Issue(
                "warning",
                "disconnected",
                f"grafo com {components} componentes desconexas (nao ha ciclovia ligando todas)",
            )
        )

    return issues


def _validate_segment(segment, arc_id: str, issues: list[Issue], ratio: float) -> None:
    if segment.distance_m <= 0:
        issues.append(
            Issue("error", "segment-distance", "distancia nao positiva", arc_id=arc_id)
        )
    if segment.elevation_gain_m < 0 or segment.elevation_loss_m < 0:
        issues.append(
            Issue(
                "error",
                "segment-elevation-negative",
                "ganho/perda de elevacao negativos",
                arc_id=arc_id,
            )
        )

    if len(segment.geometry) >= 2:
        measured = line_length_m(segment.geometry)
        if measured > 0 and abs(measured - segment.distance_m) > max(
            2.0, measured * ratio
        ):
            issues.append(
                Issue(
                    "warning",
                    "segment-distance-mismatch",
                    f"distancia {segment.distance_m:.1f} m difere da geometria "
                    f"({measured:.1f} m)",
                    arc_id=arc_id,
                )
            )

    if segment.surface_status is SurfaceStatus.MAPPED and segment.surface is Surface.UNKNOWN:
        issues.append(
            Issue(
                "warning",
                "surface-inconsistent",
                "superficie marcada como mapeada, mas classificada como unknown",
                arc_id=arc_id,
            )
        )
    if segment.surface_status is SurfaceStatus.UNKNOWN and segment.surface is not Surface.UNKNOWN:
        issues.append(
            Issue(
                "warning",
                "surface-inconsistent",
                "superficie classificada, mas sem status de mapeamento",
                arc_id=arc_id,
            )
        )


def _weak_components(graph: RoutingGraph) -> int:
    parent: dict[object, object] = {node_id: node_id for node_id in graph.nodes}

    def find(value: object) -> object:
        root = value
        while parent[root] != root:
            root = parent[root]
        while parent[value] != root:
            parent[value], value = root, parent[value]
        return root

    for arc in graph.arcs.values():
        left = find(arc.source)
        right = find(arc.target)
        if left != right:
            parent[left] = right

    roots = {find(node_id) for node_id in graph.nodes}
    return len(roots)
