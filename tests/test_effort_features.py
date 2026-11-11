"""EffortFeatures: contrato, extração e propagação (ADR-025, incremento 6).

Cobertura exigida pelo incremento:

* unidades e mapeamento direto a partir de ``TraversalPath``;
* consistência de energia/potência;
* dados ausentes representados explicitamente (``None``), sem inferência;
* reprodutibilidade/determinismo;
* independência do gerador de rotas (sem dependência de ``search`` ou ``ml``);
* propagação de qualidade altimétrica, status e limitação numérica.
"""

import ast
import dataclasses
import inspect
import json

import pytest

import physical.effort as effort_module
from graph.model import Arc, ElevationSample, Segment
from physical import (
    BicycleModel,
    CyclistModel,
    EFFORT_FEATURES_VERSION,
    EffortFeatures,
    PhysicalTraversal,
    SimulationState,
    SimulationStatus,
    extract_effort_features,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
ETA = BICYCLE.drivetrain_efficiency


def make_arc(points, *, arc_id="e0", distance_m=None, max_speed_kmh=None):
    if points is None:
        distance = 0.0 if distance_m is None else float(distance_m)
        profile = ()
        ascent = descent = 0.0
    else:
        distance = points[-1][0]
        profile = tuple(
            ElevationSample(distance_m=d, elevation_m=e) for d, e in points
        )
        ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
        descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
    segment = Segment(
        id="s0",
        distance_m=distance,
        elevation_profile=profile,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        max_speed_kmh=max_speed_kmh,
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


def traverse_path(arcs, *, v0=0.0, power_w=None, environment=None, step_m=5.0):
    return PhysicalTraversal(step_m=step_m).traverse_path(
        arcs,
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(v0),
        environment=environment,
        power_w=power_w,
    )


def flat_features(v0=0.0, power_w=None):
    path = traverse_path([make_arc([(0.0, 0.0), (1000.0, 0.0)])], v0=v0, power_w=power_w)
    return path, extract_effort_features(path)


# ==========================================================================
# Unidades e mapeamento direto
# ==========================================================================
def test_features_map_directly_to_path_metrics():
    path, f = flat_features()
    m = path.metrics
    assert f.distance_m == pytest.approx(m.distance_m, rel=1e-12)
    assert f.duration_s == pytest.approx(m.time_s, rel=1e-12)
    assert f.average_speed_mps == pytest.approx(m.distance_m / m.time_s, rel=1e-12)
    assert f.mechanical_work_j == pytest.approx(m.mechanical_energy_j, rel=1e-12)
    assert f.rider_work_j == pytest.approx(m.energy_j, rel=1e-12)
    assert f.climbing_m == pytest.approx(m.ascent_m, rel=1e-12)
    assert f.descending_m == pytest.approx(m.descent_m, rel=1e-12)
    assert f.aero_loss_j == pytest.approx(m.aero_loss_j, rel=1e-12)
    assert f.rolling_loss_j == pytest.approx(m.rolling_loss_j, rel=1e-12)


def test_uphill_climbing_and_downhill_descending():
    up = extract_effort_features(
        traverse_path([make_arc([(0.0, 0.0), (500.0, 25.0)])], v0=5.0)
    )
    assert up.climbing_m == pytest.approx(25.0, rel=1e-9)
    assert up.descending_m == pytest.approx(0.0, abs=1e-12)

    down = extract_effort_features(
        traverse_path([make_arc([(0.0, 25.0), (500.0, 0.0)])], v0=5.0)
    )
    assert down.descending_m == pytest.approx(25.0, rel=1e-9)
    assert down.climbing_m == pytest.approx(0.0, abs=1e-12)


def test_multi_arc_route_aggregates():
    path = traverse_path(
        [
            make_arc([(0.0, 0.0), (300.0, 0.0)], arc_id="a"),
            make_arc([(0.0, 0.0), (300.0, 0.0)], arc_id="b"),
        ]
    )
    f = extract_effort_features(path)
    assert f.distance_m == pytest.approx(600.0, rel=1e-9)
    assert len(path.results) == 2


def test_braking_energy_is_propagated():
    path = traverse_path(
        [make_arc([(0.0, 50.0), (500.0, 0.0)], max_speed_kmh=18.0)], v0=4.0
    )
    f = extract_effort_features(path)
    assert f.braking_energy_j > 0.0
    assert f.braking_energy_j == pytest.approx(path.metrics.braking_energy_j, rel=1e-12)


# ==========================================================================
# Consistencia de energia / potencia
# ==========================================================================
def test_energy_and_power_consistency():
    _, f = flat_features()
    # Potencia media * duracao reconstroi o trabalho no pedivela.
    assert f.mean_power_w * f.duration_s == pytest.approx(f.rider_work_j, rel=1e-12)
    # Relacao pedivela <-> roda e exata por construcao (eta constante).
    assert f.rider_work_j == pytest.approx(f.mechanical_work_j / ETA, rel=1e-9)
    # Com perdas de transmissao, o pedivela entrega mais que a roda recebe.
    assert f.rider_work_j >= f.mechanical_work_j
    assert f.average_speed_mps == pytest.approx(
        f.distance_m / f.duration_s, rel=1e-12
    )


def test_mean_power_uses_rider_work_not_wheel_work():
    _, f = flat_features()
    assert f.mean_power_w == pytest.approx(f.rider_work_j / f.duration_s, rel=1e-12)
    assert f.mean_power_w != pytest.approx(
        f.mechanical_work_j / f.duration_s, rel=1e-6
    )


# ==========================================================================
# Dados ausentes explicitos (nada inferido)
# ==========================================================================
def test_unavailable_fields_are_none_not_invented():
    _, f = flat_features()
    assert f.peak_power_w is None
    assert f.power_variability is None
    assert f.metabolic_energy_j is None
    assert f.battery_energy_j is None
    fields = f.unavailable_fields
    for name in (
        "peak_power_w",
        "power_variability",
        "metabolic_energy_j",
        "battery_energy_j",
    ):
        assert name in fields


def test_metabolic_is_none_never_zero():
    """ADR-025, decisao 3: ausencia de energia metabolica e None, nunca 0."""
    _, f = flat_features()
    assert f.metabolic_energy_j is None
    assert f.metabolic_energy_j != 0.0


def test_no_physiological_fields_in_contract():
    names = {field.name for field in dataclasses.fields(EffortFeatures)}
    for forbidden in ("borg", "rpe", "heart_rate", "fatigue", "confidence"):
        assert not any(forbidden in name for name in names)


# ==========================================================================
# Qualidade altimetrica, status e limitacao numerica
# ==========================================================================
def test_observed_elevation_coverage_is_full_for_observed_profile():
    _, f = flat_features()
    assert f.elevation_coverage == pytest.approx(1.0)
    assert f.observed_elevation_coverage == pytest.approx(1.0)
    assert f.result_status is SimulationStatus.VALID
    assert f.elevation_uncertainty is not None
    assert not f.elevation_uncertainty.has_gap


def test_unknown_elevation_is_degraded_and_zero_coverage():
    path = traverse_path([make_arc(None, distance_m=1000.0)])
    f = extract_effort_features(path)
    assert f.elevation_coverage == pytest.approx(0.0)
    assert f.observed_elevation_coverage == pytest.approx(0.0)
    assert f.result_status is SimulationStatus.DEGRADED
    assert f.elevation_uncertainty is not None
    assert f.elevation_uncertainty.has_gap


def test_status_and_regularization_are_propagated():
    rest_path, rest = flat_features(v0=0.0)
    assert rest.used_min_speed_regularization is True
    assert rest.result_status is rest_path.result_status

    smooth_path, smooth = flat_features(v0=6.0)
    assert smooth.used_min_speed_regularization is False
    assert smooth.result_status is smooth_path.result_status


# ==========================================================================
# Reprodutibilidade / determinismo
# ==========================================================================
def test_extraction_is_deterministic():
    path, first = flat_features()
    second = extract_effort_features(path)
    assert first == second


def test_identical_construction_yields_equal_features():
    _, first = flat_features()
    _, second = flat_features()
    assert first == second


# ==========================================================================
# Independencia do gerador de rotas
# ==========================================================================
def test_module_depends_only_on_physical_models():
    """O extrator nao conhece roteamento nem ML.

    A extracao deve funcionar para qualquer rota (tradicional ou gerada por
    ML), logo nao pode importar ``search`` nem ``ml``. Verificamos isso pela
    AST do proprio modulo: nenhum import absoluto alem da stdlib e imports
    relativos restritos ao proprio pacote fisico.
    """
    tree = ast.parse(inspect.getsource(effort_module))
    absolute, relative = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                absolute.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            name = (node.module or "").split(".")[0]
            (relative if node.level else absolute).add(name)

    assert "search" not in absolute
    assert "ml" not in absolute
    assert absolute <= {"__future__", "dataclasses", "typing"}
    assert relative <= {"_validation", "models"}


# ==========================================================================
# Contrato: versionamento, serializacao e imutabilidade
# ==========================================================================
def test_contract_is_versioned():
    assert EffortFeatures.schema_version == EFFORT_FEATURES_VERSION
    assert EffortFeatures.schema_version == "1"


def test_to_dict_is_complete_and_json_serializable():
    _, f = flat_features()
    payload = f.to_dict()
    assert payload["schema_version"] == EFFORT_FEATURES_VERSION
    assert payload["result_status"] == "valid"
    assert payload["peak_power_w"] is None
    assert isinstance(payload["unavailable_fields"], list)
    expected = {field.name for field in dataclasses.fields(EffortFeatures)}
    assert expected <= set(payload)
    # Deve ser serializavel em JSON sem conversoes extras.
    json.dumps(payload)


def test_features_are_immutable():
    _, f = flat_features()
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.duration_s = 1.0  # type: ignore[misc]


def test_validation_rejects_invalid_values():
    _, f = flat_features()
    with pytest.raises(ValueError):
        dataclasses.replace(f, rider_work_j=-1.0)
    with pytest.raises(ValueError):
        dataclasses.replace(f, elevation_coverage=1.5)
    with pytest.raises(ValueError):
        dataclasses.replace(f, mean_power_w=-0.1)
