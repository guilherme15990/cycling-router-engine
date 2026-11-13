"""Versao e formato do contrato publico (ADR-030 F2/F3, ADR-031)."""

from __future__ import annotations

CONTRACT_FORMAT = "roteamento.cycling-route-plan"
CONTRACT_VERSION = 1

#: Faixas de intensidade (espelham ``intelligence.intensity`` sem importar o
#: pacote, para manter o contrato independente).
INTENSITY_BANDS: tuple[str, ...] = ("i1", "i2", "i3", "i4", "i5")

#: Cortes superiores (Borg) de cada faixa de intensidade (inclusivos).
INTENSITY_BAND_EDGES: tuple[float, ...] = (9.0, 12.0, 14.0, 16.0)

#: Definição versionada das faixas de intensidade (Borg 6–20). ``@2`` corrige o
#: primeiro corte (``9``, não ``10``) e é a **única** definição de faixa do
#: sistema.
INTENSITY_BAND_DEFINITION = "borg-intensity-bands@2"

#: Faixas de demanda (espelham ``core.prediction.intensity``).
DEMAND_BANDS: tuple[str, ...] = ("d1", "d2", "d3", "d4", "d5")

#: Cortes superiores de ``P_req / P_ref`` de cada faixa de demanda.
DEMAND_BAND_EDGES: tuple[float, ...] = (0.50, 0.80, 1.25, 2.00)

#: Definição versionada das faixas de demanda.
DEMAND_BAND_DEFINITION = "demand-ratio-bands@1"


class RouteContractError(ValueError):
    """Payload de rota invalido, incompleto ou de versao desconhecida."""


