# ADR-029 — Auditoria da Camada 05 (Evaluation & Guardrails)

> **Documento de apoio** ao [ADR-029 — Evaluation & Guardrails](029-evaluation-guardrails.md).
> Mesmo método das auditorias anteriores (`025-auditoria-fisica.md`,
> `026-auditoria-busca.md`, `027-auditoria-intelligence.md`): **inventário
> técnico**, **contrato × código**, **invariantes × evidência**, **matriz
> PRESERVAR / ADAPTAR / COMPLEMENTAR / SUBSTITUIR** e **análise de lacunas**.
> Esta auditoria **não altera código de produção** — ela descreve o que existe e
> embasa as decisões do ADR-029.

- **Status:** entregável da **fase 1** (auditoria) do ADR-029. **Nenhum** código
  de produção foi alterado.
- **Data:** 2026-11-11
- **Base auditada:** motor `0.18.0` (`pyproject.toml`), suíte com **743 testes**
  (`PYTHONPATH=src python -m pytest`).
- **Escopo:** validar o que a **Camada 05 (Evaluation & Guardrails)** já garante
  hoje e onde há lacunas, sob o requisito arquitetural obrigatório de **operação
  integralmente offline**.

> **Regra desta fase:** auditar e documentar apenas. **Não** modificar física,
> busca, `MultiObjectiveAStar`, ML de roteamento nem o estimador determinístico de
> RPE. **Não** criar modelo fisiológico fictício. **Não** apresentar estimativa
> computacional de intensidade como medição ou zona cardíaca.

## Nota de numeração (resolvida)

O número **`028` já estava em uso** por *"Estimativa de intensidade e ordem de
eventos de ML"* (`028-intensidade-e-ordem-ml.md`,
`028-auditoria-rpe-toporide.md`). Por decisão da **fase 2 (D1)** estes dois
documentos foram **renumerados para `029`** e o ADR-028 original (intensidade e
ordem de ML) foi **preservado sem alteração**. Referências ao ADR-028 neste
documento apontam para aquele ADR (intensidade); referências ao **ADR-029**
apontam para a Camada 05 (Evaluation & Guardrails).

---

## Escopo e método

- **Objetivo:** consolidar a **validação** de legalidade, viabilidade física,
  qualidade das recomendações, incerteza e regressões do roteador ciclístico
  **100% offline**, e verificar se as **invariantes obrigatórias** do ADR-029
  estão de fato garantidas hoje.
- **Não objetivo:** projetar novo algoritmo de busca, novo modelo físico, novo
  preditor neural ou reimplementar física/ML. A Camada 05 **orquestra** e
  **valida**; ela não recalcula ciência nova.
- **Invariantes obrigatórias verificadas** (numeradas I1–I7 para referência nas
  seções seguintes):

  | # | Invariante |
  | - | --- |
  | I1 | Nenhuma rota com **proibição de circulação aplicável** pode ser **recomendada**. |
  | I2 | Nenhuma rota **fisicamente inviável** pode ser apresentada como **viável**. |
  | I3 | Nenhuma previsão fisiológica **não calibrada** pode ser apresentada como **medição** ou previsão individual validada. |
  | I4 | Nenhum **dado ausente** pode ser substituído silenciosamente por **certeza**. |
  | I5 | Toda **rejeição** deve possuir **motivo auditável**. |
  | I6 | Todas as rotas, **independentemente do gerador**, passam pelas **mesmas** verificações. |
  | I7 | Toda execução deve funcionar **integralmente offline**. |

### Classificação de componentes

Cada componente é classificado como **PRESERVAR** (mantém-se como está),
**ADAPTAR** (pequena mudança de interface/semântica com teste e baseline),
**COMPLEMENTAR** (adiciona capacidade que não existe) ou **SUBSTITUIR**
(reimplementação justificada — **nenhuma** neste ciclo).

### Dois sentidos de "avaliação" (não confundir)

| Sentido | O que é | Onde |
| --- | --- | --- |
| **Avaliação física de rota** | simular uma rota no **simulador completo** e classificar disponibilidade | `src/evaluation/assessment.py` (`RouteAssessment`) |
| **Avaliação de qualidade preditiva** | medir o erro de um modelo treinado | `src/intelligence/evaluation.py` (ADR-027) |

Esta auditoria trata do **primeiro** sentido. O ADR-029 de decisão introduz um
**terceiro** artefato — a **avaliação final de segurança** (`VALIDATED` /
`DEGRADED` / `REJECTED`) — construída **sobre** o primeiro, sem recalculá-lo.

---

## 1. Requisito mandatório — operação integralmente offline

### 1.1 Auditoria de dependência de rede (Camada 05 e fronteiras)

| Ponto | Dependência de rede | Evidência |
| --- | --- | --- |
| `evaluation.assessment` | **Nenhuma** — só `physical`, `ml.predictor`, `physiology`, `search` | imports do módulo |
| preditor fisiológico | **Local** (artefato em disco) quando o caller informa; ausência é explícita | `ml/predictor.py` |
| `planning.plan_offline_routes` | **Nenhuma** — nunca baixa; usa o grafo já carregado | docstring + `tests/test_planning.py::test_plan_runs_with_network_disabled` |
| guard offline | `offline()` **proíbe** conexões de saída e falha de forma explícita | `offline.py`; `tests/test_offline.py` (11 testes) |

**Achado O1 (conforme).** A Camada 05 não abre rede. A prova executável é
`tests/test_offline.py::test_full_local_pipeline_runs_offline` (treinar → carregar
→ simular → prever → classificar, com a rede desabilitada) e
`tests/test_planning.py::test_plan_runs_with_network_disabled` (fluxo completo).

**Lacuna O1-o (a tratar).** Não há teste que execute o **fluxo de cidade
completo** (`plan_offline_routes` sobre um grafo real de tamanho representativo)
com a rede desabilitada e verificação de **reprodutibilidade** (mesma saída). Os
testes offline usam fixtures pequenas. Ver §11.

---

## 2. Inventário técnico — Camada 05 e fronteiras

### 2.1 Mapa de módulos

| Módulo | Papel | Símbolos-chave |
| --- | --- | --- |
| `src/evaluation/assessment.py` | **núcleo da Camada 05**: avalia a rota no simulador completo e a classifica | `assess_route`, `assess_search_route`, `assess_candidate`, `assess_candidates`, `RouteAssessment`, `AssessmentOutcome`, `RouteAssessmentError` |
| `src/evaluation/__init__.py` | fachada do pacote | reexporta o núcleo |
| `src/search/circulation.py` | **contrato de circulação** (legalidade), usado pela busca **e** pela validação final | `arc_decision`, `transition_decision`, `validate_route`, `constrained_nodes`, `Decision`, `Verdict`, `DecisionReason`, `RouteValidation`, `CirculationPolicy`, `DEFAULT_POLICY` |
| `src/planning.py` | **orquestração** offline | `plan_offline_routes`, `RoutePlan`, `PlannedRoute` |
| `src/intelligence/classification.py` | predição/estimativa × preferência (apresentação) | `classify_route`, `classify_routes`, `RouteClassification`, `EffortPreference` |
| `src/intelligence/intensity.py` | **estimativa de intensidade** (computacional, **não calibrada**) | `estimate_intensity`, `IntensityEstimate` |
| `src/ml/predictor.py` | **preditor fisiológico** guardado | `EffortPredictor`, `predict_effort`, `UnavailablePredictor` |
| `src/physiology/prediction.py` | **contrato fisiológico** | `PhysiologicalPrediction`, `PredictionStatus` |
| `src/physical/models.py` | qualidade/incerteza física | `SimulationStatus`, `ElevationUncertainty`, `worst_status`, `EnergyBalance` |

### 2.2 Fronteira com as outras camadas

```
Camada 01 (grafo/restrições)  Camada 02 (física)        Camada 03 (busca)
  AccessRule·TurnRestriction     TraversalResult           Route·RouteCandidate
  BicycleAccess                  SimulationStatus          validate_route (contrato)
        │                              │                          │
        └──────────────┬───────────────┴──────────────┬───────────┘
                       ▼                               ▼
            Camada 04 (Intelligence)          Camada 05 (Evaluation & Guardrails)
              EffortFeatures                    RouteAssessment (simulador completo)
              predict_effort (Borg/FC)          validação de circulação independente
              IntensityEstimate (não calib.)    limitações + classificação
```

- A Camada 05 **consome** o resultado físico autoritativo e o contrato de
  circulação; **não** altera a busca nem a física.
- `RouteAssessment` mantém **separadas** as métricas de **busca** (modo busca,
  carregadas na `RouteCandidate`) e as métricas de **avaliação** (simulador
  completo) — ver `RouteAssessment.metrics`.

### 2.3 Grafo de dependências (Camada 05)

```
evaluation.assessment
  ├── graph.model (Arc, RoutingGraph, GraphError)
  ├── ml.predictor (EffortPredictor, predict_effort)
  ├── physical.{bicycle, cyclist, environment, models, traversal, effort}
  ├── physiology (PhysiologicalPrediction, PredictionContext, RiderProfile)
  ├── search.candidates (RouteCandidate)
  ├── search.circulation (validate_route, CirculationPolicy, RouteValidation)
  ├── search.route (Route)
  └── intelligence.intensity (estimate_intensity)   # import tardio (evita ciclo)

planning.plan_offline_routes
  ├── envelope.adaptive_zone  ─── search.MultiObjectiveAStar ─── physical
  ├── evaluation.assessment (assess_candidates)
  ├── intelligence.classification (classify_routes)
  └── geo.query_bbox
```

### 2.4 Parâmetros numéricos relevantes (nenhum novo pela auditoria)

| Parâmetro | Valor | Origem | Observação |
| --- | --- | --- | --- |
| `MIN_SPEED_MPS` | `0.5` | `physical/speed.py` | piso de velocidade; dispara `used_min_speed_regularization` |
| `worst_status` rank | `VALID<DEGRADED<UNAVAILABLE` | `physical/models.py` | agregação monotônica |
| massas/capacidades | `CyclistModel`/`BicycleModel` | `physical/*` | `sustainable_power_w <= max_power_w` validado |
| cortes de faixa de intensidade | `10/12/14/16` (Borg) | `toporide` (heurística) | **não** são zonas cardíacas |
| escala Borg | `6..20` | convenção | única parte com origem fisiológica clara |

### 2.5 Validadores existentes (inventário)

| Validador | Tipo | O que garante | Arquivo |
| --- | --- | --- | --- |
| `is_feasible(result)` | numérico | `time/distance/final_speed/energy` finitos e `final_speed >= 0` (descarta `inf`/`nan`) | `search/astar.py` |
| `arc_decision(arc)` | legalidade | acesso do arco (escopo `bicycle>vehicle>access`), `DISMOUNT`, ausência de regra | `search/circulation.py` |
| `transition_decision(...)` | legalidade | conversão entre arcos (`via_node_id`, `via_way_ids`), NO/ONLY turns | `search/circulation.py` |
| `validate_route(graph, arc_ids)` | legalidade | **validação final independente** de toda a rota (acesso + conversões) | `search/circulation.py` |
| `constrained_nodes(graph)` | dominância | nós em que a legalidade da próxima conversão depende do arco de chegada | `search/circulation.py` |
| `_check_continuity(arcs)` | viabilidade | arcos consecutivos compartilham nó (rota contínua) | `evaluation/assessment.py` |
| `_resolve_arcs(...)` | dados | arco `None`/desconhecido ⇒ `RouteAssessmentError` (nunca silencioso) | `evaluation/assessment.py` |
| `physical.traversal` | física | `power_w > max_power_w` ⇒ `ValueError`; estado físico válido | `physical/traversal.py` |
| `worst_status` / `ElevationUncertainty` | incerteza | status mais restritivo; ausência de elevação ≠ plano silencioso | `physical/models.py` |
| `EffortPredictor`/`predict_effort` | fisiologia | ausência explícita (`UNAVAILABLE`/`NOT_CALIBRATED`/…) — nunca valor inventado | `ml/predictor.py` |
| `offline()` | rede | proíbe conexões de saída; falha explícita | `offline.py` |

### 2.6 Testes existentes (Camada 05 e fronteiras)

| Arquivo | Testes | Cobre |
| --- | ---: | --- |
| `tests/test_evaluation_assessment.py` | 23 | avaliação completa, classificação, limitações, determinismo, erros de dados |
| `tests/test_search_circulation.py` | 34 | contrato de circulação (acesso, conversão, via-way, condicional, política) |
| `tests/test_search_guardrails.py` | 13 | comportamento dos guardrails na expansão + inércia |
| `tests/test_search_reference.py` | 13 | **baseline S1–S7** da fronteira (inalterada sem restrições) |
| `tests/test_planning.py` | 23 | fluxo completo offline, box, recomendada, honestidade fisiológica |
| `tests/test_offline.py` | 11 | guard de rede + pipeline local completo |
| `tests/test_physical_uncertainty.py` | 15 | `ElevationUncertainty`, `SimulationStatus`, propagação |
| `tests/test_physical_convergence.py` | 10 | balanço energético + caracterização da discretização |
| **Total (suíte)** | **743** | (contagem executada; alguns testes são parametrizados) |

---

## 3. Responsabilidade 1 — Legalidade

### 3.1 O que existe hoje

- O contrato de circulação (`search/circulation.py`) reaproveita as entidades
  canônicas do ADR-024 (`AccessRule`, `TurnRestriction`, `BicycleAccess`) e
  **não** cria física nova.
- **Política conservadora** (`DEFAULT_POLICY`): proibição explícita ⇒ `FORBID`;
  permissão explícita ⇒ `ALLOW` com `legality_asserted=True`; **ausência** de
  regra ⇒ `ALLOW` **sem** afirmar legalidade (`legality_asserted=False`);
  condição **não avaliável** ⇒ `UNKNOWN` resolvida para `FORBID` (nunca
  "permitido por padrão").
- **`DISMOUNT`** ⇒ `ALLOW` **com limitação** (a física já caminha a
  `MIN_SPEED_MPS`; não há constante nova).

### 3.2 Contrato × código

| Regra do contrato | Implementação | Estado |
| --- | --- | --- |
| `AccessRule` (escopo `bicycle>vehicle>access`) | `arc_decision` / `_rule_decision` | **imposta** |
| `AccessRule` condicional | `_rule_decision` + `ConditionEvaluator`; sem avaliador ⇒ `UNKNOWN` | **imposta (conservadora)** |
| `TurnRestriction` por `via_node_id` | `_restriction_matches` | **imposta** |
| `TurnRestriction` por `via_way_ids` (ordenada) | `_restriction_matches` (usa o tail do histórico) | **imposta (com contexto)** |
| Tipos NO/ONLY turn | `_turn_decision` | **imposta** |
| Tipo de restrição **não interpretável** | `_turn_decision` ⇒ `UNKNOWN` ⇒ `FORBID` | **imposta (conservadora)** |
| `bicycle=no` / `access=no` (incondicional) | resolvido na **construção** (via fora do grafo) | **imposta por construção** |
| `BicycleAccess.DISMOUNT` | `arc_decision` ⇒ `ALLOW` + `dismount=True` | **imposta (limitada)** |
| Validação **durante a busca** | `astar._allows` antes de gastar física | **imposta** (`_guardrails_active`) |
| Validação **final independente** | `assess_route` chama `validate_route` | **imposta** |

### 3.3 Durante a busca × validação final (independência)

- **Busca:** `MultiObjectiveAStar._allows` consulta `arc_decision` e
  `transition_decision` **antes** de simular a física da aresta; a chave de
  dominância inclui a classe de chegada **apenas** nos `constrained_nodes`
  (ADR-026 §5). Com **nenhuma** restrição registrada, `_guardrails_active` é
  `False` e a busca é **idêntica** ao baseline S1–S7.
- **Validação final:** `validate_route` usa o **mesmo** contrato, sobre a
  sequência completa de arcos, **sem** depender do caminho de busca. Isto é o que
  garante **I6** (mesmas verificações para qualquer gerador): uma `Route` da
  busca, uma `RouteCandidate` ou (futuramente) uma rota proposta por ML passam
  todas por `validate_route` dentro de `assess_route`.

### 3.4 Regras representadas, mas ainda não impostas

| # | Situação | Efeito atual | Classificação |
| --- | --- | --- | --- |
| L-a | `AccessRule` cujo `scope` **não** está em `ACCESS_SCOPES` | ignorada (não afeta bicicleta — correto) | **PRESERVAR** |
| L-b | `TurnRestriction` com **condição** e **sem** `ConditionEvaluator` | vira `UNKNOWN`⇒`FORBID` (nunca permite em silêncio) | **PRESERVAR** (conservador) |
| L-c | `via_way_ids` sem **histórico completo** no ponto de aplicação | `CONTEXT_INSUFFICIENT` ⇒ `UNKNOWN`⇒`FORBID` | **PRESERVAR** (conservador) |
| L-d | Restrição de conversão ancorada em `via_node_id` mas a expansão parte de **arco de entrada** não rastreado no 1º passo | `_allows` só checa conversão quando `label.incoming_arc_id is not None` | **ADAPTAR** (verificar cobertura na origem) |
| L-e | Rotas com `unresolved` (resolvidas conservadoramente) | são **marcadas** (`circulation_unresolved`), mas **não** removidas nem bloqueadas | **COMPLEMENTAR** (política de apresentação) |

### 3.5 Invariantes × evidência

| Invariante | Evidência atual | Veredito |
| --- | --- | --- |
| I1 — rota proibida **não recomendada** | busca filtra; `validate_route` marca `REJECTED`; **`recommended` NÃO considera o estado** | **LACUNA** (§3.6) |
| I5 — rejeição com motivo auditável | `Decision.reason` (`DecisionReason`), `RouteValidation.violations`, `limitations` | **atendido no dado**; falta estado de segurança unificado |
| I6 — mesmas checagens por gerador | `validate_route` único, chamado em `assess_route` | **atendido** |
| I7 — offline | `offline()` + testes | **atendido** |

### 3.6 Lacuna L1 (crítica) — recomendação ignora a legalidade

`planning.plan_offline_routes` define
`PlannedRoute(..., recommended=index == 0)` e `RoutePlan.recommended =
routes[0]`. **Nada** consulta `assessment.outcome`/`circulation.compliant` para
escolher a recomendada. Hoje, na prática, a busca já filtrou as proibições, então
`routes[0]` normalmente não é `REJECTED`. Mas a **invariante I1 não está imposta
pela Camada 05** — ela depende do gerador. Uma rota `REJECTED` (ex.: validada
conservadoramente, ou vinda de um gerador alternativo) na primeira posição seria
apresentada como `[recomendada - ML]`. **Correção (ADR-029 D3/D8):** a
recomendação deve ser **gated** pelo estado de segurança; `REJECTED` nunca
recomendada.

---

## 4. Responsabilidade 2 — Viabilidade física

### 4.1 O que existe hoje

- A avaliação usa o **simulador completo** (`PhysicalTraversal.traverse_path`),
  não o estimador aproximado (`search/approximate.py` permanece
  experimental/fora do fluxo principal).
- **Continuidade:** `_check_continuity` exige que arcos consecutivos compartilhem
  nó; rota descontínua ⇒ `RouteAssessmentError` (nunca mascarada).
- **Velocidades:** propagadas entre arcos (`final_speed` de um = `initial_speed`
  do próximo); `final_speed >= 0`; `is_feasible` descarta métricas não finitas.
- **Limites de potência:** `power_w > cyclist.max_power_w` ⇒ `ValueError`; o
  `CyclistModel` valida `sustainable_power_w <= max_power_w`.
- **`stalled`:** verdadeiro quando a velocidade média cai ao piso
  `MIN_SPEED_MPS`; `used_min_speed_regularization` sinaliza o passo regularizado.
- **Qualidade da simulação:** `SimulationStatus` (`VALID`/`DEGRADED`/`UNAVAILABLE`)
  derivado da **incerteza altimétrica**; balanço energético disponível em
  `TraversalResult.energy_balance(mass)`.

### 4.2 Impossibilidade física × incerteza numérica (distinção)

| Fenômeno | Representação atual | Natureza |
| --- | --- | --- |
| Arco desconhecido / `None` / rota vazia / descontínua | `RouteAssessmentError` (exceção) | **erro de dados** |
| `inf`/`nan`/velocidade final negativa | `is_feasible=False` (descarta a label) | **impossibilidade numérica** |
| `power_w > max_power_w` | `ValueError` na travessia | **violação de limite físico** |
| Elevação ausente/estimada/regularizada | `SimulationStatus.DEGRADED` + flags | **incerteza numérica** |
| Baixa velocidade (`stalled`) | `stalled=True` + `used_min_speed_regularization` | **regime de modelo** |

**Achado P1.** A **incerteza** está bem representada (`SimulationStatus`,
`ElevationUncertainty`, flags). A **impossibilidade física** está representada
como **exceção/descarte**, não como **estado auditável** — não há um sinal
estruturado "fisicamente inviável" no resultado. Para a invariante I2 ("nenhuma
rota fisicamente inviável apresentada como viável") isso é **suficiente hoje**
(não se constrói uma rota inviável: ela é descartada ou levanta erro), mas falta
**uniformizar** em um estado explícito (ADR-029 D2).

### 4.3 Consistência energética

- `EnergyBalance` verifica `dE_k + dE_p = W_wheel − perdas` com resíduo de
  primeira ordem decrescente com o refinamento (`test_physical_convergence.py`).
- **Lacuna P2.** O balanço energético **não** é exposto em `RouteAssessment` nem
  verificado por rota na Camada 05 (é verificado por arco em testes). Para a
  responsabilidade "consistência energética", pode ser um **diagnóstico
  complementar** (opcional), sem alterar física.

### 4.4 Invariantes × evidência

| Invariante | Evidência | Veredito |
| --- | --- | --- |
| I2 — inviável não apresentada como viável | descarte por `is_feasible`; `ValueError` em limites; continuidade | **atendido**; falta estado explícito |
| I4 — ausência não vira certeza | `SimulationStatus`, `ElevationUncertainty`, flags | **atendido** |

---

## 5. Responsabilidade 3 — Comparação

### 5.1 Fronteira Pareto e diversidade

- A fronteira Pareto de destino é produzida pelo `MultiObjectiveAStar`
  (ADR-026); a reconstrução (`routes_from_result`) gera `Route`s e o pool
  **deduplica** (`RouteDeduplicator`: `PATH`/`METRICS`/`PATH_AND_METRICS`).
- **Lacuna declarada (ADR-026 §4):** não há métrica da **diversidade intrínseca
  da fronteira**; a diversidade atua só na **apresentação**.

### 5.2 "ML vs A\* Pareto"

**Achado C1 (esclarecimento).** **Não há** gerador de rotas por ML. O passo 0 de
`planning.py` (modelo de **roteamento**) é um **ponto de extensão documentado**.
O que existe é o **"pick" da ML** = `routes[0]` (primeira na ordem do perfil).
Portanto, "diferenças entre rotas geradas por ML e pelo A\*" **hoje** significa:
(a) a **ordem** do perfil (o *pick*) versus a **fronteira** completa; e (b) o
contrato obrigatório de que qualquer rota de gerador alternativo passe pelas
**mesmas** checagens (I6). O ADR-029 de decisão deve fixar o **simulador completo
como referência comum** de comparação.

### 5.3 Comparabilidade das métricas (busca × avaliação)

- `RouteAssessment.metrics` (avaliação, simulador completo) **deve** coincidir
  com `RouteCandidate.metrics` (modo busca) para a mesma rota, pois ambos usam o
  **mesmo** `PhysicalTraversal` e o mesmo estado inicial
  (`planning` passa `initial_state=from_speed(initial_speed_mps)`, igual ao
  `initial_speed_mps` da busca).
- `test_evaluation_assessment.py::test_features_match_full_simulation_metrics`
  cobre a coerência features × avaliação; **não** há teste que compare
  **candidate.metrics × assessment.metrics** no fluxo de planejamento.

### 5.4 Estabilidade do ranking

**Achado C2 (lacuna).** Não há teste de **estabilidade do ranking** sob variações
que **não** deveriam mudar a ordem (mesmo grafo, mesma origem/destino, perfis
diferentes; reexecução determinística). O baseline S1–S7 cobre a fronteira por
perfil, mas não a ordem do *pick* de forma comparativa.

---

## 6. Responsabilidade 4 — Incerteza

### 6.1 Propagação (física → candidata)

`search/state.py::advance_state` agrega **monotonicamente**:

| Sinal | Agregação |
| --- | --- |
| `result_status` | `worst_status` |
| `elevation_uncertainty` | `combine` (soma por origem) |
| `used_min_speed_regularization` / `stalled` / `dismount_used` | `OR` lógico |

`Route` (`search/route.py`) e `RouteCandidate` (`search/candidates.py`) expõem
esses campos. Na avaliação, `RouteAssessment.limitations` traduz tudo em **códigos
estáveis**: `circulation_violation`, `circulation_unresolved`, `dismount_used`,
`simulation_degraded`, `simulation_unavailable`, `min_speed_regularization`,
`elevation_missing`, `stalled`, `physiology_<status>`.

### 6.2 Estimador determinístico de Borg/RPE — rótulos

- `IntensityEstimate` tem `calibrated = False`, `is_measurement = False`,
  `source = "computational_intensity_estimate"` e **não** preenche
  `PhysiologicalPrediction.heart_rate_zone_distribution` (permanece `None`).
- As cinco faixas `i1..i5` são **faixas de intensidade** (Borg 6–20), medidas em
  fração de **distância** e de **duração** — **não** são zonas cardíacas.
- A CLI imprime `intensidade (nao calibrada)` e `RPE fisiologico indisponivel`.
- Coeficientes têm **tabela de proveniência**; só a **escala de Borg (6, 20)**
  tem origem fisiológica clara.

### 6.3 Invariantes × evidência

| Invariante | Evidência | Veredito |
| --- | --- | --- |
| I3 — não calibrado não vira medição | `calibrated=False`, `is_measurement=False`, zonas `None`, CLI rotula | **atendido** |
| I4 — ausência não vira certeza | `None` explícito; limitações declaradas | **atendido** |

**Achado U1 (a reforçar).** A inexistência de código de limitação para a
**estimativa de intensidade** (`RouteAssessment.limitations` não tem
`intensity_not_calibrated`) deixa a rotulagem dependente de **cada
apresentação**. Recomenda-se um código estável (ADR-029 D6) para que **nenhum**
consumidor possa exibir faixas como zonas cardíacas por omissão.

**Achado U2 (nominal).** Os códigos `simulation_degraded`/`simulation_unavailable`
derivam de `SimulationStatus`, que hoje reflete **qualidade altimétrica**, não
"inviabilidade física". O estado de segurança do ADR-029 deve ser explícito quanto
a isso (evitar leitura equivocada de "degradado = inviável").

---

## 7. Responsabilidade 5 — Regressões

### 7.1 O que já existe

| Tipo | Teste | Cobertura |
| --- | --- | --- |
| Baseline da fronteira | `test_search_reference.py` (S1–S7, 13) | fronteira congelada por perfil; inércia sem restrições |
| Reprodutibilidade | `test_search_reference.py::test_baseline_is_reproducible` | determinismo da busca |
| Guardrails comportamentais | `test_search_guardrails.py` (13) | proibição, condicional, via-way, dismount, propagação |
| Contrato de circulação | `test_search_circulation.py` (34) | todas as regras |
| Avaliação | `test_evaluation_assessment.py` (23) | classificação, limitações, determinismo |
| Offline | `test_offline.py` (11) + `test_planning.py::test_plan_runs_with_network_disabled` | ausência de rede |
| Física/incerteza | `test_physical_uncertainty.py` (15), `test_physical_convergence.py` (10) | incerteza + balanço |

### 7.2 Lacunas de regressão (a tratar — ADR-028 D7)

- **R-a:** **baseline Pareto com restrições** (cenário adverso) — fronteira de
  referência **com** `TurnRestriction`/`AccessRule`/condicional registradas, para
  detectar regressões nos guardrails. Hoje só há cenários construídos ad hoc em
  `test_search_guardrails.py`.
- **R-b:** **cenário adverso de incerteza em escala** — rota longa com trechos
  sem elevação, `stalled`, dismount; baseline de `limitations` congelado.
- **R-c:** **reprodutibilidade do plano completo** com a rede desabilitada
  (mesma `RoutePlan.to_dict()` em duas execuções).
- **R-d:** **consistência busca × avaliação** (métricas) — teste comparativo.
- **R-e:** **estabilidade do ranking** do *pick* por perfil.
- **R-f:** **benchmark** de tempo/expansões do fluxo `plan_offline_routes` (o A\*
  no grafo inteiro **pendura**; sempre recortar a zona).

---

## 8. Responsabilidade 6 — Segurança operacional

### 8.1 Situação atual

- `AssessmentOutcome` = `PHYSIOLOGICAL` / `PHYSICAL_ONLY` / `REJECTED`. Ele
  descreve **disponibilidade fisiológica + circulação**, **não** é um grau de
  segurança física. (`REJECTED` = violação de circulação; `PHYSICAL_ONLY` =
  fisiologia indisponível, não "rota perigosa".)
- A CLI imprime **todas** as rotas na mesma lista, com `[recomendada - ML]` na
  primeira, e as limitações como texto.

**Achado O2/§3.6.** Como a classificação atual **não** distingue "fisicamente
validada", "degradada" e "rejeitada" (para circulação **e** viabilidade física),
**não há** estado operacional formal nem regras explícitas de apresentação,
rejeição e recomendação.

### 8.2 Necessidade

Formalizar um **estado operacional** tri-estado — **VALIDATED / DEGRADED /
REJECTED** — **ortogonal** a `AssessmentOutcome`, com **motivos estruturados** e
**regras explícitas** de apresentação/rejeição/recomendação (ADR-029 D2/D8).

---

## 9. Matriz PRESERVAR / ADAPTAR / COMPLEMENTAR / SUBSTITUIR

| # | Componente | Classificação | Justificativa |
| --- | --- | --- | --- |
| 1 | `MultiObjectiveAStar` (busca) | **PRESERVAR** | referência; auditoria só consome |
| 2 | `physical` (simulador, incerteza, balanço) | **PRESERVAR** | fonte autoritativa |
| 3 | `search/circulation.py` (contrato) | **PRESERVAR** | regras já impostas; base da validação final |
| 4 | `validate_route` (validação independente) | **PRESERVAR** | garante I6 |
| 5 | `evaluation.assessment` (`RouteAssessment`, outcomes) | **PRESERVAR** | orquestra sem recalcular; nova camada **sobre** ele |
| 6 | `ml.predictor` / `physiology` (contrato) | **PRESERVAR** | honestidade da ausência |
| 7 | `intelligence.intensity` (estimativa) | **PRESERVAR** | artefato separado e rotulado |
| 8 | `planning.plan_offline_routes` (orquestração) | **ADAPTAR** | `recommended` deve respeitar o **estado de segurança** (I1) |
| 9 | CLI (`_print_plan`) | **ADAPTAR** | separar "viáveis" e "excluídas"; sinalizar DEGRADED; rótulo de estimativa |
| 10 | `RouteAssessment.limitations` | **COMPLEMENTAR** | código de limitação da **estimativa** (não calibrada) |
| 11 | Estado operacional VALIDATED/DEGRADED/REJECTED | **COMPLEMENTAR** | não existe; ortogonal a `AssessmentOutcome` |
| 12 | Contrato de **avaliação final** (sem recálculo) | **COMPLEMENTAR** | recebe candidatas + resultados disponíveis |
| 13 | Baselines de regressão (restrições, adverso, reprod.) | **COMPLEMENTAR** | §7.2 |
| 14 | Verificação de consistência energética por rota | **COMPLEMENTAR (opcional)** | diagnóstico, sem alterar física |
| 15 | Qualquer reimplementação de física/busca/ML | — | **SUBSTITUIR não se aplica** |

**Nenhum SUBSTITUIR.** Toda a Camada 05 atual é **PRESERVADA**; as mudanças são
**ADAPTAR** (recomendação/apresentação) e **COMPLEMENTAR** (estado, contrato,
regressões).

---

## 10. Análise de lacunas (gap analysis)

| # | Lacuna | Invariante afetada | Severidade | Tratamento (ADR-029) |
| --- | --- | --- | --- | --- |
| L1 | `recommended` ignora a legalidade/estado | I1, I2 | **alta** | D3/D8: gating por estado |
| P1 | Inviabilidade física não é estado auditável | I2, I5 | média | D2: estado `REJECTED` com motivo |
| O2 | Não há estado operacional formal | I2, I5 | **alta** | D2: VALIDATED/DEGRADED/REJECTED |
| U1 | Sem código de limitação da estimativa | I3 | média | D6: `intensity_not_calibrated` |
| U2 | `simulation_*` ambíguo (altimetria ≠ inviabilidade) | I4 | baixa | D2: nomear por origem |
| C2 | Sem estabilidade de ranking | qualidade | média | D5: baseline de ranking |
| R-a..R-f | Baselines adversos/reprodutibilidade/benchmark | regressões | média | D7 |
| O1-o | Offline em escala não provado no fluxo de cidade | I7 | baixa | D7 |
| P2 | Consistência energética não exposta | qualidade | baixa | opcional |
| — | Diversidade intrínseca da fronteira | qualidade | declarada | follow-up (ADR-026 §4) |

---

## 11. Cenários de referência e baselines necessários

1. **Fronteira com restrições (adverso).** Grafo pequeno com `no_left_turn`,
   `only_straight_on`, acesso condicional e `DISMOUNT`; congelar fronteira +
   `limitations` por rota.
2. **Incerteza em escala.** Rota longa com trecho sem elevação + `stalled` +
   dismount; congelar `result_status`, `limitations` e códigos.
3. **Reprodutibilidade offline.** `plan_offline_routes` sobre fixture de cidade
   com `offline()`; comparar `to_dict()` byte a byte entre execuções.
4. **Consistência busca × avaliação.** `candidate.metrics == assessment.metrics`
   (tolerância justificada) para cada rota.
5. **Ranking por perfil.** Ordem do *pick* estável para `fastest`/`flattest`/
   `balanced`/`electric`.
6. **Nenhuma rota proibida recomendada.** Cenário com proibição; afirmar que
   nenhuma rota `REJECTED` é `recommended` nem apresentada como viável.

---

## 12. Riscos e dependências

| # | Risco | Mitigação |
| --- | --- | --- |
| 1 | Mudar `recommended` quebra `test_plan_marks_recommended_route` | manter `routes[0]` como recomendada **quando válida**; só desviar se `REJECTED` |
| 2 | Novo estado duplicar vocabulário de `AssessmentOutcome` | manter **ortogonal**; docs + códigos estáveis |
| 3 | Cobertura incompleta de OSM exótico | conservador (`UNKNOWN`⇒`FORBID`); registrar em §3.4 |
| 4 | Falsa segurança de "DEGRADED = inviável" | nomear por **origem** (altimetria/regime), não por gravidade física |
| 5 | Recomputar física desnecessariamente no contrato final | contrato **consome** `RouteAssessment`; só calcula o que falta |
| 6 | Numeração `028` reutilizada | decisão pendente (§11 do ADR de decisão) |
| 7 | Suíte cresce sem baseline | todo incremento exige **teste + baseline** |

**Dependências:** `physical`, `search.circulation`, `evaluation.assessment`,
`ml.predictor`, `physiology`, `offline` (já existentes). Atualizar `src/api.py`
(`__all__`) **e** `scripts/generate_api_reference.py` (`LAYERS`) ao expor símbolos
novos.

---

## 13. Conclusão

A Camada 05 já **preserva** as invariantes I3, I4, I6 e I7 e, na prática, I1/I2
(porque a busca filtra e o simulador descarta o inviável). As lacunas são de
**formalização e imposição**: a **recomendação não é governada por um estado de
segurança explícito** (L1/O2), a **inviabilidade física** não é um estado
auditável (P1), a **estimativa de intensidade** não tem código de limitação (U1)
e faltam **baselines de regressão adversos** (R-a..R-f). Nenhuma dessas lacunas
exige reimplementar física, busca ou ML: todas se resolvem **sobre** o que já
existe, conforme as decisões do ADR-029 (`029-evaluation-guardrails.md`).
