"""Classificacao predicao x preferencia (ADR-027, decisao 6 / incremento 7).

Este modulo une, de forma **explicita e auditavel**, dois planos que o ADR-027
mantem separados:

* **previsao** (o que o modelo diz) — vem de ``RouteAssessment.prediction`` e do
  seu ``PredictionStatus``; e **nunca** uma medicao;
* **preferencia** (o que o ciclista quer) — declarada pelo proprio ciclista em
  ``EffortPreference``, com limiares **explicitos**.

Regras (ADR-027, decisao 6):

* o **ranking fisico permanece intocado**: ``classify_routes`` **preserva a
  ordem** de entrada e nao reordena nada — a classificacao e **apresentacao**;
* nenhum limiar novo escondido: os limites de esforco sao **declarados** pelo
  chamador em ``EffortPreference`` (e validados contra a escala Borg explicita);
* **previsao indisponivel** nao vira valor: o criterio correspondente fica
  ``UNKNOWN`` (conservador) e a classificacao geral continua explicita
  (``PHYSICAL_ONLY`` + motivo, vindo de ``AssessmentOutcome``);
* quando a rota viola a circulacao, a classificacao e ``REJECTED`` e a
  preferencia nao e avaliada.

Criterio de **intensidade** (ADR-028): ``preferred_intensity_bands`` avalia a
**estimativa de intensidade** (RPE/faixas ``i1``..``i5``), um artefato
**computacional e nao calibrado** — **nunca** zona cardiaca real. A base e
``distance`` (sempre disponivel) ou ``duration``; sem distribuicao na base
escolhida, o criterio fica ``UNKNOWN`` (nada e imputado).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from evaluation.assessment import AssessmentOutcome, RouteAssessment
from physiology import DEFAULT_BORG_SCALE, BorgScale

from ._validation import finite, non_empty_str, unit_interval
from .intensity import INTENSITY_BAND_NAMES


class ClassificationError(ValueError):
    """Erro de **dados/parametros** na classificacao (nunca mascarado)."""


class PreferenceVerdict(str, Enum):
    """Veredito de um criterio de preferencia (tri-estado, conservador)."""

    #: O criterio declarado foi satisfeito pelos dados disponiveis.
    SATISFIED = "satisfied"
    #: O criterio declarado foi violado pelos dados disponiveis.
    NOT_SATISFIED = "not_satisfied"
    #: Nao ha dado para decidir (previsao indisponivel) — **conservador**.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EffortPreference:
    """Preferencia de esforco **declarada** pelo ciclista.

    Nenhum limite e implicito: se um campo e ``None``, o criterio
    correspondente **nao** e avaliado (nao se assume preferencia). Os valores
    sao validados contra a ``borg_scale`` explicita.

    Args:
        borg_scale: Escala em que ``acceptable_rpe`` e expresso.
        acceptable_rpe: Intervalo fechado ``(min, max)`` de RPE aceitavel.
        preferred_zones: Nomes de zonas cardiacas preferidas (definicao vem da
            previsao; a comparacao e por **nome de zona**).
        min_preferred_fraction: Fracao minima de tempo que deve estar nas
            ``preferred_zones`` (exigida quando ``preferred_zones`` existe).
        preferred_intensity_bands: Faixas de **intensidade** preferidas
            (``i1``..``i5``, ADR-028 — **nao** zonas cardiacas). Avaliadas sobre a
            **estimativa de intensidade** (computacional e **nao calibrada**).
        min_intensity_band_fraction: Fracao minima (na base escolhida) que deve
            estar nas ``preferred_intensity_bands`` (exigida quando elas existem).
        intensity_band_basis: Base da distribuicao de intensidade — ``"distance"``
            (sempre disponivel) ou ``"duration"`` (quando ha tempos por trecho).
        notes: Observacao livre (nunca usada em calculo).
    """

    borg_scale: BorgScale = DEFAULT_BORG_SCALE
    acceptable_rpe: tuple[float, float] | None = None
    preferred_zones: tuple[str, ...] | None = None
    min_preferred_fraction: float | None = None
    preferred_intensity_bands: tuple[str, ...] | None = None
    min_intensity_band_fraction: float | None = None
    intensity_band_basis: str = "distance"
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.borg_scale, BorgScale):
            raise ClassificationError("borg_scale deve ser um BorgScale")
        if self.acceptable_rpe is not None:
            low, high = self.acceptable_rpe
            self.borg_scale.require_valid(low)
            self.borg_scale.require_valid(high)
            if low > high:
                raise ClassificationError(
                    f"acceptable_rpe com minimo maior que maximo: {self.acceptable_rpe!r}"
                )
        if self.preferred_zones is not None:
            if not self.preferred_zones:
                raise ClassificationError("preferred_zones nao pode ser vazio")
            for zone in self.preferred_zones:
                non_empty_str("preferred_zones", zone)
            if self.min_preferred_fraction is None:
                raise ClassificationError(
                    "preferred_zones exige min_preferred_fraction explicitamente"
                )
        if self.min_preferred_fraction is not None:
            unit_interval("min_preferred_fraction", self.min_preferred_fraction)
        if self.intensity_band_basis not in ("distance", "duration"):
            raise ClassificationError(
                "intensity_band_basis deve ser 'distance' ou 'duration'"
            )
        if self.preferred_intensity_bands is not None:
            if not self.preferred_intensity_bands:
                raise ClassificationError(
                    "preferred_intensity_bands nao pode ser vazio"
                )
            for band in self.preferred_intensity_bands:
                non_empty_str("preferred_intensity_bands", band)
                if band not in INTENSITY_BAND_NAMES:
                    raise ClassificationError(
                        f"faixa de intensidade desconhecida {band!r}; "
                        f"esperado uma de {INTENSITY_BAND_NAMES!r}"
                    )
            if self.min_intensity_band_fraction is None:
                raise ClassificationError(
                    "preferred_intensity_bands exige "
                    "min_intensity_band_fraction explicitamente"
                )
        if self.min_intensity_band_fraction is not None:
            unit_interval(
                "min_intensity_band_fraction", self.min_intensity_band_fraction
            )
        if self.notes is not None and not isinstance(self.notes, str):
            raise ClassificationError("notes deve ser str ou None")

    @property
    def declares_any_criterion(self) -> bool:
        return (
            self.acceptable_rpe is not None
            or self.preferred_zones is not None
            or self.preferred_intensity_bands is not None
        )


@dataclass(frozen=True, slots=True)
class CriterionResult:
    """Resultado auditavel de **um** criterio de preferencia."""

    name: str
    verdict: PreferenceVerdict
    reason: str
    observed: float | None = None
    threshold: float | None = None

    def __post_init__(self) -> None:
        non_empty_str("name", self.name)
        if not isinstance(self.verdict, PreferenceVerdict):
            raise ClassificationError("verdict deve ser um PreferenceVerdict")
        if not isinstance(self.reason, str) or not self.reason:
            raise ClassificationError("reason deve ser uma string nao vazia")
        if self.observed is not None:
            finite("observed", self.observed)
        if self.threshold is not None:
            finite("threshold", self.threshold)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "observed": self.observed,
            "threshold": self.threshold,
        }


@dataclass(frozen=True, slots=True)
class RouteClassification:
    """Classificacao final de uma rota: esforco previsto x preferencia.

    Nao altera nem reordena nada: apenas **rotula**, de forma reproduzivel, o
    resultado ja produzido por ``evaluation.assess_route``.
    """

    arc_ids: tuple[str, ...]
    outcome: AssessmentOutcome
    preference: EffortPreference | None
    prediction_status: str
    criteria: tuple[CriterionResult, ...] = ()

    @property
    def verdict(self) -> PreferenceVerdict:
        """Veredito geral (conservador) da preferencia declarada."""
        if self.outcome is AssessmentOutcome.REJECTED:
            return PreferenceVerdict.UNKNOWN
        if (
            self.preference is None
            or not self.preference.declares_any_criterion
            or not self.criteria
        ):
            return PreferenceVerdict.SATISFIED
        verdicts = {criterion.verdict for criterion in self.criteria}
        if PreferenceVerdict.UNKNOWN in verdicts:
            return PreferenceVerdict.UNKNOWN
        if PreferenceVerdict.NOT_SATISFIED in verdicts:
            return PreferenceVerdict.NOT_SATISFIED
        return PreferenceVerdict.SATISFIED

    @property
    def fits_preference(self) -> bool | None:
        """``True``/``False`` se decidivel; ``None`` quando ``UNKNOWN``."""
        verdict = self.verdict
        if verdict is PreferenceVerdict.SATISFIED:
            return True
        if verdict is PreferenceVerdict.NOT_SATISFIED:
            return False
        return None

    @property
    def reasons(self) -> tuple[str, ...]:
        """Motivos dos criterios (vazio quando nao ha preferencia avaliada)."""
        return tuple(criterion.reason for criterion in self.criteria)

    @property
    def limitations(self) -> tuple[str, ...]:
        """Limitacoes declaradas (codigos estaveis; nada e ocultado).

        Reutiliza os **mesmos codigos** da Camada 05 (``RouteAssessment``) para
        as parcelas comuns, de modo que a apresentacao nao introduza vocabulario
        divergente: ``circulation_violation`` (parcela a) e
        ``physiology_<status>`` (parcela b). A parcela de preferencia acrescenta
        ``preference_unknown`` quando o veredito e indeterminado.
        """
        codes: list[str] = []
        if self.outcome is AssessmentOutcome.REJECTED:
            codes.append("circulation_violation")
        if not self.prediction_status == "available":
            codes.append(f"physiology_{self.prediction_status}")
        if self.verdict is PreferenceVerdict.UNKNOWN:
            codes.append("preference_unknown")
        return tuple(codes)

    def to_dict(self) -> dict[str, object]:
        return {
            "arcs": list(self.arc_ids),
            "arc_count": len(self.arc_ids),
            "outcome": self.outcome.value,
            "prediction_status": self.prediction_status,
            "verdict": self.verdict.value,
            "fits_preference": self.fits_preference,
            "limitations": list(self.limitations),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }


def _evaluate_rpe(
    assessment: RouteAssessment, preference: EffortPreference
) -> CriterionResult:
    low, high = preference.acceptable_rpe  # type: ignore[misc]
    prediction = assessment.prediction
    if not prediction.status.is_available or prediction.predicted_rpe is None:
        return CriterionResult(
            name="rpe",
            verdict=PreferenceVerdict.UNKNOWN,
            reason=(
                "RPE previsto indisponivel "
                f"(status={prediction.status.value}); nada e imputado"
            ),
            threshold=high,
        )
    observed = float(prediction.predicted_rpe)
    satisfied = low <= observed <= high
    return CriterionResult(
        name="rpe",
        verdict=(
            PreferenceVerdict.SATISFIED
            if satisfied
            else PreferenceVerdict.NOT_SATISFIED
        ),
        reason=(
            f"RPE previsto {observed:.6g} "
            f"{'dentro' if satisfied else 'fora'} do intervalo "
            f"[{low:g}, {high:g}] na escala {preference.borg_scale.value!r}"
        ),
        observed=observed,
        threshold=high,
    )


def _evaluate_zones(
    assessment: RouteAssessment, preference: EffortPreference
) -> CriterionResult:
    prediction = assessment.prediction
    distribution = prediction.heart_rate_zone_distribution
    if distribution is None:
        return CriterionResult(
            name="heart_rate_zones",
            verdict=PreferenceVerdict.UNKNOWN,
            reason=(
                "distribuicao de zonas indisponivel "
                f"(status={prediction.status.value}); nada e imputado"
            ),
            threshold=preference.min_preferred_fraction,
        )
    fraction = sum(
        float(distribution.get(zone, 0.0))
        for zone in (preference.preferred_zones or ())
    )
    threshold = float(preference.min_preferred_fraction)  # type: ignore[arg-type]
    satisfied = fraction >= threshold
    return CriterionResult(
        name="heart_rate_zones",
        verdict=(
            PreferenceVerdict.SATISFIED
            if satisfied
            else PreferenceVerdict.NOT_SATISFIED
        ),
        reason=(
            f"fracao nas zonas preferidas {fraction:.6g} "
            f"{'>= ' if satisfied else '< '}{threshold:g}"
        ),
        observed=fraction,
        threshold=threshold,
    )


def _evaluate_intensity_bands(
    assessment: RouteAssessment, preference: EffortPreference
) -> CriterionResult:
    """Avalia a fracao da base nas faixas de **intensidade** preferidas.

    Usa a **estimativa de intensidade** (``RouteAssessment.intensity``) — um
    artefato **computacional e nao calibrado** (ADR-028), distinto da previsao
    fisiologica. Quando a base escolhida nao tem distribuicao (por exemplo,
    ``"duration"`` sem tempos por trecho), o veredito e ``UNKNOWN`` (nada e
    imputado).
    """
    estimate = assessment.intensity
    basis = preference.intensity_band_basis
    if basis == "distance":
        distribution = estimate.band_distance_fraction
    else:
        distribution = estimate.band_duration_fraction
    threshold = float(preference.min_intensity_band_fraction)  # type: ignore[arg-type]
    if not distribution:
        return CriterionResult(
            name="intensity_bands",
            verdict=PreferenceVerdict.UNKNOWN,
            reason=(
                f"distribuicao de intensidade por {basis} indisponivel "
                "(estimativa nao calibrada); nada e imputado"
            ),
            threshold=threshold,
        )
    fraction = sum(
        float(distribution.get(band, 0.0))
        for band in (preference.preferred_intensity_bands or ())
    )
    satisfied = fraction >= threshold
    return CriterionResult(
        name="intensity_bands",
        verdict=(
            PreferenceVerdict.SATISFIED
            if satisfied
            else PreferenceVerdict.NOT_SATISFIED
        ),
        reason=(
            f"fracao por {basis} nas faixas de intensidade preferidas "
            f"{fraction:.6g} {'>= ' if satisfied else '< '}{threshold:g} "
            "(estimativa de intensidade nao calibrada)"
        ),
        observed=fraction,
        threshold=threshold,
    )


def _evaluate_criteria(
    assessment: RouteAssessment, preference: EffortPreference | None
) -> tuple[CriterionResult, ...]:
    if preference is None:
        return ()
    results: list[CriterionResult] = []
    if preference.acceptable_rpe is not None:
        results.append(_evaluate_rpe(assessment, preference))
    if preference.preferred_zones is not None:
        results.append(_evaluate_zones(assessment, preference))
    if preference.preferred_intensity_bands is not None:
        results.append(_evaluate_intensity_bands(assessment, preference))
    return tuple(results)


def classify_route(
    assessment: RouteAssessment,
    preference: EffortPreference | None = None,
) -> RouteClassification:
    """Classifica **uma** rota avaliada: esforco previsto x preferencia.

    A classificacao **nao** altera a rota nem a ordem; apenas rotula o resultado
    de ``assess_route``. Quando a rota e ``REJECTED``, a preferencia nao e
    avaliada (o veredito geral fica ``UNKNOWN``).
    """
    if not isinstance(assessment, RouteAssessment):
        raise ClassificationError("assessment deve ser um RouteAssessment")
    if preference is not None and not isinstance(preference, EffortPreference):
        raise ClassificationError("preference deve ser um EffortPreference ou None")

    criteria: tuple[CriterionResult, ...] = ()
    if assessment.outcome is not AssessmentOutcome.REJECTED:
        criteria = _evaluate_criteria(assessment, preference)

    return RouteClassification(
        arc_ids=assessment.arc_ids,
        outcome=assessment.outcome,
        preference=preference,
        prediction_status=assessment.prediction.status.value,
        criteria=criteria,
    )


def classify_routes(
    assessments: Iterable[RouteAssessment],
    preference: EffortPreference | None = None,
) -> tuple[RouteClassification, ...]:
    """Classifica varias rotas **preservando a ordem de entrada**.

    O ranking fisico (fronteira, ordenacao por perfil, diversidade) permanece
    **intocado**: esta funcao nao reordena nem descarta candidatas.
    """
    return tuple(classify_route(a, preference) for a in assessments)
