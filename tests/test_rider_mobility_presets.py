import pytest

from router.integration import RiderPresets, MobilityProfile
from test_mobility_toolkit import engine


@pytest.mark.parametrize("capacity,power", [
    ("sedentary", 75), ("beginner", 112.5), ("recreational", 150),
    ("trained", 225), ("competitive", 315), ("elite", 412.5),
])
def test_existing_capacity_presets_feed_the_new_engine(capacity, power):
    plan = engine().plan((0, 0), (0, .002), rider=dict(capacity_profile=capacity, weight_kg=75))
    assert plan.reached
    assert plan.mobility["nominal_power_w"] == pytest.approx(power)
    assert plan.diagnostics["rider"]["reference"]["source"] == "profile_estimated"
    assert plan.diagnostics["scenario"]["propulsion_parameters"]["power_w"] == pytest.approx(power)
    assert plan.diagnostics["rider"]["capacity_profile"] == capacity


def test_activity_is_used_only_without_explicit_capacity_and_measurement_has_priority():
    mobility = MobilityProfile.create()
    active, context = RiderPresets.resolve(mobility, dict(weight_kg=75, activity_level="active"))
    assert active.nominal_power_w == 225
    assert context["capacity_profile"] == "trained"
    beginner, context = RiderPresets.resolve(mobility, dict(weight_kg=75, activity_level="active", capacity_profile="beginner"))
    assert beginner.nominal_power_w == 112.5
    measured, context = RiderPresets.resolve(mobility, dict(weight_kg=75, activity_level="active", capacity_profile="beginner", ftp_w=240))
    assert measured.nominal_power_w == 240
    assert context["reference"]["kind"] == "ftp"
    assert context["reference"]["source"] == "measured"
    assert dict(measured.parameter_sources)["nominal_power_w"] == "rider_reference_assumption"


def test_manual_power_override_and_previous_default_are_preserved():
    profile, context = RiderPresets.resolve(MobilityProfile.create(), dict(weight_kg=75))
    assert profile.nominal_power_w == 200
    assert context["reference"]["source"] == "provisional_default"
    explicit = MobilityProfile.create(power_w=180)
    profile, context = RiderPresets.resolve(explicit, dict(weight_kg=75, capacity_profile="elite"))
    assert profile.nominal_power_w == 180
    assert context["reference"]["power_w"] == 412.5
    assert context["simulation_power_source"] == "provided"


def test_cyclist_presets_do_not_replace_electric_motor_power():
    mobility = MobilityProfile.create("bicycle", "electric")
    profile, context = RiderPresets.resolve(mobility, dict(capacity_profile="sedentary", weight_kg=75))
    assert profile.nominal_power_w == mobility.nominal_power_w == 250
    assert context["reference"]["power_w"] == 75
    assert context["applies_to_propulsion"] is False


def test_critical_power_keeps_its_type_and_unknown_heart_rate_stays_unknown():
    profile, context = RiderPresets.resolve(MobilityProfile.create(), dict(weight_kg=75, critical_power_w=210))
    assert profile.nominal_power_w == 210
    assert context["reference"]["kind"] == "critical_power"
    assert context["heart_rate"]["max_heart_rate_bpm"] is None
    assert context["heart_rate"]["resting_heart_rate_bpm"] is None
    with pytest.raises(ValueError, match="critical_power_w"):
        RiderPresets.resolve(profile, dict(w_prime_j=15000))
    with pytest.raises(ValueError, match="máxima"):
        RiderPresets.resolve(MobilityProfile.create(power_w=700), dict(max_power_w=600))
