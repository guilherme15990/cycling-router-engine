# Auditoria da Camada 03 — Optimization (Cost & Search)

> Documento de apoio ao **ADR-026**. Mesmo método da auditoria física
> (`025-auditoria-fisica.md`): inventário, contrato × código, invariantes ×
> evidência e lacunas declaradas. Esta auditoria **não altera código** — ela
> descreve o que existe na versão **0.18.0** para embasar as cinco decisões do
> ADR-026.

## Escopo e método

- **Objetivo:** verificar se os algoritmos de busca já implementados geram um
  conjunto de rotas candidatas **suficientemente diverso e fisicamente coerente**
  para a Camada 04 (Intelligence) avaliar.
- **Não objetivo:** projetar um novo algoritmo de busca. O A\* multiobjetivo
  (MOA\*) existente é a referência; algoritmos de caminho mínimo de fonte única
  **não** entram nesta camada (não carregam estado físico nem múltiplos
  objetivos).
- **Baseline:** `0.18.0`, suíte com **426 testes** (146 deles em `tests/test_search*`).
  Nenhum comportamento foi alterado por esta auditoria.

### Classificação de componentes

Cada componente é classificado como **PRESERVAR** (mantém-se como está),
**ADAPTAR** (evolui sem trocar a física), **COMPLEMENTAR** (adiciona contrato que
falta) ou **SUBSTITUIR** (troca justificada). Ver a matriz na §7.

## 1. Inventário técnico

### 1.1 Mapa de módulos — `src/search/`

| Módulo | Papel | Grupo |
| --- | --- | --- |
| `objectives.py` | `Objective`, `ObjectiveSet`, `SearchProfile`, objetivos por perfil | Núcleo da busca |
| `dominance.py` | `SpeedBucketDominance`, `speed_bucket` | Núcleo da busca |
| `frontier.py` | `ParetoFrontier`, `FrontierMap`, `FrontierManager` | Núcleo da busca |
| `queue.py` | `LexicographicQueue` (ordem de exploração) | Núcleo da busca |
| `state.py` | `PhysicalState`, `SearchState`, `advance_state`, `route_arcs` | Núcleo da busca |
| `traversal_model.py` | `TraversalModel` (protocolo) + `PhysicalTraversalModel` (adaptador físico) | Fronteira 02↔03 |
| `heuristic.py` | `Heuristic`, `HeuristicBounds` (limites inferiores admissíveis) | Núcleo da busca |
| `config.py` | `SearchConfig` (controles de desempenho) | Núcleo da busca |
| `astar.py` | `MultiObjectiveAStar`, `SearchResult` | Núcleo da busca |
| `route.py` | `Route` reconstruída (arcos + métricas) | Pós-processamento |
| `candidates.py` | `RouteCandidate`, `CandidateMetrics`, `RouteCandidatePool`, `RouteDeduplicator` | Pós-processamento |
| `diversity.py` | `arc_jaccard`, `shared_distance_ratio`, `RouteDiversity` | Pós-processamento |
| `ranking.py` | `rank_candidates`, `ProfileRanker`, score normalizado | Pós-processamento |
| `selection.py` | `select_candidates`, `RouteSelector`, diversidade + fallback | Pós-processamento |
| `explanation.py` | `RouteExplainer`, `RouteExplanation` | Pós-processamento |
| `energy.py` | `estimate_route_energy` (ponte para `physical.energy_model`) | Pós-processamento |
| `pipeline.py` | `search_profile`, `ProfileSearchRequest/Response` | Orquestração |
| `approximate.py` | `SteadyStateTraversalModel` (estimador) | **Experimental (ADR-025 §10.1)** |

`approximate.py` é **experimental/opcional e fora do fluxo principal** (ADR-025
§10.1): não é consumido por `pipeline.py`, `selection.py` nem `astar.py`.

### 1.2 Fronteira com as outras camadas

```
Camada 01 (Data Model)        Camada 02 (Physical Model)
  graph.model (Arc, Segment)    physical.models.TraversalResult
  geo.haversine_m               PhysicalTraversal / Environment
        │                              │
        └──────────────┬───────────────┘
                       ▼
              Camada 03 (Optimization)
        TraversalModel → SearchState (acúmulo) → Pareto A*
        → Route → RouteCandidate → ranking + diversidade
                       │
                       ▼
        Camada 04 (Intelligence): EffortFeatures → predição
```

- A busca **não** calcula física: consome `TraversalModel.traverse(arc, v)` e
  acumula o `TraversalResult` (`astar.py` L293; `state.py` L114-L140).
- A busca **não** decide preferência de superfície nem penalidades arbitrárias
  (`objectives.py` L1-L14). Preferência entra apenas no ranking/seleção, **depois**
  da fronteira Pareto.
- A predição fisiológica (Camada 04) consome `EffortFeatures` do **simulador
  completo** (`physical/effort.py`), não das métricas da busca.

### 1.3 Grafo de dependências (busca → física/grafo)

- `astar.py` → `graph.model`, `physical.models`, `dominance`, `frontier`,
  `heuristic`, `objectives`, `queue`, `state`, `traversal_model`, `config`.
- `state.py` → `graph.model`, `physical.energy.kinetic_energy_j`,
  `physical.models.TraversalResult`.
- `pipeline.py` → `graph.model`, `physical.energy_model`, e o restante de
  `search/`.
- **Nenhum** módulo de `search/` importa `physical.speed`, `physical.forces` ou
  `physical.traversal` diretamente — a dependência física é mediada por
  `TraversalModel` (exceto a energia cinética, reexportada por `physical.energy`).

### 1.4 Parâmetros numéricos atuais

| Parâmetro | Default | Onde | Natureza |
| --- | --- | --- | --- |
| `SearchConfig.speed_step_mps` | `0.5` | `config.py` | **Aproximação** (bucket de dominância) |
| `DEFAULT_SPEED_STEP_MPS` | `0.5` | `dominance.py` | Aproximação |
| `SearchConfig.max_labels_per_node` | `None` | `config.py` | Desempenho (sem limite) |
| `SearchConfig.max_expansions` | `None` | `config.py` | Desempenho (sem limite) |
| `SearchConfig.epsilon_dominance` | `0.0` | `config.py` | Aproximação (desligada) |
| `Heuristic.DEFAULT_MAX_SPEED_MPS` | `22.0` | `heuristic.py` | Teto p/ admissibilidade (= `Environment.DEFAULT_SAFE_MAX_SPEED_MPS`) |
| `DiversityConfig.max_similarity` | `0.7` | `profiles.py` | Preferência de apresentação |
| `DiversityRelaxation.max_threshold` / `step` | `1.0` / `0.1` | `profiles.py` | Preferência (fallback) |
| `RouteDeduplicator.metric_quantum` | `1e-3` | `candidates.py` | Aproximação (dedup por métricas) |
| Tolerâncias lexicográficas | time 5/10 s, dist 10/20 m, ascent 1/2 m, energy 1000 J | `profiles.py` | Preferência (**não calibrada**) |
| `BalancedWeights` | 1/3 cada | `profiles.py` | Preferência |
| `ScoreReference` | time 1800 s, dist 10 000 m, ascent 100 m | `profiles.py` | Preferência (**referência fixa**) |

Nenhum desses parâmetros é uma **constante física**: são aproximações de estado,
controles de desempenho ou preferências, e nenhum altera a dinâmica (ADR-025).

### 1.5 Testes existentes (`tests/test_search*`)

| Arquivo | Testes | Cobre |
| --- | --- | --- |
| `test_search.py` | 18 | objetivos, dominância, fronteira, heurística, config |
| `test_search_astar.py` | 20 | A\*, poda, velocidade, truncamento, heurística admissível |
| `test_search_state.py` | 14 | estado físico, bucket, `FrontierManager` |
| `test_search_candidates.py` | 22 | candidatas, dedup, seleção, ranking |
| `test_search_selection.py` | 22 | reconstrução, diversidade, ranking, fallback |
| `test_search_profiles.py` | 17 | perfis, tolerâncias, explicação, `search_profile` |
| `test_search_electric.py` | 8 | energia elétrica + bateria |
| `test_search_approximation.py` | 25 | estimador experimental (ADR-025 §10.1) |

**Total:** 146 testes de busca. **Lacuna:** não existe um *baseline de
caracterização* da busca análogo ao §7 da auditoria física (cenário congelado com
fronteira de destino conhecida). A §9 propõe esse baseline.

## 2. Decisão 1 — função de custo

### 2.1 Como está hoje

A busca **não** minimiza um escalar único. Ela mantém uma **fronteira de Pareto**
sobre um conjunto de objetivos físicos:

- cada perfil escolhe um `ObjectiveSet` — lista ordenada de nomes de métrica
  (`objectives.py` L134-L140): `fastest = (time_s, ascent_m)`,
  `shortest = (distance_m, time_s)`, `flattest = (ascent_m, distance_m)`,
  `balanced = (time_s, distance_m, ascent_m)`, `electric = (energy_j, time_s)`;
- a dominância é **minimização em todos** os objetivos (`dominates`,
  `objectives.py` L80-L97);
- a ordem do conjunto importa **apenas** para a prioridade lexicográfica da fila
  (`SearchProfile.primary`, `astar.py` L324-L337), não para a fronteira.

As métricas acumuladas por arco vêm **todas** do resultado físico
(`state.py` L127-L140): `time_s`, `distance_m`, `ascent_m`, `descent_m` e
`energy_j` (= `mechanical_energy_j`). A busca **não** soma penalidades de
superfície, ruído, segurança ou preferência. `descent_m` é acumulado mas **não** é
objetivo de nenhum perfil.

### 2.2 Contrato × código

| Contrato (ADR-025 §10; arquitetura do ADR-026) | Código atual | Situação |
| --- | --- | --- |
| Física é a única fonte de tempo/energia | `state.py` acumula `TraversalResult` | **Atende** |
| Perfil não altera a física | `profiles.py` L1-L16; física inalterada | **Atende** |
| "Quais métricas entram" é decisão de produto | `PROFILE_OBJECTIVES` declarativo | **Atende** |
| Custo antes do ML = métricas físicas, sem peso arbitrário | sem penalidade de superfície/peso | **Atende** |
| Custo é um escalar | é um **vetor** Pareto + prioridade lexicográfica | **Diferença declarada** |

### 2.3 Invariantes × evidência

- *Todos os objetivos são minimizados:* `test_dominates_semantics`
  (`test_search.py` L62).
- *Perfis usam conjuntos realmente diferentes:* `test_profile_objectives_are_distinct_and_lookup_normalizes`
  (`test_search.py` L88).
- *A ordem do conjunto só afeta a exploração:* `test_frontier_keeps_non_dominated`
  e `SearchProfile.dominates` independem da ordem.

### 2.4 Observação

Não há, hoje, uma "função de custo" escalar a auditar. O ADR-026 deve decidir se
**mantém** o vetor Pareto como custo de referência (recomendado) e deixa a
agregação escalar **apenas** para ranking/seleção — nunca para a física.

## 3. Decisão 2 — estado e dominância

### 3.1 Como está hoje

- `PhysicalState(speed_mps, kinetic_energy_j)`: a energia cinética é **derivada**
  da massa e da velocidade (`state.py` L38-L44) — não é escolhida de forma
  independente. Momentum **não** é campo separado: com massa constante
  (`PhysicalTraversalModel.mass_kg`, `traversal_model.py` L83-L85), ele é
  proporcional à velocidade.
- `SearchState` carrega métricas acumuladas + estado físico + `parent` /
  `incoming_arc_id` para reconstrução (`state.py` L47-L65).
- A fronteira é indexada por **chave** `(node_id, speed_bucket)`, com
  `bucket = int(speed // speed_step_mps)` (`dominance.py` L45-L50, L73-L85).
  **Só** labels no mesmo nó **e** mesmo bucket são comparadas por dominância.
- Controles de explosão (`config.py`): `max_labels_per_node` (aplicado **por
  chave** em `FrontierManager`, `astar.py` L250-L256), `max_expansions`
  (interrompe e marca `truncated=True`, `astar.py` L284-L289), `epsilon_dominance`
  (dominância tolerante, `objectives.py` L100-L128).

> **Atualização (incremento de observabilidade).** Na redação original, a poda
> por `max_labels_per_node` era **silenciosa**; só `max_expansions` marcava
> `truncated`. O incremento passou a sinalizar a poda com campos próprios
> (`frontier_truncated`/`dropped_labels`, de `FrontierManager`), **sem** alterar
> `truncated`. `epsilon_dominance` segue **inativo** no A\* (a
> `SpeedBucketDominance` padrão não recebe epsilon e `MultiObjectiveAStar` a
> constrói sem epsilon). Ver `026-optimization-custo-e-busca.md` (Decisão 2).

### 3.2 Achado A — o critério de parada é exaustivo

O laço principal **não** para ao alcançar o destino: labels de destino nunca são
expandidas (`astar.py` L280-L282) e a busca só termina quando a **fila ativa
esvazia** (`astar.py` L273, L311). Consequência arquitetural importante:

- a **corretude da fronteira** não depende da admissibilidade da heurística — ela
  depende da exaustão + da regra de dominância;
- a heurística afeta **ordem de exploração** (e, portanto, o efeito de
  `max_expansions` / `max_labels_per_node`, que **podem** remover rotas não
  dominadas). Com os caps desligados (default), o resultado independe da
  heurística.

Isso **não** dispensa a checagem de admissibilidade (`_check_heuristic_admissible`,
`astar.py` L87-L121): ela evita uma heurística *provadamente* enganosa e protege o
caso em que os caps estão ligados. Mas o ADR-026 deve registrar que, nesta
implementação, **admissibilidade é requisito de ordenação/desempenho**, não de
corretude da fronteira.

### 3.3 Achado B — bucketing é a aproximação central do estado

`speed_step_mps = 0.5` quantiza a velocidade **apenas para decidir quando duas
labels competem** (`dominance.py` L8-L14). Duas labels no mesmo bucket são
comparadas como se tivessem a mesma velocidade de saída; a fisicamente pior pode
ser descartada. É uma **aproximação configurável**, documentada e coerente
(mesma política na chave e na dominância). Não há teste que **quantifique** o
efeito do passo do bucket na fronteira — lacuna a fechar no baseline (§9).

### 3.4 Contrato × código

| Contrato | Código | Situação |
| --- | --- | --- |
| Estado preserva o necessário p/ a próxima travessia | `PhysicalState` (velocidade) | **Atende** (massa constante) |
| Energia cinética não é independente | `from_speed` deriva | **Atende** |
| Estado canônico (ADR-025 dec. 1) | busca transporta só velocidade; reconstrói `SimulationState.from_speed` (`traversal_model.py` L100-L116) | **Atende** |
| "Não explodir o espaço" | bucket + caps | **Atende** (com aproximação) |

## 4. Decisão 3 — heurística

### 4.1 Como está hoje

`Heuristic` (`heuristic.py` L49-L128) devolve limites inferiores por objetivo:

- `distance_m`: haversine nó→destino (`geo.haversine_m`);
- `time_s`: `distance / max_speed_mps`; se `max_speed_mps is None`, o limite é
  `0.0`;
- `ascent_m`: `max(0, h_destino − h_nó)` quando há elevação nos nós;
- objetivos sem limite (ex.: `energy_j`) recebem `0.0` em `lower_bounds`
  (`heuristic.py` L109-L128).

Admissibilidade do teto de velocidade: três estados distintos
(`traversal_model.py` L28-L61). O teto do modelo físico é
`Environment.safe_max_speed_mps` (default `22.0` m/s). A guarda
`_check_heuristic_admissible` (`astar.py` L87-L121) recusa heurística explicitamente
inadmissível.

### 4.2 Evidência de admissibilidade (existente)

- Teto anunciado pelo modelo é usado pela heurística padrão:
  `test_default_heuristic_uses_model_speed_ceiling`.
- Sem teto anunciado, o limite de tempo cai para `0`:
  `test_default_heuristic_is_admissible_without_announced_ceiling`.
- Heurística finita recusada quando o modelo declara ilimitado:
  `test_finite_heuristic_rejected_when_model_declares_unlimited`.
- Heurística finita aceita quando o teto é desconhecido:
  `test_finite_heuristic_allowed_when_model_ceiling_unknown`.
- Limites geométricos são de fato inferiores: `test_heuristic_bounds_are_admissible_lower_bounds`.

### 4.3 Verificação do limite de energia (resolvida nesta auditoria)

`lower_bounds` justifica `0.0` para `energy_j` com "nenhum objetivo é negativo".
Isso exige que a energia mecânica acumulada **nunca seja negativa**. Verificado:

- **No código:** `wheel_energy_j` é o trabalho **positivo** entregue à roda
  (`physical/speed.py` L42); `advance_speed` inicia em `wheel_energy_j=0.0`
  (`speed.py` L96).
- **Empiricamente:** em descidas com potência zero (ex.: 20% de declive a 5 m/s),
  `mechanical_energy_j = 0.0` (clampado), nunca negativo — sonda executada na
  versão `0.18.0`.

Portanto o limite `0.0` para `energy_j` é admissível. Registrado para não
"consertar" o limite sem evidência.

### 4.4 Limitações declaradas

- O limite de `ascent_m` exige elevação nos **nós**; com elevação ausente, o
  limite é `0` (conservador, admissível).
- Não há limite geométrico para `descent_m` nem para `energy_j` (usam `0.0`).
- **Achado A (§3.2):** nesta implementação a admissibilidade **não** é requisito
  de corretude da fronteira; é requisito de ordenação e do comportamento sob caps.

## 5. Decisão 4 — diversidade

### 5.1 Como está hoje

Duas métricas (`diversity.py`):

- `ARC_JACCARD`: `|E_A ∩ E_B| / |E_A ∪ E_B|` sobre arcos **direcionados**;
- `SHARED_DISTANCE_RATIO` (**padrão**): `D_shared / min(D_A, D_B)`, usando
  `Route.arc_length_map`; recai para contagem de arcos sem comprimentos.

Configuração por perfil (`profiles.py` L198-L224): `max_similarity = 0.7`,
`fallback = RELAX_THRESHOLD`, relaxamento `max_threshold = 1.0`, `step = 0.1`.
A seleção (`selection.py` L224-L284) aceita greedy a candidata que **não** é
demasiado semelhante às já escolhidas; o fallback relaxa o limiar até preencher
`min_routes`, marcando `diversity_relaxed`.

Antes do ranking há **deduplicação** (`candidates.py` L99-L157): `PATH` (padrão)
colapsa a mesma sequência de arcos; `METRICS` colapsa o mesmo resultado físico
por quantum `1e-3`; `PATH_AND_METRICS` exige ambos.

### 5.2 Contrato × código

| Contrato | Código | Situação |
| --- | --- | --- |
| Alternativas "genuinamente diferentes" | diversidade greedy + dedup | **Atende parcialmente** |
| Diversidade não é objetivo físico | diversidade só na seleção (`selection.py` L1-L17) | **Atende** |
| Determinismo | empates por `(label_id, route_id)` (`ranking.py` L201-L203) | **Atende** |
| Fronteira já diversa | não garantida (buckets geram labels próximas) | **Lacuna declarada** |

### 5.3 Observação

A fronteira Pareto pode conter várias labels **quase idênticas** no mesmo nó com
velocidades de saída em buckets diferentes (mesmo caminho, métricas próximas). A
deduplicação `PATH` colapsa as de mesmo caminho, mas **não** há métrica que meça a
diversidade **intrínseca da fronteira** antes da seleção. O ADR-026 deve decidir
se isso basta (recomendado no MVP: diversidade só na apresentação) ou se a busca
precisa de um critério de diversidade na fronteira.

## 6. Decisão 5 — interface de candidatos

### 6.1 Como está hoje

- `RouteCandidate = {route_id, Route, CandidateMetrics}` (`candidates.py` L61-L96).
  Identidade padrão `route-{label_id}`; `Route` guarda arcos direcionados,
  comprimentos e métricas (`route.py`).
- `RouteCandidatePool.from_result` reconstrói a fronteira de destino e deduplica
  (`candidates.py` L194-L203).
- `pipeline.search_profile` devolve `ProfileSearchResponse` com rotas
  selecionadas, metadados, explicações e estimativa energética opcional.
- A Camada 04 consome `EffortFeatures` do **simulador completo** — a interface de
  candidatos entrega **métricas físicas**, sem predição acoplada.

### 6.2 Contrato × código

| Contrato | Código | Situação |
| --- | --- | --- |
| Candidatas válidas e comparáveis | `RouteCandidate` + métricas | **Atende** |
| Predição não atrelada ao gerador | Camada 04 lê `EffortFeatures`, não a busca | **Atende** |
| Reproduzível | ordenação determinística | **Atende** |
| Rota gerada por ML passa pelas mesmas regras | não há caminho ML na busca hoje | **A definir (ADR-026)** |

### 6.3 Lacuna — a candidata não carrega qualidade/limitação

`Route`/`RouteCandidate` **não** propagam:
`TraversalResult.result_status` (`SimulationStatus`), `elevation_uncertainty`,
`used_min_speed_regularization` nem `stalled`. A busca descarta apenas travessias
**não finitas** (`is_feasible`, `astar.py` L41-L53). Assim, a Camada 04 recebe uma
rota sem saber que ela passou por elevação ausente, regime regularizado ou trecho
"stalled" — a limitação existe na física mas **se perde** na interface de
candidatos. O ADR-026 deve fechar isso (a Camada 04 só pode declarar ausência se a
limitação chegar até ela).

## 7. Matriz de compatibilidade

| Componente | Classificação | Justificativa |
| --- | --- | --- |
| `MultiObjectiveAStar` / `SearchResult` | **PRESERVAR** | A\* Pareto com estado físico; base da Camada 03 |
| `state.py` / `advance_state` | **PRESERVAR** | Acúmulo dirigido pelo modelo físico |
| `frontier.py` / `queue.py` | **PRESERVAR** | Contratos estáveis e testados |
| `dominance.py` (`SpeedBucketDominance`) | **ADAPTAR** | Manter; quantificar efeito de `speed_step_mps` |
| `heuristic.py` | **PRESERVAR** | Admissibilidade verificada; registrar Achado A |
| `objectives.py` / `profiles.py` | **PRESERVAR** | Preferência declarativa |
| `candidates.py` / `route.py` | **ADAPTAR** | Propagar qualidade/limitação (Decisão 5) |
| `diversity.py` / `selection.py` | **PRESERVAR** | Diversidade de apresentação |
| Guardrails de circulação (restrições/acesso) | **COMPLEMENTAR** | Hoje ausentes (§8) |
| `approximate.py` | **PRESERVAR** (isolado) | Experimental/opcional (ADR-025 §10.1) |
| Algoritmo de fonte única (caminho mínimo) | **FORA DE ESCOPO** | Não carrega estado físico nem multiobjetivo |

## 8. Análise de lacunas — guardrails operacionais (Camadas 01 e 03)

A arquitetura consolidada exige que os guardrails de **legalidade e
conectividade** atuem **durante a geração de caminhos**, não só na Camada 05.

### 8.1 Achado C — restrições são representadas, não impostas

O ADR-024 é explícito (L110-L119): as restrições são **representadas e
preservadas**, mas "nenhuma lógica de busca/validação avalia `TurnRestriction` ou
condicionais; portanto **não se pode afirmar que o guardrail já protege contra
conversões proibidas**".

Confirmado na Camada 03: **nenhuma** referência a `restriction`,
`turn_restriction`, `bicycle_access` ou `AccessRule` em `src/search/` (grep sem
resultados). Consequências:

- **restrições de conversão** (`TurnRestriction`) não são avaliadas na transição
  entre arcos — a busca pode propor conversões proibidas;
- **regras de acesso** (`AccessRule`, `bicycle=no`, `*:conditional`) não são
  avaliadas — a busca pode usar vias proibidas;
- **`bicycle_access = DISMOUNT`** existe no `Segment`/`Arc` mas não vira custo nem
  restrição na busca.

Isso é exatamente o "guardrail operacional" que o ADR-026 deve formalizar como
contrato (não necessariamente implementar agora), mantendo os invariantes do
ADR-024 (L126-L132): ausência de restrição ≠ permissão; restrição legal não é
penalidade ajustável de custo; arco proibido não se torna válido por rota mais
curta.

### 8.2 Conectividade

Estruturalmente garantida: a busca só percorre **arcos existentes** via
`graph.out_arcs(node)` (`astar.py` L292). Não há como "atravessar o vazio". A
lacuna de conectividade é **semântica** (legalidade de transição), não estrutural.

### 8.3 Limites físicos

- `Environment.safe_max_speed_mps` é aplicado **na física** e usado pela heurística
  para admissibilidade — não é uma restrição da busca.
- `Segment.max_speed_kmh` (limite legal da via) **é** aplicado na física via
  `physical/arc_profile._max_speed_mps` → `ProfileStep.max_speed_mps` →
  `advance_speed` (`min(max_speed_mps, safe_max_speed_mps)`, `speed.py` L232-L239).
  Portanto o limite de velocidade **já é respeitado** na avaliação física; não é
  lacuna.

### 8.4 Interface de candidatos (§6.3)

Qualidade/limitação da física não chega à Camada 04. Lacuna de contrato.

## 9. Cenários de referência (baseline atual)

**Criado em `026-baseline-busca.md`** (com `tests/test_search_reference.py`): um
baseline de caracterização da busca que fixa, em `0.18.0`, grafos determinísticos,
a fronteira de destino por perfil (arcos + métricas), o comportamento dos limites
(`max_expansions`, `max_labels_per_node`), o efeito dos buckets e as métricas de
desempenho (`expanded`/`generated`). Ele cobre os requisitos antes em aberto:

1. grafo pequeno determinístico com **duas ou mais** rotas não dominadas (S1);
2. fronteira de destino por perfil (arcos + métricas) para os cinco perfis (S1);
3. comportamento do bucket (`speed_step_mps`) sobre a fronteira (S3);
4. ordem de seleção e `diversity_relaxed` (S7);
5. truncamento (`max_expansions`) e corte por chave (S4, S5).

Sem esse baseline, qualquer mudança futura (bucket, diversidade, guardrails) não
tem contra o que ser comparada.

## 10. Riscos e questões abertas

| # | Questão | Estado |
| --- | --- | --- |
| 1 | Semântica das restrições de conversão (tipos `no_*`/`only_*`, `via_way_ids`) | Contrato em `026-contrato-circulacao.md` §2/§3 |
| 2 | Política conservadora para `AccessRule`/condicionais não avaliáveis | Contrato em `026-contrato-circulacao.md` §3 |
| 3 | Quantificar o efeito de `speed_step_mps` na fronteira | Aberta — baseline §9 carateriza, não quantifica |
| 4 | Calibração das tolerâncias lexicográficas e de `max_similarity` | Aberta (preferência) |
| 5 | Propagar `SimulationStatus`/limitação até a candidata | Planejada em `026-contrato-circulacao.md` §6 |
| 6 | Admissibilidade da heurística de energia (`0.0`) | **Resolvida** (§4.3) |
| 7 | Admissibilidade como requisito de corretude | **Esclarecida** (§3.2) |
| 8 | Diversidade intrínseca da fronteira | Aberta (§5.3) |

## 11. Conclusão

A Camada 03 **já atende** a maior parte do que as cinco decisões vão formalizar:
física como fonte única de custo, estado físico propagado, dominância que respeita
a velocidade de saída, heurística admissível verificada e interface de candidatas
determinística. **Nada exige substituir a busca existente.**

As lacunas são **específicas e localizadas**:

1. **guardrails de circulação ausentes** (§8.1) — a maior lacuna; pertence ao
   contrato do ADR-026;
2. **qualidade/limitação não propagadas** até a candidata (§6.3);
3. **baseline de caracterização inexistente** (§9);
4. **efeito do bucket não quantificado** (§3.3) e **diversidade da fronteira não
   medida** (§5.3).

Nenhuma dessas lacunas foi introduzida agora: todas são **declaradas** na física
(ADR-024/ADR-025) ou são ausências de contrato. O ADR-026 fecha as cinco decisões
com base nesta evidência, **sem** alterar a dinâmica nem introduzir parâmetros
físicos arbitrários.
