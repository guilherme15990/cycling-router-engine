# ADR-031 — Organização de módulos e fronteira pública

- **Status:** **aceita e implementada** (2026-11-11). Código movido para
  `src/router/`; suíte verde (836 + testes de arquitetura).
  Nomenclatura do namespace **decidida**: `router` (inglês).
- **Data:** 2026-11-11
- **Relação:** complementa o ADR-030 (consolidação arquitetural). Não altera
  física, busca, ML nem o contrato v1; define **onde** cada coisa mora e **quem
  pode importar quem**. A revisão posterior da árvore — achatamento de
  `physical`/`intelligence`, `physiology` como irmão da física e identidade de
  pacote — está registrada no **ADR-032** (proposta).
- **Base:** 80 módulos em `src/` (14 na raiz + 8 pacotes), 836 testes verdes,
  contrato público `route_contract` v1 e superfície curada `integration`
  (ADR-030 Fases 2–3).

---

## 0. Contexto

O ADR-030 consolidou o conteúdo (uma fonte de verdade por conceito, um DTO
canônico, um envelope versionado), mas o **layout** continua refletindo a ordem
histórica de implementação: 14 módulos soltos na raiz de `src/` convivem com
pacotes de camada, e a fronteira pública é uma lista de 336 símbolos.

O grafo de imports atual já é quase acíclico, com uma violação conhecida:

```
evaluation   -> graph, intelligence, ml, physical, physiology, search
intelligence -> evaluation (1), physical (5), physiology (6)
search       -> graph, physical
osm          -> graph
physical     -> graph
raiz         -> graph, osm, physical, search, intelligence, ml, evaluation, physiology
```

A violação `intelligence -> evaluation` (ADR-030 F8) é sintoma de um problema de
**lugar**, não de código: `classification.py` julga rotas, mas mora na camada de
predição. Este ADR resolve isso pela organização, sem tocar na lógica.

**Objetivo:** tornar as cinco responsabilidades explícitas no caminho dos
arquivos e fixar **uma** fronteira pública pequena, estável e verificável.

---

## 1. Princípios

1. **Um nome de pacote por responsabilidade.** A árvore deve responder “onde
   está a física?” sem consultar documentação.
2. **Dependência aponta para baixo.** Cada camada só importa camadas inferiores;
   violação é erro de teste, não convenção oral.
3. **Contrato no fundo, não no topo.** O contrato v1 é o vocabulário comum e não
   depende de nada do domínio.
4. **Aplicação não é domínio.** Orquestração (`planning`, `multi_leg`) é uma
   camada à parte; não é física, busca nem avaliação.
5. **Fronteira pequena e chata.** O consumidor externo importa **um** módulo
   (`router.integration`) e, no máximo, os tipos de
   `router.contracts`. O resto é interno.
6. **Nada de fachada nova e ampla.** `router/__init__.py` **não** reexporta
   o motor; a fachada ampla legada (`api`) sobrevive como compatibilidade
   congelada.

---

## 2. Árvore proposta

```
src/
  router/
    __init__.py                  # só __version__ (sem reexports)
    offline.py                   # L0 — guard de rede (transversal, sem deps)

    contracts/                   # L0 — vocabulário público, versionado
      __init__.py
      version.py                 # CONTRACT_FORMAT, CONTRACT_VERSION
      geo.py                     # LL, BBox canônicos
      route.py                   # RoutePlanV1, RouteV1, RoutePointV1, ...
      serialize.py               # dumps/loads estritos, read_plan/write_plan
      errors.py                  # RouteContractError

    core/                        # L1–L5 — núcleo interno (sem estabilidade)
      __init__.py
      data/                      # L1 — dados e grafo
        __init__.py
        graph/                   # model, restrictions, serialize, simplify, surface, validate
        osm/                     # parse, build, pbf
        elevation/               # providers/ (rede), cache/, offline/, tiles.py
        geo.py                   # geometria de apoio
        envelope.py              # envelope de consulta
        snapping.py              # grafo temporário de consulta
      physics/                   # L2 — simulação física
        __init__.py
        physical/                # arc_profile, bicycle, cyclist, effort, energy,
                                 # energy_model, environment, forces, models, speed, traversal
        physiology/              # scales, zones, metabolic, prediction
      search/                    # L3 — busca e otimização
        __init__.py              # superfície interna do pacote (não pública)
        astar.py, objectives.py, profiles.py, frontier.py, dominance.py,
        heuristic.py, queue.py, state.py, route.py, candidates.py,
        evaluator.py, ranking.py, selection.py, diversity.py,
        circulation.py, energy.py, approximate.py, pipeline.py, config.py,
        explanation.py, traversal_model.py
      prediction/                # L4 — predição e estimativa de esforço
        __init__.py
        ml/                      # fronteira de inferência
        intelligence/            # features, observations, targets, models,
                                 # training, model_eval, model_store, predictor, intensity
      evaluation/                # L5 — avaliação e segurança
        __init__.py
        assessment.py, safety.py, diagnostics.py
        classification.py         # movido de intelligence (ver D2)

    app/                         # L6 — orquestração (API estável do motor)
      __init__.py                 # plan_routes, plan_offline_routes, PROFILE_IDS
      planning.py                 # RoutePlan/PlannedRoute internos
      multi_leg.py                # plan_routes multi-perna
      ports.py                    # protocolos de inversão (ver §5)

    interfaces/                  # L7 — adaptadores (não são núcleo)
      __init__.py
      integration.py              # superfície curada (~28 símbolos), zero lógica
      api.py                      # fachada ampla legada (compatibilidade, congelada)
      cli/                        # argparse + comandos
        __init__.py               # main
        commands/
          build.py, plan.py, ...
  __main__.py                    # delega a interfaces.cli.main
```

### Por que `router/` como namespace único

**Decisão de nomenclatura (2026-11-11):** o namespace é `router` — em inglês,
como os demais nomes de código —, não `roteamento`. O texto e os ADRs podem
seguir em português; identificadores de código, não.

- separa visualmente “motor instalável” de “arquivos de repositório”;
- impede que um consumidor importe `graph` ou `search` por acidente — nomes
  genéricos deixam de ser públicos;
- o porte futuro para TypeScript mapeia 1:1 (`router.core.search` →
  `core/search`), sem o ruído de 14 módulos de raiz;
- permite uma migração **sem big bang**: os nomes antigos viram shims (ver §7).

---

## 3. As cinco responsabilidades

| # | Responsabilidade | Pacote | Inclui | Não inclui |
| --- | --- | --- | --- | --- |
| 1 | **Dados e grafo** | `core.data` | ingestão OSM/PBF, grafo, superfície, restrições, serialização do grafo, altimetria (provider/cache/offline), snapping, envelope, `geo` | física, busca, ML |
| 2 | **Simulação física** | `core.physics` | travessia, forças, energia, modelos de ciclista/bicicleta/ambiente, perfis de arco, contratos fisiológicos (Borg, zonas, metabólico) | decisão de rota |
| 3 | **Busca e otimização** | `core.search` | A\* multiobjetivo, fronteira Pareto, dominância, heurística, perfis/objetivos, ranking, seleção, diversidade, circulação, estimador aproximado | física nova, avaliação final |
| 4 | **Predição e estimativa de esforço** | `core.prediction` | features, observações, alvos, treino, avaliação de modelo, registro/artefato, preditor local, `ml` (inferência), estimativa de intensidade (RPE/faixas) | julgar elegibilidade, segurança |
| 5 | **Avaliação e segurança** | `core.evaluation` | avaliação física completa, consistência energética, guardrails de circulação, `RouteSafety`, diagnóstico, **classificação predição × preferência** | produzir predição |

**Nota sobre `physiology`:** fica em `core.physics` porque define contratos
usados pela física e por todas as camadas acima. `intelligence/intensity`
(predição) consome `TraversalResult`; não precisa estar abaixo da física.

**Movimentos em relação ao layout atual**

| De | Para | Motivo |
| --- | --- | --- |
| `route_contract.py` | `contracts/` (dividido em `version`, `geo`, `route`, `serialize`, `errors`) | contrato é L0; 698 linhas com 4 papéis |
| `graph/`, `osm/`, `elevation*.py`, `snapping.py`, `envelope.py`, `geo.py` | `core/data/` | responsabilidade 1 |
| `elevation.py` | `core/data/elevation/{providers,cache,offline}.py` + `tiles.py` | separar rede de offline (ADR-030 F5/F9) |
| `physical/`, `physiology/` | `core/physics/` | responsabilidade 2 |
| `search/` | `core/search/` | responsabilidade 3 |
| `ml/`, `intelligence/` | `core/prediction/` | responsabilidade 4 |
| `intelligence/classification.py` | `core/evaluation/classification.py` | D2: julgar rota é avaliação |
| `evaluation/` | `core/evaluation/` | responsabilidade 5 |
| `planning.py`, `multi_leg.py` | `app/` | orquestração |
| `integration.py`, `api.py`, `cli.py` | `interfaces/` | adaptadores |
| `offline.py` | `router/offline.py` | guard transversal, sem deps |

`multileg` continua sendo o **único** ponto de entrada de rota: `app.plan_routes`
resolve `stops` no núcleo e devolve `contracts.RoutePlanV1`.

---

## 4. Regras de dependência

### 4.1 Grafo permitido

```
contracts        -> (nada além da stdlib)
offline          -> (nada)
core.data        -> contracts
core.physics     -> core.data, contracts
core.search      -> core.data, core.physics, contracts
core.prediction  -> core.data, core.physics, contracts
core.evaluation  -> core.data, core.physics, core.search, core.prediction, contracts
app              -> todas as camadas core + contracts + offline
interfaces       -> app, contracts, offline
```

`core.search` e `core.prediction` são **independentes** entre si: a avaliação é
quem combina os dois.

### 4.2 Invariantes

1. `core.*` **nunca** importa `app` nem `interfaces`.
2. `interfaces` **nunca** importa `core.*` — só `app` e `contracts`. É esta
   regra que torna a fronteira real, e não decorativa.
3. `contracts` não importa nenhum pacote do domínio (nem para tipos).
4. `app` não implementa física/busca: só orquestra pontos de entrada eleitos.
5. `offline` não importa ninguém; quem precisa dele importa `offline`.

### 4.3 Teste de arquitetura (executável)

O ADR-030 já provou que regra sem teste vira comentário. Proposta:
`tests/test_architecture.py` com AST, sem dependência externa:

```python
ALLOWED = {
    "contracts": set(),
    "offline": set(),
    "core.data": {"contracts"},
    "core.physics": {"core.data", "contracts"},
    "core.search": {"core.data", "core.physics", "contracts"},
    "core.prediction": {"core.data", "core.physics", "contracts"},
    "core.evaluation": {"core.data", "core.physics", "core.search",
                        "core.prediction", "contracts"},
    "app": {"contracts", "offline", "core.data", "core.physics",
            "core.search", "core.prediction", "core.evaluation"},
    "interfaces": {"app", "contracts", "offline"},
}

def test_import_boundaries():
    for module, target in imports_in("src/router"):
        assert target in ALLOWED[module], f"{module} -> {target}"
```

Mais três testes baratos:

- `test_contracts_are_stdlib_only` (nenhum import de `router.*`);
- `test_interfaces_do_not_touch_core` (subconjunto da matriz acima);
- `test_no_top_level_domain_imports` (nenhum `import graph`/`search` fora dos
  shims de compatibilidade).

---

## 5. Ports (inversões explícitas)

Só onde a dependência precisa ser invertida para manter o grafo acima:

| Porta | Definida em | Implementações | Consumidor |
| --- | --- | --- | --- |
| `ElevationProvider` | `core/data/elevation` | constante, OSM tag, Terrarium, OpenTopoData | `core/data`, `app` |
| `GraphRepository` | `app/ports.py` | JSON versionado (`graph/serialize`) | `app`, `interfaces` |
| `EffortPredictor` | `core/prediction` | const/linear/artefato local | `core.search` (opcional), `app` |
| `RouteAssessmentView` | `core/evaluation` | `RouteAssessment` | `classification` (D2) |

`RouteAssessmentView` é a inversão que elimina `intelligence -> evaluation`:
a classificação passa a receber uma **visão mínima** (métricas + features +
predição), sem importar o módulo de avaliação.

---

## 6. Fronteira pública

Três níveis, com promessas diferentes:

| Nível | Módulo | Promessa | Quem importa |
| --- | --- | --- | --- |
| **A — dados** | `router.contracts` | schema `v1`, versionado, serialização estrita | qualquer consumidor |
| **B — API** | `router.integration` | superfície curada (~28 símbolos), estável dentro do major | aplicativo / porte TS |
| **C — interno** | `router.core.*` | **sem** estabilidade; pode mudar a qualquer momento | apenas o motor |

- `router/__init__.py` expõe apenas `__version__`.
- `interfaces/integration.py` **não tem lógica**: reexporta de `app` e
  `contracts` e mantém `__all__` fixo.
- `interfaces/api.py` é a fachada ampla legada (336 símbolos): permanece para
  não quebrar consumidores, **congelada** — nenhum símbolo novo entra nela; a
  referência gerada marca a seção como compatibilidade.
- `cli` consome `app` e `interfaces/integration`; nunca `core` diretamente.

**Regra de evolução:** adicionar algo à API pública = adicionar a
`contracts` (se for dado) ou a `app` + `integration` (se for operação). Nunca
“exportar o módulo interno porque é mais rápido”.

---

## 7. Migração (sem big bang)

O layout atual tem 836 testes e imports curtos (`from search import ...`). A
troca precisa ser mecânica e reversível.

**Fase A — andaime (risco zero).**
1. Criar `src/router/` com `__init__.py` e `contracts/` **reexportando** o
   `route_contract.py` atual (sem mover ainda).
2. Adicionar `tests/test_architecture.py` com a matriz permitida, ignorando os
   shims legados.
3. `pyproject.toml`: incluir `router` em `packages.find` e manter
   `py-modules` atuais.

**Fase B — baixo nível.**
4. Mover `route_contract.py` para `contracts/` (dividir em 5 módulos) e deixar
   `src/route_contract.py` como shim `from router.contracts import *`.
5. Mover `core.data` e `core.physics`; criar shims `graph/`, `osm/`,
   `physical/`, `physiology/` que reexportam.
6. Rodar a suíte inteira: nenhum teste deve mudar.

**Fase C — camadas centrais.**
7. Mover `search/` → `core/search/`, `ml/` + `intelligence/` →
   `core/prediction/`, `evaluation/` → `core/evaluation/`
   (com `classification` realocada em D2).
8. Shims idem; atualizar imports internos para os caminhos definitivos.

**Fase D — aplicação e interfaces.**
9. `planning.py` + `multi_leg.py` → `app/`; `integration.py` + `api.py` +
   `cli.py` → `interfaces/`.
10. `_cli.py`, `_integration.py`, `_api.py` viram shims.
11. `pyproject.toml`: `py-modules` reduzido ao mínimo (`__main__` eventual);
    `router` é o pacote. Console script: adicionar `router = "router.interfaces.cli:main"`
    e manter `roteamento` como alias de compatibilidade até a Fase E.

**Fase E — remoção.**
12. Apagar shims e imports curtos; atualizar `docs/reference/api.md`,
    quickstart e ADRs citados.
13. Congelar `api.__all__` (teste de contagem) e documentar a API pública como
    `contracts` + `integration`.

Critério de saída: suíte verde em todas as fases, `api.__all__` **sem
entradas removidas** até a Fase E, e o teste de arquitetura falhando se alguém
importar `core.*` a partir de `interfaces`.

---

## 8. Decisões em aberto (D1–D4)

- **D1 — namespace `router/` vs. `core/` na raiz.** **Decidido: `router/`**
  (inglês, alinhado ao código). Alternativa descartada: manter top-level e só
  criar `core/`; perde a barreira contra imports acidentais.
- **D1.1 — `format` do contrato permanece `roteamento.cycling-route-plan`.**
  É dado versionado do envelope v1, não nome de pacote; renomear quebraria
  consumidores. Um eventual `format` em inglês exigiria `version: 2`.
- **D2 — dono da classificação.** Proposta: mover
  `intelligence/classification.py` para `core/evaluation/`. Alternativa:
  mantê-la em predição e injetar `RouteAssessmentView` (protocolo). Ambas
  eliminam a violação; a primeira é mais honesta com a responsabilidade.
- **D3 — onde mora `dumps`/`loads`.** Proposta: `contracts/serialize.py`
  (puro, sem domínio). Alternativa: manter em `route_contract.py` único.
- **D4 — `elevation` online.** Proposta: `providers/network.py` só existe no
  pacote; o caminho padrão importa apenas `offline/` e `cache/`. Isso alinha o
  código à Fase 4 do ADR-030 (`--allow-network` explícito).

---

## 9. Riscos e não-objetivos

- **Não** alterar física, busca, ML, contrato v1 ou comportamento de rota.
- **Não** substituir a fachada ampla agora; ela é compatibilidade e será
  congelada, não removida neste ADR.
- **Risco de churn:** mover 80 módulos mexe em todos os imports. Mitigação:
  shims + teste de arquitetura + suíte completa a cada fase.
- **Risco de empacotamento:** o wheel precisa incluir `router/**`; validar
  em ambiente com `setuptools` (hoje ausente), como no ADR-030 Fase 0.
- **Risco de falsa fronteira:** se `interfaces` importar `core`, a API curada
  deixa de ser uma fronteira real. O teste de §4.3 é o que impede isso.

---

## 10. Critérios de aceite

1. `tests/test_architecture.py` passa e falha de propósito se `interfaces`
   importar `core` ou `contracts` importar o domínio.
2. `integration.__all__` continua entre 20 e 30 símbolos e é subconjunto
   declarado da API ampla (exceções documentadas).
3. `api.__all__` não perde símbolos durante a migração; na Fase E, a contagem
   final é documentada e congelada.
4. `pytest` completo verde em cada fase (hoje: 836).
5. `python -m router` e `router --help` funcionam após a Fase D.

---

## 11. Registro de implementação (2026-11-11)

Executado em fases A–E, com a suíte verde em cada etapa.

- **Resultado:** `src/` contém apenas `router/` (+ `__main__.py` legado); não
  restam shims nem módulos soltos.
- **Testes:** `843` verdes: `836` originais + `7` novos (`tests/test_architecture.py`
  e o aliasing público em `tests/test_integration.py`). `api.__all__` permanece
  em `336` símbolos, agora verificado por teste de contagem
  (`test_wide_facade_is_frozen`).
- **`contracts`:** dividido em `version`, `geo`, `route`, `serialize`,
  `errors` e `_validation` (auxiliares internos de validação).
- **`elevation`:** dividido em `providers`, `cache`, `offline`, `tiles` e
  `profile` (o perfil de arco não cabia em nenhum dos quatro anteriores).
- **`interfaces`:** `api.py` passou a montar a fachada a partir de
  `app.legacy`; `interfaces` não importa `core.*`. `integration` reexporta
  apenas `app`/`contracts`/`offline`.
- **Atalho público:** `router/integration.py` reexporta
  `interfaces.integration`, honrando o nome `router.integration` da §6.
- **CLI:** mantida em `interfaces/cli/__init__.py` (um único módulo). A divisão
  em `commands/build.py`, `commands/plan.py` etc. ficou como evolução
  posterior; o critério de aceite (`router --help`) está atendido.
- **Empacotamento:** `pyproject.toml` reduzido a `py-modules = ["__main__"]` e
  `packages.find = ["router*"]`; console scripts `roteamento` e `router`.
  A validação do wheel em ambiente com `setuptools` continua pendente (o
  ambiente de execução atual não o possui).
- **Revisão posterior (ADR-032, proposta):** nenhum código foi movido. O ADR-032
  registra a estrutura-alvo simplificada (`core/physics/` e
  `core/physiology/` como irmãos, `core/prediction/` sem `intelligence/`,
  `ml.py`), a fonte única de `router.integration`, o inventário de consumidores
  de `interfaces.api` e a separação de dados/cache do artefato distribuído.
