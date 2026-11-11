# ADR-033 — Arquitetura por componentes de pipeline

- **Status:** **aceita** (2026-11-11) como decisão da Fase 3 da auditoria
  arquitetural. **Não executada**: nenhum arquivo de produção foi alterado por
  este ADR.
- **Data:** 2026-11-11
- **Relação:** complementa o ADR-030 e o ADR-031 (organização de módulos) e
  **reposiciona** o ADR-032 (estrutura-alvo e identidade de pacote). Não altera
  física, busca, ML, contrato `v1`, CLI nem comportamento de rota.
- **Base medida:** **105 módulos** `router.*` (104 submódulos + raiz),
  **350 arestas únicas** de import, **596 imports**, **0 ciclos**, **0
  auto-imports**, **0 violações** da matriz de camadas, **0 imports dinâmicos**;
  **843 testes verdes**; `api.__all__` = **336**, `integration.__all__` = **28**;
  todos os **104 submódulos importáveis** em interpretador limpo.
- **Regra da auditoria:** primeiro medir e diagnosticar; só depois alterar. Este
  ADR é o registro da **decisão** (Fase 3). O guia normativo completo é a Fase 4
  e a execução é a Fase 5 — nenhuma das duas começou.

> **Diferença em relação aos ADRs anteriores.** O ADR-031 decidiu *onde* cada
> coisa mora; o ADR-032 propôs *para onde* mover. Este ADR decide **qual
> arquitetura adotar** à luz do que foi medido e, principalmente, **o que não
> mover**. Ele existe para impedir dois erros simétricos: manter a estrutura por
> inércia e reorganizá-la por estética.

---

## 0. Contexto

A auditoria das Fases 1–2 mapeou o sistema e encontrou um grafo de dependências
**limpo**: o motor já é um DAG com direção única (`contracts` → `core.data` →
`core.physics` → `core.search`/`core.prediction` → `core.evaluation` → `app` →
`interfaces`), sem ciclos e sem violações da matriz do ADR-031. A suíte está
verde e todos os módulos importam.

Os problemas encontrados **não são de macroestrutura**. São de:

1. **fronteira pública ambígua** — três níveis declarados, quatro caminhos
   reais, e documentação que chama a fachada legada de "API pública";
2. **coesão local** — `interfaces/cli/__init__.py` com 1.176 linhas e cinco
   responsabilidades; `app/planning.py` misturando orquestração, DTO e projeção
   de contrato;
3. **acoplamento acidental na borda** — `app` como depósito de reexportações
   (`legacy`, `tooling`); `integration` com 28 nomes, mas 247 módulos carregados;
4. **abstrações sem consumidor** — `RouteAssessmentView`, `GraphRepository`,
   `JsonGraphRepository`;
5. **verificação incompleta** — o teste de arquitetura compara pares de camadas,
   mas não detecta ciclos intra-camada nem imports de fachada.

O ADR-032 havia proposto uma árvore-alvo (achatamentos, `physiology` irmão da
física, `ml.py`, fonte única de integração). A auditoria mediu o custo e o ganho
dessas mudanças: **o diagnóstico do ADR-032 está correto; a prioridade, não**.
Os achatamentos não alteram nenhuma aresta do grafo — são movimentos de caminho
com atualização mecânica de dezenas de imports. Já a fronteira pública, as portas
mortas e a coesão da CLI/`planning` afetam manutenção real.

---

## 1. O problema de decisão

A decisão precisa responder a três perguntas independentes:

1. **Macroestrutura:** o sistema deve ser organizado por camada técnica, por
   funcionalidade, por híbrido ou por componente/estágio de pipeline?
2. **Fronteira:** qual é a superfície pública, quantas existem e o que cada uma
   promete?
3. **Custo de mudança:** o que se move agora, o que se adia e sob qual
   condição?

Misturar as três produz decisões ruins: mover diretórios para "resolver" a
fronteira (churn sem ganho) ou declarar a fronteira resolvida sem tocar no
acoplamento (documentação sem efeito).

---

## 2. O que a medição mostrou

### 2.1 Estrutura

| Medida | Valor |
|---|---|
| Módulos `router.*` | 105 (104 submódulos + raiz) |
| Arestas únicas / imports totais | 350 / 596 |
| Ciclos (SCC > 1) | 0 |
| Violações da matriz de camadas | 0 |
| Imports dinâmicos (`import_module`, `__import__`) | 0 |
| Módulos importáveis | 104/104 |

### 2.2 Volume por estágio

| Pacote | Linhas |
|---|---:|
| `core.data` | 6.142 |
| `core.search` | 5.448 |
| `core.physics` | 3.313 |
| `core.prediction` | 2.853 |
| `app` | 2.159 |
| `core.evaluation` | 1.414 |
| `interfaces` | 1.285 |
| `contracts` | 871 |

### 2.3 Acoplamento

Fan-in alto: `core.data.graph.model` (27), `physical.models` (13),
`core.data.geo` (12), `physiology` (11). É o **modelo canônico compartilhado** —
acoplamento esperado e legítimo em um pipeline.

Fan-out alto: `core.search.__init__` (21), `app.legacy` (20),
`app.planning` (17), `app.tooling` (17), `core.evaluation.assessment` (14).

Fachadas grandes: `core.search` (125 nomes, 21 imports), `intelligence` (76),
`physical` (51), `evaluation` (25).

### 2.4 Custo de dependência das superfícies

| Import | Tempo | Módulos carregados |
|---|---:|---:|
| `router` | 1,0 ms | 39 |
| `router.integration` | 208,8 ms | **247** |
| `router.interfaces.api` | 204,9 ms | 255 |
| `router.interfaces.cli` | 196,6 ms | 256 |
| `router.app.planning` | 181,3 ms | 243 |
| `router.core.search` | 81,2 ms | 127 |

Medição única por processo, Python 3.15.2, Windows. Serve para ordem de
grandeza, não como benchmark.

**Leitura:** a superfície "curada" tem 28 nomes, mas custa o mesmo que a fachada
de 336 — porque `interfaces/integration.py` importa `router.app`, que importa o
núcleo inteiro. A fronteira é pequena em **nomes**, não em **dependências**.

### 2.5 Fatos que orientam a decisão

1. `app.legacy` e `app.tooling` têm **0 definições próprias** e existem apenas
   para reexportar.
2. `classification.py` importa o concreto `RouteAssessment`; o protocolo
   `RouteAssessmentView` **não tem consumidor**.
3. `GraphRepository` e `JsonGraphRepository` **não têm consumidor**; só as
   funções `load_graph`/`save_graph` são usadas.
4. `interfaces.integration` exclui `load_graph`/`save_graph` de `api` — a
   diferença entre as duas superfícies é intencional e documentada.
5. **Nenhum módulo de `core` importa fachada de estágio** (`search`,
   `evaluation`, `prediction`, `physical`). Importam a fachada de `physiology`
   (8 módulos) e, em um caso, a de `elevation` por import tardio
   (`osm/build.py:464`) — ambas são pacotes de contrato/infraestrutura, não
   estágios.
6. `core.physics.physical` **não importa** `physiology`; o limite já é real no
   grafo, mesmo com o caminho aninhado.
7. `interfaces/cli/__init__.py`: 1.176 linhas, 16 funções de topo, 8 comandos,
   parser, renderização e configuração de stdout no mesmo módulo.
8. `app/planning.py`: 767 linhas, com DTOs, projeção para `RoutePlanV1` e
   helpers geométricos junto da orquestração.
9. `docs/reference/api.md` documenta os 336 símbolos legados como "API pública",
   contradizendo o ADR-031 §6.
10. `docs/nomenclatura.md` cita `src/graph/model.py` e `src/domain/`, caminhos
    inexistentes.
11. Cobertura **não medida**; lint e type-check **não executados**; wheel **não
    verificado** (ferramentas ausentes no ambiente).
12. Todo o estado arquitetural está **não commitado** (3 commits no histórico;
    `src/router/` não rastreado; 85 deleções).

---

## 3. Alternativas consideradas

### 3.1 Alternativa A — Package by Layer

Organização por responsabilidade técnica (`services/`, `repositories/`,
`models/`, `controllers/`).

**Avaliação:** não corresponde ao sistema. Não há CRUD, controllers, ORM nem
persistência de negócio. A física, a busca e a avaliação seriam fatiadas por
tipo técnico, dispersando cada estágio em várias pastas e criando camadas que
só encaminham chamadas.

**Rejeitada.**

### 3.2 Alternativa B — Package by Feature

Features como `ingestion/`, `routing/`, `prediction/`, `evaluation/` com
`shared/`.

**Avaliação:** falha no teste decisivo — **as funcionalidades não são
independentes**. Planejar uma rota exige grafo, física, busca, predição e
avaliação na mesma ordem, sempre. `planning.py` já importa 17 módulos de cinco
estágios. Uma "feature de rota" ou importaria todas as outras (acoplamento pior
que o atual) ou duplicaria o modelo canônico (dois vocabulários incompatíveis).
`shared/` viraria o núcleo inteiro, apenas renomeado.

A ordem do pipeline é uma regra de domínio documentada (ADR-028 D7, ADR-029 D4),
não um detalhe de implementação. Organizar por feature esconde essa ordem.

**Rejeitada.**

### 3.3 Alternativa C — Híbrida (feature + camadas internas)

`features/routing/{domain,application,infrastructure}` etc.

**Avaliação:** introduziria três subcamadas onde existe uma responsabilidade só.
Em `core.search`, "domínio" e "aplicação" seriam o mesmo conjunto de algoritmos;
em `core.physics`, idem. O custo de criar, documentar e manter a simetria não
tem contrapartida em localidade de mudança, porque o eixo real de mudança é o
estágio científico.

**Rejeitada.**

### 3.4 Alternativa D — Package by Component / estágios de pipeline

Cada pacote é um **componente coeso com interface pública explícita**; as
dependências são unidirecionais e verificadas; o contrato é L0; os adaptadores
ficam nas bordas.

**Avaliação:** é o que a estrutura atual já é, na prática. O que falta não é
outra árvore, e sim: fronteira pública inequívoca, remoção de andaimes,
coesão dos módulos grandes e verificação automática das regras.

**Recomendada.**

### 3.5 Matriz de decisão

| Critério | A — Layer | B — Feature | C — Híbrida | D — Componente/pipeline |
|---|---|---|---|---|
| Coesão | Técnica, não funcional | Alta **se** houver features independentes | Média | Alta por estágio |
| Acoplamento | Baixo entre tipos, alto entre camadas | Alto entre features | Médio-alto | Direcional e explícito |
| Localidade da mudança | Fraca | Boa em teoria, ruim na prática | Média | Média — o eixo é o estágio, que é o eixo real |
| Clareza dos limites | Média | Enganosa | Média | Alta |
| Testabilidade | Alta para unidades técnicas | Média | Média | Alta (funções puras + contratos) |
| Onboarding | Médio | Rápido no início, confuso depois | Médio | Rápido com guia |
| Escala organizacional | Fraca | Boa com times por feature | Média | Média (adequada a um motor) |
| Adequação ao domínio | Fraca | Fraca | Média | **Forte** |
| Custo de migração | Alto | Alto | Alto | **Baixo (incremental)** |
| Risco de regressão | Alto | Alto | Alto | **Baixo** |

As avaliações são qualitativas e justificadas; não foram atribuídas notas
numéricas porque não há medição que as sustente.

---

## 4. Decisão

**Adotar a Alternativa D — organização por componente/estágio de pipeline —
mantendo a árvore atual e corrigindo suas arestas.**

Em termos concretos:

- **Não** migrar para Package by Feature nem para camadas técnicas.
- **Não** executar os achatamentos de diretório do ADR-032 neste ciclo.
- **Sim** tornar a fronteira pública inequívoca, remover andaimes sem
  consumidor, separar responsabilidades dentro dos módulos grandes e
  automatizar as regras que hoje dependem de disciplina.

### 4.1 Invariantes (normativos)

1. `contracts` é **L0**: apenas stdlib, nenhum import de `router.*`.
2. `offline` é **L0**: apenas stdlib.
3. A dependência entre estágios é **unidirecional**:
   `data → physics → search`, `data/physics → prediction`,
   `search + prediction → evaluation`, `evaluation → app → interfaces`.
4. `core.*` **nunca** importa `app` nem `interfaces`.
5. `interfaces` **nunca** importa `core.*` — apenas `app`, `contracts`,
   `offline` e a raiz.
6. `core.search` e `core.prediction` permanecem **independentes entre si**;
   apenas `evaluation` combina os dois.
7. Cada estágio expõe **uma** fachada pública; o resto é detalhe interno.
8. `core` e `app` importam o **módulo definidor**, não a fachada de outro
   estágio. Exceções admitidas: `physiology` e `elevation`, que são pacotes de
   contrato/infraestrutura (o segundo preferencialmente por import tardio).
9. `app.legacy` é **artefato de compatibilidade congelado**: apenas
   reexportações, nenhuma definição, nenhum nome novo sem ADR.
10. `router.integration` é a **única** superfície pública nível B.
    `router.interfaces.integration` é alias interno documentado.
    `router.interfaces.api` é **legado congelado** (336 nomes), não a API
    recomendada.
11. Nenhuma abstração entra sem **consumidor real e testado**. Porta sem
    consumidor é removida ou implementada — nunca mantida "por simetria".

### 4.2 O que "componente" significa aqui

Um componente é definido pela **responsabilidade científica**, não pelo
diretório:

| Componente | Interface pública | Esconde |
|---|---|---|
| Dados/grafo | `core.data.graph`, `osm`, `elevation`, `snapping`, `geo` | formato PBF, índices espaciais, split de arcos |
| Física | `core.physics.physical` | forças, energia, integração, perfis de arco |
| Contratos fisiológicos | `core.physics.physiology` | escalas, zonas, metabolismo |
| Busca | `core.search` | A*, dominância, fronteira, deduplicação |
| Predição | `core.prediction` | features, treino, artefato, intensidade |
| Avaliação | `core.evaluation` | segurança, classificação, diagnóstico |
| Aplicação | `app` | ordem do pipeline, fusão multi-perna |
| Adaptadores | `interfaces` | argparse, formatação, fachada |

---

## 5. Justificativa

1. **O grafo já é um DAG limpo e testado.** 0 ciclos e 0 violações não são um
   ponto de partida trivial; substituir a estrutura descartaria essa propriedade.
2. **O domínio é um pipeline ordenado.** O eixo de mudança é "mudou a física",
   "mudou a busca", "mudou o contrato" — exatamente os pacotes existentes.
3. **Há um modelo canônico compartilhado** (`graph.model`, fan-in 27). Qualquer
   organização por feature teria de duplicá-lo ou criar dependências cruzadas.
4. **Os problemas reais são locais.** Nenhum dos 11 problemas priorizados na
   Fase 2 se resolve movendo diretórios: todos se resolvem em contrato, coesão
   de módulo, remoção de andaime ou teste.
5. **O custo de migração é o menor** e o risco de regressão também — requisito
   explícito da auditoria.
6. **A fronteira já está quase certa.** `contracts` é stdlib-only, `interfaces`
   não toca `core`, e a direção entre estágios é respeitada. O que falta é
   **tornar isso inequívoco e verificável**, não reconstruí-lo.
7. **A árvore do ADR-032 tem custo desproporcional ao ganho.** Os achatamentos
   não alteram nenhuma aresta; movem dezenas de arquivos e exigem atualizar
   cerca de 95 referências textuais, shims e testes, sem melhorar acoplamento.

---

## 6. Consequências

### 6.1 Positivas

- A arquitetura conceitual (estágios) coincide com a implementada (pacotes) —
  requisito de Simon Brown.
- O contrato permanece isolado e versionável; a superfície pública passa a ter
  um caminho só.
- Refatorações passam a ser pequenas, reversíveis e verificáveis por teste.
- O conhecimento do domínio é preservado: quem entende o pipeline sabe onde
  mexer.
- A remoção de andaimes reduz o "custo de leitura" de `app` e `interfaces`.

### 6.2 Trade-offs (custos aceitos)

- **Localidade de mudança continua média.** Uma mudança de ponta a ponta
  (novo critério de segurança) atravessa `core.evaluation`, `app` e `contracts`.
  Isso é inerente ao pipeline e será mitigado por testes de contrato e pelo guia
  da Fase 4 — não por outra árvore.
- **A árvore fica com nomes redundantes** (`core/physics/physical`,
  `core/prediction/intelligence`). Aceito temporariamente: o custo de mover é
  maior que o de conviver, e o caminho não induz dependência errada.
- **`app` continua abrigando `legacy`.** Aceito enquanto `interfaces/api.py`
  existir; o módulo é congelado, testado como "só reexports" e não recebe nomes
  novos.
- **Não há ganho de deploy independente.** O motor continua sendo um artefato
  único. Se isso mudar, a decisão precisa ser revista (ver §13).

### 6.3 O que muda na prática

- `docs/reference/api.md` deixa de apresentar os 336 nomes como API pública e
  passa a marcá-los como compatibilidade congelada.
- `router.integration` passa a ser o caminho recomendado, com imports tardios
  para não carregar o núcleo inteiro.
- As portas sem consumidor são decididas (implementar ou remover), não mantidas
  como decoração.
- A projeção `RoutePlan → RoutePlanV1` sai da orquestração.
- A CLI é dividida por responsabilidade, começando pelas funções puras de
  renderização.
- O teste de arquitetura passa a cobrir ciclos e imports de fachada.

---

## 7. Relação com o ADR-032

O ADR-032 permanece como **estrutura-alvo condicional**. Cada item recebe uma
decisão explícita:

| Item do ADR-032 | Decisão neste ADR | Condição |
|---|---|---|
| §1 achatamento `physical/` → `core/physics/` (D1) | **Adiado** | Só junto de mudança real no pacote, ou se o caminho causar erro de importação recorrente |
| §1 achatamento `intelligence/` → `core/prediction/` (D1) | **Adiado** | Idem |
| §1 `physiology` como irmão de `physics` (D3) | **Adiado** | O limite já existe no grafo (0 imports de `physical`); mover é cosmético |
| §1 `ml/` → `prediction/ml.py` (D2) | **Adiado** | Só quando a fronteira de inferência mudar |
| §1 fonte única `router/integration.py` (D4) | **Aceito em princípio** | Executado como alias documentado + imports tardios, sem mover implementação |
| §1 `interfaces/api.py` congelada (D5) | **Aceito** | Mantida com 336 nomes; rótulo de legado; remoção só após inventário |
| §3 movimentos de módulos | **Não executar agora** | Ver §9, etapa E8 |
| §6 identidade da distribuição (D6) | **Adiado** | Decidir antes de publicação externa |
| §6 dados geográficos fora do artefato (D7) | **Aceito** | Já observado: `data/` e `.cache/` estão fora de `src/` |
| §7 pré-condições | **Aceitas** | Linha de base, inventário, build e testes de importação |
| §8 fases | **Substituídas** pelo plano de §9 | Ordem por valor/risco, não por diretório |

**Justificativa do adiamento:** os achatamentos não alteram o grafo de
dependências, não reduzem acoplamento e não melhoram testabilidade. Seu único
ganho é a extensão do caminho. O ADR-032 já registra que 80–90% da árvore deve
ser mantida; este ADR explicita que os 10–20% restantes não são prioridade.

---

## 8. Regras operacionais decorrentes

Estas regras valem a partir da aprovação deste ADR e serão detalhadas na Fase 4.

### 8.1 Onde colocar código novo

| Tipo de código | Destino |
|---|---|
| Estrutura de dados do contrato `v1` | `contracts/` (nunca importa domínio) |
| Leitura/escrita de formato, grafo, geometria | `core.data` |
| Força, energia, integração, modelo de ciclista/bicicleta | `core.physics.physical` |
| Escala, zona, metabolismo, previsão fisiológica | `core.physics.physiology` |
| Algoritmo de busca, dominância, perfil, circulação | `core.search` |
| Feature, treino, artefato, intensidade | `core.prediction` |
| Regra de elegibilidade, segurança, classificação | `core.evaluation` |
| Ordem do pipeline, fusão de pernas, projeção de contrato | `app` |
| argparse, formatação, fachada pública | `interfaces` |

### 8.2 Compartilhamento

- Compartilhar **modelo canônico e contratos**, não utilitários.
- **Não criar** `shared/`, `common/` ou `utils/`.
- Um elemento só é promovido a compartilhado quando: (a) tem responsabilidade
  própria, (b) é usado por mais de um estágio, (c) tem interface estável e
  (d) não depende do estágio consumidor.

### 8.3 Fachadas e imports

- Importar o **módulo definidor**. Fachadas existem para o consumidor externo e
  para `app.legacy`.
- `core` e `app` não importam fachada de **estágio** irmão.
- `app.legacy` é a única exceção, porque seu propósito é agregar.
- Imports tardios são permitidos quando evitam carregar rede/IO no import do
  módulo; devem ser comentados com o motivo.

### 8.4 Nomenclatura

- Um conceito por módulo; `snake_case`; sem nome de módulo repetido entre
  **estágios** diferentes.
- Sufixos com significado: `_ports`, `_validation`, `_model` — não
  `_utils`.
- Nenhum módulo novo entra em `__all__` de outra camada sem justificativa.

### 8.5 Testes

- Toda regra de fronteira deve ter teste executável.
- Todo contrato público deve ter teste de serialização/schema.
- Toda porta deve ter teste com o consumidor real (não apenas com um dublê).

---

## 9. Plano incremental (Fase 5)

**Nada abaixo foi executado.** A ordem é por valor/risco, não por diretório.
Cada etapa é reversível e exige suíte verde antes e depois.

### E0 — Preservar (pré-condição)

- **Objetivo:** criar linha de base versionada.
- **Ação:** commit/branch com o estado atual (inclui `src/router/`, ADRs 030–032,
  testes). Sem isso, não há rollback.
- **Verificação:** `git status` limpo; suíte verde no commit.
- **Risco:** nenhum. **Decisão do responsável pelo repositório.**

### E1 — Fronteira pública e documentação

- **Objetivo:** uma superfície recomendada, uma legada, sem ambiguidade.
- **Arquivos:** `docs/reference/api.md`, `docs/reference/README.md`,
  `README.md`, `docs/nomenclatura.md`, `src/router/offline.py` (docstring).
- **Ação:** rotular `api` como compatibilidade congelada; declarar
  `router.integration` como nível B; corrigir caminhos obsoletos; documentar a
  diferença `load_graph`/`save_graph`.
- **Verificação:** referência regenerada; nenhum caminho inexistente.
- **Risco:** nenhum (documentação).

### E2 — Portas sem consumidor

- **Objetivo:** eliminar abstração decorativa.
- **Arquivos:** `core/evaluation/ports.py`, `core/evaluation/classification.py`,
  `app/ports.py`, `app/__init__.py`.
- **Ação:** escolher **uma** via: (a) aplicar `RouteAssessmentView` na assinatura
  de `classify_routes` (tipagem estrutural; suíte deve continuar verde); ou
  (b) removê-la com prazo de depreciação. Para `GraphRepository` /
  `JsonGraphRepository`: manter `load_graph`/`save_graph` e remover as classes
  sem uso, ou passar a usá-las.
- **Verificação:** `pytest` completo; teste que garanta que a assinatura não
  regrediu para o concreto (se (a)).
- **Risco:** baixo em (a); médio em (b) por causa de `app.__all__`.

### E3 — Projeção de contrato fora da orquestração

- **Objetivo:** separar `RoutePlan → RoutePlanV1` do pipeline.
- **Arquivos:** `app/planning.py`, novo `app/plan_view.py`.
- **Ação:** mover `_bbox_to_v1`, `_planned_to_v1`, `to_contract`; manter
  `RoutePlan.to_contract` delegando.
- **Verificação:** `tests/test_route_contract.py` e `test_multi_leg.py` verdes;
  saída byte a byte idêntica para um plano de fumaça.
- **Risco:** baixo.

### E4 — Verificação arquitetural

- **Objetivo:** transformar invariantes em testes.
- **Arquivos:** `tests/test_architecture.py`.
- **Ação:** adicionar (1) detecção de ciclos por SCC; (2) proibição de importar
  fachada de estágio irmão; (3) `app.legacy` sem `def`/`class`; (4) paridade
  `pyproject.version == router.__version__`.
- **Verificação:** os testes passam no estado atual (linha de base limpa).
- **Risco:** baixo; se algum falhar, é achado novo — investigar antes de mover.

### E5 — Custo de dependência da superfície curada

- **Objetivo:** fazer `router.integration` custar o que promete.
- **Arquivos:** `interfaces/integration.py`, `interfaces/api.py`.
- **Ação:** manter no topo apenas `contracts`; resolver `plan_routes`, modelos e
  persistência dentro das funções.
- **Verificação:** medir import de novo (meta: redução substancial de módulos e
  tempo); `tests/test_integration.py` verde; todos os módulos importáveis.
- **Risco:** baixo-médio (import tardio pode mascarar dependência); mitigado por
  teste de importação total.

### E6 — Coesão da CLI e dos casos de uso

- **Objetivo:** reduzir responsabilidades por módulo.
- **Arquivos:** `interfaces/cli/__init__.py` → `cli/render.py`,
  `cli/parser.py`, `cli/commands/`; `app/tooling.py` → casos de uso nomeados.
- **Ação:** começar pelo renderizador (funções puras sobre DTOs). Só dividir
  comandos se houver mudança recorrente.
- **Verificação:** testes da CLI verdes; `python -m router --help` idêntico.
- **Risco:** baixo por etapa; médio se feito de uma vez. **Não fazer de uma
  vez.**

### E7 — Empacotamento

- **Objetivo:** artefato verificável.
- **Ação:** construir wheel, inspecionar conteúdo, `py.typed` por pacote,
  `LICENSE`, decidir entry points duplicados.
- **Verificação:** wheel sem `data/`, `.cache/`, `tests/`; instalação limpa;
  scripts funcionam.
- **Risco:** baixo.

### E8 — Achatamentos do ADR-032 (condicional)

- **Objetivo:** reduzir extensão de caminho.
- **Condição de entrada:** E1–E7 concluídas, inventário de consumidores feito,
  e uma mudança real prevista naqueles pacotes.
- **Ação:** rewrite mecânico por AST + shims + atualização do teste de
  arquitetura.
- **Verificação:** árvore-alvo, suíte verde, zero imports antigos.
- **Risco:** médio-alto (churn). **Sem essa condição, não executar.**

---

## 10. Critérios de aceite

1. `tests/test_architecture.py` cobre ciclos, fachadas de estágio e
   `legacy` sem definições; todos passam.
2. `router.integration` é o único caminho recomendado; `api` está rotulada como
   legado congelado na documentação e mantém 336 símbolos.
3. Nenhuma abstração exportada em `app.__all__` está sem consumidor.
4. `app/planning.py` não contém projeção de contrato.
5. `interfaces/cli/__init__.py` não concentra parser, comandos e renderização.
6. `router.integration` carrega menos módulos que `router.interfaces.api`,
   medido e registrado.
7. `router.__version__` é única e igual a `pyproject.version`.
8. Suíte completa verde; todos os módulos importáveis; contrato `v1` e
   comportamento de rota idênticos à linha de base.

---

## 11. Riscos e não-objetivos

### Riscos

- **Fachada eterna:** `app.legacy` e `api` podem permanecer para sempre.
  Mitigação: congelamento testado + inventário de consumidores antes de
  qualquer remoção.
- **Fachada que volta a crescer:** `core.search.__init__` pode voltar a ser
  depósito. Mitigação: teste de import de fachada.
- **Import tardio mascarar erro:** mitigação: teste que importa os 104 módulos.
- **Refatoração da CLI virar reescrita:** mitigação: uma extração por vez,
  começando pelo que é puro.
- **Perda de trabalho local:** o estado não está commitado. Mitigação: E0.

### Não-objetivos

- Não alterar física, busca, ML, contrato `v1`, validações ou comportamento de
  rota.
- Não remover `interfaces/api.py`.
- Não mover `core/physics/physical`, `core/prediction/intelligence` ou
  `physiology` neste ciclo.
- Não introduzir DDD tático, repositórios genéricos, eventos, plugins ou
  camadas de serviço.
- Não buscar cobertura de 100% nem uniformidade estética.
- Não publicar no PyPI neste ciclo.

---

## 12. Pendências de decisão

| # | Pendência | Impacto |
|---|---|---|
| 1 | Inventário de consumidores de `router.interfaces.api` | Define se os 336 podem ser depreciados |
| 2 | Uso real de `router.integration` pelo porte TypeScript/Toporide | Valida se 28 nomes bastam |
| 3 | Frequência de mudança por módulo (histórico insuficiente: 3 commits) | Priorização de E6 por dados, não por julgamento |
| 4 | Intenção de publicar no PyPI e disponibilidade de `router` | Urgência da identidade do pacote |
| 5 | Latência de import é requisito? | Prioridade de E5 |
| 6 | Versão mínima real de Python (declarado `>=3.10`, testado 3.15) | Matriz de suporte |
| 7 | Autorização para `ruff`/`mypy`/`coverage` em `dev` | Habilita as verificações que hoje faltam |
| 8 | Aceitação de churn de diretórios (E8) | Escopo do ADR-032 |

---

## 13. Condições de revisão

Reavaliar esta decisão se:

1. surgirem **produtos independentes** com cadência de release própria;
2. aparecer uma camada web/HTTP com múltiplos contextos de negócio;
3. o núcleo passar a ser mantido por **times paralelos** com ownership distinto;
4. houver necessidade real de distribuir `core` como pacote separado;
5. o número de consumidores externos justificar um contrato maior que 28 nomes;
6. os achatamentos do ADR-032 passarem a resolver um problema **medido** (não
   estético).

---

## 14. Registro de execução

| Data | Evento |
|---|---|
| 2026-11-11 | Fases 1–2 concluídas: 843 testes verdes, 105 módulos, 350 arestas, 0 ciclos, 0 violações, 0 imports dinâmicos; custo de import medido |
| 2026-11-11 | Fase 3 concluída com a decisão deste ADR; ADR-032 reposicionado como estrutura-alvo condicional |
| — | Fases 4–6 **não iniciadas**; nenhum arquivo de produção alterado |

**Nota de honestidade:** cobertura de testes não foi medida; lint, type-check e
build de wheel não foram executados por ausência de ferramentas no ambiente.
Este ADR não afirma melhoria verificada — afirma uma decisão fundamentada em
medições de estrutura e dependência, com critérios de verificação definidos.
