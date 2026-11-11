"""Fronteira de inferencia (``ml/``) sobre ``EffortFeatures`` (incremento 7).

Cobertura exigida:

* o preditor padrao declara a ausencia de modelo (tudo ``None``), sem inventar;
* ``MetabolicEnergyPredictor`` preenche **apenas** a energia metabolica;
* o protocolo ``EffortPredictor`` e satisfeito pelas implementacoes;
* independencia do gerador de rotas (sem ``search``) e reprodutibilidade;
* a previsao consome features de qualquer rota (integracao fisica -> features ->
  previsao) sem alterar geometria/restricoes.
"""

import ast
import inspect
import pytest
import router.core.prediction.ml.predictor as predictor_module
from router.core.data.graph.model import Arc, ElevationSample, Segment
from router.core.prediction.ml import (
    EffortPredictor,
    MetabolicEnergyPredictor,
    UnavailablePredictor,
    predict_effort,
)
from router.core.physics.physical import (
    BicycleModel,
    CyclistModel,
    EffortFeatures,
    PhysicalTraversal,
    SimulationState,
    SimulationStatus,
    extract_effort_features,
)
from router.core.physics.physiology import (
    DEFAULT_BORG_SCALE,
    MetabolicParameters,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()


def features(**overrides):
    """``EffortFeatures`` deterministico e explicito para testes unitarios."""
    rider_work_j = 90_000.0
    duration_s = 600.0
    base = dict(
        duration_s=duration_s,
        distance_m=3000.0,
        average_speed_mps=5.0,
        mechanical_work_j=rider_work_j * BICYCLE.drivetrain_efficiency,
        rider_work_j=rider_work_j,
        mean_power_w=rider_work_j / duration_s,
        peak_power_w=None,
        power_variability=None,
        climbing_m=100.0,
        descending_m=50.0,
        elevation_coverage=1.0,
        observed_elevation_coverage=1.0,
        braking_energy_j=0.0,
        aero_loss_j=1000.0,
        rolling_loss_j=2000.0,
        battery_energy_j=None,
        metabolic_energy_j=None,
        result_status=SimulationStatus.VALID,
        used_min_speed_regularization=False,
        elevation_uncertainty=None,
    )
    base.update(overrides)
    return EffortFeatures(**base)


def make_arc(points, *, arc_id="e0"):
    distance = points[-1][0]
    profile = tuple(ElevationSample(distance_m=d, elevation_m=e) for d, e in points)
    ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
    descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id="s0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


# ==========================================================================
# Preditor padrao: ausencia explicita
# ==========================================================================
def test_unavailable_predictor_returns_explicit_nones():
    p = UnavailablePredictor().predict(features(), RiderProfile(), PredictionContext())
    assert p.predicted_rpe is None
    assert p.heart_rate_zone_distribution is None
    assert p.confidence is None
    assert p.metabolic_energy_j is None
    assert p.model_version == "unavailable"
    assert p.status is PredictionStatus.MODEL_NOT_CALIBRATED
    assert p.is_measurement is False


def test_unavailable_predictor_uses_default_borg_scale():
    p = UnavailablePredictor().predict(features(), RiderProfile(), PredictionContext())
    assert p.borg_scale is DEFAULT_BORG_SCALE
    assert p.borg_scale.value == "6-20"


def test_unavailable_predictor_interval_tracks_duration():
    p = UnavailablePredictor().predict(
        features(duration_s=1234.0), RiderProfile(), PredictionContext()
    )
    assert p.prediction_interval_s == pytest.approx(1234.0)


def test_predict_effort_without_predictor_is_unavailable():
    p = predict_effort(features())
    assert p.status is PredictionStatus.UNAVAILABLE
    assert p.model_version == "unavailable"
    assert p.predicted_rpe is None
    assert p.metabolic_energy_j is None


def test_absent_predictor_vs_uncalibrated_model_are_distinct():
    no_predictor = predict_effort(features())
    uncalibrated = predict_effort(features(), predictor=UnavailablePredictor())
    assert no_predictor.status is PredictionStatus.UNAVAILABLE
    assert uncalibrated.status is PredictionStatus.MODEL_NOT_CALIBRATED
    # Em ambos, nada fisiologico e inventado.
    for p in (no_predictor, uncalibrated):
        assert p.predicted_rpe is None
        assert p.heart_rate_zone_distribution is None


def test_contract_incompatible_is_detected():
    class _FutureFeatures:
        schema_version = "99"
        duration_s = 120.0

    p = predict_effort(_FutureFeatures())  # type: ignore[arg-type]
    assert p.status is PredictionStatus.CONTRACT_INCOMPATIBLE
    assert p.predicted_rpe is None
    assert p.prediction_interval_s is None
    assert "99" in (p.reason or "")


def test_insufficient_data_is_detected_before_calling_predictor():
    class _NeedsWeight:
        model_version = "needs-weight"
        required_inputs = ("weight_kg",)

        def predict(self, features, rider, context):
            return PhysiologicalPrediction(
                borg_scale=DEFAULT_BORG_SCALE,
                predicted_rpe=13.0,
                heart_rate_zone_distribution=None,
                model_version=self.model_version,
                status=PredictionStatus.AVAILABLE,
            )

    missing = predict_effort(features(), RiderProfile(), predictor=_NeedsWeight())
    assert missing.status is PredictionStatus.INSUFFICIENT_DATA
    assert missing.missing_inputs == ("weight_kg",)
    assert missing.predicted_rpe is None

    # Com o dado presente, o preditor e chamado normalmente.
    present = predict_effort(
        features(), RiderProfile(weight_kg=70.0), predictor=_NeedsWeight()
    )
    assert present.status is PredictionStatus.AVAILABLE
    assert present.predicted_rpe == 13.0


def test_predict_effort_rejects_non_predictor():
    with pytest.raises(TypeError):
        predict_effort(features(), predictor=object())  # type: ignore[arg-type]


# ==========================================================================
# Preditor metabolico: preenche apenas a energia metabolica
# ==========================================================================
def metabolic_params():
    return MetabolicParameters(
        gross_efficiency=0.25, version="v1", provenance="laboratorio"
    )


def test_metabolic_predictor_fills_only_metabolic_energy():
    predictor = MetabolicEnergyPredictor(metabolic=metabolic_params())
    f = features(rider_work_j=100_000.0)
    p = predictor.predict(f, RiderProfile(), PredictionContext())
    assert p.metabolic_energy_j == pytest.approx(400_000.0)
    # Nada fisiologico e inventado.
    assert p.predicted_rpe is None
    assert p.heart_rate_zone_distribution is None
    assert p.confidence is None
    # O estado fisologico continua explicitamente nao calibrado.
    assert p.status is PredictionStatus.MODEL_NOT_CALIBRATED


def test_metabolic_predictor_is_versioned():
    predictor = MetabolicEnergyPredictor(metabolic=metabolic_params())
    assert predictor.model_version == "metabolic@v1"
    p = predictor.predict(features(), RiderProfile(), PredictionContext())
    assert p.model_version == "metabolic@v1"


def test_predict_effort_with_metabolic_predictor():
    predictor = MetabolicEnergyPredictor(metabolic=metabolic_params())
    p = predict_effort(features(rider_work_j=1000.0), predictor=predictor)
    assert p.metabolic_energy_j == pytest.approx(4000.0)
    assert p.predicted_rpe is None


# ==========================================================================
# Protocolo
# ==========================================================================
def test_implementations_satisfy_effort_predictor_protocol():
    assert isinstance(UnavailablePredictor(), EffortPredictor)
    assert isinstance(MetabolicEnergyPredictor(metabolic=metabolic_params()), EffortPredictor)


def test_protocol_exposes_model_version():
    assert isinstance(UnavailablePredictor().model_version, str)
    assert isinstance(
        MetabolicEnergyPredictor(metabolic=metabolic_params()).model_version, str
    )


# ==========================================================================
# Reprodutibilidade
# ==========================================================================
def test_prediction_is_deterministic():
    f = features()
    first = predict_effort(f)
    second = predict_effort(f)
    assert first == second


def test_prediction_does_not_mutate_features():
    f = features()
    before = f.to_dict()
    predict_effort(f, predictor=MetabolicEnergyPredictor(metabolic=metabolic_params()))
    assert f.to_dict() == before


# ==========================================================================
# Independencia do gerador de rotas
# ==========================================================================
def test_predictor_module_does_not_import_routing():
    """A inferencia nao conhece o roteamento.

    Ela depende apenas da fisica (``physical.effort``) e do contrato
    fisiologico (``physiology``). Assim, vale para qualquer rota — tradicional
    ou gerada por ML — sem alterar a busca.
    """
    tree = ast.parse(inspect.getsource(predictor_module))
    absolute, relative = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                absolute.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            (relative if node.level else absolute).add(name)

    assert relative == set()
    assert not any("search" in name for name in absolute)
    allowed = {"__future__", "dataclasses", "typing"}
    assert all(
        name in allowed or name.startswith("router.core.physics") for name in absolute
    ), absolute


# ==========================================================================
# Integracao fisica -> features -> previsao (qualquer rota)
# ==========================================================================
def test_end_to_end_from_physical_traversal():
    arcs = [make_arc([(0.0, 0.0), (1000.0, 20.0)])]
    path = PhysicalTraversal(step_m=5.0).traverse_path(
        arcs,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(3.0),
    )
    f = extract_effort_features(path)
    predictor = MetabolicEnergyPredictor(metabolic=metabolic_params())
    p = predictor.predict(f, RiderProfile(), PredictionContext())
    assert f.rider_work_j > 0.0
    assert p.metabolic_energy_j == pytest.approx(
        f.rider_work_j / 0.25, rel=1e-12
    )
    assert p.is_measurement is False


def test_multiple_routes_same_contract():
    """Rotas fisicas diferentes produzem o mesmo contrato de previsao."""
    paths = [
        PhysicalTraversal(step_m=5.0).traverse_path(
            [make_arc([(0.0, 0.0), (1000.0, 0.0)], arc_id="a")],
            CYCLIST,
            BICYCLE,
            initial_state=SimulationState.from_speed(4.0),
        ),
        PhysicalTraversal(step_m=5.0).traverse_path(
            [make_arc([(0.0, 0.0), (800.0, 30.0)], arc_id="b")],
            CYCLIST,
            BICYCLE,
            initial_state=SimulationState.from_speed(2.0),
        ),
    ]
    versions = {
        predict_effort(extract_effort_features(path)).model_version
        for path in paths
    }
    assert versions == {"unavailable"}
