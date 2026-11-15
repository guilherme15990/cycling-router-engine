"""Streamlit presentation of the native MobilityPlan, without routing logic."""
import json
import streamlit as st


def render(plan, *, render_chart=None):
    from layer_lab import _table
    s = st.session_state
    st.divider()
    st.subheader("Resultado da toolkit", icon=":material/route:")
    if st.button("Limpar resultado", disabled=s.get("route_job") is not None, width="stretch"):
        s.last_plan = s.last_run = s.layer_report = None
        st.rerun()
    status = s.get("progress_status")
    if status == "interrupted":
        st.warning("Consulta interrompida. O último resultado validado foi preservado e está incompleto.")
    elif not plan.search_complete:
        st.info("Resultado parcial validado. A busca continua; alternativas e recomendação podem mudar.")
    else:
        st.caption("Consulta concluída sobre a rede preparada. A cobertura e a precisão estão declaradas abaixo.")
    if plan.state_approximated:
        st.caption("Comparação por buckets de velocidade: a fronteira é aproximada.")
    if plan.diagnostics.get("truncated") or plan.diagnostics.get("frontier_truncated"):
        st.warning("Um limite de busca foi atingido; a fronteira pode estar incompleta.")
    run = s.get("last_run") or {}
    if run:
        st.caption(f"{run['timestamp']} · {run['elapsed']:.2f} s · {plan.mobility['vehicle']} / {plan.mobility['propulsion']}")
    st.caption(f"{plan.pareto_route_count} candidata(s) na fronteira · {len(plan.routes)} apresentada(s) · acesso: {plan.diagnostics.get('access_mode', '—')}")
    rider = plan.diagnostics.get("rider")
    if rider:
        from rider_controls import CAPACITY_LABELS, ACTIVITY_LABELS
        st.subheader("Ciclista usado nesta consulta")
        reference = rider["reference"]
        a, b, c = st.columns(3)
        a.metric("Perfil de capacidade", CAPACITY_LABELS.get(rider["capacity_profile"], "Padrão do motor"))
        b.metric("Nível de exercício", ACTIVITY_LABELS.get(rider["activity_level"], "Não informado"))
        c.metric("Potência da simulação", f"{rider['simulation_power_w']:g} W")
        st.caption(f"Referência: {reference['power_w']:g} W · {reference['w_per_kg']:g} W/kg · tipo: {reference['kind']} · origem: {reference['source']}")
        with st.expander("Dados e origem do perfil do ciclista"):
            st.json(rider, expanded=False)
    if plan.routes:
        selected = s.get("selected_route", 0)
        if not isinstance(selected, int) or not 0 <= selected < len(plan.routes):
            s.selected_route = 0
        index = st.selectbox("Rota", range(len(plan.routes)), key="selected_route",
            format_func=lambda i: f"{'[recomendada] ' if plan.routes[i].recommended else ''}{plan.routes[i].route_id} | {plan.routes[i].metrics.distance_m / 1000:.2f} km | {plan.routes[i].metrics.time_s / 60:.1f} min")
        route = plan.routes[index]
        m = route.metrics
        a, b, c, d = st.columns(4)
        a.metric("Distância", f"{m.distance_m / 1000:.2f} km")
        b.metric("Tempo estimado", f"{m.time_s / 60:.1f} min")
        c.metric("Subida", f"{m.ascent_m:.0f} m")
        d.metric("Descida", f"{m.descent_m:.0f} m")
        a, b, c, d = st.columns(4)
        a.metric("Trabalho mecânico na roda", f"{m.mechanical_energy_j / 1000:.1f} kJ")
        b.metric("Energia de entrada da transmissão", f"{m.drive_input_energy_j / 1000:.1f} kJ")
        c.metric("Energia da fonte", "Desconhecida" if m.source_energy_j is None else f"{m.source_energy_j / 3600:.1f} Wh")
        d.metric("Consumo de bateria", "Desconhecido" if m.battery_energy_j is None else f"{m.battery_energy_j / 3600:.1f} Wh")
        st.caption(f"Simulação: {route.simulation_status} · regularização na partida: {route.used_min_speed_regularization} · circulação não resolvida: {route.circulation_unresolved}")
        if route.elevation_uncertainty:
            with st.expander("Qualidade da altimetria"):
                st.json(route.elevation_uncertainty, expanded=False)
        if route.elevation_profile and render_chart is not None:
            st.caption("Perfil de elevação dos passos efetivamente usados na simulação; lacunas permanecem sem altitude.")
            rows, section = [], 0
            for distance, elevation in route.elevation_profile:
                if elevation is None:
                    section += 1
                else:
                    rows.append({"distancia_km": distance / 1000, "altitude_m": elevation, "trecho": section})
            render_chart([{"data": {"values": rows}, "mark": "line",
                           "encoding": {"detail": {"field": "trecho", "type": "nominal"},
                                        "x": {"field": "distancia_km", "type": "quantitative", "title": "Distância (km)"},
                                        "y": {"field": "altitude_m", "type": "quantitative", "title": "Altitude (m)", "scale": {"zero": False}}}}],
                         "Perfil de elevação indisponível para renderização.")
        if st.button("Centralizar no mapa", width="stretch"):
            import time
            s.map_fit_key = f"route:{time.time()}"
            st.rerun()
        with st.expander("Geometria da rota"):
            _table([{"Latitude": point[0], "Longitude": point[1]} for point in route.geometry[:200]])
            st.caption(f"{len(route.geometry)} pontos; tabela limitada a 200. A geometria completa está no JSON.")
    else:
        st.warning("Nenhuma rota elegível foi encontrada. Consulte os diagnósticos da consulta.")
    with st.expander("Parâmetros e hipóteses"):
        st.json(plan.mobility, expanded=False)
        for warning in plan.warnings:
            st.caption(warning)
    with st.expander("Diagnósticos da busca"):
        st.json(plan.diagnostics, expanded=False)
    with st.expander("Contrato público da toolkit (JSON)"):
        text = plan.to_json(indent=2)
        st.download_button("Baixar mobility-route-plan.json", text,
                           file_name="mobility-route-plan.json", mime="application/json")
        st.json(json.loads(text), expanded=False)
