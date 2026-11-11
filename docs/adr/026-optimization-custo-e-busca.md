# ADR-026 — Optimization, Cost & Search (Camada 03 — Optimization)

- **Status:** proposto (aguardando revisão técnica)
- **Contexto:** Camada 03 (Optimization — Cost & Search) do projeto
  `roteamento com ml`. Consome a Camada 01 (Data Model, ADR-024) e a Camada 02
  (Physical Model, ADR-025) e alimenta a Camada 04 (Intelligence) e a Camada 05
  (Evaluation & Guardrails).
- **Documentos de apoio:**
  - `026-auditoria-busca.md` (inventário, contrato × código, invariantes ×
    evidência e lacunas);
  - `026-baseline-busca.md` (caracterização congelada da busca — etapa 1);
  - `026-contrato-circulacao.md` (contrato de circulação, política conservadora,
    ponto de aplicação, dominância, propagação, testes propostos e impacto —
    etapas 2–7).
  Este ADR **não** altera código; ele fixa o contrato que a implementação deve
  seguir.

## 1. Contexto e objetivo

A Camada 03 é responsável por **gerar o conjunto de rotas candidatas** a partir do
grafo e das restrições, antes que qualquer predição fisiológica aconteça. Ela já
possui um A\* multiobjetivo (MOA\*) com estado físico, dominância por bucket de
velocidade e uma etapa de seleção por perfil (ver auditoria §1).

O objetivo deste ADR **não** é criar outro A\*. É formalizar e auditar a camada,
fechando cinco decisões, para garantir que ela entregue à Camada 04 um conjunto de
rotas **válido, diverso e fisicamente coerente**.

### Ordem de organização (não cadeia rígida de execução)

```
01 DATA MODEL        Graph & Environment
02 PHYSICAL MODEL    Physics & Rider
03 OPTIMIZATION      Cost & Search         ← este ADR
04 INTELLIGENCE      ML & Prediction
05 EVALUATION & GUARDRAILS  Validation & Safety
```

Os **guardrails de legalidade e conectividade** atuam **desde as Camadas 01 e 03**
(§4), não apenas na Camada 05. A Camada 05 consolida a **avaliação sistêmica**
(qualidade das recomendações, comparação ML × algoritmos tradicionais, regressões,
vazamento de dados, calibração e incerteza).

### Regra arquitetural permanente

> O **A\*** gera candidatos; o **ML** prevê esforço e apoia a classificação; o
> **simulador físico completo** permanece a referência de avaliação.

Isso **não** impede o ML de atuar como **gerador opcional** de rotas — mas, se o
fizer, essas rotas passam pelas **mesmas regras de circulação** e pela **mesma
avaliação física** (Decisão 5, §4).

## 2. Decisões propostas

| # | Questão arquitetural | Decisão proposta |
| --- | --- | --- |
| 1 | O que a busca minimiza antes da avaliação por ML? | **Vetor Pareto de métricas físicas** por perfil; sem peso arbitrário de superfície |
| 2 | Como preservar velocidade/momentum sem explodir o espaço? | **Estado físico mínimo** (velocidade; energia cinética derivada) + dominância **por bucket** |
| 3 | Quais limites inferiores são admissíveis? | Heurística geométrica com **teto de velocidade explícito**; `0.0` onde não há limite provado |
| 4 | Como gerar alternativas genuinamente diferentes? | **Deduplicação** + **diversidade greedy** na apresentação, com fallback explícito |
| 5 | Como entregar rotas válidas e comparáveis ao ML? | `Route`/`RouteCandidate` com métricas físicas + **qualidade/limitação** propagadas; sem predição acoplada |

### Decisão 1 — Função de custo

**Decisão.** A busca mantém uma **fronteira de Pareto** sobre métricas **físicas**
acumuladas (`time_s`, `distance_m`, `ascent_m`, `energy_j`; `descent_m` é
acumulado mas não é objetivo). Cada perfil escolhe um `ObjectiveSet` declarativo
(produto, não algoritmo). **Nenhum** peso, penalidade de superfície, ruído ou
custo de segurança é somado à física.

**Justificativa (auditoria §2).** A física já é a fonte única de tempo/energia
(`astar.py`, `state.py`); "quais métricas entram" é decisão de produto
(`objectives.py`); não há função escalar a auditar. Introduzir pesos arbitrários
violaria a norma do projeto (nada de constantes/pesos mágicos).

**Contrato.**

- O custo antes do ML é o **vetor** de objetivos físicos do perfil.
- A agregação escalar (score, tolerância, pesos) existe **apenas** em
  ranking/seleção, **nunca** na física nem na fronteira.
- Ordem do `ObjectiveSet` afeta só a prioridade de exploração — não a fronteira.

**Invariantes.**

- Todos os objetivos minimizados (`dominates`).
- Perfil não altera física nem superfície.
- Igualdade não é dominância.

### Decisão 2 — Estado e dominância

**Decisão.** O estado de busca carrega o **mínimo** para simular o próximo arco:
`PhysicalState(speed_mps, kinetic_energy_j derivada)`. A dominância compara labels
**somente no mesmo nó e no mesmo bucket de velocidade**; a explosão é contida por
`max_labels_per_node` (por chave) e `max_expansions`, **cada um com seu próprio
sinal** (ver Contrato).

**Justificativa (auditoria §3).** Preserva a continuidade dinâmica (ADR-025 dec. 1)
sem duplicar o estado canônico; o bucket é a aproximação central e já é
documentado como tal.

**Contrato.**

- Momentum não é campo independente: com massa constante é proporcional à
  velocidade; a energia cinética é **derivada** (`from_speed`).
- O bucket é a **única** aproximação de estado; a mesma política vale na chave da
  fronteira e na dominância.
- Controles de desempenho **podem** remover rotas não dominadas; isso **deve**
  ficar **exposto**, com sinais **distintos** por natureza do efeito (não os
  confundir com deduplicação de candidatas, que é etapa posterior):
  - `max_expansions` **interrompe** a busca → `SearchResult.truncated`;
  - `max_labels_per_node` (por chave) **poda** labels não dominadas na fronteira
    → `SearchResult.frontier_truncated` e `SearchResult.dropped_labels` (> 0).
- **Caveat de leitura (não confundir ausência de sinal com prova de ótimo).**
  `truncated=False` **e** `frontier_truncated=False` significam apenas que
  **nenhum dos dois limites atuou**; **não** provam completude nem otimalidade
  **global**. A exploração termina quando a fila ativa esvazia sob os caps e a
  heurística correntes, e a granularidade do bucket (`speed_step_mps`) continua
  uma **aproximação** deliberada (auditoria §3.3). Os dois sinais são
  **necessários, não suficientes** para afirmar corretude da fronteira.
- `epsilon_dominance` é um controle **reservado**: aceito e validado em
  `SearchConfig`, mas **inativo** no caminho do `MultiObjectiveAStar` (a
  `SpeedBucketDominance` usada por padrão não aplica epsilon). Ver “Ponto em
  aberto”.

> **Reconciliação (incremento de observabilidade da fronteira).** A redação
> anterior dizia “sempre sinalizados (`truncated`, `dropped_duplicates`)”. A
> `dropped_duplicates` mede **outra** coisa — a deduplicação do pool de candidatas
> **após** a busca (`candidates.py`/`pipeline.py`/`selection.py`) — e **nunca**
> refletiu a poda por `max_labels_per_node`, que era **silenciosa**. O incremento
> implementou o sinal que faltava (`frontier_truncated`/`dropped_labels`). A
> implementação prevalece e a divergência fica aqui registrada.

**Achado registrado (auditoria §3.2).** O critério de parada é exaustivo (a fila
ativa esvazia; o destino não é expandido). Logo, **a admissibilidade da heurística
não é requisito de corretude da fronteira** nesta implementação — é requisito de
ordenação e de comportamento sob caps. A checagem de admissibilidade permanece
como proteção (Decisão 3).

**Lacuna a fechar.** Quantificar o efeito de `speed_step_mps` na fronteira
(baseline, §6).

**Ponto em aberto — `epsilon_dominance` (avaliado neste incremento).**

O campo `SearchConfig.epsilon_dominance` é aceito e validado, mas **não tem
efeito** no caminho principal: `MultiObjectiveAStar` constrói
`SpeedBucketDominance(self.profile.objective_set, speed_step_mps=..., arrival_class_nodes=...)`
**sem** epsilon, e essa regra de dominância **não** consome epsilon. Quem aplica
epsilon de fato é `ParetoFrontier`/`FrontierMap` (caminho legado), via
`epsilon_dominates` (`objectives.py`).

Decisão deste incremento (**sem alterar a semântica de dominância agora**):

- **Documentar** o estado (feito aqui);
- **não** ligar epsilon ao `SpeedBucketDominance` neste incremento (mudaria a
  política de dominância, fora do escopo de observabilidade);
- **não** transformar `epsilon_dominance > 0` em erro **ainda**: seria uma
  mudança de comportamento que precisa de tratamento próprio (teste + baseline +
  nota de migração).

Diferença de classe de risco: `epsilon_dominance` **inativo** não causa **perda
de completude** (não remove nada); é um **no-op silencioso** de uma configuração
declarada. Já a poda por `max_labels_per_node` **removia** labels não dominadas em
silêncio — essa é a lacuna que este incremento fecha.

**Follow-up (rastreável).** Ou (a) ligar `epsilon_dominance` ao
`SpeedBucketDominance`, com evidência de efeito no baseline; ou (b) recusar
explicitamente `epsilon_dominance > 0` enquanto não suportado (erro claro em vez
de no-op). Ambas exigem entrega própria.

### Decisão 3 — Heurística

**Decisão.** A heurística devolve **limites inferiores** por objetivo, sempre
admissíveis: distância haversine; tempo `≥ distância / teto_de_velocidade`;
subida `≥ max(0, Δh)`. Onde **não** houver limite provado, o limite é `0.0`.

**Justificativa (auditoria §4).** O teto de velocidade tem três estados
(finito/ilimitado/desconhecido) e a guarda recusa heurísticas provadamente
inadmissíveis. O limite `0.0` para energia foi **verificado**: o trabalho na roda
nunca é negativo (`speed.py`) e descidas com potência zero dão `mechanical_energy_j
= 0.0` (sonda na `0.18.0`).

**Contrato.**

- `time_s` só tem limite real se `max_speed_mps` for um **teto verdadeiro**;
  modelo sem teto anunciado ⇒ limite `0.0`.
- Objetivos sem limite geométrico (`energy_j`) ⇒ `0.0`.
- Subida sem elevação nos nós ⇒ `0.0` (conservador).

**Invariantes.**

- Nenhum limite superestima o custo restante (admissibilidade).
- Não usar "velocidade média" nem constantes de conveniência.
- A admissibilidade é exigida **e** documentada como requisito de
  ordenação/desempenho, não de corretude (ver Decisão 2).

### Decisão 4 — Diversidade

**Decisão.** Alternativas são deduplicadas (por caminho e/ou por métricas) e
filtradas por **diversidade greedy** na apresentação, com **fallback explícito**
(`RETURN_FEWER`, `RELAX_THRESHOLD`, `NONE`). A diversidade é **preferência de
apresentação**, **não** objetivo físico.

**Justificativa (auditoria §5).** Evita encher a lista com rotas quase iguais sem
contaminar a fronteira nem a física.

**Contrato.**

- Dedup antes do ranking; `PATH` é o padrão.
- Diversidade aceita/não aceita por limiar; relaxamento marca
  `diversity_relaxed` e `threshold_used`.
- Ordenação determinística (`label_id`, `route_id`).

**Lacuna declarada.** Não há métrica da **diversidade intrínseca da fronteira**; no
MVP aceita-se que a diversidade atue só na apresentação. Reavaliar com o baseline.

### Decisão 5 — Interface de candidatos

**Decisão.** A interface entrega `Route`/`RouteCandidate` com **métricas físicas**
(arcos direcionados, comprimentos, tempo/distância/subida/energia) e identidade
estável. **Nenhuma predição** (Borg/FC) é acoplada à candidata. **Qualidade e
limitação da física devem ser propagadas** para que a Camada 04 possa declarar
ausência.

**Justificativa (auditoria §6).** Rotas válidas e comparáveis, sem atrelar a
predição ao gerador; hoje a limitação (`result_status`,
`elevation_uncertainty`, `used_min_speed_regularization`, `stalled`) **se perde**.

**Contrato.**

- A candidata é um **dado** físico + identidade; a predição é da Camada 04.
- Rotas geradas por ML, se houver, passam pelas **mesmas** regras de circulação
  (§4) e pela **mesma** avaliação física.
- Toda limitação da física que afete a interpretação **deve** acompanhar a rota.

## 3. Arquitetura lógica

```
Camada 01 (Data Model)            Camada 02 (Physical Model)
  Arc · Segment · Link              TraversalModel · PhysicalTraversal
  restrições · acesso · geometria   tempo · velocidade · energia · esforço
        │                                    │
        └───────────────┬────────────────────┘
                        ▼
             Camada 03 (Optimization)
  Objetivos (Pareto) → A* com estado físico → Fronteira de destino
        → Route → candidatas (dedup) → ranking + diversidade → N rotas
                        │
                        ▼
             Camada 04 (Intelligence)
  EffortFeatures (do simulador completo) → Borg/RPE · zonas FC → classificação
                        │
                        ▼
          Camada 05 (Evaluation & Guardrails)
  avaliação sistêmica · regressões · calibração · incerteza
```

O ML **não** substitui a dinâmica física. Ele consome as métricas e as features
produzidas pela simulação.

## 4. Guardrails operacionais (Camadas 01 e 03)

Estes guardrails atuam **durante a geração de caminhos e a avaliação**, não apenas
na Camada 05. O ADR-024 (L110-L139) já declara que as restrições estão
**representadas mas não impostas**; a auditoria §8 confirma que **nada** em
`src/search/` avalia restrições de conversão, regras de acesso ou `DISMOUNT`.

**Decisão proposta.** Formalizar o contrato de circulação, sem implementar física
nova:

| Guardrail | Escopo | Regra |
| --- | --- | --- |
| Restrições de conversão (`TurnRestriction`) | transição entre arcos | proibição é **dura**; avaliar `via_node_id`/`via_way_ids` |
| Regras de acesso (`AccessRule`) | arco | `bicycle=no` ⇒ arco proibido; condicional não avaliável ⇒ **política conservadora explícita** |
| `bicycle_access = DISMOUNT` | arco | física **já** modela caminhada (1.4 m/s); **não** proibir; **expor** a limitação na candidata |
| Conectividade | caminho | garantida por construção (só arcos existentes) |

**Invariantes (ADR-024).**

- Ausência de restrição **não** é permissão absoluta.
- Restrição legal **não** é penalidade ajustável de custo.
- Arco proibido **não** se torna válido por rota mais curta.
- O guardrail valida rotas **tradicionais e ML**.
- Condição não avaliável ⇒ política conservadora **explícita** (nunca "permitido
  por padrão").

**Ordem de implementação.** Primeiro o **contrato** (política + ponto de
avaliação) e os testes de invariante; a avaliação temporal completa entra em
incremento seguinte, como no ADR-024. O contrato detalhado (política
conservadora, ponto de aplicação, dominância × contexto legal, propagação de
limitação e testes propostos) está em **`026-contrato-circulacao.md`**.

### Plano aprovado (etapas)

| # | Etapa | Entregável | Estado |
| - | ----- | ---------- | ------ |
| 1 | Baseline de caracterização da busca | `026-baseline-busca.md` + `tests/test_search_reference.py` (13 testes) | **concluído** |
| 2 | Contrato de circulação (`AccessRule`, `TurnRestriction`, via-way, condicional, `DISMOUNT`) | `026-contrato-circulacao.md` §2 + `src/search/circulation.py` | **concluído** |
| 3 | Política conservadora (proibição explícita / ausência / condição sem contexto) | `026-contrato-circulacao.md` §3 + `CirculationPolicy` | **concluído** |
| 4 | Ponto de aplicação na expansão + histórico de conversões | `026-contrato-circulacao.md` §4 + `astar.py` `_allows`/`_history_way_ids` | **concluído (comportamental)** |
| 5 | Dominância não elimina contextos legais distintos | `026-contrato-circulacao.md` §5 + `dominance.py` `arrival_class` + `constrained_nodes` | **concluído (comportamental)** |
| 6 | Propagação de `result_status`/incerteza/regularização/`stalled` até `RouteCandidate` | `026-contrato-circulacao.md` §6 + `state.py`/`route.py`/`candidates.py` | **concluído (comportamental)** |
| 7 | Testes de "nenhuma rota viola circulação" | `026-contrato-circulacao.md` §7 + `tests/test_search_circulation.py` (34 testes) + `tests/test_search_guardrails.py` (16 testes) | **concluído** |
| 8 | Avaliação física completa + classificação (Camada 05) | §5 + `src/evaluation/` + `tests/test_evaluation_assessment.py` (20 testes) | **concluído** |

**Implementação comportamental:** **concluída** (etapas 2–4 do §9 de
`026-contrato-circulacao.md`). O contrato é consultado pela expansão
(`astar.py`) **antes** de gastar física e a chave de dominância inclui a classe de
chegada **apenas** nos nós com restrição de conversão (`constrained_nodes`). A
qualidade/limitação (`result_status`, incerteza altimétrica, regularização,
`stalled`, `dismount_used`) propaga até `RouteCandidate`. A avaliação é **guiada
pelos dados**: sem restrições, guardrail inerte e baseline S1–S7 idêntico
(`tests/test_search_reference.py` inalterado). Suíte: **509 testes**.
Comparação antes × depois: `026-contrato-circulacao.md` §11;
registro de inércia: `026-baseline-busca.md`.

**Camada 05 (avaliação + classificação):** **concluída** em `src/evaluation/`
(§5). O simulador **completo** avalia a rota, `EffortFeatures` alimentam o
preditor e a classificação (`REJECTED`/`PHYSICAL_ONLY`/`PHYSIOLOGICAL`) declara
explícitamente a ausência de modelo calibrado — sem inventar valor e sem
recalcular física no ML.

## 5. Contrato de integração com a Camada 04 (Intelligence)

- A Camada 04 recebe **rotas avaliadas pelo simulador completo** e extrai
  `EffortFeatures`; ela **não** reavalia a física.
- Se o preditor não tiver **modelo calibrado**, ele **declara a ausência** —
  nunca apresenta previsão como medição (invariantes do ADR-025 §6).
- A predição **não** altera geometria, restrições, fronteira nem ordem física das
  candidatas; ela apoia a **classificação final**.
- Rotas propostas por ML entram como **candidatas adicionais** e passam pelo mesmo
  pipeline das tradicionais.

### Estado da implementação (Camada 05)

**Entregue** em `src/evaluation/` (pacote novo, Camada 05), exportado por
`evaluation` e pela fachada `api`:

| Símbolo | Papel |
| --- | --- |
| `assess_route(graph, arc_ids, cyclist=…, bicycle=…)` | avalia a rota pelo **simulador completo** (`PhysicalTraversal.traverse_path`), extrai `EffortFeatures`, roda `predict_effort` e valida a circulação (`validate_route`) |
| `assess_search_route` / `assess_candidate` / `assess_candidates` | adaptadores para `Route` (Camada 03) e `RouteCandidate` |
| `RouteAssessment` | resultado imutável: `path`, `features`, `prediction`, `circulation`, `metrics`, `limitations` |
| `AssessmentOutcome` | classificação: `REJECTED`, `PHYSICAL_ONLY`, `PHYSIOLOGICAL` |
| `RouteAssessmentError` | erro de **dados** (rota vazia/arco desconhecido/descontínua), nunca mascarado |

**Classificação sem limiar novo** (deriva só de sinais existentes):

* `REJECTED` — `circulation.compliant` é `False`;
* `PHYSIOLOGICAL` — `prediction.status.is_available` (modelo **calibrado**);
* `PHYSICAL_ONLY` — caso contrário, com o motivo explícito
  (`prediction.status`, ex.: `model_not_calibrated` / `unavailable`) exposto em
  `outcome_reason` e `limitations` (código `physiology_<status>`).

**Honestidade preservada:** a ausência de modelo calibrado **nunca** vira valor —
`predicted_rpe`/zonas continuam `None`, `is_measurement` é `False`, e a
qualidade/limitação da física (`result_status`, elevação ausente, regularização,
desmontagem, `stalled`) é propagada para o resultado. Nenhuma física é
recalculada pelo ML: o módulo apenas orquestra componentes existentes.

Testes: `tests/test_evaluation_assessment.py` (**20 testes**). Suíte total:
**509 testes**.

## 6. Critérios de aceite

A Camada 03 é considerada **implementada** quando:

| Cenário | Comportamento esperado |
| --- | --- |
| Duas rotas com trade-off real | Ambas sobrevivem na fronteira |
| Rota dominada | É podada |
| Mesmo nó, velocidades de saída diferentes | **Não** são comparadas entre buckets |
| Modelo sem teto de velocidade | Limite de tempo da heurística = `0` (admissível) |
| Heurística inadmissível explícita | É recusada |
| Descida com potência zero | Energia mecânica **não** negativa; limite de energia `0.0` admissível |
| Rotas quase idênticas | Deduplicadas / colapsadas antes do ranking |
| Diversidade impede preencher | Fallback explícito com `diversity_relaxed` relatado |
| Truncamento (`max_expansions`) | Resultado marcado `truncated=True` |
| **Conversão proibida** | **Não** aparece em nenhuma rota |
| **Via com `bicycle=no`** | **Não** aparece em nenhuma rota |
| Elevação ausente / regime regularizado | Limitação **propagada** até a candidata |
| Mesma rota por caminhos diferentes | Mesmo resultado físico |

Os testes devem incluir **baseline de caracterização** da fronteira (auditoria §9)
com tolerâncias **justificadas**, sem "consertar" valores sem comparação
registrada.

## 7. Consequências arquiteturais

**Benefícios.** Física reutilizável como fonte única de custo; busca auditável e
reproduzível; interface estável e honesta (com limitação) para o ML; guardrails de
legalidade explícitos desde a geração.

**Custos.** Buckets e caps são aproximações que **podem** remover alternativas não
dominadas (mitigado por exposição); avaliar restrições de conversão adiciona
trabalho por transição; a diversidade é heurística de apresentação, não garantia.

**Fora de escopo imediato.** Novo algoritmo de busca; heurísticas aprendidas;
integração do estimador experimental (`approximate.py`) ao fluxo principal; custo
único escalar substituindo o vetor Pareto.

## 8. Fora de escopo e follow-ups registrados

- **Baseline de caracterização da busca** (fronteira por perfil congelada) —
  exigido antes de mudar bucket/diversidade/guardrails.
- **Quantificar `speed_step_mps`** na fronteira.
- **Avaliação temporal de restrições** (`TurnRestriction`, `AccessRule`,
  condicionais) — contrato agora, avaliação em incremento seguinte.
- **Propagar qualidade/limitação** (`SimulationStatus`, `elevation_uncertainty`,
  `used_min_speed_regularization`, `stalled`) até a candidata.
- **Calibração** das tolerâncias lexicográficas e de `max_similarity` (preferência).
- **Diversidade intrínseca da fronteira** — reavaliar com dados.
- `approximate.py` permanece **experimental/opcional** (ADR-025 §10.1), fora do
  fluxo principal.

## 9. Instrução de implementação (portão de fase)

**ATENÇÃO: a engine Python já possui busca, dominância, heurística e seleção.
Não recrie o A\* existente.** Antes de modificar qualquer código:

- inspecionar os módulos de `src/search/` e seus testes (auditoria §1);
- identificar quais contratos deste ADR **já são atendidos** (auditoria §2-§6);
- classificar cada componente como **PRESERVAR, ADAPTAR, COMPLEMENTAR** ou
  **SUBSTITUIR** (matriz da auditoria §7), justificando qualquer substituição;
- produzir o **baseline de caracterização** (auditoria §9) antes de qualquer
  mudança de comportamento;
- registrar os **testes de referência** necessários para detectar regressões.

**Não** introduzir novos algoritmos de busca, parâmetros arbitrários de
preferência ou reorganizações de módulos. Toda mudança de comportamento exige
**teste + justificativa + comparação com o baseline**.
