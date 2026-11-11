# Contrato de circulação e guardrails da Camada 03

> Documento de apoio ao **ADR-026** (etapas 2–7 do plano aprovado). **Não
> implementa** mudança de comportamento: define o contrato, a política, o ponto
> de aplicação, a interação com a dominância, o plano de propagação, os testes
> propostos e a análise de impacto. Preserva o `MultiObjectiveAStar`, a estrutura
> Pareto e o simulador físico; **não** introduz um segundo mecanismo de
> roteamento.

## 1. Princípio

- Os guardrails de **legalidade e conectividade** atuam **durante a geração de
  caminhos** (Camada 03), não só na Camada 05.
- Os guardrails **filtram** a expansão existente; não geram rotas, não
  recalculam física e não substituem a busca.
- **Inércia:** com **nenhuma** restrição registrada, a busca permanece
  **idêntica** ao baseline (`026-baseline-busca.md`). O guardrail é opt-in pelos
  dados, não por configuração.

## 2. Contrato de circulação (etapa 2)

### 2.1 Entidades reaproveitadas (ADR-024 — nada novo de física)

| Entidade | Onde | Papel no guardrail |
| --- | --- | --- |
| `AccessRule(scope, value, condition, raw)` | `Segment.restrictions` (agregado em `Arc.restrictions`) | Proibir/permitir arco |
| `TurnRestriction(id, from_way_id, to_way_id, restriction_type, via_node_id, via_way_ids, condition, source)` | `RoutingGraph.turn_restrictions` / `turn_restrictions_at(node)` | Proibir transição entre arcos |
| `RestrictionSource` | dentro de `TurnRestriction` | Proveniência (não decisão) |
| `BicycleAccess.YES / DISMOUNT` | `Segment`/`Arc` | Tratamento explícito de desmontar |
| `parse_conditional_restrictions` | `graph/restrictions.py` | Extrai regras condicionais |
| `turn_restriction_from_relation` | `graph/restrictions.py` | Normaliza relação OSM |

Nenhuma dessas entidades é reescrita; o contrato apenas define **quando** e
**como** são consultadas.

### 2.2 Duas decisões de circulação

O guardrail expõe **duas** perguntas, ambas devolvendo um veredito tri-estado:

```
ArcDecision(verdict ∈ {ALLOW, FORBID, UNKNOWN}, reason ∈ {...})
TransitionDecision(verdict ∈ {ALLOW, FORBID, UNKNOWN}, reason ∈ {...})
```

- **`arc_decision(arc, context)`** — acesso do arco (`AccessRule`, `bicycle=*`)
  e desmontagem (`BicycleAccess.DISMOUNT`).
- **`transition_decision(from_arc, to_arc, node, context)`** — restrições de
  conversão (por `via_node_id` **e** por `via_way_ids`).

`UNKNOWN` é um veredito de **primeira classe** (nunca "permitido por padrão"); a
política da §3 decide o tráfego a partir dele.

### 2.3 Restrições via-way

`TurnRestriction.via_way_ids` é uma **sequência ordenada** de vias (ADR-024,
endurecimento item 5). Para restrições `only_*`, avaliar a transição exige os
`way_id` dos **últimos k arcos** do caminho, onde `k = len(via_way_ids)`. O
histórico necessário está no `parent` chain (ver §4.2).

### 2.4 `bicycle_access = DISMOUNT`

A física **já** modela desmontar: `PhysicalTraversal` percorre o trecho a
`DEFAULT_DISMOUNT_SPEED_MPS = 1.4 m/s` (`physical/traversal.py` L37, L175-L182).
Portanto `DISMOUNT`:

- **não** é proibição legal (é permitido desmontar e caminhar);
- **não** é ignorado (a física já impõe a velocidade de caminhada);
- **deve** ser **exposto** na candidata (a Camada 04 precisa saber que houve
  trecho desmontado) — sem criar custo novo nem constante física.

No contrato, `arc_decision` marca `DISMOUNT` como `ALLOW` com
`reason = "dismount"`; a limitação viaja pela propagação da §6.

## 3. Política conservadora (etapa 3)

Três situações **distintas**, nunca confundidas:

| Situação | Decisão | Legitimidade afirmada? |
| --- | --- | --- |
| **Proibição explícita** incondicional (`bicycle=no`, `access=no`, `dismount:yes`?) | **FORBID** | Não se aplica |
| **Permissão explícita** incondicional (`bicycle=yes`) | **ALLOW** | Sim (declarada) |
| Condicional **proibitiva**, condição **avaliável** e verdadeira | **FORBID** | — |
| Condicional **proibitiva**, condição **avaliável** e falsa | **ALLOW** | — |
| Condicional **proibitiva**, condição **não avaliável** (sem contexto) | **FORBID** (conservador) | — |
| Condicional **permissiva**, condição **não avaliável** | **não libera** proibição base | — |
| **Ausência** de regra | **ALLOW** para rotear | **Não** — ausência ≠ permissão absoluta |
| `BicycleAccess.DISMOUNT` | **ALLOW** com limitação | — |

**Distinções exigidas pelo plano:**

1. **Proibição explícita** → proibição **dura** (nunca penalidade de custo
   ajustável).
2. **Ausência de regra** → roteável, mas a **legalidade não é afirmada**; o
   resultado **não** pode declarar "rota legal", apenas "sem restrição
   registrada".
3. **Condição sem contexto suficiente** → **política conservadora explícita**
   (`FORBID`), registrada como `UNKNOWN`/limitada, **nunca** "permitido por
   padrão".

**Invariantes (ADR-024 L126-L132):** ausência de restrição não é permissão;
restrição legal não é custo ajustável; arco proibido não se torna válido por rota
mais curta; o guardrail valida transições e vale para rotas tradicionais **e** ML.

A política é **declarativa** (um `CirculationPolicy`), não `if` espalhado; a
condição não avaliável vira `UNKNOWN` **relatado**, não silêncio.

## 4. Ponto de aplicação na expansão (etapa 4)

### 4.1 Local exato

O único ponto de expansão é o laço de arcos em `MultiObjectiveAStar.search`
(`astar.py` L291-L308):

```
for arc in self.graph.out_arcs(label.node_id):
    # [G1] guardrail de acesso ao arco  -> antecipa a travessia fisica
    # [G2] guardrail de transicao       -> usa incoming_arc_id de ``label``
    result = self.traversal.traverse(arc, label.physical_state.speed_mps)
    if not is_feasible(result): continue
    next_label = advance_state(...)
    if manager.insert(next_label): queue.push(...)
```

- **[G1]** `arc_decision(arc)` executado **antes** de `traverse` — evita gastar
  física com arco proibido.
- **[G2]** `transition_decision(from_arc, arc, label.node_id)`, onde `from_arc`
  é o arco de entrada da label corrente (`label.incoming_arc_id`); executado
  também **antes** de `traverse`.
- Aplicar **após** `is_feasible` mantém a física como filtro de sanidade
  numérica; a ordem recomendada é `G1 → G2 → traverse → is_feasible`.

Nenhum laço novo, nenhum segundo roteador: o guardrail apenas **encurta** a lista
de arcos considerados.

### 4.2 Histórico necessário para verificar conversões

- **Conversão simples (`via_node_id`)**: basta o **arco de entrada** da label —
  já disponível (`SearchState.incoming_arc_id`, `state.py` L63), **sem estado
  novo**.
- **Conversão via-way (`via_way_ids`, `only_*`)**: precisa dos `way_id` dos
  últimos `k` arcos. Estão no `parent` chain (`SearchState.parent`), recuperável
  sem duplicar estado. Encaminhamentos candidatos:
  - **(a)** percorrer o `parent` chain no ponto [G2] (O(k)); ou
  - **(b)** manter um **histórico de conversão limitado** na label (últimos `k`
    `way_id`), apenas quando o nó tem restrições via-way.
- **Recomendação:** começar por **(a)** (sem novo estado) e migrar para **(b)**
  somente se o baseline de desempenho (§8) mostrar custo relevante.

## 5. Dominância × contexto legal (etapa 5)

### 5.1 O problema

A chave de dominância é `(node_id, speed_bucket)` (`dominance.py` L73-L74). Duas
labels podem chegar ao **mesmo nó e bucket** por **arcos de entrada diferentes**
— e a restrição de conversão do **próximo** passo depende desse arco de entrada.

```
no n
   X chega por u  (melhores metricas)   -> u -> w  PROIBIDO
   Y chega por v  (piores metricas)     -> v -> w  permitido
```

Se `X` domina e `Y` é descartada, perde-se a **única** continuação legal para
`w`. A dominância atual **eliminaria um estado com contexto legal distinto** —
violando o que o plano exige evitar.

### 5.2 Correção proposta (contrato)

Estender a chave com uma **classe de chegada** que só diferencia arcos quando isso
importa:

```
key(label) = (node_id, speed_bucket,
              arrival_class(label) se o no tem restricao de conversao senao None)
```

- `arrival_class` = `incoming_arc_id` (ou o `way_id` do arco de entrada) —
  suficiente para avaliar a conversão do próximo passo.
- Em nós **sem** restrição de conversão, `arrival_class = None` → **chave igual à
  atual** (comportamento preservado).
- `FrontierManager.labels_at(node)` continua funcionando: a chave segue sendo uma
  tupla com `key[0] == node_id` (`frontier.py` L278-L281).

### 5.3 Impacto

- **Só mantém mais labels** (poda menos) → a fronteira é **superconjunto** da
  atual; nenhuma rota antes presente é perdida.
- **Inércia:** sem restrições registradas, nenhum nó tem `arrival_class` → chave
  idêntica → baseline intacto.
- **Risco:** explosão do espaço em nós com muitas chegadas **e** restrições.
  Mitigação: `arrival_class` só em nós com restrição; `max_labels_per_node`
  continua como controle de desempenho (e passa a ser por chave estendida).

## 6. Propagação de qualidade e limitação até `RouteCandidate` (etapa 6)

### 6.1 Campos a propagar

| Campo | Origem (`TraversalResult`) | Agregação na rota |
| --- | --- | --- |
| `result_status` (`SimulationStatus`) | por arco | **pior** (`UNAVAILABLE < DEGRADED < VALID`) |
| `elevation_uncertainty` | por arco | agregado (cobertura/soma de incerteza) |
| `used_min_speed_regularization` | por arco (bool) | **OR** (qualquer arco) |
| `stalled` | por arco (bool) | **OR** (qualquer arco) |
| `bicycle_access = DISMOUNT` usada | por arco | **OR** → `dismount_used` |

### 6.2 Caminho de propagação

```
TraversalResult  ->  SearchState (advance_state agrega)  ->  Route (route_from_state)
                 ->  RouteCandidate / CandidateMetrics  ->  Camada 04
```

Hoje `advance_state` (`state.py` L114-L140) **não** copia nenhum desses campos:
`is_feasible` só descarta travessias não finitas (`astar.py` L41-L53). A limitação
**se perde** entre a física e a candidata (auditoria §6.3).

### 6.3 Regras

- A agregação é **monotônica**: uma rota nunca "melhora" o status de um arco.
- Sem dados suficientes, o campo vira **ausência explícita** (nunca valor otimista).
- A Camada 04 **só** pode declarar ausência/limitação se ela **chegar** até a
  candidata; por isso a propagação é pré-requisito da interface de predição.
- Nada disso altera geometria, custo, fronteira ou ordem.

## 7. Testes propostos (etapa 7)

### 7.1 Unitários de decisão

- `arc_decision`: proibição explícita, permissão explícita, ausência, condicional
  avaliável (T/F), condicional **não** avaliável (conservador), `DISMOUNT`.
- `transition_decision`: `no_left_turn` (via_node), `only_straight_on`
  (via_node), restrição `via_way_ids` (ordem significativa), ausência, condição
  não avaliável.

### 7.2 Validador independente de legalidade

Função de teste (não do fluxo) que recebe uma `Route` + o grafo e verifica:

1. todo arco passa em `arc_decision`;
2. toda transição consecutiva passa em `transition_decision`;
3. reporta `UNKNOWN`/limitações encontradas.

É o mesmo guardrail usado para validar **rotas ML** — um único contrato.

### 7.3 Testes de integração (propriedade)

- **Nenhuma rota viola circulação:** para grafos determinísticos com restrições
  registradas, o validador (§7.2) **aprova todas** as rotas da fronteira e as
  selecionadas.
- **Inércia:** com **zero** restrições, os cenários S1–S7 do baseline permanecem
  **idênticos** (prova que o guardrail não altera o comportamento quando não há
  o que guardar).
- **Segurança da dominância (§5):** grafo em que a única continuação legal
  depende do arco de chegada; o guardrail **preserva** a rota legal (e, sem a
  chave estendida, o teste falharia — o teste documenta o motivo).
- **via-way:** restrição `only_*` cujo caminho intermediário tem `k > 1` vias.

### 7.4 O que os testes **não** fazem

Não afirmam que uma rota sem restrição é "legal" — apenas que **não viola** as
restrições **aplicáveis e registradas** (a ausência não é permissão absoluta).

## 8. Análise de impacto na busca

| Dimensão | Efeito | Mitigação |
| --- | --- | --- |
| **Desempenho** | +1 `arc_decision` por arco gerado; +1 `transition_decision` por transição; via-way O(k) no `parent` chain | checagem **antes** de `traverse` evita física inútil; histórico (b) só se medido |
| **Memória** | label pode carregar assinatura de chegada / histórico via-way | só em nós com restrição; histórico limitado a `k` |
| **Espaço de estados** | chave de dominância estendida → menos poda **apenas** em nós com restrição | `max_labels_per_node` continua controlando |
| **Corretude** | fronteira ⊆ conjunto legal; guardrail **inertes**sem restrições | prova de inércia no baseline (S1–S7) |
| **Compatibilidade** | nenhum componente substituído; `MultiObjectiveAStar` preservado | matriz da auditoria §7 (COMPLEMENTAR) |
| **Física** | **inalterada**; `DISMOUNT` já modelado em `traversal.py` | sem constante física nova |

**Risco principal:** a política conservadora (§3) pode **bloquear trechos legais**
quando a condição não é avaliável. Mitigação: `UNKNOWN` **relatado**, política
declarativa e teste dedicado; nunca silenciar.

**Nada disso altera a busca.** O contrato e o validador já existem como código
puro (§10); o que permanece **não implementado** é o uso deles na expansão
(etapa comportamental). Esta é a análise prévia exigida antes dela.

## 9. Ordem de implementação sugerida (quando autorizada)

1. **Contrato + validador + testes unitários** (§2, §7.1–§7.2) — **sem** alterar
   a busca. → **concluído** (`src/search/circulation.py`, `tests/test_search_circulation.py`).
2. **Inércia** provada (baseline S1–S7 idêntico) + testes de propriedade. →
   **concluído** (`test_search_reference.py` verde + `test_search_guardrails.py`).
3. **Ligar o guardrail** na expansão (§4) e na dominância (§5) — mudança
   comportamental com **comparação de baseline** e justificativa. → **concluído**.
4. **Propagação** de qualidade/limitação (§6) com testes próprios. → **concluído**.

Cada passo exige **teste + justificativa + comparação com o baseline**
(`026-baseline-busca.md`). A comparação antes × depois está em §11.

## 10. Estado da implementação (código)

**Entregue.** O contrato das §2–§3 e o validador da §7.2 existem em
`src/search/circulation.py`, exportados por `src.search`:

| Símbolo | Papel |
| --- | --- |
| `Verdict` (`ALLOW`/`FORBID`/`UNKNOWN`) | veredito tri-estado |
| `DecisionReason` | motivo explícito e auditável de cada decisão |
| `Decision` | veredito **efetivo** + `raw_verdict` + `legality_asserted` + `dismount` |
| `CirculationPolicy` / `DEFAULT_POLICY` | política declarativa; `UNKNOWN → FORBID` |
| `arc_decision(arc, …)` | acesso do arco (§2.2) |
| `transition_decision(graph, …, history_way_ids=…)` | conversão entre arcos (§2.2, §4.2) |
| `validate_route(graph, arc_ids, …)` / `RouteValidation` | validador independente (§7.2) |
| `constrained_nodes(graph)` | nós onde a próxima conversão depende do arco de chegada (§5) |

Testes:
- `tests/test_search_circulation.py` (**34 testes**) — contrato puro (§7.1–§7.2);
- `tests/test_search_guardrails.py` (**16 testes**) — comportamentais (§4–§6).

Suíte total: **509 testes** (`439 + 34 + 16 + 20`; os 20 últimos são a Camada
05 em `evaluation/` — ver ADR-026 §5).

**Contrato × código (comportamental):** o guardrail agora é consultado pela
expansão do `MultiObjectiveAStar` (§4) e a chave de dominância inclui a classe de
chegada nos nós com restrição de conversão (§5). A avaliação é **guiada pelos
dados**: sem `arc.restrictions` nem `graph.turn_restrictions`, `_guardrails_active`
é `False` e `constrained_nodes` é vazio — a busca, a física e a estrutura Pareto
são **idênticas** ao baseline (prova em §11).

**Decisões de implementação** (alinhadas ao contrato):

- `verdict` é o veredito **efetivo**; `raw_verdict` guarda o tri-estado bruto
  (onde `UNKNOWN` distingue resolução conservadora). Nenhum chamador pode
  esquecer de aplicar a política.
- `legality_asserted` é `True` **apenas** com permissão explícita; ausência de
  regra permite rotear **sem afirmar legalidade** (ADR-024).
- A ordem de escopos de acesso é `bicycle → vehicle → access`; uma regra
  condicional **inativa** (condição falsa) **não** bloqueia escopos mais gerais.
- `via_way_ids` é sequência **ordenada**; sem histórico completo, o veredito
  bruto é `UNKNOWN` (`CONTEXT_INSUFFICIENT`), nunca "permitido por padrão".
- `bicycle=no`/`access=no` **incondicionais** já são resolvidos na construção
  (a via não entra no grafo); o contrato ainda os decide, para grafos montados à
  mão e para a validação de rotas ML.

## 11. Etapa comportamental — implementação e comparação

Esta seção registra a mudança de comportamento autorizada (etapas 2–4 do §9),
com **teste + justificativa + comparação com o baseline**, como exige
`026-baseline-busca.md`.

### 11.1 Onde o guardrail foi ligado

| Ponto | Arquivo | O que faz |
| --- | --- | --- |
| Expansão (G1 acesso → G2 conversão → `traverse` → `is_feasible`) | `astar.py` `_allows` | consulta `arc_decision`/`transition_decision` **antes** de gastar física |
| Histórico de conversão | `astar.py` `_history_way_ids` | segue a cadeia `parent` (opção (a), §4.2); só quando há restrição de conversão |
| Chave de dominância | `dominance.py` `SpeedBucketDominance` | acrescenta `arrival_class` (`incoming_arc_id`) **apenas** em `constrained_nodes` |
| Nós com contexto legal | `circulation.py` `constrained_nodes` | via-node: `via_node_id`; via-way: destino dos arcos cujo `osm_way_id == via_way_ids[-1]` |
| Propagação de qualidade/limitação | `state.py` `advance_state`, `route.py`, `candidates.py` | agregação monotônica (§6) |

### 11.2 Inércia (sem restrições) — antes × depois

Contrato de avaliação: sem `arc.restrictions` nem `graph.turn_restrictions`,
`_guardrails_active = False` e `constrained_nodes = {}`.

| Cenário | Antes | Depois |
| --- | --- | --- |
| S1–S3 (fronteira + ordem) | congelado | **idêntico** |
| S4 (`truncated`, `expanded`, `generated`) | congelado | **idêntico** |
| S5 (`max_labels_per_node`) | congelado | **idêntico** |
| S6 (modelo físico real) | congelado | **idêntico** |
| S7 (fluxo completo) | congelado | **idêntico** |

`tests/test_search_reference.py` (13 testes) passa **sem alteração** — nenhum
valor congelado foi tocado. `test_search_guardrails.py::test_guardrails_inactive_without_restrictions`
reforça: `_guardrails_active is False` e `constrained_nodes == frozenset()`.

### 11.3 Com restrições — o que muda (correto por construção)

| Caso | Antes | Depois |
| --- | --- | --- |
| Arco com `bicycle=no` na melhor rota | rota usava o arco | arco **evitado**; sobra a alternativa legal |
| Conversão `no_left_turn` na melhor rota | rota fazia a conversão | conversão **evitada** |
| `only_straight_on` | conversão proibida podia ser escolhida | só a conversão satisfeita sobrevive |
| Mesma chegada (nó, bucket) com continuidades legais distintas | dominância podia **podar** a única legal | `arrival_class` mantém os dois contextos (ADR-026 §5) |
| `DISMOUNT` | — | `ALLOW` com limitação; `dismount_used` propaga |

### 11.4 Propriedade global

`test_no_returned_route_violates_circulation` (4 perfis × cenários restritos)
verifica, para **toda** label retornada, que `validate_route(...).compliant` é
`True`. O validador é **independente** da expansão: usa a mesma função pública que
as rotas geradas pelo ML usarão, de modo que tradição e ML compartilham um único
contrato.

### 11.5 Limitações declaradas

- **Incoming-arc-based (aprovado):** a classe de chegada é o `incoming_arc_id`.
  Duas chegadas pelo **mesmo** arco com prefixo via-way diferente ainda podem ser
  tratadas como equivalentes; documentado como limitação, aceito pelo contrato.
- **UNKNOWN → FORBID:** sem avaliador de condição, toda condição é conservadora.
  Isso pode descartar rotas potencialmente legais — nunca libera as proibidas.
- **Custo:** apenas poda (filtra antes da física); **nenhum** custo fictício é
  somado às métricas. `energy_j`/`time_s`/`ascent_m` continuam sendo física pura.


