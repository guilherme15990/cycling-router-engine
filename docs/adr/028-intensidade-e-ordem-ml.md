# ADR-028 — Estimativa de intensidade e ordem de eventos de ML

- **Status:** aceito (fase 2 — implementação incremental). Fase 1 (auditoria) em
  [`028-auditoria-rpe-toporide.md`](028-auditoria-rpe-toporide.md).
- **Data:** 2026-11-11
- **Contexto:** espelhar a **ordem de eventos** do `toporide` (ML → achar rotas →
  devolver dados → dimensionar o box a partir das rotas devolvidas) e garantir que
  **toda rota receba RPE e faixas de intensidade, sempre**, com "carrega o modelo
  do cache; senão treina e gera". A auditoria de RPE está em
  [`028-auditoria-rpe-toporide.md`](028-auditoria-rpe-toporide.md).
- **Depende de:** [ADR-024](024-modelo-canonico-de-grafo-e-ambiente.md),
  [ADR-025](025-modelo-fisico-e-dinamica-do-ciclista.md),
  [ADR-026](026-optimization-custo-e-busca.md),
  [ADR-027](027-intelligence-ml-prediction.md).
- **Preserva:** `EffortFeatures`, `PhysiologicalPrediction`, `predict_effort`,
  a física, o A\* Pareto e a busca (baseline S1–S7).
- **Requisito mandatório:** operação **integralmente offline** (ADR-027 §0).

---

## Decisões

### D1 — O contrato fisiológico **não** muda

`EffortFeatures`, `PhysiologicalPrediction` e `predict_effort` permanecem
**inalterados**. Sem modelo **calibrado**, o estado é o explícito de
indisponibilidade (`UNAVAILABLE`/`MODEL_NOT_CALIBRATED`) e
`heart_rate_zone_distribution` continua `None`. **Nenhuma** faixa de intensidade
preenche esse campo (cujo contrato exige definição em **bpm**).

### D2 — **Não** haverá rede fisiológica com rótulos artificiais

É **proibido** criar modelo neural que produza RPE/FC/zonas a partir de rótulos
sintéticos de frequência cardíaca. O contrato fisiológico fica **para evolução
futura**, quando houver observações reais e calibração.

### D3 — Estimativa de intensidade **separada e rotulada**

A RPE/faixas do `toporide` são reproduzidas como um artefato **novo e distinto**:
`intelligence.intensity.IntensityEstimate` — uma **estimativa computacional
não calibrada fisiologicamente** (`calibrated = False`), com **origem e
limitações** registradas. Ela:

- é **determinística** e **auditável** (fórmula explícita, sem estado oculto);
- **não** é medição e **não** é previsão fisiológica calibrada;
- **nunca** é apresentada como zona cardíaca real.

### D4 — Faixas de intensidade (não zonas cardíacas)

As cinco faixas reproduzem os cortes do `toporide` derivados do Borg 6–20
(`10/12/14/16`), identificadas por uma **definição versionada**
(`borg-intensity-bands@1`), nomeadas `i1`…`i5`. São **faixas de intensidade**.

### D5 — Distribuição por **distância** e por **duração**

A distribuição é calculada **por distância** (sempre) e **por duração** (quando
há tempos por trecho — a engine os fornece em `TraversalResult.time_s`).

### D6 — Coeficientes com **proveniência**

Todo coeficiente reutilizado do `toporide` entra em uma **tabela versionada** com
valor, local de origem, natureza ("heurística de engenharia") e limitação (ver
`intensity.py`). **Nenhum** parâmetro arbitrário entra sem essa tabela.

### D7 — Ordem de eventos espelhada do `toporide`

O fluxo de `planning.plan_offline_routes` passa a ser, nesta ordem:

0. **MODELO (roteamento)** — carregar o modelo de **roteamento** do cache local;
   se não existir, é **permitido** treinar localmente com exemplos derivados do
   grafo e dos custos físicos, **identificados como dados computacionalmente
   gerados** (nunca observações fisiológicas). *Opcional*: a busca usa o A\*
   Pareto; um roteador neural, quando existir, é injetado (hoje ausente).
1. **CANDIDATAS** — `search.MultiObjectiveAStar` (e, opcionalmente, o roteador
   neural).
2. **GUARDRAILS** — validação de circulação (`search.circulation`).
3. **FÍSICA COMPLETA** — `PhysicalTraversal.traverse_path` + `EffortFeatures`.
4. **ML (sempre)** — `intelligence.intensity.estimate_intensity` para **cada**
   rota (RPE + faixas, por distância e duração).
5. **CLASSIFICAÇÃO** — física + intensidade + preferências
   (`intelligence.classification`).
6. **BOX** — `geo.query_bbox(pins, traçado das rotas DEVOLVIDAS)` (recomendada
   "ML" + alternativas). O box é calculado **depois** da ML.

A rota **recomendada** (`RoutePlan.recommended`) é a **primeira na ordem do
perfil** (o "pick" da ML), e é **sempre** incluída no box junto com as demais.

### D8 — Incremental e testável

Cada incremento vem com **testes de regressão**, execução **offline** (rede
desabilitada), sem alterar física, A\* Pareto nem contratos. A suíte completa é
rodada a cada incremento.

---

## Incrementos

| # | Incremento | Arquivos |
| - | --- | --- |
| 1 | Estimativa de intensidade (RPE + faixas, distância e duração) + testes | `src/intelligence/intensity.py`, `tests/test_intelligence_intensity.py` |
| 2 | `RouteAssessment.intensity` (por rota) + testes | `src/evaluation/assessment.py`, `tests/test_evaluation_assessment.py` |
| 3 | Ordem de eventos + box pós-ML + `RoutePlan.recommended` + CLI + testes | `src/planning.py`, `src/cli.py`, `tests/test_planning.py` |
| 4 | Classificação por faixas de intensidade (preferência) + testes | `src/intelligence/classification.py` |

Todos os incrementos 1–4 estão implementados. O incremento 4 acrescenta a
preferência `preferred_intensity_bands` (+ `min_intensity_band_fraction` e
`intensity_band_basis`) avaliada sobre a **estimativa de intensidade** (não
calibrada) por **distância** ou **duração**; sem distribuição na base escolhida,
o critério fica `UNKNOWN` (nada é imputado). O passo 0 (modelo de roteamento
cache-ou-treino) permanece **opcional/futuro**: a busca hoje usa o A\* Pareto.

---

## Não objetivos

- Prever zonas cardíacas reais (bpm) — exige modelo calibrado com observações
  reais (ADR-027).
- Treinar rede fisiológica com rótulos artificiais (D2).
- Alterar física, A\* Pareto ou contratos (D1, D8).
