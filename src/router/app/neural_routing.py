"""Aprendizado dos objetivos de rota com dados simulados, antes das buscas."""
from router.core.prediction.route_network import FEATURE_NAMES, NeuralRouteModel, route_features, train_route_network
from router.core.search.profiles import route_profile_for, RankingStrategy
from router.core.search.circulation import arc_decision, DEFAULT_POLICY
from router.core.search.astar import is_feasible
from router.core.physics.models import SimulationStatus


def prepare_route_model(graph, traversal, profile, *, check=lambda: None,
                        condition_evaluator=None, circulation_policy=DEFAULT_POLICY):
    """Até 128 arcos × duas velocidades; nenhum resultado Pareto é entrada."""
    samples = []
    stride = max(1, (graph.arc_count + 127) // 128)
    for index, arc in enumerate(graph.arcs.values()):
        if index % stride:
            continue
        check()
        if not traversal.allows_arc(arc) or arc_decision(arc, evaluator=condition_evaluator,
                policy=circulation_policy, access_scopes=traversal.access_scopes, access_from_tags=True).forbids:
            continue
        for speed in (0.0, 5.0):
            part = traversal.traverse(arc, speed)
            if not is_feasible(part) or part.stalled or part.result_status is SimulationStatus.UNAVAILABLE or part.distance_m <= 0:
                continue
            values = dict(time_s=part.time_s, distance_m=part.distance_m,
                          ascent_m=part.ascent_m, energy_j=part.mechanical_energy_j)
            samples.append((arc.id, route_features(part), tuple(values[name] / part.distance_m for name in profile.names)))
    if not samples:
        return None
    return train_route_network(profile.id, profile.names, samples, check=check)


def learned_step_cost(model: NeuralRouteModel, part) -> tuple[float, ...]:
    values = tuple(value * part.distance_m for value in model.predict(route_features(part)))
    profile = route_profile_for(model.profile)
    if profile.ranking_strategy is RankingStrategy.WEIGHTED_NORMALIZED:
        score = sum(profile.weights.for_objective(objective) * value / profile.references.for_objective(objective)
                    for objective, value in zip(profile.objectives, values))
        return score, part.distance_m
    # Mesma ordem de objetivos lexicográficos. Distância exata no último
    # desempate impede ciclos gratuitos quando a previsão do primário é zero.
    return (*values, part.distance_m)
