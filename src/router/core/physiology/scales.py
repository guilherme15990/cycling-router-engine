"""Escalas de percepcao subjetiva de esforco (RPE).

A escala e **sempre explicita**: nenhum valor numerico carrega escala implicita.
Duas escalas sao suportadas:

* ``RPE_6_20`` — escala de **Borg original**, de 6 a 20 (padrao do projeto);
* ``CR10`` — escala Borg CR10, de 0 a 10.

Os limites vivem aqui, junto da escala, para que uma previsao possa ser
validada contra eles (ADR-025, decisao 8).
"""

from __future__ import annotations

from enum import Enum

from ._validation import finite

#: Limites de cada escala, em pontos.
_BORG_BOUNDS: dict["BorgScale", tuple[float, float]] = {}


class BorgScale(str, Enum):
    """Escala de Borg/RPE, com limites explicitos.

    O ``value`` (string) identifica a escala no contrato serializado. Os limites
    sao acessados por ``min_score``/``max_score`` e usados por ``contains``,
    ``clamp`` e ``require_valid``.
    """

    #: Escala de Borg original, 6 a 20 (padrao).
    RPE_6_20 = "6-20"
    #: Escala Borg CR10, 0 a 10.
    CR10 = "cr10"

    @property
    def min_score(self) -> float:
        """Menor valor valido da escala."""
        return _BORG_BOUNDS[self][0]

    @property
    def max_score(self) -> float:
        """Maior valor valido da escala."""
        return _BORG_BOUNDS[self][1]

    def contains(self, value: float) -> bool:
        """True se ``value`` (finito) esta dentro dos limites da escala."""
        import math
        return (
            math.isfinite(value)
            and self.min_score <= value <= self.max_score
        )

    def clamp(self, value: float) -> float:
        """Limita ``value`` aos extremos da escala."""
        return min(max(float(value), self.min_score), self.max_score)

    def require_valid(self, value: float) -> float:
        """Devolve ``value`` ou levanta ``ValueError`` se fora da escala."""
        finite("value", value)
        if not self.contains(value):
            raise ValueError(
                f"valor de Borg {value!r} fora da escala {self.value!r} "
                f"[{self.min_score}, {self.max_score}]"
            )
        return float(value)


_BORG_BOUNDS.update(
    {
        BorgScale.RPE_6_20: (6.0, 20.0),
        BorgScale.CR10: (0.0, 10.0),
    }
)

#: Escala padrao do projeto: Borg **6-20** (original).
DEFAULT_BORG_SCALE = BorgScale.RPE_6_20
