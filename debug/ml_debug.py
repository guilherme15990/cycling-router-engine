"""Explicação dos modelos locais a partir das entradas da inferência real."""
from __future__ import annotations

import math

from router.core.prediction.models import ConstantModel, LinearModel


def explain_model(model, row):
    """Preserva todas as features; não executa o modelo nem imputa valores."""
    if not isinstance(model, (LinearModel, ConstantModel)):
        return None
    coefficients = dict(zip(model.feature_names, model.coefficients)) if isinstance(model, LinearModel) else {}
    names = list(dict.fromkeys([*coefficients, *row]))
    rows = []
    for name in names:
        value = row.get(name)
        coefficient = coefficients.get(name)
        contribution = None if value is None or coefficient is None else coefficient * float(value)
        rows.append(dict(feature=name, value=value, used=name in coefficients,
                         coefficient=coefficient, contribution=contribution,
                         coefficient_percent=None, contribution_percent=None))
    used = [item for item in rows if item["used"]]
    coefficient_total = math.fsum(abs(item["coefficient"]) for item in used)
    complete = all(item["contribution"] is not None for item in used)
    contribution_total = math.fsum(abs(item["contribution"]) for item in used) if complete else None
    for item in used:
        if coefficient_total:
            item["coefficient_percent"] = 100 * abs(item["coefficient"]) / coefficient_total
        if contribution_total:
            item["contribution_percent"] = 100 * abs(item["contribution"]) / contribution_total
    return dict(kind="linear" if isinstance(model, LinearModel) else "constant",
                intercept=model.intercept if isinstance(model, LinearModel) else model.value,
                complete=complete, rows=rows)


def render_neural(info, table):
    import streamlit as st
    if not info or info.get("status") == "disabled":
        return False
    st.subheader("Rede de geração de rota")
    if not info.get("model_version"):
        st.info("Modelo de rota indisponível: não houve amostras físicas viáveis para treinamento.")
        return True
    st.caption(f"Perfil de rota: {info['profile']} · modelo {info['model_version']} · busca: {info['status']}. "
               "Treinado com objetivos físicos simulados por metro, sem calibração com ciclistas reais.")
    st.caption(f"Objetivos: {', '.join(info['objectives'])} · treino: {info['training_samples']} amostras · "
               f"validação: {info['validation_samples']} amostras de arcos separados · "
               f"erro quadrático padronizado: {info['validation_mse'] if info['validation_mse'] is not None else 'indisponível'}. "
               "Amostras humanas: 0.")
    table([{"Feature": name, "Influência (%)": f"{percent:.2f}%"}
           for name, percent in info['feature_influence'].items()])
    st.caption("Influências calculadas antes das buscas pelos caminhos absolutos dos pesos da rede, "
               "com entradas padronizadas. São uma explicação estrutural, não pesos de custo nem causalidade. "
               "Features ou alvos sem variação não recebem influência aprendida. "
               "As previsões da rede orientam a busca; as métricas exibidas continuam vindo da física.")
    return True


def render(report, table):
    import streamlit as st
    from router.core.prediction.features import ALL_FEATURE_NAMES

    st.subheader("Variáveis e pesos da ML")
    neural = render_neural((report or {}).get("neural_routing"), table)
    events = [event for event in (report or {}).get("events", []) if event.get("ml")]
    if not events:
        if neural:
            return
        st.info("Nenhuma inferência de modelo com pesos foi capturada. A consulta não executa ML treinada; o RPE opcional por perfil é uma estimativa provisória. Percentuais de pesos de ML estão indisponíveis.")
        with st.expander("Variáveis disponíveis no contrato da ML"):
            table([{"Variável": name, "Valor enviado": "Não capturado", "Peso (%)": "—"}
                   for name in ALL_FEATURE_NAMES])
        return
    index = st.selectbox("Inferência para inspecionar", range(len(events)), key="inspect_ml_call",
                         format_func=lambda i: f"#{events[i]['order']} · {events[i]['function']}")
    event = events[index]
    explanation = event["ml"]
    if explanation["kind"] == "constant":
        st.info("Modelo constante: nenhuma variável tem peso; a saída é o valor constante.")
    if not explanation["complete"] or event.get("exceptions"):
        st.warning("Inferência incompleta ou com exceção. Confira as entradas ausentes e o retorno capturado.")
    st.caption(f"Intercepto / constante: {explanation['intercept']:.6g} · Saída bruta capturada: {event.get('output', '—')}")

    def number(value, percent=False):
        if value is None:
            return "—"
        return f"{value:.2f}%" if percent else f"{value:.6g}"

    table([{"Variável": item["feature"], "Valor recebido": number(item["value"]),
            "Usada pelo modelo": "Sim" if item["used"] else "Não",
            "Coeficiente": number(item["coefficient"]),
            "Peso do coeficiente (%)": number(item["coefficient_percent"], True),
            "Contribuição (coef. × valor)": number(item["contribution"]),
            "Participação na previsão (%)": number(item["contribution_percent"], True)}
           for item in explanation["rows"]])
    st.caption("Peso do coeficiente = 100 × |coeficiente| / Σ|coeficientes|. "
               "Depende das unidades das variáveis e não representa importância global. "
               "Participação na previsão = 100 × |coeficiente × valor| / Σ|coeficiente × valor|, "
               "sem o intercepto. O sinal da contribuição indica aumento ou redução da saída bruta, "
               "antes do recorte à escala Borg. Denominador zero ou entrada ausente deixa o percentual indisponível (—). "
               "Cada tabela corresponde a uma chamada amostrada; não é uma média da rota.")
