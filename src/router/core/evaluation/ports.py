"""Portas da avaliacao (ADR-031, secao 5).

``RouteAssessmentView`` e a visao minima que a classificacao consome: apenas
as metricas/features/predicao de que precisa, sem acoplar a classificacao ao
DTO completo ``RouteAssessment``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from router.core.evaluation.assessment import AssessmentOutcome
from router.core.physiology import PhysiologicalPrediction
from router.core.prediction.intensity import IntensityEstimate


@runtime_checkable
class RouteAssessmentView(Protocol):
    """Visao minima de uma avaliacao (estruturalmente satisfeita por
    :class:`router.core.evaluation.assessment.RouteAssessment`)."""

    outcome: AssessmentOutcome
    prediction: PhysiologicalPrediction
    intensity: IntensityEstimate
    arc_ids: Sequence[str]


__all__ = ["RouteAssessmentView"]
