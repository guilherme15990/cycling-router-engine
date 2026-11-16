"""Campos de entrada preenchidos pelos presets públicos da API."""
import streamlit as st
from router.integration import MANUAL_RIDER_PRESETS, ManualRiderProfile


def is_manual_bicycle():
    return (st.session_state.mobility_vehicle in ("bicycle", "cargo_bike")
            and st.session_state.mobility_propulsion == "manual")


def sync_power_controls():
    """Preserva potência/unidade dos outros veículos enquanto o campo fica oculto."""
    state = st.session_state
    manual = is_manual_bicycle()
    previous = state.get("power_controls_manual")
    if manual and previous is False:
        state.saved_vehicle_power = state.get("mobility_power", 0.0)
        state.saved_vehicle_power_unit = state.get("mobility_power_unit", "W")
    elif not manual and previous is True:
        state.mobility_power = state.get("saved_vehicle_power", 0.0)
        state.mobility_power_unit = state.get("saved_vehicle_power_unit", "W")
    state.power_controls_manual = manual


def render_controls():
    if not is_manual_bicycle():
        return
    presets = {preset.profile.value: preset for preset in MANUAL_RIDER_PRESETS}
    st.subheader("Ciclista manual · estimativas por perfil")
    st.selectbox("Perfil do ciclista", list(presets), key="manual_fitness",
                 format_func=lambda key: presets[key].label)
    if st.session_state.mobility_rider_mass <= 0:
        st.session_state.mobility_rider_mass = 75.0
    a, b = st.columns(2)
    a.number_input("Peso do ciclista (kg)", min_value=1.0, step=1.0, key="mobility_rider_mass")
    b.number_input("Idade (anos; opcional)", min_value=5, max_value=100, step=1, key="manual_age")
    rider = ManualRiderProfile(st.session_state.manual_fitness, st.session_state.mobility_rider_mass,
                               st.session_state.manual_age)
    st.number_input("Potência estimada do perfil (W)", value=rider.reference_power_w,
                   disabled=True, key=f"manual_reference_{st.session_state.manual_fitness}")
    st.caption("A potência vem do preset da API e não muda com o peso. O ritmo é automático: "
               "a simulação busca 18 km/h, limitada pela potência do perfil e pelos limites da via. "
               "Esse ritmo é uma hipótese provisória; subidas podem reduzir a velocidade e descidas podem permitir embalo. "
               "Peso inicial de 75 kg é provisório; informe seu peso. "
               "A idade é registrada, mas não altera o baseline de RPE.")


def request_payload():
    if not is_manual_bicycle():
        return None
    return dict(profile=st.session_state.manual_fitness,
                mass_kg=st.session_state.mobility_rider_mass,
                age_years=st.session_state.manual_age)


def render_estimate(estimate, table):
    if not estimate:
        return
    st.subheader("RPE estimado por perfil · Borg 6–20")
    a, b, c = st.columns(3)
    a.metric("RPE médio estimado", f"{estimate['mean_rpe']:.1f}")
    b.metric("Pico de RPE estimado", f"{estimate['peak_rpe']:.1f}")
    c.metric("Intensidade relativa média", f"{100 * estimate['relative_intensity']:.0f}%")
    st.caption(f"{estimate['rider']['label']} · referência {estimate['rider']['reference_power_w']:g} W · "
               f"potência humana média simulada {estimate['mean_power_w']:.1f} W. "
               "Estimativa provisória por perfil; não é uma previsão de ML treinada.")
    with st.expander("Entradas e cálculo do RPE"):
        st.json(estimate['rider'])
        if estimate.get('pacing'):
            st.json(estimate['pacing'])
        table([{"Trecho": row['arc_id'], "Tempo (s)": round(row['duration_s'], 1),
                "Potência humana (W)": round(row['human_power_w'], 1),
                "Intensidade (%)": round(100 * row['relative_intensity'], 1),
                "RPE estimado": round(row['rpe'], 1)} for row in estimate['segments']])
        for limitation in estimate['limitations']:
            st.caption(limitation)
