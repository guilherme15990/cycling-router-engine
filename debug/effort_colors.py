"""Escala de cores do RPE (Borg) usada pelos apps de depuracao.

A **definicao** das faixas (cortes Borg e rotulos ``i1``..``i5``) vem do nucleo
(``core.prediction.intensity``): aqui vive apenas a *cor* de cada faixa, para
que o mapa, o dashboard e qualquer legenda compartilhem uma unica definicao de
faixa. Nada aqui e uma estimativa fisiologica — e apresentacao da faixa de RPE
ja calculada.
"""

from __future__ import annotations

import json

from router.core.prediction.intensity import (
    INTENSITY_BAND_EDGES,
    INTENSITY_BAND_LABELS,
    INTENSITY_BAND_NAMES,
    band_for_rpe,
)

#: Cor de cada faixa de intensidade, por nome de faixa (``i1``..``i5``).
RPE_BAND_COLORS: dict[str, str] = {
    "i1": "#387c2e",
    "i2": "#8ec244",
    "i3": "#f5c029",
    "i4": "#e66e10",
    "i5": "#bd2d2e",
}

#: Cor de um trecho sem RPE conhecido (nunca um vermelho de alarme).
RPE_COLOR_NONE = "#b6bcc2"

if set(RPE_BAND_COLORS) != set(INTENSITY_BAND_NAMES):  # pragma: no cover - guarda
    raise RuntimeError(
        "RPE_BAND_COLORS deve cobrir exatamente INTENSITY_BAND_NAMES"
    )


def rpe_color(rpe: float | None) -> str:
    """Cor da faixa de RPE; usa ``band_for_rpe`` do nucleo. ``None`` vira cinza."""
    if rpe is None:
        return RPE_COLOR_NONE
    return RPE_BAND_COLORS[band_for_rpe(rpe)]


def rpe_band_label(band: str) -> str:
    """Rotulo de apresentacao da faixa de intensidade (``i1``..``i5``)."""
    return INTENSITY_BAND_LABELS[band]


def rpe_color_js() -> str:
    """Trecho JS com a tabela de cores e ``rpeColor(rpe)``.

    Os cortes vem de ``INTENSITY_BAND_EDGES`` e as cores de ``RPE_BAND_COLORS``;
    o mapa apenas executa a mesma regra do nucleo, sem cortes duplicados.
    """
    entries = ", ".join(
        f"[{edge}, {json.dumps(RPE_BAND_COLORS[name])}]"
        for edge, name in zip(INTENSITY_BAND_EDGES, INTENSITY_BAND_NAMES)
    )
    last = json.dumps(RPE_BAND_COLORS[INTENSITY_BAND_NAMES[-1]])
    return (
        f"const RPE_COLORS = [{entries}];\n"
        f"const RPE_COLOR_NONE = {json.dumps(RPE_COLOR_NONE)};\n"
        "const rpeColor = (rpe) => {\n"
        "    if (rpe == null) return RPE_COLOR_NONE;\n"
        "    for (const entry of RPE_COLORS) { if (rpe <= entry[0]) return entry[1]; }\n"
        f"    return {last};\n"
        "};"
    )


__all__ = [
    "RPE_BAND_COLORS",
    "RPE_COLOR_NONE",
    "rpe_color",
    "rpe_band_label",
    "rpe_color_js",
]
