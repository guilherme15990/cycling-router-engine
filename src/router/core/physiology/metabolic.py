"""Energia metabolica a partir de parametros **explicitos** (ADR-025, decisao 3).

Nao existe eficiencia "padrao": sem um conjunto de parametros calibrado,
versionado e com proveniencia, a energia metabolica e ``None`` — **nunca** 0.
Isso mantem a ausencia de calibracao visivel em vez de escondida atras de um
numero arbitrario.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._validation import non_negative, positive


@dataclass(frozen=True, slots=True)
class MetabolicParameters:
    """Parametros metabolicos explicitos, versionados e com proveniencia.

    ``gross_efficiency`` e a razao entre o trabalho mecanico no **pedivela** e a
    energia metabolica gasta: ``metabolica = trabalho_pedivela / gross_efficiency``.
    Nao ha valor padrao — cada conjunto precisa declarar ``version`` e
    ``provenance`` (origem/calibracao).
    """

    gross_efficiency: float
    version: str
    provenance: str

    def __post_init__(self) -> None:
        positive("gross_efficiency", self.gross_efficiency)
        if self.gross_efficiency > 1.0:
            raise ValueError(
                "gross_efficiency deve estar em (0, 1] "
                f"(recebido {self.gross_efficiency!r})"
            )
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("MetabolicParameters exige version nao vazia")
        if not isinstance(self.provenance, str) or not self.provenance:
            raise ValueError("MetabolicParameters exige provenance nao vazia")


def metabolic_energy_j(
    rider_work_j: float,
    params: MetabolicParameters | None,
) -> float | None:
    """Energia metabolica estimada, ou ``None`` sem parametros calibrados.

    Args:
        rider_work_j: Trabalho no **pedivela** (crank), em joules.
        params: Parametros metabolicos explicitos. ``None`` significa
            "nao calibrado" e devolve ``None`` (nunca 0).

    Returns:
        Energia metabolica em joules, ou ``None``.
    """
    if params is None:
        return None
    non_negative("rider_work_j", rider_work_j)
    return float(rider_work_j) / params.gross_efficiency
