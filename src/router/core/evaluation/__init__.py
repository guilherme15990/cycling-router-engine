"""Camada 05 — avaliacao fisica completa, seguranca e classificacao de rota.

Fecha o pipeline declarado no ADR-025 §10 / ADR-026 §3 / ADR-029: candidatas ->
mesmas restricoes de circulacao -> simulador fisico completo -> ``EffortFeatures``
-> preditor fisiologico -> classificacao -> **estado de seguranca operacional**
(``VALIDATED``/``DEGRADED``/``REJECTED``). Nao reimplementa fisica nem ML.

Pontos centrais de honestidade:

* quando **nao** ha modelo fisiologico calibrado, a classificacao e
  ``PHYSICAL_ONLY`` e o motivo fica explicito (``PredictionStatus``), nunca um
  valor inventado — e a seguranca **nao** e rebaixada por isso (ADR-029 D2);
* a **estimativa de intensidade** e sempre rotulada nao calibrada (ADR-029 D6);
* a **avaliacao final** (``evaluate_candidates``) **consome** o que ja foi
  calculado, sem repetir simulacao fisica nem inferencia fisiologica (ADR-029 D4);
* **toda** rejeicao carrega um motivo auditavel (ADR-029 D5).
"""

from .assessment import (
    AssessmentOutcome,
    RouteAssessment,
    RouteAssessmentError,
    assess_candidate,
    assess_candidates,
    assess_route,
    assess_search_route,
)
from .classification import (
    ClassificationError,
    CriterionResult,
    EffortPreference,
    PreferenceVerdict,
    RouteClassification,
    classify_route,
    classify_routes,
)
from .diagnostics import EnergyConsistency, energy_consistency
from .ports import RouteAssessmentView
from .safety import (
    EvaluatedRoute,
    EvaluationReport,
    Reason,
    ReasonKind,
    RouteSafety,
    RouteSafetyState,
    classify_safety,
    evaluate_candidates,
)

__all__ = [
    "AssessmentOutcome",
    "ClassificationError",
    "CriterionResult",
    "EffortPreference",
    "EnergyConsistency",
    "EvaluatedRoute",
    "EvaluationReport",
    "PreferenceVerdict",
    "Reason",
    "ReasonKind",
    "RouteAssessment",
    "RouteAssessmentError",
    "RouteAssessmentView",
    "RouteClassification",
    "RouteSafety",
    "RouteSafetyState",
    "assess_candidate",
    "assess_candidates",
    "assess_route",
    "assess_search_route",
    "classify_route",
    "classify_routes",
    "classify_safety",
    "energy_consistency",
    "evaluate_candidates",
]
