# ADR-027 — Auditoria da Camada 04 (Intelligence, ML & Prediction) — operação offline

- **Status:** entregável da **fase 1** do [ADR-027](027-intelligence-ml-prediction.md).
  (A **fase 2** — implementação da Camada 04 e do caminho offline — foi concluída
  depois; ver `CHANGELOG.md` `[Unreleased]`.) À época da auditoria, **nenhum
  código de produção foi alterado**; este documento foi o **portão de revisão**
  antes da implementação de ML.
- **Data:** 2026-11-11
- **Escopo:** auditoria dos módulos de ML, fisiologia, persistência e avaliação
  de rotas da Camada 04, **sob o requisito arquitetural obrigatório de operação
  integralmente offline**; inventário de contratos; auditoria de dependência de
  rede; matriz de compatibilidade; análise de lacunas; baseline; riscos.
- **Base auditada:** motor `0.18.0`, suíte com **509 testes**
  (`PYTHONPATH=src python -m pytest`).

> **Regra desta fase:** auditar e documentar apenas. **Não** modificar código de
> produção, **não** criar modelos fisiológicos fictícios, **não** tratar targets
> sintéticos como observações reais, **não** introduzir parâmetros de calibração
> arbitrários e **não** introduzir dependência de rede na execução.

---

## Escopo e método

- **Objetivo:** verificar o que a Camada 04 **já entrega** (contratos de features,
  previsão e fronteira de inferência) e **isolar as lacunas** para estruturar um
  pipeline de ML que funcione **integralmente offline, no dispositivo do
  cliente**, sem internet, nuvem ou API externa.
- **Não objetivo:** reimplementar a física nem o mecanismo de busca; projetar um
  novo algoritmo de roteamento; treinar um modelo agora.
- **Preservação obrigatória:** os contratos de `EffortFeatures`
  (`physical/effort.py`), `PhysiologicalPrediction` e `predict_effort`
  (`physiology/` + `ml/`) são **fixos**, bem como a arquitetura das Camadas
  01–03 (grafo, física, busca/circulação). A auditoria descreve o estado que eles
  permitem, não os substitui.
- **Método:** o mesmo das auditorias física ([`025-auditoria-fisica.md`](025-auditoria-fisica.md))
  e de busca ([`026-auditoria-busca.md`](026-auditoria-busca.md)) — inventário,
  contrato × código, invariantes × evidência e lacunas declaradas —, acrescido de
  uma **auditoria de dependência de rede**.

### Classificação de componentes

Cada componente é classificado como **PRESERVAR** (mantém-se como está),
**ADAPTAR** (evolui sem trocar os contratos), **COMPLEMENTAR** (adiciona contrato
que falta) ou **SUBSTITUIR** (troca justificada). Ver a matriz na §9.

### Dois sentidos de "avaliação" (não confundir)

- **avaliação de rota** (Camada 05, `src/evaluation/`): usa o **simulador
  completo** para medir a física e produzir `REJECTED`/`PHYSICAL_ONLY`/
  `PHYSIOLOGICAL` — **existe**;
- **avaliação de modelo** (decisão 5 deste ADR): mede a **qualidade preditiva**
  de um modelo (erro, calibração, generalização) — **não existe**.

---

## 1. Requisito mandatório — operação integralmente offline

O aplicativo **deve** executar, **localmente, sem rede**, todas as operações de:
roteamento, simulação física, inferência de ML e classificação de rotas. As
fontes geográficas são **OSM.PBF e dados altimétricos locais**; os **modelos de
ML** também são locais. Não há download automático de modelos ou dados durante a
execução.

### 1.1 Auditoria de dependência de rede

Grep por `urllib|requests|http|socket|urlopen|endpoint|https://` em `src/`:
**toda** a rede está confinada à **preparação de dados da Camada 01**.

| Componente | Faz rede? | Endpoint / mecanismo | Alternativa local |
| --- | :---: | --- | --- |
| `osm/overpass.py` (`OverpassClient`) | **Removido** | — (ingestão online eliminada) | `osm/pbf.py` (`read_osm_pbf`, `.osm.pbf` local) |
| `elevation.py` (`OpenTopoDataElevation`) | **Sim** | `https://api.opentopodata.org/v1` via `urllib.request.urlopen` (L214) | `RasterDemElevation` (GeoTIFF local) |
| `elevation.py` (`RasterDemElevation`) | Não | `rasterio.open` de arquivo local (L259) | — (é a alternativa local) |
| `elevation.py` (`SqliteElevationCache`, `MemoElevation`, `ConstantElevation`, `OsmTagElevation`, `CallableElevation`) | Não | cache em disco / memória / função | — |
| `graph/serialize.py` (`load_json`/`save_json`) | Não | arquivo JSON local | — |
| `physical/**` (física) | Não | — | — |
| `search/**` (busca, circulação, ranking, seleção) | Não | — | — |
| `ml/**`, `physiology/**` (features, contratos, inferência) | Não | — | — |
| `evaluation/**` (avaliação de rota) | Não | — | — |

**Conclusão central:** o **caminho de execução** (carregar grafo local →
simular → buscar → extrair features → prever → classificar) **já é livre de
rede**. A rede existe **apenas** na **geração/preparação do grafo** (Camada 01),
que a norma considera um passo anterior com dados locais (OSM.PBF + DEM local).

> **Atualização.** O `OverpassClient` (`osm/overpass.py`) foi **removido**: a
> geração do grafo passou a ser 100% offline a partir de `.osm.pbf` (`osm.pbf`),
> que lê vias, nós e relações `type=restriction`. A rede remanescente em `src/`
> é só a **preparação de elevação** (`OpenTopoDataElevation`/tiles Terrarium).

### 1.2 Lacunas do requisito offline

| # | Lacuna | Gravidade |
| - | --- | --- |
| O1 | ~~**Não há leitor de OSM.PBF.**~~ **Resolvido:** `osm.pbf` é a fonte offline; a ingestão online via `OverpassClient` foi **removida** | ~~**Alta**~~ |
| O2 | **Não há armazenamento/carregamento de modelo.** Nenhum `load_model`/`save_model`/registro/artefato (grep sem resultados) | **Bloqueante** (ML local) |
| O3 | **Não há versionamento de modelo persistido** (só a string `model_version`) | Alta |
| O4 | **Não há compatibilidade verificável `EffortFeatures` × modelo** no carregamento (o contrato recusa schema no *uso*, via `CONTRACT_INCOMPATIBLE`, mas não há checagem no *carregamento*) | Alta |
| O5 | **Não há trilha "rede desabilitada" nos testes** (nenhum teste que falhe se houver acesso à rede) | **Alta** (exigido) |
| O6 | **Não há modo/guard "offline"** explícito nem política que **proíba** download em execução | Média |
| O7 | **Cache de elevação pode existir, mas o fetch não é bloqueado** quando ausente | Média |

Nenhuma dessas lacunas foi introduzida agora: são ausências de infraestrutura,
coerentes com o escopo deliberado do ADR-025 (§11). Elas são **pré-requisito** do
ML offline.

---

## 2. Inventário técnico — Camada 04 e fronteiras

### 2.1 Mapa de módulos

A Camada 04 distribui-se em **três lugares** por desenho (ADR-025, decisão 07):
features na saída da física, contratos fisiológicos num pacote próprio e
inferência em `ml/`.

| Módulo | Papel | Grupo |
| --- | --- | --- |
| `physical/effort.py` | `EffortFeatures` (frozen, versionado), `extract_effort_features`, `EFFORT_FEATURES_VERSION` | Features (saída da física) |
| `physiology/scales.py` | `BorgScale` (`RPE_6_20` padrão, `CR10`), `DEFAULT_BORG_SCALE`, `contains`/`clamp`/`require_valid` | Contrato fisiológico |
| `physiology/zones.py` | `HeartRateZoneDefinition`, `HeartRateZoneModel`, `validate_distribution`, `is_normalized`, `DISTRIBUTION_TOLERANCE` | Contrato fisiológico |
| `physiology/metabolic.py` | `MetabolicParameters` (eficiência + `version` + `provenance`), `metabolic_energy_j` | Contrato fisiológico |
| `physiology/prediction.py` | `PHYSIOLOGY_VERSION`, `PredictionStatus`, `RiderProfile`, `PredictionContext`, `PhysiologicalPrediction` | Contrato fisiológico |
| `physiology/_validation.py` | `finite`, `non_negative`, `positive`, `unit_interval` | Validação compartilhada |
| `ml/predictor.py` | `EffortPredictor` (protocolo), `UnavailablePredictor`, `MetabolicEnergyPredictor`, `predict_effort`, `missing_inputs`, `SUPPORTED_EFFORT_SCHEMA_VERSIONS` | Inferência (fronteira) |
| `ml/__init__.py`, `physiology/__init__.py` | Fachadas | Fachada |

**Componentes adjacentes que a Camada 04 consome ou serve:**

| Módulo | Papel | Grupo |
| --- | --- | --- |
| `evaluation/assessment.py` | `RouteAssessment`, `AssessmentOutcome`, `assess_route`/`assess_candidate`/`assess_candidates` | Avaliação de rota (Camada 05) |
| `search/ranking.py`, `evaluator.py`, `selection.py`, `explanation.py`, `profiles.py` | Ranking/seleção por **preferência** (métricas físicas) | Ranking |
| `search/pipeline.py` | `search_profile`, `ProfileSearchRequest/Response` | Orquestração |
| `search/circulation.py` | `validate_route`, `RouteValidation`, `Verdict` | Guardrails (Camada 03) |
| `graph/serialize.py` | `graph_to_dict`/`graph_from_dict`, `save_json`/`load_json`, `FORMAT`/`VERSION=4` | Persistência do **grafo** |
| `physical/energy_model.py` | `EnergyModel`, `GenericEnergyConfig`, níveis `generic`/`personalized`/**`calibrated`**, `BatteryConfig` | Energia **elétrica** (distinta do metabólico) |

**Observação de fronteira.** `evaluation/` **não** é avaliação de modelo de ML; é
avaliação **de rota**. Não há módulo de treinamento, conjunto de dados,
observação, **armazenamento de modelo** ou registro.

### 2.2 Fronteira com as outras camadas

```
Camada 01 (Data Model)  — DADOS LOCAIS (OSM.PBF + DEM local)   [preparação]
        │  graph.serialize.load_json  (sem rede no uso)
        ▼
Camada 02 (Physical Model)
   PhysicalTraversal.traverse_path ─► TraversalPath ─► EffortFeatures
        │                                      │
        ▼                                      │
Camada 03 (Optimization)                       │
   A* Pareto → Route/RouteCandidate            │
   validate_route (circulação)                 │
        └───────────────┬──────────────────────┘
                        ▼
              Camada 04 (Intelligence)  — TUDO LOCAL
   EffortFeatures → predict_effort(modelo local) → PhysiologicalPrediction
        │
        ▼
        Camada 05 (Evaluation & Guardrails)  — LOCAL
   assess_route → RouteAssessment → REJECTED / PHYSICAL_ONLY / PHYSIOLOGICAL
        │
        ▼
   classificação por preferência (search/) — SEPARADA da predição
```

- A Camada 04 **não** calcula física: recebe `EffortFeatures` produzidas pelo
  **simulador completo**, nunca pelo estimador aproximado.
- A predição **não** altera geometria, restrições, fronteira nem ordem física
  (`ml/predictor.py` L98-L101, L204-L205).
- Rotas tradicionais e rotas geradas por ML produzem o **mesmo** `EffortFeatures`
  (o extrator depende apenas do resultado físico) — coberto por teste de
  independência (`tests/test_effort_features.py`, AST: sem `search`/`ml`).

### 2.3 Grafo de dependências (Camada 04)

- `physical/effort.py` → `physical.models`, `physical._validation`. **Não**
  importa `search`, `ml` nem `physiology`.
- `physiology/*` → `physiology._validation` (stdlib). Sem física, sem busca.
- `ml/predictor.py` → `physical.effort`, `physiology`, stdlib. **Não** importa
  `search` — a fronteira é estável para qualquer gerador de rota.
- `evaluation/assessment.py` → `graph.model`, `ml.predictor`, `physical.*`,
  `physiology`, `search.candidates`, `search.circulation`, `search.route`. Usa o
  **simulador completo**, nunca `search.approximate` (teste AST).

### 2.4 Parâmetros numéricos atuais

| Parâmetro | Default | Onde | Natureza |
| --- | --- | --- | --- |
| `EFFORT_FEATURES_VERSION` | `"1"` | `physical/effort.py` | **Contrato** (versão de schema) |
| `PHYSIOLOGY_VERSION` | `"1"` | `physiology/prediction.py` | Contrato |
| `DEFAULT_BORG_SCALE` | `RPE_6_20` (`6–20`) | `physiology/scales.py` | **Contrato** (escala explícita) |
| `_BORG_BOUNDS` | `6–20` / `0–10` | `physiology/scales.py` | Contrato |
| `DISTRIBUTION_TOLERANCE` | `1e-6` | `physiology/zones.py` | Tolerância numérica |
| `UNAVAILABLE_MODEL_VERSION` | `"unavailable"` | `ml/predictor.py` | **Contrato** (identificador) |
| `MetabolicParameters.gross_efficiency` | — (sem default) | `physiology/metabolic.py` | **Calibração** (exige `version` + `provenance`) |
| Tolerâncias lexicográficas | 5–10 s / 10–20 m / 1–2 m / 1000 J | `search/profiles.py` | **Preferência** (não calibrada) |
| `ScoreReference` | 1800 s / 10 000 m / 100 m | `search/profiles.py` | Preferência |
| `BalancedWeights` | 1/3 cada | `search/profiles.py` | Preferência |

**Nenhum** parâmetro fisiológico calibrado existe. `MetabolicParameters` recusa
defaults de propósito: sem calibração explícita, a energia metabólica é `None`.

### 2.5 Testes existentes (Camada 04)

| Arquivo | Testes | Cobre |
| --- | --- | --- |
| `tests/test_effort_features.py` | 19 | contrato, extração, ausência explícita, propagação, independência do gerador |
| `tests/test_physiology.py` | 36 | escalas/limites, zonas/distribuição, metabólico (`None` vs presente), invariantes de previsão |
| `tests/test_ml_predictor.py` | 18 | protocolo, ausência explícita, estados de indisponibilidade, integração física→features→previsão |
| `tests/test_evaluation_assessment.py` | 20 | avaliação de rota, classificação, ausência de modelo, não uso do estimador |

**93 testes**; suíte total **509**. **Lacuna:** nada cobre **qualidade preditiva**
(não há observações) nem **operação sem rede** (lacuna O5).

---

## 3. Decisão 1 — Training Data

### 3.1 Como está hoje

Busca por `training|dataset|observa[ct]|pseudonym|anonym|pickle|joblib` em todo o
`src/`: **nenhum** módulo de treinamento, conjunto de dados ou observação.
Ocorrências alheias: `surface_observation` (superfície **OSM**), `dataset`/`_dataset`
(**raster** de elevação), `drivetrain_efficiency` (parâmetro **físico**).

Não há fonte de observações, identidade pseudonimizada, esquema de registro,
metadados de qualidade/proveniência de observações nem alvo observado. O padrão
de **proveniência versionada** já existe para calibração
(`MetabolicParameters.version`/`provenance`) e para o grafo
(`graph/serialize.py` `FORMAT`+`VERSION=4`) e deve ser **reutilizado**.

### 3.2 Contrato × código

| Requisito do ADR-027 | Código atual | Situação |
| --- | --- | --- |
| Dados treináveis disponíveis **localmente** | — | **Ausente** |
| Observações reais de esforço | — | **Ausente** |
| Identidade pseudonimizada do ciclista | `RiderProfile` (sem identidade) | **Ausente** |
| Versionamento + proveniência do dado | padrão em `MetabolicParameters`/`serialize` | **Padrão existe, não aplicado** |
| Alvo disponível para treinar | — | **Ausente** |

### 3.3 Invariantes

- *Treino offline:* o conjunto de dados vive em **armazenamento local** (arquivo
  ou base local); o treino **não** depende de rede.
- *Sem alvo sintético:* derivar RPE de uma fórmula e tratá-lo como observação é
  **proibido**. Sem observação, não há treino.
- *Origem reprodutível:* reutilizar o padrão `version`+`provenance` e a recusa de
  versão desconhecida (`graph/serialize.py`).

---

## 4. Decisão 2 — Prediction Targets

### 4.1 Como está hoje

Contratos de **saída** existem; **alvos observados**, não.

- **Escala Borg explícita:** `BorgScale` (`RPE_6_20` padrão, `CR10`), com limites,
  `contains`/`clamp`/`require_valid`.
- **RPE previsto:** `predicted_rpe` validado contra a escala; só existe com
  `status.is_available`. **Não** há tipo para **RPE observado**.
- **FC:** `RiderProfile` (`max_heart_rate_bpm`, `resting_heart_rate_bpm`,
  `threshold_power_w`, `weight_kg`); `HeartRateZoneModel` com bordas **explícitas
  em bpm** + `definition` + `version`; distribuição validada (≥ 0, soma 1).
- **Zonas individualizadas:** o **modelo** existe; o **método de derivar** as
  bordas de um ciclista **não** — deliberadamente (ADR-025, decisão 8: sem
  percentuais de FC máxima codificados).

### 4.2 Contrato × código

| Alvo | Saída | Observação (entrada de treino) | Situação |
| --- | --- | --- | --- |
| RPE (Borg 6–20 / CR10) | `predicted_rpe` + `borg_scale` | — | **Saída existe; observação ausente** |
| FC por zona | `heart_rate_zone_distribution` + definição | — | **Saída existe; observação ausente** |
| FC pontual | `RiderProfile.max_heart_rate_bpm` | — | **Ausente** |
| Zonas individualizadas | `HeartRateZoneModel` | — | **Modelo existe; derivação ausente** |

### 4.3 Observação

`PhysiologicalPrediction` prevê **RPE** e **distribuição de FC por zona**, mas o
treino supervisionado exige **alvos observados** correspondentes. Sem
observações de RPE e/ou passagem por zona (na **mesma** definição), não há
treino nem validação. O ADR-027 deve fixar os alvos e exigir a observação
correspondente antes do treino.

---

## 5. Decisão 3 — Feature Engineering

### 5.1 Como está hoje

`EffortFeatures` (`physical/effort.py`) é **imutável** (`frozen`) e **versionado**
(`schema_version = "1"`), obtido **exclusivamente** do resultado físico
(`TraversalPath`). Campos: tempo/distância/velocidade; energia (roda/pedivela) e
`mean_power_w`; potência dinâmica (`peak_power_w`, `power_variability` — **`None`**);
terreno; altimetria + qualidade; perdas (assinadas); `battery_energy_j` (None
conv.) e `metabolic_energy_j` (**None**); `result_status` e
`used_min_speed_regularization`.

**Ciclista/contexto** (fora das features): `CyclistModel`, `BicycleModel`,
`Environment`; `RiderProfile` (4 campos); `PredictionContext`
(`temperature_c`, `duration_s`, `notes`).

**Dados ausentes:** `None` + `unavailable_fields`; faltantes detectados por
`predictor.required_inputs`/`missing_inputs` (`ml/predictor.py` L47-L69,
L243-L253). Nada é estimado.

**Vazamento:** por construção, o contrato **não contém alvo**; é função apenas do
resultado físico. Falta a garantia **documentada/testada** de alinhamento
treino/serviço (isso pertence ao processo de treino offline, §10).

### 5.2 Contrato × código

| Requisito | Código atual | Situação |
| --- | --- | --- |
| Features físicas | `EffortFeatures` (determinístico) | **Atende** |
| Features do ciclista | `RiderProfile` (4 campos) | **Parcial** |
| Contexto | `PredictionContext` (3 campos) | **Parcial** |
| Dados ausentes explícitos | `None` + `unavailable_fields` + `missing_inputs` | **Atende** |
| Qualidade propagada | `result_status`/incerteza/regularização | **Atende** |
| Sem vazamento no contrato | nenhum alvo presente | **Atende por construção** |
| Granada fina (pico/variabilidade/zonas de potência) | `None` | **Ausente** (sem série por passo) |
| **Compatibilidade de versão de schema registrada no modelo** | `CONTRACT_INCOMPATIBLE` só no uso | **Parcial** (ver O4) |

### 5.3 Lacuna declarada

`peak_power_w`/`power_variability` permanecem `None` porque a engine **não retém
a série de potência por passo**. O ADR-025 §6 antecipa a extensão **quando houver
granularidade**. Enquanto isso, qualquer feature derivada disso seria
**inventada** — e está proibida.

---

## 6. Decisão 4 — Model Training (e armazenamento local)

### 6.1 Como está hoje

**Não existe treinamento nem persistência de modelo.** Preditores existentes:

- `UnavailablePredictor` (**padrão**): declara `MODEL_NOT_CALIBRATED`; tudo `None`.
- `MetabolicEnergyPredictor`: preenche **apenas** `metabolic_energy_j` a partir de
  `MetabolicParameters` explícitos (fórmula determinística); RPE/FC `None`.

Não há: **baseline**, **modelo candidato**, **pipeline de treino reproduzível**,
**registro/armazenamento de modelo** nem **calibração**. `pyproject.toml` **não**
inclui biblioteca de ML (nenhum `numpy` no núcleo, `torch`, `sklearn`); `numpy`
só aparece como dependência **opcional** de `dem`. O único versionamento é a
string `model_version`.

### 6.2 Contrato × código

| Requisito | Código atual | Situação |
| --- | --- | --- |
| Baseline preditivo | — | **Ausente** |
| Modelo candidato plugável | `EffortPredictor` (Protocol) | **Seam pronto** |
| Treino reproduzível | — | **Ausente** |
| **Armazenamento local do modelo** | — | **Ausente** (O2) |
| **Versionamento persistido + registro** | `model_version` (string) | **Parcial** (O3) |
| **Carregamento offline + checagem de compatibilidade** | — | **Ausente** (O4) |
| Calibração | `confidence` exige `confidence_method` | **Gancho só** |
| **Tratamento de modelo ausente/incompatível/não calibrado** | `PredictionStatus` (5 estados) | **Atende no *uso*; falta no *carregamento*** |

### 6.3 Observação

O **seam está pronto** (`EffortPredictor`): um modelo calibrado futuro implementa
o protocolo **sem tocar** na física nem no roteamento. Falta todo o fluxo
**offline** (dados → treino → calibração → **artefato local versionado**) e um
**registro** ligando `model_version` ao artefato, ao dataset e à versão de
schema de features. O modelo deve ser **carregado do armazenamento local** e
**executado sem internet**.

**Distrato a evitar:** `CALIBRATED_LEVEL`/`PERSONALIZED_LEVEL`
(`physical/energy_model.py`) são níveis de **energia elétrica**, não um modelo
fisiológico calibrado.

---

## 7. Decisão 5 — Evaluation (qualidade preditiva)

### 7.1 Como está hoje

Não há avaliação de **qualidade preditiva** — só de **invariantes de contrato**.
Não existem: validação temporal, validação por ciclista, erro de previsão,
calibração, critérios mínimos. O contrato **prepara** parte disso: `confidence`
exige `confidence_method`; `prediction_interval_s` existe (intervalo temporal,
não de incerteza); RPE/zonas/metabólico são separáveis.

### 7.2 Contrato × código

| Requisito | Código atual | Situação |
| --- | --- | --- |
| Invariantes de contrato | 93 testes da Camada 04 | **Atende** |
| Gancho de confiança | `confidence` exige `confidence_method` | **Seam pronto** |
| Intervalo da previsão | `prediction_interval_s` | **Parcial** (tempo) |
| Validação temporal/por ciclista | — | **Ausente** |
| Erro/calibração/generalização | — | **Ausente** |
| Critérios mínimos de aceitação | — | **Ausente** |

### 7.3 Observação

Sem observações reais (§3), **não há o que medir**. A avaliação de modelo depende
da decisão 1. Os **critérios mínimos** devem ser fixados antes de qualquer treino.

---

## 8. Decisão 6 — Route Ranking (predição × preferência)

### 8.1 Como está hoje

A separação está **arquiteturalmente correta e deve ser preservada**:

- **Ranking é preferência pura** (`search/ranking.py`, `evaluator.py`): lê apenas
  `time_s`, `distance_m`, `ascent_m`, `energy_j`. `LEXICOGRAPHIC` (com tolerância)
  e `WEIGHTED_NORMALIZED`. **Nenhuma** predição entra no ranking/fronteira.
- **Predição não acopla à candidata:** `RouteCandidate` é física + identidade; a
  predição é da Camada 04, aplicada **depois** da seleção física.
- **Classificação de disponibilidade:** `RouteAssessment` →
  `REJECTED`/`PHYSICAL_ONLY`/`PHYSIOLOGICAL` (sem limiar novo).
- **Determinismo:** empates por `(label_id, route_id)` (`ranking.py` L201-L203).
- **Explicação compara de verdade** (`explanation.py` L1-L7).

### 8.2 Contrato × código

| Requisito | Código atual | Situação |
| --- | --- | --- |
| Predição **separada** da preferência | ranking lê só métricas físicas | **Atende** |
| Predição **não** altera fronteira/ordem | `predict_effort` pós-seleção | **Atende** |
| Critérios **auditáveis** que unam predição + preferência | — | **Ausente** |
| Tratamento de previsão **indisponível** na classificação | predição ignorada no ranking; `PHYSICAL_ONLY` explicado | **Lacuna declarada** |
| Classificação explícita | `AssessmentOutcome` (3 estados) | **Atende (parcial)** |

### 8.3 Lacuna declarada

Falta decidir **como** a previsão apoia a classificação **sem** contaminar o
ranking físico: (1) rotas `PHYSIOLOGICAL` e `PHYSICAL_ONLY` não são diferenciadas
no ranking; (2) não há critério auditável que combine "esforço previsto" com
"preferência"; (3) não há política explícita para previsão **indisponível** (e
hoje **sempre** está, por falta de modelo calibrado).

---

## 9. Matriz de compatibilidade

Classificação: **P** = PRESERVAR, **A** = ADAPTAR, **C** = COMPLEMENTAR,
**S** = SUBSTITUIR (nenhum item em S).

| Componente | Classe | Ação mínima proposta | Justificativa |
| --- | :---: | --- | --- |
| `physical/effort.py` (`EffortFeatures`, extrator) | **P** | não tocar o contrato; usar `schema_version` | Contrato versionado, determinístico, independente do gerador |
| `physiology/scales.py`, `zones.py`, `metabolic.py`, `prediction.py` | **P** | — | Escalas/zonas/estado explícitos; "nunca medição" |
| `ml/predictor.py` (`EffortPredictor`, `predict_effort`, `UnavailablePredictor`, `MetabolicEnergyPredictor`) | **P** | implementar preditor calibrado **sobre** o protocolo | Seam pronto; não substituir |
| `evaluation/assessment.py` | **P** | estender classificação só com sinal existente | Camada 05 já entregue |
| `search/ranking.py`, `evaluator.py`, `selection.py`, `explanation.py`, `profiles.py` | **P** | — | Preferência separada da física; não acoplar predição |
| `graph/serialize.py` (persistência do grafo) | **P** | reutilizar o padrão versionado para artefatos locais | Já versiona e recusa versão desconhecida |
| `physical/energy_model.py` (níveis de energia) | **P** (vocabulário distinto) | — | Energia **elétrica**, não fisiológica |
| `elevation.OpenTopoDataElevation` | **P** (fora da execução) | manter como **preparação** (Camada 01) | Rede só na geração de elevação (o `OverpassClient` foi removido) |
| **Leitor de OSM.PBF local** | **C** | novo (lacuna O1) | Requisito: fonte geográfica local |
| **Armazenamento/registro de modelo local + versionamento + carregamento offline + checagem de compatibilidade** | **C** | novo (lacunas O2–O4) | Requisito: modelos locais versionados |
| **Observações reais + identidade pseudonimizada + alvos** | **C** | novo (reutilizar padrão `version`+`provenance`) | Decisões 1–2 |
| **Fluxo de treino offline reproduzível** | **C** | novo | Decisão 4 |
| **Avaliação de modelo (erro/calibração/generalização)** | **C** | novo; critérios mínimos antes de treinar | Decisão 5 |
| **Classificação predição × preferência** | **C** | novo critério auditável + política para ausência | Decisão 6 |
| **Modo/guard offline + testes com rede desabilitada** | **C** | novo (lacunas O5–O6) | Requisito mandatório |

**Nenhum componente exige SUBSTITUIR.** Os contratos existentes são
reaproveitáveis; as lacunas são **aditivas**.

---

## 10. Análise de lacunas (gap analysis)

| # | Lacuna | Decisão | Gravidade |
| - | --- | --- | --- |
| L1 | Nenhuma fonte/observação real local | 1 | **Bloqueante** |
| L2 | Nenhuma identidade pseudonimizada | 1 | **Bloqueante** |
| L3 | Nenhum alvo observado (RPE/FC) | 2 | **Bloqueante** |
| L4 | Método de derivação individualizada de zonas não definido | 2 | Alta |
| L5 | Features finas (pico/variabilidade) indisponíveis | 3 | Média (limitação da engine) |
| L6 | Sem garantia documentada de alinhamento treino/serviço | 3 | Alta |
| L7 | Nenhum baseline nem modelo candidato | 4 | **Bloqueante** |
| L8 | Nenhum pipeline de treino reproduzível | 4 | **Bloqueante** |
| L9 | Nenhum armazenamento/carregamento/registro de modelo local | 4 / requisito offline | **Bloqueante** |
| L10 | Sem checagem de compatibilidade features×modelo no carregamento | 4 / requisito offline | Alta |
| L11 | Nenhuma avaliação de qualidade preditiva nem critérios mínimos | 5 | **Bloqueante** |
| L12 | Sem critério auditável que combine predição × preferência | 6 | Alta |
| L13 | Sem política explícita para previsão indisponível | 6 | Alta |
| L14 | **Sem leitor de OSM.PBF local** | requisito offline | Alta |
| L15 | **Sem modo offline/guard nem testes com rede desabilitada** | requisito offline | **Alta** |

### 10.1 Anti-padrões proibidos

1. **Modelo fisiológico fictício** — inventar Borg/FC para "demonstrar" o fluxo.
2. **Target sintético como observação** — derivar RPE de uma fórmula e tratá-lo
   como dado real.
3. **Parâmetros de calibração arbitrários** — eficiência, %FCmáx ou thresholds
   sem proveniência/`version`/dados.
4. **Vazamento** — usar o alvo (ou derivado) como feature, ou avaliar no treino.
5. **Previsão como medição** — o contrato proíbe (`is_measurement=False`).
6. **Rede na execução** — qualquer download em tempo de rota/inferência; modelo
   ou dado buscado remotamente durante o uso.

---

## 11. Cenários de referência (baseline atual)

Não existe baseline de ML. O baseline relevante é o **comportamento honesto
atual**, que o ADR-027 deve preservar como não-regressão:

| Cenário | Comportamento esperado (0.18.0) | Evidência |
| --- | --- | --- |
| Sem preditor | `UNAVAILABLE`, tudo `None` | `test_ml_predictor.py` |
| Preditor padrão | `MODEL_NOT_CALIBRATED`, tudo `None` | `test_ml_predictor.py` |
| `EffortFeatures` de schema desconhecido | `CONTRACT_INCOMPATIBLE` | `test_ml_predictor.py` |
| Preditor exige entrada ausente | `INSUFFICIENT_DATA` + `missing_inputs` | `test_ml_predictor.py` |
| Só metabólico | preenche `metabolic_energy_j`; RPE/FC `None` | `test_ml_predictor.py` |
| Rota sem modelo calibrado | `PHYSICAL_ONLY`, motivo exposto | `test_evaluation_assessment.py` |
| Rota violando circulação | `REJECTED` | `test_evaluation_assessment.py` |
| Rotas tradicionais vs ML | **mesmo** `EffortFeatures` | `test_effort_features.py` (AST) |
| Execução (grafo→inferência) | **sem rede** | auditoria §1.1 (grep) |

**Baseline a criar (implementação):** com observações reais (§3), congelar um
**conjunto de avaliação** reproduzível e os **critérios mínimos** (decisão 5), e
um teste que **falhe se houver acesso à rede** na execução (lacuna O5). **Não**
criar baseline com dados sintéticos.

---

## 12. Riscos e questões abertas

| # | Questão | Estado |
| - | --- | --- |
| 1 | Fonte das observações reais locais e consentimento/anonimização | **Aberta** (decisão 1) |
| 2 | Formato de identidade pseudonimizada estável entre sessões | **Aberta** (decisão 1) |
| 3 | Alvos a prever e sua observabilidade | **Aberta** (decisão 2) |
| 4 | Derivação de zonas individualizadas sem percentual implícito | **Aberta** (decisão 2) |
| 5 | Séries por passo (pico/variabilidade) entrarão na engine? | **Aberta** (decisão 3; ADR-025 §6) |
| 6 | Modelo candidato e baseline preditivo | **Aberta** (decisão 4) |
| 7 | **Formato de artefato de modelo local e carregamento sem rede** | **Aberta** (decisão 4 / offline) |
| 8 | **Compatibilidade features×modelo checada no carregamento** | **Aberta** (decisão 4) |
| 9 | **Leitor de OSM.PBF local (dependência e formato)** | **Aberta** (offline) |
| 10 | **Como testar "rede desabilitada" de forma determinística** | **Aberta** (offline) |
| 11 | Critérios mínimos de aceitação (erro/calibração) | **Aberta** (decisão 5) |
| 12 | Como combinar predição × preferência sem contaminar o ranking | **Aberta** (decisão 6) |

---

## 13. Conclusão

A Camada 04 **já entrega os contratos** que tornam o ML honesto, plugável e
**offline por natureza**: `EffortFeatures` (imutável, versionado, sem alvo, do
simulador completo); `PhysiologicalPrediction` (nunca medição, escala explícita,
zonas identificadas, estado explícito, confiança condicionada a método);
`predict_effort` + `EffortPredictor` (fronteira guardada, `UnavailablePredictor`
como ausência honesta); `RouteAssessment` (classificação sem limiar novo, **sem**
acoplar predição ao ranking físico). O **caminho de execução não usa rede**.

As lacunas são **aditivas**:
1. **dados** — observações locais, identidade pseudonimizada, alvos (§3–§4);
2. **treino e persistência** — baseline, modelo candidato, pipeline reproduzível,
   **artefato local versionado + carregamento offline + compatibilidade** (§6, O2–O4);
3. **avaliação de modelo** — erro/calibração/validação temporal e por ciclista,
   critérios mínimos (§7);
4. **classificação** — critério auditável predição × preferência, com política
   para ausência (§8);
5. **requisito offline** — leitor de OSM.PBF local, modo/guard offline e testes
   com rede desabilitada (§1.2, O1/O5/O6).

O eixo do ADR-027 é **dois processos** — **treinamento offline** (em
desenvolvimento ou, futuramente, no dispositivo) e **inferência local offline** —
ambos usando o **mesmo simulador físico** e os **mesmos contratos**. O ADR-027
formaliza as seis decisões e o requisito mandatório com base nesta evidência,
**sem** criar modelo fictício, target sintético ou parâmetro de calibração
arbitrário, e **sem** introduzir rede na execução.
