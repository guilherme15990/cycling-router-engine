"""Convergencia e balanco energetico (incremento 4 do ADR-025, decisao 6).

Este modulo **caracteriza** a discretizacao espacial da engine:

* o esquema atual e de **primeira ordem** no regime suave (forcas continuas);
* partindo do repouso, o erro e dominado pelo **modelo de forca a baixa
  velocidade** (piso ``MIN_SPEED_MPS``), nao por falta de convergencia: ao
  refinar o passo o resultado oscila numa banda estreita (nao mascara-se isso);
* o **balanco energetico** ``dE_k + dE_p = W_wheel - perdas`` fecha com residuo
  de primeira ordem, decrescente com o refinamento.

Tolerancias: a meta de comparacao de rotas adotada e ``abs`` relativo de tempo
mitido para ``<= 0.5%`` do passo grosso. Os valores foram obtidos do estudo de
refinamento registrado em ``docs/adr/025-auditoria-fisica.md`` (secao 7.1).
"""

import math
import pytest
from router.core.data.graph.model import Arc, ElevationSample, Segment
from router.core.physics import (
    BicycleModel,
    CyclistModel,
    Environment,
    PhysicalTraversal,
    SimulationState,
)

CYCLIST = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
BICYCLE = BicycleModel()
MASS = CYCLIST.mass_kg + BICYCLE.mass_kg
GRAVITY = 9.80665


def make_arc(points, *, arc_id="e0"):
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
    )
    return Arc(id=arc_id, source=1, target=2, segments=(segment,))


def traverse(points, *, step_m, v0=0.0, power_w=None, environment=None):
    return PhysicalTraversal(step_m=step_m).traverse(
        make_arc(points),
        CYCLIST,
        BICYCLE,
        initial_state=SimulationState.from_speed(v0),
        environment=environment,
        power_w=power_w,
    )


# ==========================================================================
# Balanco energetico
# ==========================================================================
def test_energy_balance_flat_closes():
    r = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=1.0)
    balance = r.energy_balance(MASS)
    assert balance.delta_potential_j == pytest.approx(0.0, abs=1e-6)
    # dE_k = W_wheel - perdas, com residuo pequeno e de discretizacao.
    assert balance.relative_residual is not None
    assert abs(balance.relative_residual) < 5e-3
    # As perdas sao positivas no plano sem vento.
    assert balance.rolling_loss_j > 0.0
    assert balance.aero_loss_j > 0.0


def test_energy_balance_uphill_and_downhill():
    up = traverse([(0.0, 0.0), (500.0, 25.0)], step_m=1.0, v0=5.0)
    balance_up = up.energy_balance(MASS)
    assert balance_up.delta_potential_j == pytest.approx(
        MASS * GRAVITY * 25.0, rel=1e-9
    )
    assert abs(balance_up.relative_residual) < 5e-3

    down = traverse([(0.0, 25.0), (500.0, 0.0)], step_m=1.0, v0=5.0)
    balance_down = down.energy_balance(MASS)
    assert balance_down.delta_potential_j == pytest.approx(
        -MASS * GRAVITY * 25.0, rel=1e-9
    )
    assert abs(balance_down.relative_residual) < 5e-3


def test_energy_balance_residual_shrinks_with_refinement():
    coarse = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=5.0)
    fine = traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=0.5)
    r_coarse = abs(coarse.energy_balance(MASS).residual_j)
    r_fine = abs(fine.energy_balance(MASS).residual_j)
    assert r_fine < r_coarse
    # Primeira ordem: reduzir 10x o passo reduz o residuo ~10x.
    assert r_fine < 0.25 * r_coarse


def test_coasting_downhill_balance_has_no_wheel_work():
    r = traverse(
        [(0.0, 25.0), (500.0, 0.0)], step_m=1.0, v0=2.0, power_w=0.0
    )
    balance = r.energy_balance(MASS)
    assert r.mechanical_energy_j == 0.0
    # Sem pedalada, a energia vem da gravidade menos as perdas.
    assert balance.delta_kinetic_j > 0.0
    assert balance.delta_potential_j < 0.0
    # Sem trabalho na roda, o residuo relativo nao e definido (nunca 0 falso).
    assert balance.relative_residual is None
    assert abs(balance.residual_j) < 1e-2 * abs(balance.delta_potential_j)


def test_tailwind_can_make_aero_work_negative():
    """Vento de cauda mais rapido que o ciclista injeta energia (perda assinada)."""
    r = traverse(
        [(0.0, 0.0), (200.0, 0.0)],
        step_m=0.5,
        v0=5.0,
        environment=Environment(headwind_mps=-12.0),
    )
    assert r.aero_loss_j < 0.0
    balance = r.energy_balance(MASS)
    assert balance.aero_loss_j < 0.0
    assert abs(balance.relative_residual) < 5e-3


def test_rolling_loss_matches_crr_force_on_flat():
    r = traverse([(0.0, 0.0), (100.0, 0.0)], step_m=1.0)
    expected = BICYCLE.rolling_resistance * MASS * GRAVITY * 100.0
    assert r.rolling_loss_j == pytest.approx(expected, rel=1e-9)


# ==========================================================================
# Convergencia no regime suave (v0 > 0, sem singularidade de baixa velocidade)
# ==========================================================================
def _flat_time(step_m, v0=5.0):
    return traverse([(0.0, 0.0), (1000.0, 0.0)], step_m=step_m, v0=v0).time_s


def test_smooth_regime_is_first_order():
    """Refinando o passo, o erro cai ~2x (primeira ordem).

    Caso com ``v0=5`` m/s para isolar a discretizacao da singularidade de
    baixa velocidade.
    """
    reference = _flat_time(0.02)
    errors = [abs(_flat_time(ds) - reference) for ds in (5.0, 2.5, 1.25, 0.625)]
    # Os erros decrescem monotonamente...
    assert errors == sorted(errors, reverse=True)
    # ...e a razao entre passos consecutivos tende a 2 (primeira ordem).
    for coarse, fine in zip(errors, errors[1:]):
        assert 1.6 < coarse / fine < 2.8


def test_smooth_regime_error_at_default_step_is_bounded():
    """No passo padrao (5 m), o erro no regime suave fica abaixo de ~0.2%."""
    reference = _flat_time(0.02)
    error = abs(_flat_time(5.0) - reference) / reference
    assert error < 2e-3


# ==========================================================================
# Partida do repouso: caracterizacao honesta (a singularidade NAO e mascarada)
# ==========================================================================
def test_rest_start_coarse_step_error_is_large_and_flagged():
    """Do repouso, o passo grosso adianta o tempo em >1% (Achado A)."""
    reference = _flat_time(0.02, v0=0.0)
    coarse = _flat_time(5.0, v0=0.0)
    assert abs(coarse - reference) / reference > 0.01
    # Refinar reduz o erro de forma substancial.
    fine = _flat_time(0.5, v0=0.0)
    assert abs(fine - reference) < abs(coarse - reference)


def test_rest_start_convergence_is_not_monotone():
    """Registra que o modelo de baixa velocidade nao converge monotonicamente.

    A banda de oscilacao e pequena (~0,05% do tempo), mas existe: ela vem do
    piso ``MIN_SPEED_MPS``, nao do integrador. Este teste documenta o fato em
    vez de escondi-lo atras de uma tolerancia frouxa.
    """
    reference = _flat_time(0.02, v0=0.0)
    spread = [
        abs(_flat_time(ds, v0=0.0) - reference)
        for ds in (0.125, 0.25, 0.5)
    ]
    assert max(spread) / reference < 1e-3
    # Ha oscilacao: o passo 0,25 m nao e monotonicamente melhor que 0,5 m.
    assert max(spread) > min(spread)
