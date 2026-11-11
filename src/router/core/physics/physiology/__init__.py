"""Camada de fisiologia: escalas, zonas, metabólico e contrato de previsao.

Esta camada **nao** contem fisica nem inferencia neural. Ela define contratos
imutaveis e versionados — escala de Borg, zonas cardiacas, parametros
metabolicos e ``PhysiologicalPrediction`` — e as regras de validacao que uma
previsao precisa satisfazer. A inferencia vive em ``ml/`` (ADR-025, decisao 7).

Principios (ADR-025):

* escala Borg **explicita** (padrao: 6–20 original; CR10 tambem suportado);
* zonas cardiacas por **definicao identificada**, sem percentuais implicitos;
* energia metabolica só com parametros calibrados, versionados e com
  proveniencia — caso contrario ``None`` (nunca 0);
* uma previsao **nunca** e apresentada como medicao, e o estado da previsao
  (``PredictionStatus``) e sempre explicito.
"""

from .metabolic import MetabolicParameters, metabolic_energy_j
from .prediction import (
    PHYSIOLOGY_VERSION,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
)
from .scales import DEFAULT_BORG_SCALE, BorgScale
from .zones import (
    DISTRIBUTION_TOLERANCE,
    HeartRateZoneDefinition,
    HeartRateZoneModel,
)

__all__ = [
    "PHYSIOLOGY_VERSION",
    "BorgScale",
    "DEFAULT_BORG_SCALE",
    "DISTRIBUTION_TOLERANCE",
    "HeartRateZoneDefinition",
    "HeartRateZoneModel",
    "MetabolicParameters",
    "PhysiologicalPrediction",
    "PredictionContext",
    "PredictionStatus",
    "RiderProfile",
    "metabolic_energy_j",
]
