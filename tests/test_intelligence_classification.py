"""Classificacao predicao x preferencia (ADR-027, decisao 6 / inc. 7).

Cobrem:

* a classificacao combina circulacao, disponibilidade da previsao e preferencia;
* **previsao indisponivel** => criterio ``UNKNOWN`` (conservador), nunca valor;
* rota violando circulacao => ``REJECTED`` e preferencia nao avaliada;
* limiares **declarados** (nunca escondidos) e validados contra a escala Borg;
* criterio de **intensidade** (ADR-028) sobre a estimativa **nao calibrada**,
  com base distancia/duracao e ``UNKNOWN`` quando nao ha distribuicao;
* ``classify_routes`` **preserva a ordem** (ranking fisico intocado).
"""

import pytest
from router.core.evaluation import (
    AssessmentOutcome,
    ClassificationError,
    EffortPreference,
    PreferenceVerdict,
    RouteClassification,
    assess_route,
    classify_route,
    classify_routes,
)
from router.core.data.graph.model import Arc, BicycleAccess, ElevationSample, Node, RoutingGraph, Segment
from router.core.data.graph.restrictions import AccessRule
from router.core.prediction.ml import UnavailablePredictor
from router.core.physics import BicycleModel, CyclistModel
from router.core.physiology import (
    DEFAULT_BORG_SCALE,
    PhysiologicalPrediction,
    PredictionStatus,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()


# --------------------------------------------------------------------------
# Infra
# --------------------------------------------------------------------------
def make_arc(arc_id, source, target, *, restrictions=(), bicycle_access=BicycleAccess.YES):
    points = ((0.0, 0.0), (200.0, 0.0))
    profile = tuple(ElevationSample(distance_m=d, elevation_m=e) for d, e in points)
    segment = Segment(
        id=f"{arc_id}:0",
        distance_m=points[-1][0],
        elevation_profile=profile,
        elevation_gain_m=0.0,
        elevation_loss_m=0.0,
        restrictions=tuple(restrictions),
        bicycle_access=bicycle_access,
    )
    return Arc(id=arc_id, source=source, target=target, segments=(segment,))


def make_graph(arcs, node_ids):
    graph = RoutingGraph()
    for node_id in node_ids:
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    for arc in arcs:
        graph.add_arc(arc)
    return graph


def two_arc_graph(**a12):
    return make_graph(
        [make_arc("a12", 1, 2, **a12), make_arc("a23", 2, 3)], (1, 2, 3)
    )


class _RpeStubPredictor:
    model_version = "stub:rpe@1"
    borg_scale = DEFAULT_BORG_SCALE
    required_inputs = ()

    def __init__(self, rpe):
        self._rpe = rpe

    def predict(self, features, rider, context):
        return PhysiologicalPrediction(
            borg_scale=DEFAULT_BORG_SCALE,
            predicted_rpe=self._rpe,
            heart_rate_zone_distribution=None,
            model_version=self.model_version,
            status=PredictionStatus.AVAILABLE,
        )


class _ZoneStubPredictor:
    model_version = "stub:zones@1"
    borg_scale = DEFAULT_BORG_SCALE
    required_inputs = ()

    def __init__(self, distribution, definition="lthr@1"):
        self._distribution = distribution
        self._definition = definition

    def predict(self, features, rider, context):
        return PhysiologicalPrediction(
            borg_scale=DEFAULT_BORG_SCALE,
            predicted_rpe=None,
            heart_rate_zone_distribution=dict(self._distribution),
            heart_rate_zone_definition=self._definition,
            model_version=self.model_version,
            status=PredictionStatus.AVAILABLE,
        )


def assess(graph, **kwargs):
    return assess_route(graph, ("a12", "a23"), cyclist=CYCLIST, bicycle=BICYCLE, **kwargs)


# ==========================================================================
# Preferencia declarada e validacao
# ==========================================================================
def test_preference_validates_rpe_against_scale():
    with pytest.raises(ValueError):
        EffortPreference(acceptable_rpe=(4.0, 10.0))  # 4 fora de 6-20
    with pytest.raises(ClassificationError):
        EffortPreference(acceptable_rpe=(15.0, 10.0))  # min > max


def test_preference_requires_fraction_with_zones():
    with pytest.raises(ClassificationError):
        EffortPreference(preferred_zones=("z1",))
    with pytest.raises(ValueError):
        EffortPreference(preferred_zones=("z1",), min_preferred_fraction=1.5)


# ==========================================================================
# Sem preferencia / indisponibilidade
# ==========================================================================
def test_without_preference_verdict_is_satisfied():
    classification = classify_route(assess(two_arc_graph()))
    assert classification.verdict is PreferenceVerdict.SATISFIED
    assert classification.fits_preference is True
    assert classification.criteria == ()


def test_unavailable_prediction_yields_unknown_criterion():
    classification = classify_route(
        assess(two_arc_graph(), predictor=UnavailablePredictor()),
        EffortPreference(acceptable_rpe=(8.0, 14.0)),
    )
    assert classification.prediction_status == PredictionStatus.MODEL_NOT_CALIBRATED.value
    assert classification.criteria[0].verdict is PreferenceVerdict.UNKNOWN
    assert classification.verdict is PreferenceVerdict.UNKNOWN
    assert classification.fits_preference is None
    assert "physiology_model_not_calibrated" in classification.limitations
    assert "preference_unknown" in classification.limitations


def test_missing_predictor_keeps_physical_only_and_unknown():
    assessment = assess(two_arc_graph())  # sem preditor -> UNAVAILABLE
    classification = classify_route(
        assessment, EffortPreference(acceptable_rpe=(8.0, 14.0))
    )
    assert classification.outcome is AssessmentOutcome.PHYSICAL_ONLY
    assert "physiology_unavailable" in classification.limitations


def test_limitation_codes_follow_the_documented_convention():
    # ADR-026 §5 / ADR-027 decisao 6: a parcela de previsao usa o codigo
    # ``physiology_<status>`` (mesmo da Camada 05), sem vocabulario divergente.
    assessment = assess(two_arc_graph(), predictor=UnavailablePredictor())
    classification = classify_route(
        assessment, EffortPreference(acceptable_rpe=(8.0, 14.0))
    )
    expected = f"physiology_{assessment.prediction.status.value}"
    assert expected in assessment.limitations
    assert expected in classification.limitations
    assert classification.prediction_status == assessment.prediction.status.value


# ==========================================================================
# RPE com previsao disponivel
# ==========================================================================
def test_rpe_within_preference_is_satisfied():
    assessment = assess(two_arc_graph(), predictor=_RpeStubPredictor(12.0))
    classification = classify_route(
        assessment, EffortPreference(acceptable_rpe=(10.0, 14.0))
    )
    assert classification.verdict is PreferenceVerdict.SATISFIED
    assert classification.fits_preference is True
    assert classification.criteria[0].observed == pytest.approx(12.0)


def test_rpe_outside_preference_is_not_satisfied():
    assessment = assess(two_arc_graph(), predictor=_RpeStubPredictor(17.0))
    classification = classify_route(
        assessment, EffortPreference(acceptable_rpe=(10.0, 14.0))
    )
    assert classification.verdict is PreferenceVerdict.NOT_SATISFIED
    assert classification.fits_preference is False


# ==========================================================================
# Zonas cardiacas
# ==========================================================================
def test_zone_fraction_preference_is_evaluated():
    assessment = assess(
        two_arc_graph(),
        predictor=_ZoneStubPredictor({"z1": 0.4, "z2": 0.6}, definition="lthr@1"),
    )
    classification = classify_route(
        assessment,
        EffortPreference(preferred_zones=("z1",), min_preferred_fraction=0.3),
    )
    criterion = classification.criteria[0]
    assert criterion.name == "heart_rate_zones"
    assert criterion.observed == pytest.approx(0.4)
    assert criterion.verdict is PreferenceVerdict.SATISFIED


def test_zone_preference_unknown_without_distribution():
    assessment = assess(two_arc_graph())  # preditor ausente -> sem distribuicao
    classification = classify_route(
        assessment,
        EffortPreference(preferred_zones=("z1",), min_preferred_fraction=0.3),
    )
    assert classification.criteria[0].verdict is PreferenceVerdict.UNKNOWN


# ==========================================================================
# Intensidade (ADR-028): estimativa NAO calibrada, base distancia/duracao
# ==========================================================================
def _with_intensity(distribution, *, duration_distribution=None):
    from dataclasses import replace

    from router.core.prediction.intensity import IntensityEstimate

    estimate = IntensityEstimate(
        borg_scale=DEFAULT_BORG_SCALE,
        mean_rpe=10.0,
        peak_rpe=12.0,
        band_distance_fraction=dict(distribution),
        band_duration_fraction=(
            None if duration_distribution is None else dict(duration_distribution)
        ),
        distance_m=100.0,
        duration_s=20.0,
    )
    return replace(assess(two_arc_graph()), intensity=estimate)


def test_intensity_preference_requires_fraction_and_known_bands():
    with pytest.raises(ClassificationError):
        EffortPreference(preferred_intensity_bands=("i1",))
    with pytest.raises(ClassificationError):
        EffortPreference(
            preferred_intensity_bands=("z1",), min_intensity_band_fraction=0.3
        )
    with pytest.raises(ClassificationError):
        EffortPreference(intensity_band_basis="pace")


def test_intensity_distance_fraction_is_evaluated():
    assessment = _with_intensity({"i1": 0.2, "i2": 0.3, "i3": 0.5})
    classification = classify_route(
        assessment,
        EffortPreference(
            preferred_intensity_bands=("i1", "i2"),
            min_intensity_band_fraction=0.4,
        ),
    )
    criterion = classification.criteria[0]
    assert criterion.name == "intensity_bands"
    assert criterion.observed == pytest.approx(0.5)
    assert criterion.verdict is PreferenceVerdict.SATISFIED
    assert "nao calibrada" in criterion.reason


def test_intensity_not_satisfied_below_threshold():
    assessment = _with_intensity({"i1": 0.2, "i2": 0.3, "i3": 0.5})
    classification = classify_route(
        assessment,
        EffortPreference(
            preferred_intensity_bands=("i3",),
            min_intensity_band_fraction=0.8,
        ),
    )
    assert classification.verdict is PreferenceVerdict.NOT_SATISFIED
    assert classification.fits_preference is False


def test_intensity_duration_basis_unknown_without_times():
    assessment = _with_intensity({"i1": 1.0}, duration_distribution=None)
    classification = classify_route(
        assessment,
        EffortPreference(
            preferred_intensity_bands=("i1",),
            min_intensity_band_fraction=0.5,
            intensity_band_basis="duration",
        ),
    )
    criterion = classification.criteria[0]
    assert criterion.name == "intensity_bands"
    assert criterion.verdict is PreferenceVerdict.UNKNOWN


def test_intensity_criterion_never_touches_physiological_contract():
    assessment = _with_intensity({"i1": 1.0})
    classification = classify_route(
        assessment,
        EffortPreference(
            preferred_intensity_bands=("i1",),
            min_intensity_band_fraction=0.5,
        ),
    )
    assert assessment.prediction.heart_rate_zone_distribution is None
    assert assessment.prediction.predicted_rpe is None
    assert classification.fits_preference is True


# ==========================================================================
# Circulacao: rejeitada
# ==========================================================================
def test_rejected_route_skips_preference_evaluation():
    graph = two_arc_graph(
        restrictions=(AccessRule(scope="bicycle", value="no", condition=None, raw="no"),)
    )
    classification = classify_route(
        assess(graph), EffortPreference(acceptable_rpe=(8.0, 14.0))
    )
    assert classification.outcome is AssessmentOutcome.REJECTED
    assert classification.criteria == ()
    assert classification.verdict is PreferenceVerdict.UNKNOWN
    assert "circulation_violation" in classification.limitations


# ==========================================================================
# Ranking intocado e serializacao
# ==========================================================================
def test_classify_routes_preserves_input_order():
    graph = two_arc_graph()
    assessments = [assess(graph), assess(graph)]
    classifications = classify_routes(assessments, EffortPreference(acceptable_rpe=(8.0, 14.0)))
    assert len(classifications) == 2
    assert [c.arc_ids for c in classifications] == [a.arc_ids for a in assessments]


def test_classification_does_not_mutate_assessment():
    assessment = assess(two_arc_graph(), predictor=_RpeStubPredictor(12.0))
    before = assessment.to_dict()
    classify_route(assessment, EffortPreference(acceptable_rpe=(10.0, 14.0)))
    assert assessment.to_dict() == before


def test_to_dict_is_explicit():
    payload = classify_route(
        assess(two_arc_graph(), predictor=_RpeStubPredictor(12.0)),
        EffortPreference(acceptable_rpe=(10.0, 14.0)),
    ).to_dict()
    assert payload["verdict"] == "satisfied"
    assert payload["fits_preference"] is True
    assert payload["outcome"] == "physiological"
    assert payload["criteria"][0]["name"] == "rpe"


def test_classify_route_rejects_wrong_types():
    with pytest.raises(ClassificationError):
        classify_route(object())  # type: ignore[arg-type]
    with pytest.raises(ClassificationError):
        classify_route(assess(two_arc_graph()), preference=object())  # type: ignore[arg-type]


def test_classification_does_not_import_concrete_dto():
    """A classificacao depende de ``RouteAssessmentView``, nao do DTO concreto.

    Regressao do ADR-033 (E2): se alguem reintroduzir o import concreto, o
    protocolo estrutural volta a nao ter consumidor real.
    """
    import router.core.evaluation.classification as classification_module

    assert not hasattr(classification_module, "RouteAssessment")


def test_classification_accepts_a_structural_view():
    """Um objeto que satisfaz o protocolo (sem ser o DTO) e classificado."""
    from types import SimpleNamespace

    from router.core.evaluation import RouteAssessment

    real = assess(two_arc_graph())
    view = SimpleNamespace(
        outcome=real.outcome,
        prediction=real.prediction,
        intensity=real.intensity,
        arc_ids=real.arc_ids,
    )
    assert not isinstance(view, RouteAssessment)
    classification = classify_route(view)  # type: ignore[arg-type]
    assert classification.arc_ids == real.arc_ids
    assert classification.outcome is real.outcome
