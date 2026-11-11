"""Avaliacao fisica completa de uma rota e classificacao (Camada 05).

Este modulo fecha o pipeline declarado no ADR-026 §3/§5 e no ADR-025 §10:

1. as candidatas passam pelas **mesmas** restricoes de circulacao;
2. o **simulador fisico completo** avalia cada rota (nao o estimador aproximado);
3. ``EffortFeatures`` alimentam o **preditor fisiologico**;
4. o sistema **classifica** a rota de forma explicita.

Ele **nao** reimplementa fisica nem ML: apenas orquestra componentes ja
existentes (``physical.PhysicalTraversal``, ``physical.extract_effort_features``,
``ml.predict_effort``, ``search.validate_route``). O resultado carrega a
qualidade e a limitacao de cada etapa, de modo que a ausencia de um modelo
fisiologico **calibrado** nunca vire valor inventado: ela aparece como estado
explicito (``PredictionStatus``) e como limitacao declarada.

Classificacao (``AssessmentOutcome``), sempre derivada de sinais ja existentes —
nenhum limiar ou parametro novo:

* ``REJECTED`` — a rota viola restricao de circulacao aplicavel e registrada;
* ``PHYSIOLOGICAL`` — um modelo fisiologico **calibrado** produziu a previsao;
* ``PHYSICAL_ONLY`` — so ha fisica; a fisiologia e declarada indisponivel.

Ref.: ADR-026 (§3, §5, §6), ADR-025 (§10, decisao 07), ADR-024 (restricoes).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from router.core.data.graph.model import Arc, GraphError, RoutingGraph
from router.core.prediction.ml import EffortPredictor, predict_effort
from router.core.physics.bicycle import BicycleModel
from router.core.physics.cyclist import CyclistModel
from router.core.physics.effort import EffortFeatures, extract_effort_features
from router.core.physics.environment import Environment
from router.core.physics.models import (
    PhysicsRouteMetrics,
    SimulationState,
    SimulationStatus,
    TraversalPath,
)
from router.core.physics.traversal import PhysicalTraversal
from router.core.physiology import (
    PhysiologicalPrediction,
    PredictionContext,
    RiderProfile,
)
from router.core.search.candidates import RouteCandidate
from router.core.search.circulation import (
    CirculationPolicy,
    ConditionEvaluator,
    DEFAULT_POLICY,
    RouteValidation,
    validate_route,
)
from router.core.search.route import SearchRoute

from .diagnostics import EnergyConsistency, energy_consistency

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from router.core.prediction.intensity import IntensityEstimate


class RouteAssessmentError(ValueError):
    """Erro de **dados** na avaliacao de rota.

    Sinaliza rota vazia, arco inexistente, arco ausente (``None``) ou
    descontinuidade entre arcos consecutivos. Nao mascara falha numerica: um
    resultado fisico degradado e propagado, nao levantado como erro.
    """


class AssessmentOutcome(str, Enum):
    """Classificacao explicita de uma rota avaliada."""

    PHYSIOLOGICAL = "physiological"
    PHYSICAL_ONLY = "physical_only"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RouteAssessment:
    """Resultado da avaliacao fisica completa de uma rota.

    ``path`` e a simulacao autoritativa (simulador completo); ``features`` e a
    extracao deterministica; ``prediction`` e a previsao fisiologica (podendo
    declarar ausencia); ``circulation`` e a validacao independente de
    legalidade; ``intensity`` e a **estimativa de intensidade** computacional
    (RPE + faixas, **nao calibrada fisiologicamente** — ADR-028). Nenhum campo
    reinterpreta a fisica (``energy`` e o diagnostico energetico opcional,
    ADR-029 D7, reutilizando o balanco ja calculado).
    """

    arc_ids: tuple[str, ...]
    path: TraversalPath
    features: EffortFeatures
    prediction: PhysiologicalPrediction
    circulation: RouteValidation
    intensity: "IntensityEstimate"
    energy: EnergyConsistency | None = None

    @property
    def metrics(self) -> PhysicsRouteMetrics:
        """Metricas fisicas completas do percurso (fonte: simulador completo)."""
        return self.path.metrics

    @property
    def result_status(self) -> SimulationStatus:
        return self.path.result_status

    @property
    def stalled(self) -> bool:
        """True se algum arco do percurso estagnou na simulacao."""
        return any(result.stalled for result in self.path.results)

    @property
    def physiologically_available(self) -> bool:
        """True apenas se um modelo fisiologico **calibrado** previu RPE/FC."""
        return self.prediction.status.is_available

    @property
    def outcome(self) -> AssessmentOutcome:
        """Classificacao derivada de sinais explicitos (sem limiar novo)."""
        if not self.circulation.compliant:
            return AssessmentOutcome.REJECTED
        if self.prediction.status.is_available:
            return AssessmentOutcome.PHYSIOLOGICAL
        return AssessmentOutcome.PHYSICAL_ONLY

    @property
    def outcome_reason(self) -> str:
        outcome = self.outcome
        if outcome is AssessmentOutcome.REJECTED:
            return "rota viola restricoes de circulacao aplicaveis"
        if outcome is AssessmentOutcome.PHYSIOLOGICAL:
            return "previsao fisiologica calibrada disponivel"
        return (
            "previsao fisiologica indisponivel "
            f"(status={self.prediction.status.value})"
        )

    @property
    def limitations(self) -> tuple[str, ...]:
        """Limitacoes declaradas (codigos estaveis; nada e ocultado).

        Codigos: ``circulation_violation``, ``circulation_unresolved``,
        ``dismount_used``, ``simulation_degraded``, ``simulation_unavailable``,
        ``min_speed_regularization``, ``elevation_missing``, ``stalled``,
        ``intensity_not_calibrated`` (a estimativa de intensidade e sempre
        declarada nao calibrada — ADR-029 D6) e ``physiology_<status>`` (a
        ausencia da fisiologia e sempre declarada).
        """
        codes: list[str] = []
        if not self.circulation.compliant:
            codes.append("circulation_violation")
        if self.circulation.unresolved:
            codes.append("circulation_unresolved")
        if self.circulation.dismount_used:
            codes.append("dismount_used")
        if self.path.result_status is SimulationStatus.DEGRADED:
            codes.append("simulation_degraded")
        elif self.path.result_status is SimulationStatus.UNAVAILABLE:
            codes.append("simulation_unavailable")
        if self.features.used_min_speed_regularization:
            codes.append("min_speed_regularization")
        uncertainty = self.path.elevation_uncertainty
        if uncertainty is None or uncertainty.has_gap:
            codes.append("elevation_missing")
        if self.stalled:
            codes.append("stalled")
        # A estimativa de intensidade e sempre computacional e nao calibrada
        # (ADR-029 D6): nunca apresentada como zona cardiaca.
        codes.append("intensity_not_calibrated")
        if not self.prediction.status.is_available:
            codes.append(f"physiology_{self.prediction.status.value}")
        return tuple(codes)

    def to_dict(self) -> dict[str, object]:
        return {
            "arcs": list(self.arc_ids),
            "arc_count": len(self.arc_ids),
            "outcome": self.outcome.value,
            "outcome_reason": self.outcome_reason,
            "physiologically_available": self.physiologically_available,
            "limitations": list(self.limitations),
            "result_status": self.path.result_status.value,
            "stalled": self.stalled,
            "metrics": self.metrics.to_dict(),
            "features": self.features.to_dict(),
            "prediction": self.prediction.to_dict(),
            "intensity": self.intensity.to_dict(),
            "circulation": self.circulation.to_dict(),
            "energy": None if self.energy is None else self.energy.to_dict(),
        }


def _resolve_arcs(
    graph: RoutingGraph, arc_ids: Sequence[str | None]
) -> list[Arc]:
    """Resolve arcos da rota; erro explicito de dados se algum faltar."""
    resolved: list[Arc] = []
    for index, arc_id in enumerate(arc_ids):
        if arc_id is None:
            raise RouteAssessmentError(
                f"rota contem arco ausente (None) na posicao {index}"
            )
        try:
            resolved.append(graph.get_arc(arc_id))
        except GraphError as exc:
            raise RouteAssessmentError(
                f"arco desconhecido na posicao {index}: {arc_id!r}"
            ) from exc
    return resolved


def _check_continuity(arcs: Sequence[Arc]) -> None:
    """Garante que arcos consecutivos compartilham no (rota continua)."""
    for index in range(1, len(arcs)):
        previous, current = arcs[index - 1], arcs[index]
        if previous.target != current.source:
            raise RouteAssessmentError(
                "rota descontinua entre as posicoes "
                f"{index - 1} e {index}: {previous.id!r} termina em "
                f"{previous.target!r}, mas {current.id!r} comeca em "
                f"{current.source!r}"
            )


def assess_route(
    graph: RoutingGraph,
    arc_ids: Sequence[str | None],
    *,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    traversal: PhysicalTraversal | None = None,
    environment: Environment | None = None,
    initial_state: SimulationState | None = None,
    power_w: float | None = None,
    rider: RiderProfile | None = None,
    context: PredictionContext | None = None,
    predictor: EffortPredictor | None = None,
    condition_evaluator: ConditionEvaluator | None = None,
    circulation_policy: CirculationPolicy = DEFAULT_POLICY,
) -> RouteAssessment:
    """Avalia uma rota com o **simulador fisico completo** e a classifica.

    A rota e validada contra a circulacao (mesmo contrato da busca), simulada
    por ``PhysicalTraversal.traverse_path`` (nao pelo estimador aproximado),
    convertida em ``EffortFeatures`` e enviada ao preditor fisiologico. Nenhum
    valor fisiologico e inventado quando o modelo nao esta calibrado.

    Args:
        graph: Grafo de roteamento de onde os arcos sao resolvidos.
        arc_ids: Sequencia ordenada de ``arc.id`` (direcional).
        cyclist: Modelo do ciclista.
        bicycle: Modelo da bicicleta.
        traversal: Simulador fisico; padrao ``PhysicalTraversal()`` (modo busca).
        environment: Ambiente; padrao do simulador.
        initial_state: Estado dinamico na origem; padrao: repouso.
        power_w: Potencia no pedivela; padrao do ciclista.
        rider: Perfil fisiologico (opcional).
        context: Contexto da predicao (opcional).
        predictor: Preditor fisiologico; ``None`` => ``UNAVAILABLE`` explicito.
        condition_evaluator: Avaliador de condicoes de restricao de circulacao.
        circulation_policy: Politica para vereditos ``UNKNOWN`` (conservadora).

    Returns:
        ``RouteAssessment`` com fisica, features, previsao, circulacao e
        classificacao. A ausencia de modelo calibrado fica explicita.

    Raises:
        RouteAssessmentError: rota vazia, arco inexistente/ausente ou descontinua.
    """
    ids = tuple(arc_ids)
    if not ids:
        raise RouteAssessmentError("rota sem arcos nao pode ser avaliada")
    arcs = _resolve_arcs(graph, ids)
    _check_continuity(arcs)

    simulator = traversal if traversal is not None else PhysicalTraversal()
    path = simulator.traverse_path(
        arcs,
        cyclist,
        bicycle,
        initial_state=initial_state,
        environment=environment,
        power_w=power_w,
    )

    features = extract_effort_features(path)
    prediction = predict_effort(features, rider, context, predictor=predictor)
    circulation = validate_route(
        graph,
        ids,
        evaluator=condition_evaluator,
        policy=circulation_policy,
    )

    # Estimativa de intensidade (ADR-028): computacional e NAO calibrada. Nao
    # preenche a previsao fisiologica; e um artefato distinto e rotulado.
    from router.core.prediction.intensity import estimate_intensity, segments_from_traversal

    intensity = estimate_intensity(
        segments_from_traversal(path, arcs),
        mass_kg=cyclist.mass_kg + bicycle.mass_kg,
        ftp_w=cyclist.sustainable_power_w,
    )

    # Diagnostico energetico (ADR-029 D7): agrega o balanco ja calculado pelo
    # simulador; nenhuma re-simulacao, nenhuma equacao nova.
    energy = energy_consistency(path, cyclist.mass_kg + bicycle.mass_kg)

    return RouteAssessment(
        arc_ids=tuple(arc.id for arc in arcs),
        path=path,
        features=features,
        prediction=prediction,
        circulation=circulation,
        intensity=intensity,
        energy=energy,
    )


def assess_search_route(
    graph: RoutingGraph, route: SearchRoute, **kwargs
) -> RouteAssessment:
    """Avalia uma ``SearchRoute`` da busca (Camada 03) pelo simulador completo."""
    return assess_route(graph, route.arc_ids, **kwargs)


def assess_candidate(
    graph: RoutingGraph, candidate: RouteCandidate, **kwargs
) -> RouteAssessment:
    """Avalia uma ``RouteCandidate`` (identidade + rota) pelo simulador completo."""
    return assess_route(graph, candidate.route.arc_ids, **kwargs)


def assess_candidates(
    graph: RoutingGraph,
    candidates: Iterable[RouteCandidate],
    **kwargs,
) -> tuple[RouteAssessment, ...]:
    """Avalia um conjunto de candidatas, preservando a ordem de entrada."""
    return tuple(
        assess_candidate(graph, candidate, **kwargs)
        for candidate in candidates
    )
