# ADR-030 — Consolidação arquitetural e contrato de integração com o Toporide

- **Status:** auditoria concluída. **Fase 0 (higiene) executada**, **Fase 1
  parcialmente executada**, **Fase 2 executada** (contrato público
  `route_contract` v1, geometria por ponto e serialização estrita) e **Fase 3
  executada nos itens 1–2** (`plan_routes` multi-perna e `integration` curada)
  em 2026-11-11. O item 3 da Fase 3 (adaptador TypeScript) foi **descartado
  neste ciclo**: o diretório será portado para TypeScript, e não há
  compatibilização Python → TypeScript agora. Demais itens das Fases 1 e 4–6
  propostos.
- **Data:** 2026-11-11
- **Base auditada:** motor `0.18.0` (`pyproject.toml`), **80 módulos** Python em
  `src/`, **294** arestas internas de import, **809 testes** coletados na
  auditoria (`python -m pytest`); **823 testes** após a Fase 2 e **836** após
  os itens 1–2 da Fase 3.
- **Escopo:** preparar a engine Python para ser o motor de roteamento do
  `toporide`, eliminando duplicidades internas e fixando **um** contrato de
  resposta estável, versionado e verificável.
- **Regra da auditoria:** primeiro inventariar e documentar; só depois alterar.
  A auditoria em si **não alterou código de produção**. As execuções posteriores
  (Fase 0 e parte da Fase 1 do plano) estão registradas no próprio documento e no
  `CHANGELOG.md`.

> **Documento de decisão e de auditoria no mesmo artefato.** As auditorias
> anteriores (`025`, `026`, `027`, `028`, `029`) separaram “auditoria” e
> “decisão” em dois arquivos. Aqui a separação não agrega: o objeto é uma
> consolidação transversal, e o inventário é a própria justificativa das fases.
> O documento segue o mesmo método: **inventário → contrato × código →
> invariantes × evidência → matriz PRESERVAR / ADAPTAR / COMPLEMENTAR /
> SUBSTITUIR → lacunas → plano**.

---

## 0. Contexto

O `toporide` é uma aplicação TypeScript/React com worker de roteamento, grafo
em SQLite, busca multiobjetivo e um pipeline de ML próprio. A engine Python
(`roteamento com ml`) já cobre a maior parte da ciência — ingestão OSM/PBF,
grafo, física longitudinal, busca Pareto, avaliação, intensidade e ML — mas
**nunca foi integrada**. O contrato que o `toporide` consome hoje está em
`toporide/src/domain/types.ts` (`RouteRequest`, `RouteResult`, `RoutePoint`,
`MLMetrics`) e é servido pelo worker em `routeWorker.ts`.

A integração expõe três problemas distintos, que não devem ser confundidos:

1. **Duplicidade interna** — a engine tem mais de uma fonte de verdade para
   perfis, mais de uma representação de rota e mais de um ponto de entrada para
   a mesma operação. Antes de exportar qualquer coisa, é preciso escolher uma.
2. **Ausência de contrato de transporte** — `RoutePlan.to_dict()` é um dicionário
   interno, sem `format`, sem `version` e sem geometria de rota. Ele não é um
   contrato público, mas é o que a CLI serializa hoje (`src/cli.py:896`).
3. **Lacunas funcionais em relação ao app** — o app pede `points[]` por rota,
   múltiplas paradas (`stops`), parâmetros de ciclista detalhados, bandas de
   esforço e métricas de ML. A engine devolve `origin`/`destination` únicos,
   `arc_ids` e métricas — sem coordenadas por ponto.

**Decisão:** consolidar primeiro, exportar depois. A engine passa a ter **uma
fonte de verdade por conceito**, **um DTO canônico de rota** e **um envelope
JSON versionado**. O `toporide` consome esse envelope; o mapeamento de nomes
(`fastest` → `faster`, etc.) fica no consumidor, não no núcleo. **Nota
(2026-11-11):** não haverá adaptador Python → TypeScript; este diretório será
portado para TypeScript (ver status).

---

## 1. Método

Inventário estático e executável, sem alterar o repositório:

| Verificação | Como | Resultado |
| --- | --- | --- |
| Módulos e tamanho | AST em `src/**/*.py` | 80 módulos; 7 acima de 600 linhas |
| Dependências internas | AST de `Import`/`ImportFrom`, arestas por módulo | 294 arestas; **0 ciclos em nível de módulo** |
| Ciclos de pacote | análise por pacote de topo | 1 ciclo conceitual `intelligence ↔ evaluation` |
| Superfície pública | `api.__all__`, `search.__all__` | **315** e **125** símbolos |
| Símbolos mortos | referência textual por nome em `src/`, `tests/`, `scripts/` | 4 sem referência externa |
| Testes | `pytest --collect-only` | 809 testes |
| Empacotamento | `pip wheel . --no-build-isolation` | **não verificável** neste ambiente: `setuptools` ausente |
| Documentação | conferência de contagens e links | 2 contagens obsoletas, 2 links quebrados |

Comandos relevantes no apêndice A.

---

## 2. Inventário

### 2.1 Camadas e módulos

| Camada | Pacote/módulo | Papel | Observação |
| --- | --- | --- | --- |
| 0 | `offline.py` | guard de rede | usado **apenas por testes** |
| 1 | `osm/`, `graph/`, `elevation.py`, `elevation_tiles.py` | ingestão, grafo, altimetria | rede e offline no mesmo arquivo |
| 2 | `physical/` | física longitudinal | estável, referência dos ADRs 025 |
| 3 | `search/` | busca Pareto, perfis, ranking, seleção | maior superfície pública |
| 4 | `intelligence/` | ML, intensidade, classificação | importa `evaluation` |
| 5 | `evaluation/` | avaliação física e guardrails | importa `intelligence`, `search` |
| 6 | `planning.py`, `api.py`, `cli.py` | orquestração e adaptadores | `cli.py` com 1.171 linhas |
| — | `snapping.py`, `envelope.py`, `geo.py` | apoio geométrico | `snapping.py` com 1.144 linhas |

### 2.2 Módulos maiores

| Linhas | Arquivo |
| ---: | --- |
| 1.171 | `src/cli.py` |
| 1.144 | `src/snapping.py` |
| 846 | `src/elevation.py` |
| 789 | `src/graph/model.py` |
| 749 | `src/api.py` |
| 697 | `src/osm/pbf.py` |
| 662 | `src/search/approximate.py` |

Tamanho não é defeito por si só. O problema é **mistura de responsabilidades**:
`cli.py` é adapter + orquestração + serialização; `elevation.py` é provider de
rede + cache SQLite + leitura de tiles + provider constante.

### 2.3 Cadeia de representações de rota

```
SearchState (search/state.py:53)
  └─ Route (search/route.py:24)
       └─ CandidateMetrics / RouteCandidate (search/candidates.py:28,62)
            └─ SelectedRoute / SelectionResult (search/selection.py:107,137)
                 └─ RouteAssessment (evaluation/assessment.py)
                      └─ EvaluatedRoute (evaluation/safety.py:301)
                           └─ PlannedRoute / RoutePlan (planning.py)
```

Sete tipos com métricas sobrepostas (`time_s`, `distance_m`, `ascent_m`,
`descent_m`, `energy_j`) e **`to_dict()` em cada um**. Cada campo novo precisa
ser propagado manualmente por toda a cadeia — foi exatamente o que aconteceu
com `frontier_truncated`/`dropped_labels` no ADR-026.

### 2.4 Superfície pública

| Fachada | Símbolos | Consequência |
| --- | ---: | --- |
| `api.__all__` (`src/api.py`) | 315 na auditoria; 336 após as Fases 2–3 (+21 símbolos) | qualquer limpeza interna vira quebra pública |
| `search.__all__` (`src/search/__init__.py:186`) | 125 | reexporta quase todo o pacote |

A fachada não é “ruim por ser grande”: ela é o motivo pelo qual remover
duplicidades hoje quebraria consumidores. A consolidação precisa, portanto,
**separar a API de integração** (pequena, estável) da **API de conveniência**
(ampla, interna).

---

## 3. Achados

Cada achado traz evidência, custo concreto e benefício esperado. Nenhum é
classificado como “overengineering” apenas por complexidade.

### F1 — Duas fontes de verdade para perfis

- `search/objectives.py:134-142` define `PROFILE_OBJECTIVES` (`fastest`,
  `shortest`, `flattest`, `balanced`, `electric`).
- `search/objectives.py:217-221` deriva `PROFILES` dessa tabela.
- `search/profiles.py:318-362` define `PROFILE_CONFIG` com os **mesmos cinco
  ids** e objetivos próprios.
- `planning.plan_offline_routes` usa `route_profile_for()` → `PROFILE_CONFIG`;
  a busca usa `objectives_for()` → `PROFILE_OBJECTIVES`.

**Custo:** há duas tabelas literais para os mesmos cinco pares id→objetivos.
Hoje a igualdade é **verificada** por `tests/test_search_profiles.py:124-131`
(`test_to_search_profile_matches_search_view`), mas o teste existe justamente
porque a duplicação existe: todo perfil novo exige editar dois lugares, e a
consistência depende de o teste continuar rodando. Um ajuste em
`PROFILE_CONFIG` que não seja replicado altera o ranking sem alterar o que a
busca otimiza.

**Benefício da consolidação:** uma única tabela (a de objetivos) e
`PROFILE_CONFIG` derivando `objectives` dela. O teste de consistência deixa de
ser a única barreira e passa a ser redundante/confirmatório.

**Atualização (fase 1):** `PROFILE_CONFIG` passou a derivar os objetivos de
`PROFILE_OBJECTIVES` por `_profile_objectives()` (`search/profiles.py`); a
tabela literal de objetivos em `PROFILE_CONFIG` foi removida. O teste existente
continua válido.

### F2 — Sprawl de DTOs e de `to_dict`

- Métricas repetidas em `search/candidates.py:28` (`CandidateMetrics`),
  `search/state.py:53` (`SearchState`), `search/route.py:24` (`Route`),
  `physical/models.py` (`RouteMetrics`), `evaluation/assessment.py`
  (`RouteAssessment`), `planning.py` (`PlannedRoute`/`RoutePlan`).
- `PlannedRoute.to_dict()` (planejamento) embute `assessment.to_dict()`, que
  embute `metrics`, `features`, `prediction`, `intensity`, `circulation`;
  `search_metrics` é um **segundo** conjunto de métricas no mesmo objeto.

**Custo:** o consumidor precisa saber qual `metrics` é “a” métrica; a app tem
`distM`, `timeS`, `energyJ`, `gainM`, `lossM` — a engine tem
`search_metrics` **e** `assessment.metrics`. Não há um campo único com o
significado “métrica autoritativa da rota”.

**Benefício:** manter os acumuladores internos (busca × física completa), mas
expor **um** bloco `metrics` no DTO de fronteira, com a origem declarada
(`source: "search" | "assessment"`). A distinção continua auditável sem
duplicar campos no payload.

### F3 — Dois contratos públicos de resposta, nenhum versionado

- `search/pipeline.ProfileSearchResponse` é o contrato da busca.
- `planning.RoutePlan` é o contrato da consulta completa.
- `RoutePlan.to_dict()` **não** tem `format` nem `version`
  (`graph/serialize.py` tem `FORMAT`/`VERSION` para o grafo, mas a rota não).
- A CLI serializa com `json.dumps(..., default=str)` (`src/cli.py:896`),
  convertendo silenciosamente qualquer valor não-JSON em string.

**Custo:** o `toporide` não consegue fixar uma versão de schema; uma mudança de
campo quebra o parser em runtime, e o `default=str` mascara o erro.

**Benefício:** um envelope `{"format": "roteamento.cycling-route-plan",
"version": 1, ...}` validado na borda; serialização estrita (sem `default`).

### F4 — Compatibilidade duplicada em `evaluator`, `ranking` e `selection`

- `search/evaluator.py`: `evaluate` (73), `evaluate_route` (81),
  `evaluate_metrics` (88) fazem a mesma extração de `ObjectiveValues`.
- `search/ranking.py`: `rank_candidates` (110), `rank_routes` (139),
  `order_routes` (157), `ProfileRanker` (171).
- `search/selection.py`: `select_candidates` (186), `select_routes` (287),
  `select_from_result` (307), `RouteSelector` (333).

**Custo:** não há uma resposta única para “como ranquear/ selecionar”; testes e
documentação cobrem caminhos diferentes, e a API pública expõe todos. A
duplicação não é hipotética: `search/explanation.py` usa `evaluate_route`,
enquanto outros pontos usam `evaluate`.

**Benefício:** eleger **um** ponto de entrada por operação (`evaluate`,
`rank`, `select`) e manter aliases apenas onde há consumidor público
comprovado. A API de integração exporta só o ponto eleito.

### F5 — Módulos grandes com responsabilidades misturadas

- `cli.py` (1.171): parsing, orquestração de `build`/`plan`, serialização JSON,
  mensagens e validação.
- `elevation.py` (846): `ConstantElevation`, `OsmTagElevation`,
  `OpenTopoDataElevation` (rede), `SqliteElevationCache`, `TerrariumElevation`.
- `snapping.py` (1.144): opções, projeção, grafo de consulta e split de arcos.
- `api.py` (749): fachada de 315 símbolos.

**Custo:** `elevation.py` importa `urllib` no mesmo módulo que o caminho
offline; a CLI é o único lugar onde o fluxo completo é visível; testar o
adapter exige testar o núcleo junto.

**Benefício:** separar `elevation/` em `providers/` (rede), `cache/` e
`offline/`; extrair de `cli.py` um `commands/` fino. Não é “dividir por
tamanho”: é separar rede de offline e adapter de orquestração.

### F6 — Fachada pública inflada

`api.__all__` = 315; `search.__all__` = 125. A fachada reexporta símbolos de
todas as camadas, inclusive aliases de compatibilidade (F4) e tipos internos de
busca.

**Custo:** a integração com o `toporide` precisa escolher entre 315 nomes; e
qualquer consolidação interna (F1–F4) é uma mudança pública.

**Benefício:** introduzir `api.integration` (ou `integration.py`) com ~20–30
símbolos: `RouteRequest`, `RoutePlan`, `Route`, `ProfileId`, `plan_routes`,
`load_graph`, `save_graph`, `offline`. A fachada ampla continua existindo para
compatibilidade, marcada como tal.

### F7 — Código sem referência e configuração inerte

Verificado por referência textual em `src/`, `tests/`, `scripts/` e `docs/`:

| Símbolo | Local | Referências externas |
| --- | --- | ---: |
| `ConstantElevation` | `elevation.py:47` | 0 (só menção em ADR-027) |
| `point_at_fraction` | `geo.py:115` | 0 |
| `format_bbox` | `geo.py:134` | 0 |
| `surface_of` | `osm/build.py:499` | 0 |
| `battery_energy_from_wheel_j` | `physical/energy.py:54` | 0 de uso (só reexport) |

Além disso, `search/config.py` documenta `speed_step_mps` como “reservado para
discretização futura”, mas o campo é lido por `SpeedBucketDominance` — a
docstring está errada, não o código.

**Custo:** o leitor não sabe o que é contrato vivo e o que é resíduo; a
docstring induz a remover um parâmetro em uso.

**Benefício:** remover o que não tem consumidor (ou documentar por que fica) e
corrigir a docstring. Ganho pequeno em linhas, ganho real em clareza.

### F8 — Ciclo conceitual `intelligence ↔ evaluation`

- `intelligence/classification.py:36` importa `evaluation.assessment` no topo.
- `evaluation/assessment.py:65` importa `intelligence.intensity` apenas sob
  `TYPE_CHECKING` e em `assessment.py:305` faz import local.

**Fato:** **não há ciclo de import em runtime** (confirmado: 0 ciclos em nível
de módulo). O que existe é violação de direção: `intelligence` (Camada 04)
depende de `evaluation` (Camada 05), que por sua vez depende de `intelligence`.

**Custo:** a Camada 05 deveria validar a 04, não ser pré-requisito dela. Isso
dificulta testar classificação sem montar avaliação e torna a ordem de
importação sensível.

**Benefício:** `classification` recebe um protocolo/`RouteAssessment` mínimo
(sem importar `evaluation`), ou a classificação é movida para a Camada 05. A
direção `evaluation → intelligence` permanece.

### F9 — Garantia offline não está ligada ao caminho de produção

- `offline.offline()` (`offline.py:83`) é usado **apenas** em testes.
- `planning.plan_offline_routes` nunca entra no guard (o nome “offline” diz
  respeito a não baixar dados, não a proibir rede).
- `cli.cmd_plan` (`cli.py:787`) chama `plan_offline_routes` (`cli.py:876`) sem
  `offline()`.
- `cli build --elevation` tem `default="opentopodata"` (`cli.py:103-104`), que
  faz chamadas de rede.

**Custo:** a afirmação “100% offline” não é executável no caminho padrão de
`build`; o guard existe, mas é opcional e não testado na produção.

**Benefício:** `build`/`plan` entram em `offline()` por padrão; provedores de
rede viram `--allow-network` explícito. O teste de CLI com rede desabilitada
passa a cobrir o caminho real.

### F10 — Empacotamento inconsistente

`pyproject.toml` declara `[tool.setuptools] py-modules = ["api", "cli", ...]`
sem `package-dir = {"" = "src"}`. Os módulos estão em `src/`. O console script
declara `roteamento = "cli:main"`.

**Custo provável:** a instalação não encontra `api.py`, `cli.py`, `planning.py`
etc. (o `py-modules` é resolvido na raiz do projeto, não em `src/`). O wheel não
pôde ser verificado neste ambiente porque `setuptools` não está instalado
(`backend-unavailable`).

**Benefício:** adicionar `package-dir` e verificar o wheel em ambiente com
`setuptools`; sem isso, a integração por instalação (`pip install`) não é
confiável.

**Atualização (fase 0):** corrigido com `package-dir = { "" = "src" }`. Wheel
construído em venv com `setuptools` 84.2.1 (`roteamento_com_ml-0.18.0-py3-none-any.whl`,
233.815 bytes); 79 módulos `.py` no wheel, incluindo `api`, `cli`, `planning`,
`geo`, `snapping`; console script `roteamento --help` e `import api` (315
símbolos) verificados após instalação.

### F11 — Configuração de teste duplicada

`pytest.ini` repete `testpaths`/`addopts` de `[tool.pytest.ini_options]` em
`pyproject.toml`. Apenas `pytest.ini` define `pythonpath = src`.

**Custo:** rodar sem `pytest.ini` (ou em ferramenta que só lê `pyproject`)
perde o `pythonpath`; a duplicação convida à divergência.

**Benefício:** uma única fonte (`pyproject` com `pythonpath = ["src"]` ou
`pytest.ini` exclusivo), documentada.

**Atualização (fase 0):** `pytest.ini` removido; `pythonpath = ["src"]` passou
para `[tool.pytest.ini_options]` em `pyproject.toml`. Coleta e suíte completas
verificadas sem `pytest.ini` (809 testes).

### F12 — Documentação desatualizada

- `docs/reference/quickstart.md:165` e `docs/reference/README.md:9` dizem
  **141** símbolos; `api.md` e `api.__all__` têm **315**.
- Ambos apontam para `../gui/README.md`, que **não existe**.
- O `README.md` está com mojibake (encoding).

**Custo:** o primeiro contato do integrador mostra números e links errados.
**Benefício:** regenerar a referência, corrigir contagens e remover o link
morto. É barato e elimina ruído antes da integração.

**Atualização (fase 0):** `quickstart.md` e `docs/reference/README.md` passaram
a declarar **315** símbolos; os links para `../gui/README.md` (inexistente)
foram removidos.

### F13 — Lacunas de contrato em relação ao `toporide`

Comparação campo a campo com `toporide/src/domain/types.ts`:

| Necessidade do app | Hoje na engine | Lacuna |
| --- | --- | --- |
| `RouteResult.points: RoutePoint[]` (`lat`, `lon`, `ele`, `distM`, `vKmh`, `gradePct`, `band`, `rpe?`, `watts?`) | `PlannedRoute.to_dict()` não inclui geometria; `assessment.arcs` traz só ids | **sem geometria por ponto** |
| `RouteRequest.stops: LL[]` | `plan_offline_routes(origin, destination)` | **sem múltiplas paradas** |
| `goal: 'faster' \| 'shorter' \| 'flat'` | perfis `fastest`, `shortest`, `flattest`, `balanced`, `electric` | **nomes e cardinalidade diferentes** |
| `CyclistParams` (`bikeType`, `cda`, `crr`, `airDensity`, `maxGradePct`, `lambdaJs`, `contraflow`, vento) | `CyclistModel`/`BicycleModel`/`Environment` com parâmetros físicos | **sem mapeamento declarado** |
| `MLMetrics` (`avgRPE`, `peakRPE`, `hrZoneDist`, `arterialM`, ...) | `IntensityEstimate` (`mean_rpe`, `peak_rpe`, bandas) | **sem `hrZoneDist`/arterial** (e não deve inventar) |
| `metersBand: [number, number, number]` | `band_distance_fraction` (frações) | **sem metros por banda** |
| `contraM`, `walkM`, `maxGradePct` | não expostos no DTO | **sem agregação** |
| `RouteResult[]` + `corridor`/`streets`/`heatmap` | `RoutePlan` aninhado | **sem lista plana nem corredor** |

**Custo:** sem esses campos, o `toporide` não consegue trocar o próprio motor
pelo Python sem reimplementar a camada de apresentação.

**Benefício:** fixar um contrato canônico que já nasce com `points`, `stops`,
métricas agregadas e proveniência; o que a engine **não** calcula (zonas de FC,
arterial) fica explicitamente ausente — nunca preenchido com valor inventado.

---

## 4. Matriz de decisão

| Componente | Decisão | Justificativa |
| --- | --- | --- |
| `physical/` (física, traversal, modelos) | **PRESERVAR** | é a referência auditada (ADR-025); não há duplicata |
| `graph/` (modelo, serialização versionada) | **PRESERVAR** | `FORMAT`/`VERSION` já são o padrão correto a copiar para rotas |
| `search/astar`, `objectives`, `state` | **PRESERVAR** | núcleo da busca; alterações aqui exigem baseline |
| `search/profiles` × `search/objectives` | **ADAPTAR** | eliminar a segunda tabela; manter uma direção de derivação |
| `search/evaluator`, `ranking`, `selection` | **ADAPTAR** | eleger um ponto de entrada; aliases só com consumidor |
| `search/pipeline` | **ADAPTAR** | passa a ser detalhe interno; a borda é o novo contrato |
| `evaluation/assessment`, `safety` | **PRESERVAR** | guardrails do ADR-029; só o DTO de borda muda |
| `intelligence/classification` | **ADAPTAR** | remover dependência de `evaluation` (F8) |
| `intelligence/intensity` | **PRESERVAR** | estimativa não calibrada, honesta; não vira “zona de FC” |
| `planning.plan_offline_routes` | **ADAPTAR** | aceitar `stops`; devolver `RoutePlan` canônico com geometria |
| `planning.RoutePlan.to_dict` | **SUBSTITUIR** | envelope versionado + serialização estrita |
| `cli.py` | **ADAPTAR** | extrair comandos; `plan` entra em `offline()`; `build` sem rede por padrão |
| `elevation.py` | **ADAPTAR** | separar provider de rede, cache e offline |
| `offline.py` | **COMPLEMENTAR** | ligar ao caminho de produção (CLI e `plan_routes`) |
| `api.py` | **ADAPTAR** | manter a fachada ampla; adicionar `api.integration` curada |
| `pyproject.toml` / `pytest.ini` | **ADAPTAR** | `package-dir`; uma fonte de config de teste |
| símbolos mortos (F7) | **SUBSTITUIR/REMOVER** | sem consumidor; remoção com teste de superfície |
| `docs/reference` | **ADAPTAR** | regenerar e corrigir contagens/links |

Nenhum componente de física, busca ou ML é **substituído** neste ciclo.

---

## 5. Contrato canônico — `RoutePlan` v1 (implementado na Fase 2)

Objetivo: um envelope único, estrito, versionado e independente de nomes
internos. A implementação vive em `src/route_contract.py`; o consumidor
(futuro porte em TypeScript) traduz para `RouteResult`/`RoutePoint`.

```json
{
  "format": "roteamento.cycling-route-plan",
  "version": 1,
  "request": {
    "origin": {"lat": -23.55, "lon": -46.63},
    "destination": {"lat": -23.56, "lon": -46.64},
    "stops": [],
    "profile": "fastest"
  },
  "reached": true,
  "truncated": false,
  "frontier_truncated": false,
  "dropped_labels": 0,
  "iterations": 1234,
  "bbox": {"south": -23.56, "west": -46.64, "north": -23.55, "east": -46.63},
  "search_bbox": {"south": -23.57, "west": -46.65, "north": -23.54, "east": -46.62},
  "zone": {"nodes": 420, "arcs": 880, "graph_nodes": 15000, "graph_arcs": 31000},
  "pareto_route_count": 3,
  "route_count": 1,
  "rejected_count": 0,
  "physiological_count": 0,
  "recommended_route_id": "r0",
  "routes": [
    {
      "id": "r0",
      "profile": "fastest",
      "recommended": true,
      "geometry": [
        {"lat": -23.55, "lon": -46.63, "ele": 760.0, "dist_m": 0.0,
         "speed_kmh": 18.2, "grade_pct": 1.2, "band": 0,
         "rpe": 11.4, "watts": 148.0}
      ],
      "metrics": {
        "source": "assessment",
        "distance_m": 4210.5,
        "time_s": 830.2,
        "energy_j": 124000.0,
        "ascent_m": 42.0,
        "descent_m": 39.5,
        "max_grade_pct": 8.1,
        "meters_band": [3800.0, 350.0, 60.5],
        "contra_m": null,
        "walk_m": null
      },
      "ml": {
        "mean_rpe": 11.4,
        "peak_rpe": 14.0,
        "calibrated": false,
        "is_measurement": false,
        "method_id": "toporide-heuristic",
        "model_version": "1",
        "borg_scale": "6-20",
        "band_distance_fraction": {"i1": 0.3, "i2": 0.5, "i3": 0.2, "i4": 0.0, "i5": 0.0},
        "band_duration_fraction": null
      },
      "safety": {"state": "validated", "eligible": true, "reasons": [], "advisories": []},
      "provenance": {"kind": "search", "profile": "fastest"}
    }
  ],
  "rejected": [],
  "warnings": []
}
```

Exemplo **abreviado**: a geometria real tem um ponto por vértice do traçado.
`request.params` **não** faz parte do v1 — a engine não ecoa os parâmetros de
simulação; o consumidor usa os parâmetros que já conhece. Um envelope completo
tem os 19 símbolos de `route_contract`.

Regras do contrato:

1. **Toda** rota tem `geometry` com ao menos origem e destino; `dist_m` é
   acumulado e monotônico (validado no construtor).
2. `metrics.source` declara se a métrica veio da busca ou da avaliação
   completa. Nunca há dois blocos “metrics” ambíguos.
3. `ml.calibrated` e `ml.is_measurement` são **sempre** explícitos. Campos não
   calculados são **ausentes** ou `null`, nunca zero inventado.
4. `format`/`version` são obrigatórios; o parser rejeita versão desconhecida.
5. Serialização estrita: `json.dumps` sem `default=str`; valores não JSON são
   erro, não string.

### 5.1 Mapeamento para `toporide`

| Canonical | `toporide/RouteResult` |
| --- | --- |
| `routes[].geometry[]` | `points[]` |
| `geometry[].dist_m` | `points[].distM` |
| `geometry[].speed_kmh` | `points[].vKmh` |
| `geometry[].grade_pct` | `points[].gradePct` |
| `geometry[].band` | `points[].band` |
| `metrics.distance_m` | `distM` |
| `metrics.time_s` | `timeS` |
| `metrics.energy_j` | `energyJ` |
| `metrics.ascent_m` | `gainM` |
| `metrics.descent_m` | `lossM` |
| `metrics.meters_band` | `metersBand` |
| `ml.mean_rpe` | `mlMetrics.avgRPE` |
| `ml.peak_rpe` | `mlMetrics.peakRPE` |
| `route.id` / `profile` | `id` / `algoId` |

`hrZoneDist`, `arterialM`, `arterialCrossings` **não** são preenchidos pela
engine: são métricas do modelo do `toporide`. O consumidor (porte em
TypeScript) pode calculá-las ou deixá-las ausentes; a engine não inventa.

Perfis: o consumidor mapeia `faster → fastest`, `shorter → shortest`,
`flat → flattest`. `balanced` e `electric` ficam disponíveis como extensão.

---

## 6. Plano de execução

### Fase 0 — Higiene (baixo risco, sem mudança de comportamento) — ✅ executada

1. ✅ `package-dir = { "" = "src" }` em `pyproject.toml`; `pytest.ini` removido
   e `pythonpath` movido para `pyproject.toml` (F10/F11).
2. ✅ Contagens (**315**) e links da documentação corrigidos (F12).
3. ✅ Docstring de `speed_step_mps` corrigida — o campo **é usado** por
   `SpeedBucketDominance` (F7).

**Aceite verificado:** wheel construído e instalado em venv com `setuptools`;
`roteamento --help` e `import api` funcionam; `pytest` sem `pytest.ini`
coletou e passou os **809** testes.

### Fase 1 — Uma fonte de verdade por conceito

1. ✅ `PROFILE_OBJECTIVES` (objetivos) como fonte única; `PROFILE_CONFIG`
   deriva `objectives` por `_profile_objectives()` (F1). A direção escolhida
   evita ciclo: `profiles.py` já importa `objectives.py`.
2. ✅ Teste de consistência já existente
   (`test_to_search_profile_matches_search_view`) cobre
   `route_profile_for(id) == profile_for(id)`; permanece como confirmação.
3. ⬜ Eleger `evaluate`/`rank`/`select`; aliases marcados e sem novos usos (F4).

**Aceite verificado (itens 1–2):** baseline de busca
(`test_search_reference.py`) inalterado; `api.__all__` sem entradas removidas;
suíte de perfis/seleção/busca verde.

### Fase 2 — DTO canônico e serialização — ✅ executada

1. ✅ `src/route_contract.py` com `RoutePlanV1`, `RouteV1`, `RoutePointV1`,
   `RouteMetricsV1`, `RouteMLV1`, `RouteSafetyV1`, `RouteProvenanceV1`,
   `RouteRequestV1`, `BBoxV1`, `ZoneV1` e `ReasonV1`; `dumps`/`loads`
   **estritos** (`allow_nan=False`, sem `default=str`), `write_plan`/`read_plan`.
2. ✅ `planning.RoutePlan.to_contract()` projeta o resultado interno;
   `to_dict()` delega a `to_contract().to_dict()` (caminho único). O CLI
   (`cmd_plan --json`) usa `route_contract.dumps`.
3. ✅ `PlannedRoute.geometry` construída em `_route_points` a partir de
   `query.graph.arc_geometry` + `TraversalResult` + `IntensityEstimate`
   (`segment_rpes`/`segment_bands`), sem equação nova.
4. ✅ `meters_band` derivado das faixas por ponto; `max_grade_pct` derivado da
   grade por ponto; `contra_m`/`walk_m` permanecem `None` (não calculados pela
   engine) — nada é inventado. `metrics.source` declara `"assessment"`.
5. ✅ Contrato exportado na fachada (`api`, +19 símbolos, 334 no total) e na
   referência gerada.

**Aceite verificado:** `tests/test_route_contract.py` cobre round-trip
`dumps`/`loads`, `format`/`version` obrigatórios e versão desconhecida,
`dist_m` monotônico, `recommended_route_id` dentro de `routes`, perfil da rota
igual ao da consulta, `NaN` rejeitado, ausência preservada (`ele=None`) e
geometria real começando na origem e terminando no destino. Suíte completa:
**823 testes** verdes.

**Nota de precisão:** a serialização canonicaliza a precisão declarada
(7 casas em coordenadas, 3 em metros/segundos). Por isso a igualdade
garantida é no nível do envelope (`loads(dumps(x)).to_dict() == x.to_dict()`),
não necessariamente bit a bit nos floats internos.

### Fase 3 — Integração com o app

1. ✅ `plan_routes(graph, origin, destination, stops=(), ...)` em
   `src/multi_leg.py`: uma perna por trecho, reutilizando
   `plan_offline_routes` e fundindo geometria/métricas no núcleo (equivalente
   a `mergeLegRoutes`). Sem paradas, devolve exatamente o contrato da perna
   única. Valores desconhecidos em qualquer perna permanecem `None`; sem rota
   em **qualquer** perna não há trajeto parcial.
2. ✅ `src/integration.py` com superfície curada (28 símbolos): `plan_routes`,
   `PROFILE_IDS`, modelos de entrada, grafo (`load_graph`/`save_graph`),
   contrato v1 e serialização estrita, guard offline. `api.integration`
   expõe o módulo e a fachada ampla ganhou `plan_routes`.
3. ❌ **Descartado neste ciclo:** adaptador TypeScript no `toporide`
   (`routeEngine.ts`). Decisão de 2026-11-11: **não** haverá compatibilização
   Python → TypeScript; este diretório será **portado** para TypeScript no
   futuro. O contrato v1 (§5) e o mapeamento (§5.1) ficam como referência do
   porte, não como camada de integração a construir agora.

**Aceite (itens 1–2):** `tests/test_multi_leg.py` cobre `stops=[]` idêntico à
perna única, 2 paradas com geometria contínua e `dist_m` acumulado, soma das
métricas, round-trip do contrato, limite de `max_routes` e recusa de pernas
degeneradas; `tests/test_integration.py` fixa o tamanho da superfície curada
(20–30), a existência/unicidade dos nomes e o subconjunto da fachada ampla
(exceção declarada: `load_graph`/`save_graph`). Suíte: **836 testes**.

### Fase 4 — Offline e providers

1. `cmd_plan` e `plan_routes` entram em `offline()` por padrão.
2. `build --elevation` sem rede por padrão; `opentopodata` exige
   `--allow-network`.
3. Separar `elevation.py` em provider de rede / cache / offline (F5/F9).

**Aceite:** `tests/test_cli_offline.py` cobre `build` e `plan` com o guard
ativo; nenhum teste faz rede.

### Fase 5 — Limpeza e fachada

1. Remover símbolos mortos (F7) e aliases sem consumidor (F4), com atualização
   de `api.md`.
2. Dividir `cli.py` em `commands/` e `snapping.py` em módulos coesos.
3. Quebrar a dependência `intelligence → evaluation` (F8).

**Aceite:** `api.__all__` reduzido de forma deliberada e documentada; 809
testes verdes; nenhuma mudança de física/busca.

### Fase 6 — Porte para o `toporide`

1. Publicar o contrato v1 (sem adaptador Python → TypeScript: o porte é do
   próprio diretório para TypeScript, não uma camada de compatibilidade).
2. Portar o motor — **A\* multiobjetivo**, física e avaliação — para TypeScript.
   O algoritmo atual do `toporide` é **descartado**, não adaptado: o núcleo
   passa a ser o deste diretório.
3. Comparações com o motor descartado são **diagnóstico** (explicam divergências
   de modelo/parâmetros/snapping), não um critério de paridade a preservar.

**Aceite:** o contrato v1 é consumido pelo porte e o worker usa o novo motor;
divergências em relação ao motor antigo são explicadas, não escondidas.

---

## 7. Testes de aceitação (mínimos)

| Teste | Verifica |
| --- | --- |
| `test_profile_single_source` | `objectives_for(id)` == `route_profile_for(id).objectives` para todos os ids |
| `test_route_plan_contract_required_keys` | `format`, `version`, `request`, `routes`, `recommended_route_id` |
| `test_route_plan_rejects_unknown_version` | parser falha em versão ≠ 1 |
| `test_route_geometry_monotonic_distance` | `dist_m` não decresce e começa em 0 |
| `test_route_geometry_endpoints` | primeiro ponto ≈ origem; último ≈ destino |
| `test_route_metrics_source_declared` | todo `metrics.source` ∈ `{search, assessment}` |
| `test_no_silent_stringification` | serialização lança em valor não JSON |
| `test_multi_stop_contiguous_geometry` | 2 paradas → geometria contínua e métricas somadas |
| `test_plan_offline_guard_active` | `plan_routes` falha sob `offline()` se tentar rede |
| `test_api_integration_surface` | superfície curada é subconjunto estável e documentado |

---

## 8. Riscos e não-objetivos

- **Não** alterar física, `MultiObjectiveAStar` ou o baseline de busca nesta
  consolidação. Qualquer mudança de resultado deve ser tratada como regressão
  separada.
- **Não** preencher `hrZoneDist`/`arterialM` com estimativas: são campos do
  modelo do `toporide`; a engine declara ausência.
- **Não** remover a fachada ampla de imediato: ela é compatibilidade. A
  limpeza é fase 5, com contagem pública documentada.
- **Risco de packaging:** o wheel não foi verificado por falta de
  `setuptools`; a fase 0 precisa confirmar em ambiente limpo.
- **Risco de integração:** o `toporide` tem custo kinodinâmico e ML próprios
  (ADRs 018/022); o porte deve comparar, não presumir equivalência.

---

## Apêndice A — Evidências reproduzíveis

```powershell
# contagem de módulos/arestas/ciclos e símbolos mortos
python <script-de-auditoria>            # AST em src/; ver método

# testes
python -m pytest --collect-only -q      # 809 testes

# superfície pública
python -c "import sys; sys.path.insert(0,'src'); import api; print(len(api.__all__))"
# 315

# ciclo conceitual
Select-String -Path src\intelligence\classification.py -Pattern "from evaluation"
# 36: from evaluation.assessment import AssessmentOutcome, RouteAssessment
Select-String -Path src\evaluation\assessment.py -Pattern "from intelligence"
# 65 (TYPE_CHECKING), 305 (import local)

# referências mortas
Select-String -Path src\**\*.py,tests\*.py,scripts\*.py -Pattern "ConstantElevation|point_at_fraction|format_bbox|surface_of"

# empacotamento
python -m pip wheel . --no-deps --no-build-isolation -w <dir>
# backend-unavailable: setuptools ausente neste ambiente
```

## Apêndice B — Arquivos citados

- `src/api.py`, `src/cli.py`, `src/planning.py`, `src/multi_leg.py`,
  `src/integration.py`, `src/offline.py`, `src/elevation.py`, `src/snapping.py`,
  `src/geo.py`
- `src/search/{objectives,profiles,evaluator,ranking,selection,pipeline,route,state,candidates}.py`
- `src/evaluation/{assessment,safety}.py`, `src/intelligence/{classification,intensity}.py`
- `src/graph/{model,serialize}.py`, `src/osm/build.py`, `src/physical/{energy,models}.py`
- `pyproject.toml`, `pytest.ini`, `docs/reference/{api.md,quickstart.md,README.md}`
- `toporide/src/domain/types.ts`, `toporide/src/application/{routePipeline,mergeLegRoutes}.ts`,
  `toporide/src/infrastructure/worker/routeWorker.ts`
