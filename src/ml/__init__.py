"""Inferencia fisiologica a partir de features de esforco (ADR-025, decisao 07).

Este pacote e a **fronteira de inferencia**: consome ``EffortFeatures``
(produzidas pela fisica) e devolve ``PhysiologicalPrediction`` (contrato em
``physiology/``). Nao conhece roteamento nem recalcula fisica.

Nenhum modelo neural **treinado/calibrado** e incluido: sem dados e sem metodo
de validacao, um preditor nao pode produzir Borg/FC sem inventar. Por isso:

* ``UnavailablePredictor`` (padrao) declara a ausencia de modelo de forma
  explicita — tudo ``None``, nunca um valor fabricado;
* ``MetabolicEnergyPredictor`` cobre apenas o que depende de parametros
  **explicitamente fornecidos** (energia metabolica, decisao 3).

Treinar/plugar um modelo calibrado e trabalho futuro; o seam esta pronto.
"""

from .predictor import (
    EffortPredictor,
    MetabolicEnergyPredictor,
    UnavailablePredictor,
    predict_effort,
)

__all__ = [
    "EffortPredictor",
    "MetabolicEnergyPredictor",
    "UnavailablePredictor",
    "predict_effort",
]
