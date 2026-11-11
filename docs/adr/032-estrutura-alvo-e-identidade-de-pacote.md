# ADR-032 — Estrutura-alvo simplificada e identidade de pacote

- **Status:** **proposta** (não executada). Nenhum código foi movido.
- **Data:** 2026-11-11
- **Relação:** refina o ADR-031 (organização de módulos), que permanece
  **aceito e implementado**. Mantém integralmente o ADR-030 (contrato v1,
  superfície curada, execução offline). Não altera física, busca, ML nem
  comportamento de rota.
- **Base:** árvore produzida pelo ADR-031 — 104 módulos `router.*`, **843**
  testes verdes, `api.__all__` com **336** símbolos e `integration.__all__`
  com **28**.

---

## 0. Contexto

O ADR-031 tirou o motor da raiz de `src/` e o colocou em um namespace
instalável, `router`, com camadas explícitas. A revisão dessa árvore concluiu
que a organização conceitual está boa e que **80–90% dela deve ser mantida**.
O ganho restante não vem de criar novas pastas, e sim de:

1. **reduzir níveis redundantes** — `core/physics/physical/` e
   `core/prediction/intelligence/` repetem o nome do conceito no caminho;
2. **aproximar `physiology` da física sem aninhá-la** — são contratos
   distintos da dinâmica mecânica e não dependem dela;
3. **estabilizar uma única superfície pública** — hoje `router.integration` e
   `interfaces.integration` convivem;
4. **isolar a compatibilidade legada** — `interfaces/api.py` (336 símbolos)
   existe para não quebrar consumidores e precisa de inventário antes de
   qualquer remoção;
5. **separar execução, cache e dados** do artefato distribuído;
6. **rever a identidade do pacote** — distribuição `roteamento-com-ml`,
   versão `0.18.0`, importável como `router`.

A revisão também foi explícita: **não** recomenda uma refatoração estrutural
ampla antes de medir dependências e executar testes de importação. Este ADR
registra a **estrutura-alvo** e as condições para executá-la. Ele não é uma
ordem de movimentação imediata.

---

## 1. Decisão

Manter a separação de alto nível:

| Pacote | Papel |
| --- | --- |
| `router/contracts/` | contrato público versionado (dados) |
| `router/app/` | orquestração e API estável do motor |
| `router/core/` | algoritmos e modelos internos |
| `router/interfaces/` | adaptadores (CLI e compatibilidade) |

Simplificar os dois aninhamentos redundantes:

- `core/physics/physical/*` → `core/physics/*`;
- `core/prediction/intelligence/*` → `core/prediction/*`.

Mover a fisiologia para fora da física:

- `core/physics/physiology/` → `core/physiology/`.

Manter `ml` como uma fronteira própria, mas sem um nível extra de pacote:
`core/prediction/ml.py` (decisão D2). O nome `ml` continua existindo; o que
desaparece é o diretório `ml/` com um único `predictor.py`. Se a fronteira
crescer, volta a ser pacote.

Consolidar a superfície pública em **um** módulo: `router/integration.py` passa
a ser a fonte única da superfície curada; `interfaces/integration.py` vira
alias temporário. A fachada ampla `interfaces/api.py` permanece **congelada**
até que o inventário de consumidores permita removê-la.

---

## 2. Árvore-alvo

```
cycling-router-engine/
├── pyproject.toml
├── README.md
├── src/
│   └── router/
│       ├── __init__.py              # só __version__
│       ├── __main__.py              # python -m router
│       ├── integration.py           # superfície pública curada (fonte única)
│       ├── offline.py               # guard de rede (sem dependências)
│       ├── contracts/
│       │   ├── __init__.py
│       │   ├── version.py
│       │   ├── geo.py
│       │   ├── route.py
│       │   ├── serialize.py
│       │   ├── errors.py
│       │   └── _validation.py
│       ├── app/
│       │   ├── __init__.py          # plan_routes, plan_offline_routes, PROFILE_IDS
│       │   ├── planning.py
│       │   ├── multi_leg.py
│       │   └── ports.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── data/
│       │   │   ├── __init__.py
│       │   │   ├── graph/
│       │   │   ├── osm/
│       │   │   ├── elevation/
│       │   │   ├── geo.py
│       │   │   ├── envelope.py
│       │   │   └── snapping.py
│       │   ├── physics/             # arc_profile, bicycle, cyclist, effort,
│       │   │   │                    # energy, energy_model, environment,
│       │   │   │                    # forces, models, speed, traversal
│       │   │   └── __init__.py
│       │   ├── physiology/          # scales, zones, metabolic, prediction
│       │   ├── search/
│       │   ├── prediction/          # features, observations, targets, models,
│       │   │                        # training, model_eval, model_store,
│       │   │                        # predictor, intensity, ml.py
│       │   └── evaluation/
│       ├── interfaces/
│       │   ├── __init__.py
│       │   ├── cli/
│       │   │   └── __init__.py
│       │   └── api.py               # compatibilidade congelada (temporária)
├── tests/
├── docs/
└── scripts/
```

Alinhamento da árvore:

- `physics/` e `physiology/` são **irmãos** em `core/`. A fisiologia define
  contratos (Borg, zonas, metabólico, `PhysiologicalPrediction`) e **não
  importa** a dinâmica mecânica; a física não importa fisiologia. Quem combina
  as duas é `core.prediction` e, depois, `core.evaluation`.
- `prediction/` contém as features, o treino/artefato local, a intensidade e a
  fronteira de inferência (`ml.py`). A classificação predição × preferência
  continua em `core.evaluation` (ADR-031, D2).
- `app/legacy.py` e `app/tooling.py`, criados no ADR-031, **não** fazem parte
  da árvore conceitual: são andaimes privados (montagem da fachada ampla e
  reexports para a CLI). Podem permanecer enquanto `interfaces/api.py` existir
  e ser eliminados quando a compatibilidade e a CLI forem simplificadas.
- `interfaces/integration.py` não aparece na árvore final; sobrevive apenas
  como alias temporário (ver §5).
- `src/__main__.py` e `py-modules = ["__main__"]` saem do empacotamento;
  `router/__main__.py` já cobre `python -m router`.

---

## 3. Movimentos e impacto

| Hoje | Alvo | Observação |
| --- | --- | --- |
| `router.core.physics.physical.bicycle` | `router.core.physics.bicycle` | promoção de todos os módulos |
| `router.core.physics.physical` (pacote) | `router.core.physics` (pacote) | `__init__` reexporta os nomes de `physical` |
| `router.core.physics.physiology` | `router.core.physiology` | irmão, sem dependência de `physics` |
| `router.core.prediction.intelligence.features` | `router.core.prediction.features` | promoção de todos os módulos |
| `router.core.prediction.intelligence` | `router.core.prediction` | `__init__` reexporta os nomes de `intelligence` |
| `router.core.prediction.ml.predictor` | `router.core.prediction.ml` | `ml` vira módulo único |
| `router.interfaces.integration` | `router.integration` | fonte única da superfície curada |
| `router.interfaces.api` | `router.interfaces.api` | congelada; remoção só com inventário |
| `src/__main__.py` | `router/__main__.py` | remove `py-modules` do wheel |

**Colisões de nome (medidas antes de mover):**

- `intelligence/predictor.py` e `ml/predictor.py` têm o mesmo nome de módulo,
  mas APIs diferentes (`LocalModelPredictor`/`load_predictor` vs.
  `EffortPredictor`/`UnavailablePredictor`/`predict_effort`). Por isso `ml`
  **não** é achatado no mesmo diretório: vira `prediction/ml.py`.
- `_validation.py` existe em `physical`, `physiology` e `intelligence`. Após a
  separação, cada pacote mantém o seu (`physics/_validation.py`,
  `physiology/_validation.py`, `prediction/_validation.py`), sem conflito.
- `models.py` existe em `physical` e `intelligence`; ficam em pacotes
  distintos (`physics/models.py` e `prediction/models.py`).

**Referências textuais atuais** (src + tests + scripts; base para o rewrite
mecânico, não é o grafo completo):

| Prefixo | Ocorrências |
| --- | ---: |
| `core.physics.physical` | 46 |
| `core.physics.physiology` | 14 |
| `core.prediction.intelligence` | 17 |
| `core.prediction.ml` | 9 |
| `interfaces.integration` | 3 |
| `router.integration` | 5 |
| `interfaces.api` | 1 |

---

## 4. Matriz de dependências após a mudança

```
contracts        -> stdlib
offline          -> (nada)
core.data        -> contracts
core.physics     -> core.data, contracts
core.physiology  -> (nada; apenas stdlib)
core.search      -> core.data, core.physics, contracts
core.prediction  -> core.data, core.physics, core.physiology, contracts
core.evaluation  -> core.data, core.physics, core.physiology,
                    core.search, core.prediction, contracts
app              -> todas as camadas core + contracts + offline
interfaces       -> app, contracts, offline
```

Invariantes preservados:

1. `core.*` nunca importa `app` nem `interfaces`.
2. `interfaces` nunca importa `core.*`.
3. `contracts` não importa domínio.
4. `core.physiology` não importa `core.physics`; ambos são folhas do domínio.
5. `core.search` e `core.prediction` continuam independentes entre si.

`tests/test_architecture.py` passa a usar os caminhos novos. A regra
`contracts` stdlib-only, `interfaces` sem núcleo e ausência de shims legados
continua valendo.

---

## 5. Compatibilidade e reexportações temporárias

A mudança é **mecânica e reversível**. Nenhum comportamento muda.

1. **Antes de mover**, gerar o grafo de imports por AST (src, tests, scripts),
   congelar a linha de base (`843` testes, `api.__all__` 336,
   `integration.__all__` 28) e inventariar consumidores externos.
2. **Shims de caminho antigo**, por uma versão menor:
   - `core/physics/physical/` permanece como pacote de reexportação fina
     (`__init__.py` + um módulo por submódulo, sem lógica) enquanto houver
     importadores;
   - `core/prediction/intelligence/` idem;
   - `interfaces/integration.py` passa a `from router.integration import *`;
   - `interfaces/api.py` **não** muda de caminho nem de conteúdo.
3. **Aviso de depreciação** no `__init__` dos shims (`DeprecationWarning`),
   com prazo declarado no `CHANGELOG.md`.
4. **Remoção** dos shims somente depois de:
   - todos os imports do repositório apontarem para os caminhos novos;
   - o consumidor externo (Toporide) confirmar a migração;
   - a suíte e os testes de importação passarem sem os shims.
5. **`interfaces/api.py`**: manter congelada. Se o inventário mostrar que não
   há consumidores, remover em um ciclo próprio; se houver, manter o caminho e
   documentar a intenção de remoção. Não substituir por uma fachada nova.

**Não** usar `sys.modules` mágico nem `__getattr__` dinâmico para simular
módulos: shims explícitos são auditáveis e o teste de arquitetura continua
simples.

---

## 6. Empacotamento, identidade e versão

### 6.1 Identidade

Hoje:

- distribuição: `roteamento-com-ml`;
- versão: `0.18.0` em `pyproject.toml`;
- pacote importável: `router`;
- `router.__version__ = "0.18.0"`;
- console scripts: `roteamento` e `router` → `router.interfaces.cli:main`.

Decisão proposta (D6): padronizar a identidade **antes do primeiro release
externo**. Duas opções aceitáveis:

1. manter `roteamento-com-ml` como nome de distribuição e documentar que o
   pacote importável é `router`; ou
2. renomear a distribuição para um nome de produto (`cycling-router-engine`,
   `toporide-router` etc.), mantendo `router` como import.

O que **não** deve continuar é a divergência entre o nome do diretório-alvo
(`cycling-router-engine`), o nome da distribuição e o nome importável sem
documentação. A escolha é de produto, não de código; por isso fica como
decisão aberta, com a recomendação de resolvê-la antes de publicar.

### 6.2 Versão única

- `pyproject.toml` deve declarar a versão a partir de `router.__version__`
  (`[tool.setuptools.dynamic] version = {attr = "router.__version__"}`) ou,
  no mínimo, um teste deve garantir a igualdade.
- `api.__version__` continua reexportando a versão do pacote.
- Adicionar `tests/test_packaging.py` com:
  - `router.__version__ == pyproject.version`;
  - `packages.find.include == ["router*"]`;
  - `py.typed` presente em `src/router/`.

### 6.3 Conteúdo do artefato

O que entra no **wheel**:

- `router/**` (código e `py.typed`);
- metadados (`README.md`, licença, `pyproject.toml`).

O que **não** entra:

- `.git/`, `.cache/`, `.pytest_cache/`, `.venv/`;
- `data/` (OSM/PBF, tiles, fixtures geográficas grandes);
- `tests/`, `docs/`, `scripts/` (podem ir no sdist, não no wheel);
- caches de execução, como `.cache/roteamento/elevation.sqlite`.

Ações:

- remover `src/__main__.py` e `py-modules = ["__main__"]`;
- manter `packages.find = { where = ["src"], include = ["router*"] }`;
- `py.typed` em `src/router/py.typed` via `package-data`;
- `MANIFEST.in` explícito para o sdist, se necessário;
- validar o wheel em ambiente com `setuptools`/`build` (hoje ausente) e
  inspecionar o conteúdo (`data/`, `.cache/` e `.git/` ausentes).

### 6.4 Dados geográficos e cache

- Dados geográficos **não** são código: devem ser um artefato separado
  (pacote de dados por região, com versão e checksum) ou um passo explícito de
  preparação. O aplicativo aponta para o caminho; o wheel não os embute.
- O cache de elevação é **estado de execução** do usuário, não dado
  distribuído. O caminho deve ser configurável e o padrão deve continuar fora
  do pacote.
- Fixtures pequenas de teste podem ficar em `tests/fixtures/`; dados reais
  grandes não entram no sdist/wheel.

---

## 7. Pré-condições para executar

Nenhuma movimentação antes de:

1. **Medir dependências**: grafo de imports por AST em `src/`, `tests/` e
   `scripts/`, incluindo imports dinâmicos e `TYPE_CHECKING`.
2. **Inventariar consumidores**: repositório, Toporide, documentação e
   scripts; verificar quem importa `router.interfaces.api`,
   `router.interfaces.integration`, `router.core.physics.physical` e
   `router.core.prediction.intelligence`.
3. **Congelar a linha de base**: 843 testes, `api.__all__` 336,
   `integration.__all__` 28, todos os 104 módulos `router.*` importáveis.
4. **Testes de importação** em interpretador limpo: importar cada módulo,
   `python -m router --help`, console scripts e um plano de fumaça.
5. **Ambiente de build**: instalar `setuptools`/`build` e validar wheel antes
   de mexer em identidade/versão.
6. **Decidir D6** (nome da distribuição) antes de qualquer publicação; a
   mudança de nome é barata agora e cara depois.

---

## 8. Fases (somente após as pré-condições)

**Fase 0 — medição e travas.**
- Grafo de imports, inventário de consumidores, linha de base.
- `tests/test_packaging.py` (versão única, include, `py.typed`).
- Nenhum movimento de código.

**Fase 1 — física e fisiologia.**
- Promover `physical/*` para `core/physics/*`.
- Mover `physiology/` para `core/physiology/`.
- Atualizar imports e `tests/test_architecture.py`.
- Shims `physical/` e `physiology/` (caminho antigo) por uma versão menor.
- Suíte verde.

**Fase 2 — predição.**
- Promover `intelligence/*` para `core/prediction/*`; `ml/` → `ml.py`.
- Shims `intelligence/` e `ml/` temporários.
- Atualizar imports e testes.
- Suíte verde.

**Fase 3 — superfície pública.**
- Mover a implementação da superfície curada para `router/integration.py`.
- `interfaces/integration.py` vira alias.
- Inventário de `interfaces/api.py`; manter congelada ou remover em ciclo
  próprio.
- Testar `router.integration` e o caminho legado.

**Fase 4 — empacotamento.**
- Remover `src/__main__.py`/`py-modules`.
- Versão única, `MANIFEST.in`, validação de wheel.
- Confirmar ausência de `data/`, `.cache/`, `.git/` e caches no artefato.
- Definir a estratégia de dados geográficos (artefato separado).

**Fase 5 — limpeza.**
- Remover shims após o prazo de depreciação.
- Atualizar docs, ADR-031 (nota de revisão) e `CHANGELOG.md`.
- Congelar a árvore final e a contagem da superfície pública.

Critério de saída: a árvore-alvo de §2, suíte verde, nenhum import antigo,
wheel limpo e comportamento idêntico ao baseline.

---

## 9. Decisões em aberto

- **D1 — achatamento de `physical`/`intelligence`.** Proposta: sim, com shims
  temporários. Alternativa: manter o aninhamento e apenas documentar. A
  primeira reduz o caminho e não muda a semântica.
- **D2 — layout de `ml`.** Proposta: `prediction/ml.py` (módulo único).
  Alternativa: manter `prediction/ml/` como pacote. Reavaliar se a fronteira
  ganhar mais de um módulo.
- **D3 — `physiology` irmão de `physics`.** Proposta: sim. A fisiologia não
  depende da física e é consumida por predição/avaliação.
- **D4 — fonte única da superfície curada.** Proposta: `router/integration.py`;
  `interfaces/integration.py` é alias temporário.
- **D5 — destino de `interfaces/api.py`.** Manter congelada até o inventário;
  remover se não houver consumidores; nunca ampliar.
- **D6 — identidade da distribuição.** Manter `roteamento-com-ml` ou renomear
  para um nome de produto. Decidir antes do primeiro release externo.
- **D7 — distribuição de dados geográficos.** Artefato separado, versionado e
  com checksum; não embutir no wheel.

---

## 10. Riscos e não-objetivos

- **Não** alterar física, busca, ML, contrato v1 ou comportamento de rota.
- **Não** fazer uma refatoração ampla antes de medir dependências e rodar os
  testes de importação.
- **Risco de churn:** mover módulos toca dezenas de imports. Mitigação:
  rewrite mecânico por AST, shims explícitos, suíte completa por fase.
- **Risco de shim eterno:** cada shim precisa de prazo e de um teste que
  falhe se ele voltar a ser importado depois da remoção.
- **Risco de colisão de nomes:** `predictor.py` e `models.py` já colidem se
  achatados no mesmo diretório. O layout de `ml.py` e a separação
  `physics/` vs. `prediction/` resolvem isso; não forçar um diretório único.
- **Risco de empacotamento:** nome/versão divergentes e dados grandes no
  artefato. Mitigação: versão única, `include = ["router*"]`, validação de
  wheel e estratégia separada de dados.
- **Risco de falsa fronteira:** se `interfaces` voltar a importar `core`, a
  API curada deixa de ser fronteira real. O teste de arquitetura continua
  sendo o guarda.

---

## 11. Critérios de aceite (quando executado)

1. A árvore de §2 existe; `core/physics/physical/` e
   `core/prediction/intelligence/` não existem mais (após o prazo dos shims).
2. `physics/` e `physiology/` são irmãos em `core/`; `physiology` não importa
   `physics`.
3. `router.integration` é a única implementação da superfície curada;
   `interfaces/integration.py` é alias ou não existe.
4. `interfaces/api.py` mantém 336 símbolos enquanto existir; remoção somente
   após inventário de consumidores.
5. `pytest` completo verde (base 843 + testes novos), com
   `tests/test_architecture.py` apontando para os caminhos novos.
6. Todos os módulos `router.*` importam em interpretador limpo;
   `python -m router --help` e os console scripts funcionam.
7. `router.__version__ == pyproject.version`; wheel construído e inspecionado
   sem `data/`, `.cache/`, `.git/`, `tests/` ou caches.
8. Contrato v1 e comportamento de rota idênticos ao baseline.
