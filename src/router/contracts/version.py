"""Versao e formato do contrato publico (ADR-030 F2/F3, ADR-031)."""

from __future__ import annotations

CONTRACT_FORMAT = "roteamento.cycling-route-plan"
CONTRACT_VERSION = 1

#: Faixas de intensidade (espelham ``intelligence.intensity`` sem importar o
#: pacote, para manter o contrato independente).
INTENSITY_BANDS: tuple[str, ...] = ("i1", "i2", "i3", "i4", "i5")


class RouteContractError(ValueError):
    """Payload de rota invalido, incompleto ou de versao desconhecida."""


