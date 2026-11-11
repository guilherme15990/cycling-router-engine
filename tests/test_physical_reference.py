"""Testes de referencia da camada fisica (incremento 1 do ADR-025).

Dois grupos:

1. **Analiticos** (permanentes): formulas fechadas de forcas, energia e
   equilibrio. Nao dependem do esquema de integracao.
2. **Caracterizacao** (baseline): congelam o resultado numerico da engine
   *como esta* no momento da auditoria ADR-025. Servem para detectar qualquer
   mudanca de comportamento. Sao atualizados apenas com justificativa e
   comparacao registradas no `docs/adr/025-auditoria-fisica.md`.

Os valores de caracterizacao vem de `docs/adr/025-auditoria-fisica.md` (secao
7, baseline `0.14.0`). Qualquer divergencia deve ser investigada, nunca
"consertada" nos testes sem justificativa fisica.
"""

import math
import pytest
from router.core.data.graph.model import (
    BicycleAccess,
    Arc,
    Segment,
    ElevationSample,
)
from router.core.data.graph.surface import Surface, SurfaceStatus
from router.core.physics import (
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    SimulationState,
    SimulationStatus,
    aerodynamic_force_n,
    equilibrium_speed_mps,
    forces_at,
    gravity_force_n,
    required_rider_power_w,
    rolling_force_n,
)
from router.core.physics.energy import (
    kinetic_energy_j,
    potential_energy_j,
    rider_energy_from_wheel_j,
    wheel_energy_from_rider_j,
)

GRAVITY = 9.80665


def make_arc(
    points=None,
    *,
    distance_m=None,
    arc_id="e0",
    surface=Surface.ASPHALT,
    max_speed_kmh=None,
    dismount=False,
):
    if points is None:
        if distance_m is None:
            distance_m = 100.0
        profile = ()
        ascent = descent = 0.0
        minimum = maximum = None
    else:
        if distance_m is None:
            distance_m = points[-1][0]
        profile = tuple(
            ElevationSample(
                distance_m=d,
                elevation_m=e,
                source_id="reference",
                resolution_m=1.0,
            )
            for d, e in points
        )
        ascent = sum(max(0.0, c[1] - p[1]) for p, c in zip(points, points[1:]))
        descent = sum(max(0.0, p[1] - c[1]) for p, c in zip(points, points[1:]))
        heights = [e for _, e in points]
        minimum, maximum = min(heights), max(heights)
    segment = Segment(
        id="s0",
        distance_m=distance_m,
        surface=surface,
        surface_status=SurfaceStatus.MAPPED,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        min_elevation_m=minimum,
        max_elevation_m=maximum,
        elevation_profile=profile,
        bicycle_access=(
            BicycleAccess.DISMOUNT if dismount else BicycleAccess.YES
        ),
        max_speed_kmh=max_speed_kmh,
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
MASS = CYCLIST.mass_kg + BICYCLE.mass_kg  # 87 kg


# ==========================================================================
# Grupo 1 — analiticos (permanentes)
# ==========================================================================
def test_gravity_force_is_exact():
    expected = MASS * GRAVITY * math.sin(0.1)
    assert gravity_force_n(MASS, 0.1) == pytest.approx(expected, rel=1e-12)
    # Subida positiva, descida negativa, plano zero.
    assert gravity_force_n(MASS, 0.1) > 0.0
    assert gravity_force_n(MASS, -0.1) < 0.0
    assert gravity_force_n(MASS, 0.0) == pytest.approx(0.0, abs=1e-12)
    assert gravity_force_n(MASS, 0.1) == pytest.approx(85.175730, rel=1e-6)


def test_rolling_force_is_exact():
    flat = BICYCLE.rolling_resistance * MASS * GRAVITY
    assert rolling_force_n(MASS, 0.0, BICYCLE.rolling_resistance) == pytest.approx(
        flat, rel=1e-12
    )
    assert flat == pytest.approx(4.265893, rel=1e-6)
    # A forca normal cai com o cosseno da inclinacao.
    sloped = rolling_force_n(MASS, 0.1, BICYCLE.rolling_resistance)
    assert sloped == pytest.approx(flat * math.cos(0.1), rel=1e-12)
    # Rolamento e sempre resistivo (nao muda de sinal com a inclinacao).
    assert rolling_force_n(MASS, -0.3, BICYCLE.rolling_resistance) > 0.0


def test_aerodynamic_force_is_quadratic_with_sign():
    drag = 0.5 * 1.225 * BICYCLE.drag_coefficient * 0.5

    def aero(v, hw=0.0):
        return aerodynamic_force_n(v, BICYCLE.drag_coefficient, 0.5, 1.225, hw)

    assert aero(5.0) == pytest.approx(drag * 25.0, rel=1e-12)
    assert aero(10.0) == pytest.approx(4.0 * aero(5.0), rel=1e-12)
    assert aero(5.0) == pytest.approx(6.890625, rel=1e-6)
    # Cauda mais rapida que a bicicleta empurra para frente (forca negativa).
    assert aero(5.0, hw=-10.0) < 0.0
    assert aero(5.0, hw=5.0) > aero(5.0)


def test_kinetic_and_potential_energy_are_exact():
    assert kinetic_energy_j(87.0, 5.0) == pytest.approx(1087.5, rel=1e-12)
    assert potential_energy_j(87.0, 25.0) == pytest.approx(
        87.0 * GRAVITY * 25.0, rel=1e-12
    )
    assert potential_energy_j(87.0, 25.0) == pytest.approx(21329.46375, rel=1e-6)


def test_drivetrain_energy_conversion_is_conservative():
    eta = BICYCLE.drivetrain_efficiency
    # eta < 1: a roda recebe menos do que o pedivela entrega.
    assert wheel_energy_from_rider_j(1000.0, eta) == pytest.approx(1000.0 * eta)
    assert rider_energy_from_wheel_j(1000.0, eta) == pytest.approx(1000.0 / eta)
    assert wheel_energy_from_rider_j(1000.0, eta) < 1000.0
    # Round-trip e estavel.
    wheel = wheel_energy_from_rider_j(1234.0, eta)
    assert rider_energy_from_wheel_j(wheel, eta) == pytest.approx(1234.0, rel=1e-12)


def test_equilibrium_power_balance_is_exact():
    """Em equilibrio, a potencia requerida iguala a disponivel."""
    available = 200.0
    speed = equilibrium_speed_mps(0.0, MASS, 0.5, BICYCLE, Environment(), available)
    forces = forces_at(0.0, speed, MASS, 0.5, BICYCLE, Environment())
    required = required_rider_power_w(
        forces.total_n, speed, BICYCLE.drivetrain_efficiency
    )
    assert required == pytest.approx(available, rel=1e-3)
    # E a ordem fisica se mantem.
    uphill = equilibrium_speed_mps(0.05, MASS, 0.5, BICYCLE, Environment(), available)
    downhill = equilibrium_speed_mps(-0.05, MASS, 0.5, BICYCLE, Environment(), available)
    assert uphill < speed < downhill


# ==========================================================================
# Grupo 2 — caracterizacao (baseline 0.14.0 — atualizar so com justificativa)
# ==========================================================================
def state(speed_mps):
    """Estado dinamico de entrada com trabalho zerado (ADR-025)."""
    return SimulationState.from_speed(speed_mps)


def traverse(arc, *, step_m=5.0, **kwargs):
    return PhysicalTraversal(step_m=step_m).traverse(
        arc, CYCLIST, BICYCLE, **kwargs
    )


def test_baseline_flat_1000m_200w_from_rest():
    r = traverse(make_arc([(0.0, 0.0), (1000.0, 0.0)]))
    assert r.time_s == pytest.approx(126.8237, rel=1e-4)
    assert r.final_speed_mps == pytest.approx(8.3161, rel=1e-4)
    assert r.mechanical_energy_j == pytest.approx(24603.791, rel=1e-5)
    assert r.rider_energy_j == pytest.approx(25364.732, rel=1e-5)
    assert r.braking_energy_j == 0.0
    # A relacao pedivela <-> roda e exata por construcao.
    assert r.rider_energy_j == pytest.approx(
        r.mechanical_energy_j / BICYCLE.drivetrain_efficiency, rel=1e-9
    )


def test_baseline_uphill_and_downhill_ordering():
    uphill = traverse(
        make_arc([(0.0, 0.0), (500.0, 25.0)]), initial_state=state(5.0)
    )
    downhill = traverse(
        make_arc([(0.0, 25.0), (500.0, 0.0)]), initial_state=state(5.0)
    )
    assert uphill.time_s == pytest.approx(129.3223, rel=1e-4)
    assert downhill.time_s == pytest.approx(42.8996, rel=1e-4)
    assert downhill.time_s < uphill.time_s
    assert downhill.final_speed_mps > uphill.final_speed_mps


def test_baseline_downhill_without_pedalling():
    r = traverse(
        make_arc([(0.0, 25.0), (500.0, 0.0)]),
        initial_state=state(2.0),
        power_w=0.0,
    )
    assert r.time_s == pytest.approx(56.2662, rel=1e-4)
    assert r.final_speed_mps == pytest.approx(11.5637, rel=1e-4)
    assert r.final_speed_mps > 2.0
    assert r.rider_energy_j == 0.0
    assert r.mechanical_energy_j == 0.0


def test_baseline_braking_dissipates_energy():
    r = traverse(
        make_arc([(0.0, 50.0), (500.0, 0.0)], max_speed_kmh=18.0),
        initial_state=state(4.0),
    )
    assert r.final_speed_mps == pytest.approx(5.0, abs=1e-9)
    assert r.braking_energy_j == pytest.approx(36742.949, rel=1e-4)
    assert r.braking_energy_j > 0.0


def test_baseline_headwind_slower_than_tailwind():
    head = traverse(
        make_arc([(0.0, 0.0), (1000.0, 0.0)]),
        environment=Environment(headwind_mps=5.0),
    )
    tail = traverse(
        make_arc([(0.0, 0.0), (1000.0, 0.0)]),
        environment=Environment(headwind_mps=-5.0),
    )
    assert head.time_s == pytest.approx(182.7178, rel=1e-4)
    assert tail.time_s == pytest.approx(98.0474, rel=1e-4)
    assert head.time_s > tail.time_s
    assert head.final_speed_mps < tail.final_speed_mps


def test_baseline_consecutive_arcs_keep_state_continuous():
    first = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="a")
    second = make_arc([(0.0, 0.0), (200.0, 0.0)], arc_id="b")
    path = PhysicalTraversal(step_m=5.0).traverse_path(
        [first, second], CYCLIST, BICYCLE, initial_state=state(0.0)
    )
    assert path[1].initial_speed_mps == pytest.approx(
        path[0].final_speed_mps, rel=1e-12
    )
    assert path.final_speed_mps == pytest.approx(path[-1].final_speed_mps, rel=1e-12)


# --- Achado A: dependencia do passo (caracterizacao pre-incremento-4) ------
def _flat_time(step_m):
    return traverse(
        make_arc([(0.0, 0.0), (1000.0, 0.0)]), step_m=step_m
    ).time_s


def test_baseline_step_dependence_is_recorded():
    """Registra a dependencia do passo observada na auditoria (Achado A).

    Este teste NAO afirma que o modelo esta correto: ele congela o desvio
    atual para que o incremento 4 (integracao) possa ser comparado. Apos o
    incremento 4, estes numeros devem ser atualizados com justificativa.
    """
    t5 = _flat_time(5.0)
    t1 = _flat_time(1.0)
    t05 = _flat_time(0.5)
    assert t5 == pytest.approx(126.8237, rel=1e-4)
    assert t1 == pytest.approx(128.5754, rel=1e-4)
    assert t05 == pytest.approx(128.7451, rel=1e-4)
    # O passo grosso adianta o resultado em ~1.5% (> toleranca provisoria).
    assert (t05 - t5) / t05 > 0.01


# --- Achado B: elevacao ausente (resolvido no incremento 3) ----------------
def test_missing_elevation_is_numerically_flat_but_marked_degraded():
    """Sem elevacao, o numero permanece o de um plano — sob hipotese explicita.

    Achado B: antes do incremento 3 o resultado nao distinguia ausencia de
    elevacao. Agora o numero pode ser o mesmo (terreno plano presumido), mas o
    resultado e marcado ``DEGRADED`` e a hipotese fica registrada na incerteza.
    Um plano real continua ``VALID``.
    """
    flat = traverse(make_arc([(0.0, 0.0), (1000.0, 0.0)]))
    unknown = traverse(make_arc(distance_m=1000.0))
    # Numero identico sob a hipotese explicita de terreno plano.
    assert unknown.time_s == pytest.approx(flat.time_s, rel=1e-12)
    assert unknown.mechanical_energy_j == pytest.approx(
        flat.mechanical_energy_j, rel=1e-12
    )
    # ... mas a distincao de status existe (era o Achado B).
    assert flat.result_status is SimulationStatus.VALID
    assert unknown.result_status is SimulationStatus.DEGRADED
    assert flat.elevation_uncertainty.missing_distance_m == 0.0
    assert unknown.elevation_uncertainty.missing_distance_m == pytest.approx(1000.0)
    assert unknown.elevation_uncertainty.assumes_flat_terrain
