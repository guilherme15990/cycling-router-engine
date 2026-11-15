"""Rider preset controls; definitions and resolution come from the toolkit."""
import streamlit as st

from router.integration import RiderPresets

CAPACITY_LABELS = {
    "sedentary": "Sedentário", "beginner": "Iniciante", "recreational": "Recreacional",
    "trained": "Treinado", "competitive": "Competitivo", "elite": "Elite",
}
ACTIVITY_LABELS = {
    "sedentary": "Sedentário", "lightly_active": "Pouco ativo", "moderately_active": "Moderadamente ativo",
    "active": "Ativo", "very_active": "Muito ativo", "highly_active": "Extremamente ativo",
}


def init_state():
    s = st.session_state
    defaults = RiderPresets.catalogue()["defaults"]
    values = dict(rider_capacity="", rider_activity="", rider_ftp=0.0, rider_cp=0.0,
                  rider_w_prime=0.0, rider_age=0.0, rider_hr_max=0.0, rider_hr_rest=0.0,
                  cyclist_mass=defaults["rider_mass_kg"], bike_mass=defaults["vehicle_mass_kg"],
                  max_power=defaults["max_power_w"], frontal_area=defaults["frontal_area_m2"],
                  cd=defaults["drag_coefficient"], crr=defaults["rolling_resistance"],
                  efficiency=defaults["drive_efficiency"])
    saved = s.get("_rider_saved_values", {})
    for key, value in values.items():
        s.setdefault(key, saved.get(key, value))
    if not s.get("_rider_controls_restored"):
        # Preserve any personal refinements entered in the previous UI.
        if s.get("mobility_rider_mass", 0) > 0:
            s.cyclist_mass = s.mobility_rider_mass
        if s.get("mobility_vehicle_mass", 0) > 0:
            s.bike_mass = s.mobility_vehicle_mass
        if not s.get("mobility_power") and s.get("sustained_power", defaults["power_w"]) != defaults["power_w"]:
            s.mobility_power = s.sustained_power
            s.mobility_power_unit = "W"
        s._rider_controls_restored = True
    # Keep selections when switching temporarily to a non-cycling vehicle;
    # Streamlit otherwise removes the state of widgets no longer rendered.
    s._rider_saved_values = {key: s[key] for key in values}


def render_selectors():
    catalog = RiderPresets.catalogue()
    capacities = {row["id"]: row for row in catalog["capacity_profiles"]}
    activities = {row["id"]: row for row in catalog["activity_levels"]}
    st.subheader("Perfil do ciclista", icon=":material/directions_bike:")
    a, b = st.columns(2)
    a.selectbox("Perfil de capacidade", ["", *capacities], key="rider_capacity",
        format_func=lambda key: "Automático pelo nível de exercício / padrão" if not key else
        f"{CAPACITY_LABELS[key]} · {capacities[key]['reference_w_per_kg']:g} W/kg")
    b.selectbox("Nível de exercício habitual", ["", *activities], key="rider_activity",
        format_func=lambda key: "Não informado" if not key else
        f"{ACTIVITY_LABELS[key]} · {activities[key]['description']}")
    st.caption("FTP/CP informadas têm prioridade na referência. Depois vêm o perfil explícito e o nível de exercício; sem seleção, mantém-se o padrão de 200 W. Uma potência manual da simulação prevalece sobre essa referência.")
    if st.session_state.mobility_propulsion != "manual":
        st.caption("Na propulsão elétrica, o perfil é registrado para inspeção e não substitui a potência do conjunto elétrico.")


def render_physical_inputs():
    with st.expander("Ciclista e bicicleta — valores padrão e ajustes", expanded=True):
        a, b, c = st.columns(3)
        a.number_input("Massa do ciclista (kg)", min_value=1.0, step=1.0, key="cyclist_mass")
        b.number_input("Massa da bicicleta (kg)", min_value=1.0, step=0.5, key="bike_mass")
        c.number_input("Potência máxima do ciclista (W)", min_value=1.0, step=10.0, key="max_power")
        a, b, c, d = st.columns(4)
        a.number_input("Área frontal (m²)", min_value=0.01, step=0.01, key="frontal_area")
        b.number_input("Coeficiente aerodinâmico (Cd)", min_value=0.0, step=0.05, key="cd")
        c.number_input("Resistência ao rolamento (Crr)", min_value=0.0, step=0.001, format="%.3f", key="crr")
        d.number_input("Eficiência da transmissão", min_value=0.01, max_value=1.0, step=0.01, key="efficiency")
        st.caption("Padrões anteriores: ciclista 75 kg, bicicleta 12 kg, potência provisória 200 W, máxima 600 W, área frontal 0,5 m², Cd 0,9, Crr 0,005 e eficiência 0,97. São configurações do cenário, não medições individuais.")
    with st.expander("Referências individuais opcionais"):
        a, b, c = st.columns(3)
        a.number_input("FTP informada (W; 0 = ausente)", min_value=0.0, step=5.0, key="rider_ftp")
        b.number_input("Potência crítica CP (W; 0 = ausente)", min_value=0.0, step=5.0, key="rider_cp")
        c.number_input("W′ (J; 0 = ausente)", min_value=0.0, step=500.0, key="rider_w_prime")
        a, b, c = st.columns(3)
        a.number_input("Idade (anos; 0 = ausente)", min_value=0.0, max_value=100.0, step=1.0, key="rider_age")
        b.number_input("FC máxima informada (bpm; 0 = ausente)", min_value=0.0, step=1.0, key="rider_hr_max")
        c.number_input("FC de repouso informada (bpm; 0 = ausente)", min_value=0.0, step=1.0, key="rider_hr_rest")


def rider_request():
    s = st.session_state
    return dict(weight_kg=s.cyclist_mass, max_power_w=s.max_power,
                capacity_profile=s.rider_capacity or None, activity_level=s.rider_activity or None,
                ftp_w=s.rider_ftp or None, critical_power_w=s.rider_cp or None, w_prime_j=s.rider_w_prime or None,
                age_years=s.rider_age or None, max_heart_rate_bpm=s.rider_hr_max or None,
                resting_heart_rate_bpm=s.rider_hr_rest or None)


def render_resolved(mobility, context):
    from layer_lab import _table
    if context is None:
        return
    ref = context["reference"]
    st.caption(f"Perfil resolvido: {CAPACITY_LABELS.get(context['capacity_profile'], 'Padrão do motor')} · referência {ref['power_w']:g} W ({ref['w_per_kg']:g} W/kg) · origem: {ref['source']}.")
    _table([
        {"Parâmetro aplicado": "Massa total (ciclista + bicicleta + carga)", "Valor": f"{mobility.mass_total_kg:g} kg"},
        {"Parâmetro aplicado": "Potência constante da simulação", "Valor": f"{mobility.nominal_power_w:g} W"},
        {"Parâmetro aplicado": "Área aerodinâmica CdA", "Valor": f"{mobility.cda_m2:g} m²"},
        {"Parâmetro aplicado": "Rolamento / transmissão", "Valor": f"{mobility.rolling_resistance:g} / {mobility.drive_efficiency:g}"},
    ])
    st.caption("Quando não há potência manual, usar a referência do perfil como potência constante é uma hipótese da simulação. FC, FTP e CP mantêm seus tipos e suas origens no resultado.")
