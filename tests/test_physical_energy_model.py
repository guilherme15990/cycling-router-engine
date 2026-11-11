"""Testes do modelo energetico generico e da viabilidade de bateria."""

import pytest

from physical import (
    JOULES_PER_WH,
    BatteryConfig,
    BatteryFeasibility,
    EnergyModel,
    GenericEnergyConfig,
    assess_battery,
)


# --------------------------------------------------------------------------
# Configuracao generica
# --------------------------------------------------------------------------
def test_generic_config_has_reference_defaults():
    config = GenericEnergyConfig()
    assert config.mass_kg > 0.0
    assert 0.0 < config.drivetrain_efficiency <= 1.0
    assert config.rolling_resistance >= 0.0
    assert config.drag_coefficient >= 0.0
    assert config.frontal_area_m2 >= 0.0


def test_generic_config_validation():
    with pytest.raises(ValueError):
        GenericEnergyConfig(mass_kg=0.0)
    with pytest.raises(ValueError):
        GenericEnergyConfig(rolling_resistance=-1.0)
    with pytest.raises(ValueError):
        GenericEnergyConfig(drivetrain_efficiency=0.0)
    with pytest.raises(ValueError):
        GenericEnergyConfig(drivetrain_efficiency=1.5)


# --------------------------------------------------------------------------
# Conversao mecanica -> eletrica
# --------------------------------------------------------------------------
def test_energy_model_defaults_to_generic_level():
    model = EnergyModel.generic()
    assert model.level == "generic"
    assert model.efficiency == GenericEnergyConfig().drivetrain_efficiency


def test_electrical_energy_is_mechanical_over_efficiency():
    model = EnergyModel(GenericEnergyConfig(drivetrain_efficiency=0.5))
    assert model.electrical_energy_j(1000.0) == pytest.approx(2000.0)
    assert model.mechanical_energy_j(2000.0) == pytest.approx(1000.0)


def test_estimate_reports_joules_and_watt_hours():
    model = EnergyModel(GenericEnergyConfig(drivetrain_efficiency=0.75))
    estimate = model.estimate(2700.0)
    assert estimate.mechanical_energy_j == pytest.approx(2700.0)
    assert estimate.electrical_energy_j == pytest.approx(3600.0)
    assert estimate.electrical_energy_wh == pytest.approx(1.0)
    assert estimate.efficiency == pytest.approx(0.75)
    assert estimate.level == "generic"
    assert estimate.to_dict()["electrical_energy_wh"] == pytest.approx(1.0)


def test_negative_mechanical_energy_is_clamped():
    model = EnergyModel.generic()
    assert model.electrical_energy_j(-500.0) == 0.0
    assert model.estimate(-500.0).electrical_energy_j == 0.0


def test_configured_model_marks_level():
    configured = EnergyModel.configured(
        GenericEnergyConfig(mass_kg=100.0), level="personalized"
    )
    assert configured.level == "personalized"
    calibrated = EnergyModel.configured(
        GenericEnergyConfig(), level="calibrated"
    )
    assert calibrated.level == "calibrated"


def test_electrical_wh_conversion_constant():
    assert JOULES_PER_WH == pytest.approx(3600.0)


# --------------------------------------------------------------------------
# Bateria
# --------------------------------------------------------------------------
def test_battery_defaults_charge_to_capacity():
    battery = BatteryConfig(capacity_wh=500.0)
    assert battery.available_energy_wh == pytest.approx(500.0)
    assert battery.reserve_energy_wh == pytest.approx(50.0)
    assert battery.usable_energy_wh == pytest.approx(450.0)


def test_battery_validation():
    with pytest.raises(ValueError):
        BatteryConfig(capacity_wh=0.0)
    with pytest.raises(ValueError):
        BatteryConfig(capacity_wh=500.0, initial_charge_wh=600.0)
    with pytest.raises(ValueError):
        BatteryConfig(capacity_wh=500.0, reserve_ratio=1.5)
    with pytest.raises(ValueError):
        BatteryConfig(capacity_wh=500.0, reserve_ratio=-0.1)


def test_assess_battery_feasible_and_remaining():
    battery = BatteryConfig(capacity_wh=500.0, reserve_ratio=0.1)
    feasibility = assess_battery(battery, consumption_wh=200.0)
    assert isinstance(feasibility, BatteryFeasibility)
    assert feasibility.feasible is True
    assert feasibility.remaining_energy_wh == pytest.approx(300.0)
    assert feasibility.remaining_ratio == pytest.approx(0.6)
    assert feasibility.shortfall_wh == 0.0
    assert "Wh restantes" in feasibility.message()


def test_assess_battery_flags_insufficient_energy():
    battery = BatteryConfig(capacity_wh=500.0, reserve_ratio=0.1)
    feasibility = assess_battery(battery, consumption_wh=480.0)
    assert feasibility.feasible is False
    assert feasibility.shortfall_wh == pytest.approx(30.0)
    assert "insuficiente" in feasibility.message().lower()
    assert feasibility.to_dict()["feasible"] is False


def test_assess_battery_rejects_negative_consumption():
    with pytest.raises(ValueError):
        assess_battery(BatteryConfig(capacity_wh=500.0), consumption_wh=-1.0)
