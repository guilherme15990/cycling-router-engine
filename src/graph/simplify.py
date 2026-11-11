"""Simplificacao topologica: contrai nos de grau 2 sem perder atributos.

O caso tipico no OSM e uma avenida dividida em varias ``way`` s que so se
encontram em pontos intermediarios. Topologicamente esses pontos nao sao
decisoes de navegacao e podem sumir:

    A --- B --- C --- D   ->   A ----------- D

Mas o arco consolidado continua guardando os segmentos originais, cada um
com distancia, superficie, perfil de elevacao e restricoes proprias. Se A-B e
asfalto e B-C e gravel, a mudanca sobrevive na lista de segmentos.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Hashable

from .model import RoutingGraph, Arc, GraphError


@dataclass(frozen=True)
class SimplifyOptions:
    """``keep_nodes`` protege pontos que precisam existir (ex.: POIs, snapping)."""

    keep_nodes: frozenset[Hashable] = frozenset()


@dataclass(frozen=True)
class SimplifyStats:
    nodes_before: int
    nodes_after: int
    arcs_before: int
    arcs_after: int
    contracted_nodes: int
    chains: int
    multi_segment_arcs: int

    @property
    def node_reduction(self) -> float:
        if not self.nodes_before:
            return 0.0
        return 1.0 - self.nodes_after / self.nodes_before


def simplify_graph(
    graph: RoutingGraph,
    options: SimplifyOptions | None = None,
) -> tuple[RoutingGraph, SimplifyStats]:
    """Retorna um novo grafo com os nos de passagem contraidos."""
    options = options or SimplifyOptions()
    incoming, outgoing = _incidence(graph)
    candidates = _contraction_candidates(graph, incoming, outgoing)
    candidates -= set(options.keep_nodes)
    candidates -= _cycle_nodes(graph, candidates)

    anchors = [node_id for node_id in graph.nodes if node_id not in candidates]
    contracted = graph.node_count - len(anchors)

    simplified = RoutingGraph()
    simplified.metadata = dict(graph.metadata)
    simplified.metadata["simplified"] = True

    for node_id in anchors:
        simplified.add_node(graph.get_node(node_id))

    consumed: set[str] = set()
    chains = 0

    for anchor in sorted(anchors, key=str):
        for arc in sorted(outgoing.get(anchor, ()), key=lambda item: item.id):
            if arc.id in consumed:
                continue

            segments = list(arc.segments)
            consumed.add(arc.id)
            current = arc
            node = arc.target

            while node in candidates:
                next_arcs = [
                    candidate
                    for candidate in outgoing.get(node, ())
                    if candidate.id not in consumed and candidate.target != current.source
                ]
                if not next_arcs:
                    next_arcs = [
                        candidate
                        for candidate in outgoing.get(node, ())
                        if candidate.id not in consumed
                    ]
                if not next_arcs:
                    break

                following = sorted(next_arcs, key=lambda item: item.id)[0]
                consumed.add(following.id)
                segments.extend(following.segments)
                current = following
                node = following.target

            if node == anchor:
                # Ciclo inteiramente contraivel: nao deveria chegar aqui.
                continue

            merged = Arc(
                id=f"e{chains}",
                source=anchor,
                target=node,
                segments=tuple(segments),
            )
            simplified.add_arc(merged)
            chains += 1

    leftover = set(graph.arcs) - consumed
    if leftover:
        raise GraphError(
            f"simplificacao incompleta: {len(leftover)} arco(s) sem cadeia, "
            f"ex.: {sorted(leftover)[:3]}"
        )

    # Restricoes de conversao sao preservadas: elas referenciam vias OSM
    # (from/to/via way ids), nao arcos, entao a contracao nao as invalida.
    for restriction in graph.turn_restrictions.values():
        simplified.add_turn_restriction(restriction)

    stats = SimplifyStats(
        nodes_before=graph.node_count,
        nodes_after=simplified.node_count,
        arcs_before=graph.arc_count,
        arcs_after=simplified.arc_count,
        contracted_nodes=contracted,
        chains=chains,
        multi_segment_arcs=sum(
            1 for arc in simplified.arcs.values() if arc.segment_count > 1
        ),
    )
    simplified.metadata["simplification"] = {
        "contracted_nodes": stats.contracted_nodes,
        "chains": stats.chains,
        "multi_segment_arcs": stats.multi_segment_arcs,
    }
    return simplified, stats


# --------------------------------------------------------------------------
# Identificacao dos nos contraiveis
# --------------------------------------------------------------------------
def _incidence(
    graph: RoutingGraph,
) -> tuple[dict[object, list[Arc]], dict[object, list[Arc]]]:
    incoming: dict[object, list[Arc]] = defaultdict(list)
    outgoing: dict[object, list[Arc]] = defaultdict(list)
    for arc in graph.arcs.values():
        outgoing[arc.source].append(arc)
        incoming[arc.target].append(arc)
    return incoming, outgoing


def _contraction_candidates(
    graph: RoutingGraph,
    incoming: dict[object, list[Arc]],
    outgoing: dict[object, list[Arc]],
) -> set[object]:
    parallel_endpoints: set[object] = set()
    multiplicity = Counter((arc.source, arc.target) for arc in graph.arcs.values())
    for (source, target), count in multiplicity.items():
        if count > 1:
            parallel_endpoints.add(source)
            parallel_endpoints.add(target)

    candidates: set[object] = set()
    for node_id in graph.nodes:
        if node_id in parallel_endpoints:
            continue

        ins = incoming.get(node_id, ())
        outs = outgoing.get(node_id, ())

        # Passagem pura: mesmo numero de entradas e saidas (1 em one-way,
        # 2 em via de mao dupla), sempre com dois vizinhos distintos.
        if len(ins) != len(outs) or len(ins) not in (1, 2):
            continue

        neighbors = {arc.source for arc in ins} | {arc.target for arc in outs}
        if len(neighbors) != 2:
            continue
        if len({arc.source for arc in ins}) != len(ins):
            continue
        if len({arc.target for arc in outs}) != len(outs):
            continue

        candidates.add(node_id)

    return candidates


def _cycle_nodes(graph: RoutingGraph, candidates: set[Hashable]) -> set[Hashable]:
    """Ciclos formados so por candidatos precisam manter um no quebra-ciclo."""
    adjacency: dict[Hashable, set[Hashable]] = {node: set() for node in candidates}
    for arc in graph.arcs.values():
        if arc.source in candidates and arc.target in candidates:
            adjacency[arc.source].add(arc.target)
            adjacency[arc.target].add(arc.source)

    visited: set[Hashable] = set()
    cycles: set[Hashable] = set()

    for start in candidates:
        if start in visited:
            continue
        component: list[Hashable] = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            stack.extend(adjacency[node] - visited)

        if len(component) >= 3 and all(len(adjacency[node]) == 2 for node in component):
            cycles.update(component)

    return cycles
