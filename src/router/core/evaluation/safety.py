"""Estado de seguranca de uma rota avaliada (Camada 05, ADR-029 D2-D5).

Este modulo fecha a Camada 05 **sobre** ``evaluation.assessment`` sem
reimplementar nada: ele **consome** um ``RouteAssessment`` (fisica completa,
``EffortFeatures``, previsao fisiologica, circulacao e estimativa de intensidade)
e atribui um **estado de seguranca operacional** explicito:

* ``VALIDATED`` — fisicamente valida, legalmente conforme e **sem** limitacao
  fisica material;
* ``DEGRADED``  — fisicamente valida e legal, **com** limitacao fisica declarada
  (incerteza altimetrica, ``stalled``, regularizacao de baixa velocidade,
  simulacao indisponivel);
* ``REJECTED``  — proibicao de circulacao **aplicavel**, restricao de circulacao
  **nao resolvida** (conservadora) **ou** inviabilidade fisica **comprovada**.

Regras (ADR-029):

* o estado e **ortogonal** a ``AssessmentOutcome`` (que trata disponibilidade
  fisiologica + circulacao, nao seguranca);
* **ausencia de modelo fisiologico calibrado NAO rebaixa** a seguranca (D2): ela
  aparece como **advertencia** (``advisories``), nunca como motivo de estado;
* a **estimativa de intensidade** e sempre uma advertencia
  (``intensity_not_calibrated``, D6) e nunca altera o estado;
* **toda** rejeicao carrega um ``SafetyReason`` auditavel (D5) — nada e silencioso;
* a **ausencia de regra OSM nao e proibicao nem restricao nao resolvida** (D3):
  apenas proibicao explicita ou ``unresolved`` conservador geram ``REJECTED``;
* **nenhuma** excecao e convertida automaticamente em prova de inviabilidade
  (D5): a impossibilidade fisica e detectada apenas por sinais observaveis
  (metrica nao finita, velocidade negativa, distancia nao positiva).

O modulo **nao** recalcula fisica, fisiologia nem estimativa (D4): ele le os
campos ja produzidos por ``assess_route``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from .assessment import RouteAssessment

#: Prefixo dos codigos de limitacao fisiologica (``physiology_<status>``).
_PHYSIOLOGY_PREFIX = "physiology_"


class RouteSafetyState(str, Enum):
    """Estado de seguranca operacional de uma rota (ADR-029 D2)."""

    VALIDATED = "validated"
    DEGRADED = "degraded"
    REJECTED = "rejected"


class ReasonKind(str, Enum):
    """Motivo **estruturado** de um estado (nunca silencioso; sempre auditavel).

    Os motivos de **rejeicao** separam legalidade de viabilidade fisica; os de
    **degradacao** sao limitacoes nao impeditivas; os **informativos** nunca
    alteram o estado.
    """

    # -- legalidade (D3) ---------------------------------------------------
    CIRCULATION_VIOLATION = "circulation_violation"
    CIRCULATION_UNRESOLVED = "circulation_unresolved"
    # -- viabilidade fisica: impossibilidade comprovada (D5) ---------------
    NON_FINITE_METRIC = "non_finite_metric"
    NEGATIVE_SPEED = "negative_speed"
    NON_POSITIVE_DISTANCE = "non_positive_distance"
    # -- incerteza / limitacao fisica nao impeditiva (D5) ------------------
    ELEVATION_MISSING = "elevation_missing"
    SIMULATION_DEGRADED = "simulation_degraded"
    SIMULATION_UNAVAILABLE = "simulation_unavailable"
    MIN_SPEED_REGULARIZATION = "min_speed_regularization"
    STALLED = "stalled"
    # -- fisiologia / estimativa: informativos (D2/D6) ---------------------
    PHYSIOLOGY_UNAVAILABLE = "physiology_unavailable"
    PHYSIOLOGY_MODEL_NOT_CALIBRATED = "physiology_model_not_calibrated"
    PHYSIOLOGY_INSUFFICIENT_DATA = "physiology_insufficient_data"
    PHYSIOLOGY_CONTRACT_INCOMPATIBLE = "physiology_contract_incompatible"
    INTENSITY_NOT_CALIBRATED = "intensity_not_calibrated"
    # -- avaliacao ausente: inelegibilidade, NAO prova de inviabilidade (D5)
    EVALUATION_MISSING = "evaluation_missing"


#: Codigos de limitacao que **rejeitam** (proibicao / nao resolvido).
_REJECTING_LIMITATIONS = frozenset(
    {"circulation_violation", "circulation_unresolved"}
)

#: Mapeamento dos codigos de incerteza/limitacao fisica nao impeditiva.
_DEGRADING_LIMITATIONS: dict[str, ReasonKind] = {
    "elevation_missing": ReasonKind.ELEVATION_MISSING,
    "simulation_degraded": ReasonKind.SIMULATION_DEGRADED,
    "simulation_unavailable": ReasonKind.SIMULATION_UNAVAILABLE,
    "min_speed_regularization": ReasonKind.MIN_SPEED_REGULARIZATION,
    "stalled": ReasonKind.STALLED,
}

#: Mapeamento dos codigos fisiologicos (informativos).
_PHYSIOLOGY_REASONS: dict[str, ReasonKind] = {
    "physiology_unavailable": ReasonKind.PHYSIOLOGY_UNAVAILABLE,
    "physiology_model_not_calibrated": (
        ReasonKind.PHYSIOLOGY_MODEL_NOT_CALIBRATED
    ),
    "physiology_insufficient_data": ReasonKind.PHYSIOLOGY_INSUFFICIENT_DATA,
    "physiology_contract_incompatible": (
        ReasonKind.PHYSIOLOGY_CONTRACT_INCOMPATIBLE
    ),
}


@dataclass(frozen=True, slots=True)
class SafetyReason:
    """Motivo estruturado de um estado (auditavel).

    ``kind`` e o codigo estavel; ``detail`` e a evidencia textual; ``arc_id``
    (quando presente) localiza o passo afetado.
    """

    kind: ReasonKind
    detail: str
    arc_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "arc_id": self.arc_id,
        }


@dataclass(frozen=True, slots=True)
class RouteSafetyAssessment:
    """Estado de seguranca de uma rota e seus motivos (ADR-029 D2).

    ``reasons`` sao os motivos que **determinam** o estado (vazio apenas em
    ``VALIDATED``); ``advisories`` sao **informativos** (fisiologia ausente,
    estimativa nao calibrada) e **nunca** alteram o estado.
    """

    state: RouteSafetyState
    reasons: tuple[SafetyReason, ...] = ()
    advisories: tuple[SafetyReason, ...] = ()

    @property
    def eligible(self) -> bool:
        """True se a rota pode ser **recomendada/apresentada** (nao REJECTED)."""
        return self.state is not RouteSafetyState.REJECTED

    @property
    def validated(self) -> bool:
        return self.state is RouteSafetyState.VALIDATED

    @property
    def degraded(self) -> bool:
        return self.state is RouteSafetyState.DEGRADED

    @property
    def rejected(self) -> bool:
        return self.state is RouteSafetyState.REJECTED

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "eligible": self.eligible,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "advisories": [reason.to_dict() for reason in self.advisories],
        }


def _describe(decision) -> str:
    """Evidencia textual de um ``Decision`` de circulacao."""
    detail = f"veredito={decision.verdict.value} motivo={decision.reason.value}"
    if decision.restriction_id is not None:
        detail += f" restricao={decision.restriction_id}"
    return detail


def _physiology_reason(code: str) -> SafetyReason:
    """Converte ``physiology_<status>`` num ``SafetyReason`` informativo."""
    kind = _PHYSIOLOGY_REASONS.get(code)
    if kind is None:
        # Status fisiologico desconhecido: mantem-se informativo e explicito.
        kind = ReasonKind.PHYSIOLOGY_UNAVAILABLE
    return SafetyReason(kind, f"previsao fisiologica: {code}")


def classify_safety(assessment: RouteAssessment) -> RouteSafetyAssessment:
    """Classifica a **seguranca** de uma rota ja avaliada (sem recalcular nada).

    Le apenas ``assessment.circulation`` (legalidade), ``assessment.path`` e
    ``assessment.metrics`` (viabilidade fisica comprovada) e
    ``assessment.limitations`` (incerteza/limitacao). A fisiologia e a estimativa
    de intensidade entram como **advertencias** (D2/D6) e nao alteram o estado.

    Returns:
        ``RouteSafetyAssessment`` com estado, motivos determinantes e advertencias.
    """
    reasons: list[SafetyReason] = []
    advisories: list[SafetyReason] = []
    rejected = False
    degraded = False

    # 1) Legalidade (D3): proibicao explicita ou nao resolvida rejeita. A
    #    ausencia de regra (NO_RULE, legality_asserted=False) NAO e motivo. A
    #    restricao nao resolvida rejeita mesmo sob politica permissiva (D3).
    circulation = assessment.circulation
    proven = tuple(d for d in circulation.violations if not d.unresolved)
    unresolved = circulation.unresolved
    if proven:
        reasons.append(
            SafetyReason(
                ReasonKind.CIRCULATION_VIOLATION,
                _describe(proven[0]),
                proven[0].restriction_id,
            )
        )
        rejected = True
    if unresolved:
        reasons.append(
            SafetyReason(
                ReasonKind.CIRCULATION_UNRESOLVED,
                _describe(unresolved[0]),
                unresolved[0].restriction_id,
            )
        )
        rejected = True

    # 2) Viabilidade fisica comprovada (D5): apenas sinais observaveis.
    path = assessment.path
    metrics = assessment.metrics
    finite = all(
        math.isfinite(value)
        for value in (
            metrics.time_s,
            metrics.distance_m,
            metrics.energy_j,
            path.final_speed_mps,
        )
    )
    if not finite:
        reasons.append(
            SafetyReason(ReasonKind.NON_FINITE_METRIC, "metrica fisica nao finita")
        )
        rejected = True
    elif path.final_speed_mps < 0.0:
        reasons.append(
            SafetyReason(
                ReasonKind.NEGATIVE_SPEED,
                f"velocidade final negativa ({path.final_speed_mps})",
            )
        )
        rejected = True
    if metrics.distance_m <= 0.0:
        reasons.append(
            SafetyReason(
                ReasonKind.NON_POSITIVE_DISTANCE,
                f"distancia nao positiva ({metrics.distance_m})",
            )
        )
        rejected = True

    # 3) Incerteza / limitacao fisica declarada -> DEGRADED (nao impeditiva).
    for code in assessment.limitations:
        if code in _REJECTING_LIMITATIONS:
            continue  # ja tratado em (1)
        if code.startswith(_PHYSIOLOGY_PREFIX):
            advisories.append(_physiology_reason(code))
            continue
        kind = _DEGRADING_LIMITATIONS.get(code)
        if kind is not None:
            degraded = True
            reasons.append(SafetyReason(kind, f"limitacao fisica declarada: {code}"))

    # 4) Estimativa de intensidade: sempre informativa (D6).
    advisories.append(
        SafetyReason(
            ReasonKind.INTENSITY_NOT_CALIBRATED,
            "estimativa de intensidade computacional e nao calibrada "
            "(RPE/faixas i1..i5; nao e zona cardiaca)",
        )
    )

    if rejected:
        state = RouteSafetyState.REJECTED
    elif degraded:
        state = RouteSafetyState.DEGRADED
    else:
        state = RouteSafetyState.VALIDATED
    return RouteSafetyAssessment(state=state, reasons=tuple(reasons), advisories=tuple(advisories))


@dataclass(frozen=True, slots=True)
class EvaluatedRoute:
    """Rota avaliada com seu estado de seguranca (D4).

    ``assessment`` e o objeto **ja calculado** (reutilizado, nunca recalculado);
    ``None`` quando a avaliacao nao esta disponivel (inelegibilidade).
    """

    route_id: str
    safety: RouteSafetyAssessment
    assessment: RouteAssessment | None = None
    recommended: bool = False

    @property
    def eligible(self) -> bool:
        return self.safety.eligible

    def to_dict(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "recommended": self.recommended,
            "safety": self.safety.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Relatorio de avaliacao final: elegiveis, excluidas e recomendada (D4).

    ``routes`` (elegiveis, na ordem de entrada) e ``excluded`` (``REJECTED``,
    com motivos) **preservam** as rotas rejeitadas para auditoria. ``recommended``
    e a **primeira** elegivel na ordem, ou ``None``. ``warnings`` explicita o que
    precisa de atencao (rota degradada recomendada; nenhuma elegivel).
    """

    routes: tuple[EvaluatedRoute, ...] = ()
    excluded: tuple[EvaluatedRoute, ...] = ()
    recommended: EvaluatedRoute | None = None
    warnings: tuple[str, ...] = ()

    @property
    def eligible_count(self) -> int:
        return len(self.routes)

    @property
    def rejected_count(self) -> int:
        return len(self.excluded)

    @property
    def all_routes(self) -> tuple[EvaluatedRoute, ...]:
        """Todas as rotas avaliadas (elegiveis + excluidas), na ordem de entrada."""
        return self.routes + self.excluded

    def to_dict(self) -> dict[str, object]:
        return {
            "recommended_route_id": (
                None if self.recommended is None else self.recommended.route_id
            ),
            "eligible_count": self.eligible_count,
            "rejected_count": self.rejected_count,
            "warnings": list(self.warnings),
            "routes": [route.to_dict() for route in self.routes],
            "excluded": [route.to_dict() for route in self.excluded],
        }


def _select_recommended(
    evaluated: list[EvaluatedRoute],
) -> EvaluatedRoute | None:
    """Primeira rota elegivel na ordem de classificacao (D4)."""
    for route in evaluated:
        if route.eligible:
            return route
    return None


def evaluate_candidates(
    candidates: Iterable,
    *,
    assessments: Mapping[str, RouteAssessment] | None = None,
) -> EvaluationReport:
    """Avalia a seguranca de candidatas **sem recalcular** o ja validado (D4).

    Cada candidata traz sua identidade (``route_id``); a avaliacao fisica e
    fisiologica **ja calculada** e buscada em ``assessments`` (mapa
    ``route_id -> RouteAssessment``) e **reutilizada como esta**. Uma candidata
    sem avaliacao disponivel entra como ``REJECTED`` por **inelegibilidade**
    (``evaluation_missing``) — nunca como prova de inviabilidade (D5).

    Args:
        candidates: sequencia de ``RouteCandidate`` (ou qualquer objeto com
            ``route_id``), na ordem de classificacao.
        assessments: avaliações ja calculadas (por ``route_id``); tipicamente o
            resultado de ``assess_candidates``. Nao e recalculado nada.

    Returns:
        ``EvaluationReport`` com elegiveis, excluidas (com motivos) e a
        recomendada (primeira elegivel ou ``None``).
    """
    mapping = assessments or {}
    evaluated: list[EvaluatedRoute] = []
    warnings: list[str] = []
    for candidate in candidates:
        route_id = candidate.route_id
        assessment = mapping.get(route_id)
        if assessment is None:
            safety = RouteSafetyAssessment(
                RouteSafetyState.REJECTED,
                (
                    SafetyReason(
                        ReasonKind.EVALUATION_MISSING,
                        "sem avaliacao fisica/fisiologica disponivel para a rota "
                        "(inelegivel; nao e prova de inviabilidade)",
                    ),
                ),
            )
            warnings.append(
                f"rota {route_id} sem avaliacao: excluida da recomendacao"
            )
        else:
            safety = classify_safety(assessment)
        evaluated.append(
            EvaluatedRoute(route_id=route_id, safety=safety, assessment=assessment)
        )

    recommended = _select_recommended(evaluated)
    if recommended is not None:
        recommended = EvaluatedRoute(
            route_id=recommended.route_id,
            safety=recommended.safety,
            assessment=recommended.assessment,
            recommended=True,
        )
        # Reposiciona a recomendada marcada na lista de elegiveis.
        evaluated = [
            recommended if item.route_id == recommended.route_id else item
            for item in evaluated
        ]
        if recommended.safety.degraded:
            kinds = ", ".join(reason.kind.value for reason in recommended.safety.reasons)
            warnings.append(
                f"rota recomendada {recommended.route_id} esta DEGRADED "
                f"({kinds}): recomendada com aviso"
            )

    eligible = tuple(item for item in evaluated if item.eligible)
    excluded = tuple(item for item in evaluated if not item.eligible)
    if recommended is None and evaluated:
        warnings.append(
            "nenhuma rota elegivel: todas as "
            f"{len(evaluated)} rotas foram rejeitadas"
        )
    return EvaluationReport(
        routes=eligible,
        excluded=excluded,
        recommended=recommended,
        warnings=tuple(warnings),
    )
