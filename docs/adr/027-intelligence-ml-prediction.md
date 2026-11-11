# ADR-027 — Camada 04: Intelligence, ML & Prediction

- **Status:** aceito. Fase 1 (auditoria + ADR) e fase 2 (implementação da
  Camada 04 e do **caminho de dados offline**) concluídas — ver
  `CHANGELOG.md` `[Unreleased]`.
- **Data:** 2026-11-11
- **Contexto:** Camada 04 (Intelligence) do projeto `roteamento com ml`. A
  Camada 01 fornece o grafo (ADR-024), a Camada 02 a simulação física (ADR-025) e
  a Camada 03 a busca Pareto + circulação (ADR-026). A Camada 04 transforma o
  **resultado físico** de uma rota em uma **previsão fisiológica** e apoia a
  **classificação** da rota.
- **Depende de:** [ADR-024](024-modelo-canonico-de-grafo-e-ambiente.md) (Camada
  01), [ADR-025](025-modelo-fisico-e-dinamica-do-ciclista.md) (decisões 3, 7 e
  8), [ADR-026](026-optimization-custo-e-busca.md) (§4 circulação, §5 integração
  com a Camada 04).
- **Não substitui:** os contratos de `EffortFeatures`, `PhysiologicalPrediction`
  e `predict_effort`, que são **preservados**.
- **Requisito mandatório:** operação **integralmente offline** no dispositivo do
  cliente (ver §0) — treinamento offline e inferência local, ambos sem rede.

> **Regra de implementação (§5):** implementar por incremento, testar cada
> incremento, rodar a suíte a cada passo. **Nenhuma** decisão abaixo autoriza
> criar modelo fisiológico fictício, tratar target sintético como observação ou
> introduzir parâmetro de calibração arbitrário. Os contratos de
> `EffortFeatures`, `PhysiologicalPrediction` e `predict_effort` permanecem
> **inalterados**.

---

## 0. Requisito arquitetural obrigatório — operação integralmente offline

O aplicativo **deve** executar, **localmente, na máquina do cliente, sem
internet, sem nuvem e sem API externa**, todas as operações de:

- roteamento (busca de rotas candidatas a partir do **grafo local**);
- simulação física (Trilha física → `EffortFeatures`);
- inferência de ML (Borg/RPE e zonas cardíacas); e
- classificação de rotas (por resultados e preferências do ciclista).

As **fontes geográficas** são dados **locais** — arquivo **OSM.PBF** e dados
**altimétricos locais** — e os **modelos de ML** ficam **armazenados localmente**.
**Nenhum** download automático de modelos ou dados ocorre durante a execução.

Este requisito é **mandatório** e **vinculante**: qualquer decisão abaixo que
venha a tocar o caminho de execução deve permanecer compatível com ele.

### 0.1 Dois processos (ambos offline)

A distinção relevante **não** é remoto × local, e sim **treino × inferência** —
os dois processos rodam **localmente**:

| # | Processo | Onde roda | Descrição |
| - | --- | --- | --- |
| 1 | **Treinamento offline** | máquina de desenvolvimento **ou**, futuramente, dispositivo do cliente | Treino e calibração a partir de conjuntos de dados históricos **disponíveis localmente** ou previamente preparados |
| 2 | **Inferência local offline** | dispositivo do cliente | Busca gera candidatas do **grafo local** → simulador físico avalia → extrator produz `EffortFeatures` → modelo **carregado do armazenamento local** prevê Borg/RPE e zonas → sistema classifica conforme resultados + preferências |

Nenhuma dessas operações pode depender de conexão de rede. Ver o diagrama
consolidado na §3.

### 0.2 Auditoria de rede (resumo)

A auditoria [`027-auditoria-intelligence.md`](027-auditoria-intelligence.md) §1
mapeia, por `grep`, toda a rede em `src/`: ela está **confinada à preparação de
dados da Camada 01** (`osm/overpass.py` → `OverpassClient`; `elevation.py` →
`OpenTopoDataElevation`). O **caminho de execução** (carregar grafo local →
simular → buscar → extrair features → prever → classificar) **já é livre de
rede**.

### 0.3 Lacunas do requisito offline (a tratar)

| # | Lacuna | Gravidade | Onde |
| - | --- | --- | --- |
| O1 | **Sem leitor de OSM.PBF local** (só ingestão via Overpass/rede) | Alta | Camada 01 / §5 |
| O2 | **Sem armazenamento/carregamento de modelo local** | Bloqueante | Decisão 4 / §5 |
| O3 | **Sem versionamento persistido de modelo** (só a string `model_version`) | Alta | Decisão 4 |
| O4 | **Sem checagem de compatibilidade `EffortFeatures` × modelo no carregamento** | Alta | Decisão 4 |
| O5 | **Sem testes com rede desabilitada** | Alta | §5 §7 |
| O6 | **Sem guard/modo offline** que proíba download em execução | Média | §5 §7 |

---

## 1. Contexto e objetivo

### 1.1 O que já existe

A auditoria ([`027-auditoria-intelligence.md`](027-auditoria-intelligence.md))
mostra que a Camada 04 **já entrega** os contratos e a fronteira de inferência,
mas **não** entrega o aprendizado de máquina propriamente dito:

- **Features** (`physical/effort.py`): `EffortFeatures` imutável e versionado,
  obtido **apenas** do simulador físico completo; sem nenhum alvo; mesmo contrato
  para rotas tradicionais ou geradas por ML.
- **Contrato fisiológico** (`physiology/`): escala Borg explícita, zonas
  cardíacas por definição identificada, parâmetros metabólicos com
  `version`+`provenance`, e `PhysiologicalPrediction` que **nunca** é medição.
- **Inferência** (`ml/predictor.py`): o protocolo `EffortPredictor` e o ponto de
  entrada guardado `predict_effort`; `UnavailablePredictor` (ausência honesta) e
  `MetabolicEnergyPredictor` (só energia metabólica).
- **Avaliação de rota** (`evaluation/assessment.py`): `RouteAssessment` +
  `AssessmentOutcome` (`REJECTED`/`PHYSICAL_ONLY`/`PHYSIOLOGICAL`).

### 1.2 O que falta

Todo o **fluxo de treino offline** (dados reais → treino → calibração →
**artefato de modelo local versionado**), a **avaliação de modelo** (erro,
calibração, generalização, critérios mínimos), o **critério de classificação**
que une predição e preferência, e a **infraestrutura offline** exigida pelo §0:
**armazenamento/carregamento local do modelo**, **checagem de compatibilidade
features×modelo no carregamento**, **leitor de OSM.PBF local**, **guard offline**
e **testes com rede desabilitada**. Não há fonte de observações, identidade
pseudonimizada, alvo observado nem baseline.

### 1.3 Objetivo

Estruturar o aprendizado de máquina para **prever esforço fisiológico** e
**apoiar a classificação de rotas cicláveis**, com **dois processos
independentes**, **integralmente offline** (§0), e **contratos compartilhados**:

1. **Treinamento offline** — a partir de **observações reais** de ciclistas,
   em máquina de desenvolvimento ou, futuramente, no dispositivo do cliente.
2. **Inferência local offline** — a partir de **rotas candidatas simuladas**
   localmente e dos **perfis disponíveis**, com o modelo **carregado do
   armazenamento local**.

Nos **dois** processos, o **mesmo simulador físico** e os **mesmos contratos** de
features/predição são usados, e **nenhuma** etapa acessa a rede. Rotas geradas por
algoritmo tradicional ou por ML produzem o **mesmo** `EffortFeatures`.

### 1.4 Estado atual da suíte (baseline)

`0.18.0`, **509 testes** verdes; **93** deles cobrem a Camada 04 e adjacências
(`test_effort_features.py` 19, `test_physiology.py` 36, `test_ml_predictor.py`
18, `test_evaluation_assessment.py` 20). Nenhum código foi alterado por esta
auditoria.

---

## 2. Decisões

### Decisão 1 — Training Data

**Decisão.** O treino usa **observações reais** de esforço, com **identidade
pseudonimizada** do ciclista, **qualidade** e **proveniência** explícitas e
**alvo observado** presente. Sem fonte real e sem alvo observado, **não** se
treina.

**Justificativa (auditoria §2).** Não existe hoje nenhuma fonte de observação,
identidade nem esquema de registro. Fabricar dados para "destravar" o treino
violaria a proibição de target sintético como observação.

**Contrato (esquema a criar na implementação — não agora).**

- **Unidade de observação:** um **esforço observado** associado a uma rota
  percorrida, com granularidade temporal declarada e o **mesmo** `EffortFeatures`
  recomputável pelo simulador para o trecho correspondente (garantia de
  alinhamento treino/serviço).
- **Local e offline:** o conjunto de dados vive em **armazenamento local**
  (arquivo/base local), preparado previamente ou reunido no dispositivo; o treino
  **não** acessa a rede e **não** baixa dados (§0).
- **Identidade pseudonimizada:** um `cyclist_id` **estável e não reversível**
  (pseudônimo), **sem** dados pessoais. Usado para validação por ciclista
  (decisão 5) e nunca como feature. **Não** usar os campos atuais de
  `RiderProfile` como identidade.
- **Qualidade e proveniência:** cada observação declara origem, dispositivo/mape,
  condições e nível de confiança da medição, no **padrão versionado** já adotado
  por `MetabolicParameters` (`version`+`provenance`) e por
  `graph/serialize.py` (`FORMAT`+`VERSION`). Dado com proveniência desconhecida
  é marcado, não silenciosamente aceito.
- **Versionamento:** o conjunto de dados tem `dataset_version`, imutável por
  versão; um modelo aponta sempre para a versão do conjunto que o treinou
  (decisão 4).
- **Privacidade:** coordenadas sensíveis (origem/destino do domicílio) podem ser
  ofuscadas; retenção e consentimento são pré-requisitos, não opcionais.

### Decisão 2 — Prediction Targets

**Decisão.** Os alvos previstos são, explícita e separadamente: **RPE observado**
na escala Borg identificada (padrão **6–20**), **frequência cardíaca** (pontual
e/ou distribuição de tempo por zona) sob uma **definição de zona
individualizada e identificada**. Cada alvo só existe quando a **observação
correspondente** existe na mesma definição.

**Justificativa (auditoria §3).** Os contratos de **saída** existem
(`predicted_rpe` + `borg_scale`, `heart_rate_zone_distribution` +
`heart_rate_zone_definition`), mas não há **alvo observado**. A escala e a
definição de zonas precisam ser idênticas entre treino e inferência.

**Contrato.**

- **RPE:** sempre acompanhado de `borg_scale`. As observações são registradas na
  escala declarada; converter entre escalas exige uma transformação
  identificada, **não** um fator arbitrário.
- **FC:** as zonas são um `HeartRateZoneModel` com `definition` + `version` e
  bordas **explícitas em bpm**. A **derivação individualizada** (a partir de
  `RiderProfile`: FC máxima, de reserva, limiar etc.) é um método **nomeado e
  versionado** — **proibido** codificar percentuais de FC máxima como número
  escondido (ADR-025, decisão 8).
- **Metabólico:** permanece **separado** (`MetabolicParameters`), preenchido
  **apenas** com parâmetros explícitos; não entra na previsão de RPE/FC.
- **Ausência:** alvo observado ausente ⇒ a observação é **inutilizável** para
  aquele alvo; **nunca** imputada por fórmula e tratada como observação.

### Decisão 3 — Feature Engineering

**Decisão.** As features de treino e de inferência são **as mesmas**:
`EffortFeatures` (versão de schema fixa) + características explícitas do ciclista
(`RiderProfile`) + contexto (`PredictionContext`). Dados ausentes são explícitos
(`None`); nenhuma feature é inventada. É **proibido** introduzir o alvo (ou
derivado dele) como feature.

**Justificativa (auditoria §4).** O contrato já é imutável, versionado,
determinístico e **sem alvo**; as features finas de potência
(`peak_power_w`/`power_variability`) são `None` porque a engine não retém a série
por passo — inferi-las seria inventar.

**Contrato.**

- **Feature set = contrato existente.** Nenhuma feature nova é criada nesta
  decisão. Adicionar granularidade (séries por passo, tempo por faixa de
  potência) **exige** mudança na engine física (ADR-025 §6) e nova versão de
  `EffortFeatures`; enquanto isso, esses campos permanecem `None`.
- **Alinhamento treino/serviço.** Para cada observação, as features são
  obtidas **pelo mesmo extrator** (`extract_effort_features`) sobre a rota
  simulada correspondente — nunca por um caminho alternativo. Divergência de
  versão de schema é **recusada** (`CONTRACT_INCOMPATIBLE`).
- **Prevenção de vazamento.** O contrato não contém alvo; a garantia adicional
  (identidade temporal, ausência de features do "futuro") é requisito do fluxo
  offline e **coberta por teste** quando o fluxo existir.
- **Dados ausentes.** Explicitados por `unavailable_fields` +
  `predictor.required_inputs`/`missing_inputs`; um preditor que exija um campo
  ausente devolve `INSUFFICIENT_DATA` — nunca imputa.

### Decisão 4 — Model Training, armazenamento e carregamento local

**Decisão.** Todo modelo é comparado a um **baseline** antes de ser aceito; o
treino é **reproduzível** (dados + código + semente + versão de features); o
modelo é **versionado**, **calibrado** com método declarado e **persistido como
artefato local** (§0). O artefato é **carregado do armazenamento local** e
**executado sem internet**, com **verificação de compatibilidade**
features×modelo no carregamento. Modelos **ausentes, incompatíveis ou não
calibrados** são tratados de forma **explícita e conservadora**. Nenhum
parâmetro fisiológico arbitrário é introduzido; **nenhum** download automático de
modelo ocorre em execução.

**Justificativa (auditoria §6).** Só existem `UnavailablePredictor` (ausência) e
`MetabolicEnergyPredictor` (fórmula determinística); não há baseline, pipeline,
registro, **armazenamento/carregamento local** nem calibração. O seam
`EffortPredictor` está pronto. A auditoria de rede (§0.2) confirma que a execução
já é livre de rede, faltando apenas a infraestrutura de modelo local (lacunas
O2–O4).

**Contrato.**

- **Baseline obrigatório.** Antes de qualquer modelo, estabelecer um baseline
  simples e **auditável** (ex.: preditor constante ou regressão sobre uma única
  feature física), com proveniência. Um modelo só é aceito se **superar** o
  baseline na avaliação (decisão 5).
- **Modelo candidato.** Implementa `EffortPredictor` (`model_version` +
  `predict`), **sem** tocar em física nem roteamento. Pode declarar
  `required_inputs`.
- **Reprodutibilidade.** Treino determinístico dado (dataset_version, código,
  semente); sem dependência de estado oculto. Bibliotecas de ML, se usadas,
  entram como dependência explícita e **local** (hoje o núcleo não tem nenhuma).
- **Artefato local versionado (O2, O3).** O modelo é **persistido em
  armazenamento local** (formato próprio do projeto, análogo ao padrão versionado
  de `graph/serialize.py` com `FORMAT`+`VERSION`), com um **registro** que liga a
  `model_version` a: `dataset_version`, versão de **schema de features**
  (`EffortFeatures.schema_version`), escala/definição de alvo, método de
  calibração e condições de treino. O registro e o artefato são **locais**.
- **Carregamento offline + compatibilidade (O4).** O carregamento lê o artefato
  **do disco**, **sem rede**, e **verifica compatibilidade** antes de usar:
  - `EffortFeatures.schema_version` do modelo × versão do extrator em uso;
  - versão de schema/contrato do artefato × versão suportada pelo carregador.
  Incompatibilidade **não** é resolvida por coerção silenciosa: recusa-se o
  carregamento e o estado resultante é explícito.
- **Tratamento de ausência/erro (conservador).** Reutiliza `PredictionStatus` e
  o comportamento já testado do `predict_effort`:
  - **modelo ausente** → preditor padrão `UnavailablePredictor` (`UNAVAILABLE`);
  - **modelo não calibrado** → `MODEL_NOT_CALIBRATED`;
  - **schema de features incompatível** → `CONTRACT_INCOMPATIBLE`;
  - **entrada exigida ausente** → `INSUFFICIENT_DATA` + `missing_inputs`.
  Em qualquer um dos casos, RPE/FC permanecem `None` — **nunca** imputados.
- **Calibração.** `confidence` só é numérica com `confidence_method` (contrato
  já existente). Calibração de RPE/FC/metabólico exige **dados** e **método**;
  nenhum coeficiente é escolhido por conveniência.
- **Honestidade.** Até existir modelo calibrado **local** compatível, o preditor
  padrão permanece `UnavailablePredictor` (`MODEL_NOT_CALIBRATED`); nada é
  preenchido por antecipação e nada é baixado para "destravar" a inferência.

### Decisão 5 — Evaluation

**Decisão.** A qualidade preditiva é medida em **conjunto separado**, com
**validação temporal** e **validação por ciclista**, reportando **erro**,
**calibração** e **incerteza**, contra **critérios mínimos de aceitação**
definidos **antes** do treino.

**Justificativa (auditoria §6).** A suíte atual valida **invariantes de
contrato**, não qualidade preditiva. "Avaliação" de rota (Camada 05) **não** é
avaliação de modelo. Sem observações reais (decisão 1), não há o que medir.

**Contrato.**

- **Separação de conjuntos.** Treino/validação/teste disjuntos; por padrão,
  **temporal** (teste no futuro do treino) e, adicionalmente, **por ciclista**
  (ciclistas de teste não vistos no treino). Todo resultado reporta o esquema.
- **Métricas.** Erro de RPE (escala declarada) e de FC; **calibração**
  (observado × previsto, viés); **incerteza** (quando declarada). O conjunto de
  métricas é fixado antes do treino.
- **Generalização.** Reportar desempenho por subgrupo (ex.: por ciclista, por
  tipo de terreno) para não esconder falhas em cauda.
- **Critérios mínimos.** Definidos antes do treino (limiares de erro/viés e
  cobertura). Um modelo só é "aceito" se satisfazê-los; caso contrário, a
  classificação permanece `PHYSICAL_ONLY`.
- **Sem vazamento.** Teste nunca participa de seleção/tuning; avaliação nunca
  usa dados de treino como se fossem observações novas.

### Decisão 6 — Route Ranking

**Decisão.** A **previsão fisiológica** (o que o modelo diz) permanece
**separada** da **classificação por preferência** (o que o ciclista quer). A
classificação usa **critérios auditáveis**; quando a previsão está
**indisponível**, aplica-se uma política **conservadora e explícita** — nunca se
inventa esforço nem se altera a ordem física.

**Justificativa (auditoria §7).** O ranking (`search/ranking.py`,
`evaluator.py`, `selection.py`) lê **apenas** métricas físicas; a predição é
aplicada depois, sem contaminar a fronteira. Falta apenas o critério que une os
dois planos e a política para ausência.

**Contrato.**

- **Ranking físico intocado.** A fronteira Pareto, o ranking por perfil e a
  diversidade continuam sobre métricas físicas. A predição **não** entra na
  fronteira nem reordena as candidatas.
- **Classificação auditável.** A classificação final combina, de forma
  explícita e reproduzível: (a) legalidade/circulação (`RouteAssessment.circulation`);
  (b) disponibilidade da previsão (`PredictionStatus`); (c) preferência do
  perfil. Cada parcela é reportável.
- **Previsão indisponível.** Enquanto não houver modelo calibrado, a
  classificação é `PHYSICAL_ONLY` com motivo explícito
  (`physiology_<status>`); a rota continua sendo mostrada com suas métricas
  físicas. A ausência **não** vira valor.
- **Sem limiar novo escondido.** Nenhum limiar de esforço é introduzido sem
  dado e sem registro; qualquer limiar de preferência já existente permanece
  declarado como preferência (auditoria §1.4), não como fisiologia.

---

## 3. Arquitetura lógica — dois processos, ambos locais (offline)

```
        ┌──────────────── TREINAMENTO OFFLINE (local; dev ou dispositivo) ──────────────┐
        │  Conjunto de dados histórico LOCAL (sem rede, sem download)                    │
        │      │  observações reais (esforço + FC), cyclist_id pseudonimizado,           │
        │      │  qualidade/proveniência, dataset_version                                 │
        │      ▼                                                                        │
        │  Simulador físico completo ─► EffortFeatures (MESMO contrato)                  │
        │      │                                                                        │
        │      ▼                                                                        │
        │  Baseline ─► Modelo candidato (EffortPredictor)                                │
        │      │            │                                                           │
        │      │            ▼                                                           │
        │      │   Avaliação (temporal + por ciclista, erro/calibração)                  │
        │      ▼                                                                        │
        │  Registro + artefato de modelo LOCAL versionado                               │
        │  (model_version ↔ dataset_version ↔ schema de features ↔ alvo)                │
        └───────────────────────────────┬───────────────────────────────────────────────┘
                                        │ artefato de modelo armazenado localmente
                                        ▼  (carregado do disco, sem rede)
   ┌──────────────── INFERÊNCIA LOCAL OFFLINE (dispositivo do cliente) ────────────────┐
   │  Grafo LOCAL (OSM.PBF + DEM locais) ─► busca gera candidatas                       │
   │      │  mesmas restrições de circulação (validate_route)                           │
   │      ▼                                                                             │
   │  Simulador físico COMPLETO ─► EffortFeatures (mesmo contrato)                      │
   │      │                                                                             │
   │      ▼                                                                             │
   │  predict_effort(features, rider, context, predictor=modelo local calibrado)        │
   │      │                                                                             │
   │      ▼                                                                             │
   │  RouteAssessment → REJECTED / PHYSICAL_ONLY / PHYSIOLOGICAL                        │
   │      │                                                                             │
   │      ▼                                                                             │
   │  Classificação por preferência (ranking/seleção) — separada da predição            │
   └────────────────────────────────────────────────────────────────────────────────────┘
```

**Regras de ligação (invariantes).**

1. **Um só simulador.** Os dois processos usam o **simulador físico completo**
   (`PhysicalTraversal.traverse_path`), **nunca** o estimador aproximado
   (`search/approximate.py`, experimental).
2. **Um só contrato de features.** `EffortFeatures` é a única entrada física do
   preditor, nos dois lados; mesma versão de schema.
3. **Um só contrato de previsão.** `PhysiologicalPrediction` e `predict_effort`
   são preservados; o artefato de treino **local** é injetado como `predictor`.
4. **Mesmo pipeline para qualquer gerador.** Rotas tradicionais e geradas por ML
   passam pela **mesma** circulação e pela **mesma** avaliação física.
5. **Separação predição × preferência.** A predição **não** altera geometria,
   restrições, fronteira nem ordem física; ela apoia a classificação final.
6. **Nenhuma etapa acessa a rede.** Dados e modelo são **locais**; carregar
   grafo, simular, inferir e classificar **não** abrem conexão de rede (§0).

---

## 4. Contratos compartilhados (preservados)

| Contrato | Local | Preservação |
| --- | --- | --- |
| `EffortFeatures` + `extract_effort_features` | `physical/effort.py` | **Fixo**; schema `"1"`; sem alvo; só do resultado físico |
| `BorgScale` / `DEFAULT_BORG_SCALE` | `physiology/scales.py` | **Fixo**; escala explícita (6–20 padrão) |
| `HeartRateZoneModel` | `physiology/zones.py` | **Fixo**; bordas + definição identificada |
| `MetabolicParameters` | `physiology/metabolic.py` | **Fixo**; `version`+`provenance`; `None` sem calibração |
| `PhysiologicalPrediction` / `PredictionStatus` | `physiology/prediction.py` | **Fixo**; nunca medição; estado explícito |
| `RiderProfile` / `PredictionContext` | `physiology/prediction.py` | **Fixo**; opcional; ausência explícita |
| `EffortPredictor` / `predict_effort` | `ml/predictor.py` | **Fixo**; seam de injeção do modelo |
| `RouteAssessment` / `AssessmentOutcome` | `evaluation/assessment.py` | **Fixo**; classificação sem limiar novo |

Nenhum desses contratos é alterado por este ADR. O trabalho novo é **aditivo**:
dados, treino, avaliação de modelo e critério de classificação, **mais** a
infraestrutura offline exigida pelo §0 — **armazenamento/registro/carregamento
local do modelo**, **leitor de OSM.PBF local** e **guard offline** com testes de
rede desabilitada.

---

## 5. Plano incremental de implementação e testes

Regras de progresso (herdadas dos ADR-025/026): implementar por incremento,
**testar cada incremento**, rodar a suíte a cada passo, registrar mudanças de
comportamento, **sem** model fictício, target sintético ou parâmetro arbitrário.
Cada incremento que tocar comportamento exige **justificativa + teste +
comparação de baseline**.

| # | Incremento | Entregável | Arquivos | Status |
| - | --- | --- | --- | --- |
| 0 | **Auditoria + ADR** | este documento + `027-auditoria-intelligence.md` | `docs/adr/*` | **concluído (fase 1)** |
| 1 | **Contrato de observação e identidade pseudonimizada** | esquema de observação real (alvo + `cyclist_id` + qualidade/proveniência + `dataset_version`), **armazenado localmente**, versionado no padrão `serialize`/`MetabolicParameters` | `intelligence/observations.py` (+ persistência local) | **concluído** |
| 2 | **Alvos e definição de zonas individualizada** | alvo observado alinhado a `borg_scale` e a `HeartRateZoneModel`; método nomeado/versionado de derivação | `intelligence/targets.py` | **concluído** |
| 3 | **Baseline preditivo** | baseline auditável + métricas fixadas antes do treino | `intelligence/training.py`, `intelligence/models.py`, `intelligence/model_eval.py` | **concluído** |
| 4 | **Pipeline de treino reproduzível + registro + artefato local** | treino determinístico, `model_version` ↔ `dataset_version`, **artefato de modelo persistido localmente** (formato versionado próprio) | `intelligence/training.py`, `intelligence/model_store.py` | **concluído** |
| 5 | **Avaliação de modelo** | validação temporal + por ciclista, erro/calibração/incerteza, critérios mínimos | `intelligence/model_eval.py` | **concluído** |
| 6 | **Carregamento local + compatibilidade + preditor calibrado** | carrega artefato do disco **sem rede**, **verifica compatibilidade** features×modelo, injeta como `EffortPredictor`; `predict_effort` passa a `AVAILABLE` só quando compatível e calibrado | `intelligence/predictor.py`, `intelligence/model_store.py` (sem alterar o protocolo em `ml/`) | **concluído** |
| 7 | **Classificação predição × preferência** | critério auditável + política de previsão indisponível; laço local testado | `intelligence/classification.py` (+ `evaluation.assess_route`) | **concluído** |
| 8 | **Infraestrutura offline (requisito §0)** | **leitor de OSM.PBF local** (lacuna O1); **guard/modo offline** que **proíbe** download em execução (O6); **testes com rede desabilitada** (O5); leitura **local** de elevação (Terrarium) | `osm/pbf.py`, `offline.py`, `elevation_tiles.py`, `elevation.py`, `cli.py` | **concluído** |

**Ordem e dependências.** Os incrementos **1–2** precedem o treino (sem dados e
sem alvo não se treina). O **3** (baseline) precede o **4** (modelo). O **5**
(critérios) é definido **antes** de considerar o **6** aceito. O **7** só faz
sentido depois do **6**; até então, a classificação permanece
`PHYSICAL_ONLY`/`PHYSIOLOGICAL` como hoje. O **8** é **transversal e
obrigatório**: o guard/testes de rede desabilitada devem ser introduzidos **o
quanto antes** (idealmente junto ao inc. 0–1) e são **condição de aceite final**
do ADR-027; o leitor de OSM.PBF local é pré-requisito para a operação offline
completa da Camada 01.

**Testes por incremento (previstos).**

- **1–2:** esquema de observação versionado, pseudonimização estável, alinhamento
  alvo↔features, recusa de proveniência desconhecida, derivação de zonas
  identificada (sem percentual escondido).
- **3–4:** reprodutibilidade (mesma entrada ⇒ mesmo modelo), baseline
  superado/não superado, registro `model_version`↔`dataset_version`, **artefato
  gravado e recarregado localmente sem perda de identidade**.
- **5:** separação treino/teste (temporal e por ciclista), métricas de erro e
  calibração, critérios mínimos aplicados, **sem** vazamento.
- **6:** protocolo `EffortPredictor` satisfeito; **carregamento local sem rede**;
  **incompatibilidade de schema de features recusada** (`CONTRACT_INCOMPATIBLE`);
  artefato ausente/corrompido ⇒ estado explícito (`UNAVAILABLE`/
  `MODEL_NOT_CALIBRATED`); `predict_effort` retorna `AVAILABLE` só com modelo
  compatível e calibrado; ausência continua explícita; independência do gerador
  (AST: sem `search`).
- **7:** classificação auditável, previsão indisponível tratada de forma
  conservadora, ranking físico inalterado (não-regressão dos baselines dos
  ADR-025/026).
- **8:** **teste(s) com a rede desabilitada** demonstram que buscar, simular,
  carregar o modelo, prever e classificar **não** abrem conexão; o guard
  **proíbe** download em execução (falha explícita, não silenciosa); leitor de
  OSM.PBF local produz o grafo **sem rede**.

---

## 6. Riscos e dependências

| # | Risco / dependência | Mitigação |
| - | --- | --- |
| 1 | **Ausência de observações reais** (bloqueia decisões 1–2, 4–5) | Incrementos 1–2 primeiro; **sem** dado, não se treina |
| 2 | **Privacidade / consentimento** no registro de identidade | Pseudonimização estável; ofuscação de extremos; retenção explícita |
| 3 | **Vazamento treino/serviço** (features obtidas por caminho diferente) | Um só extrator (`extract_effort_features`) + teste de alinhamento |
| 4 | **Target sintético disfarçado de observação** | Alvo só existe se **observado**; recusa explícita |
| 5 | **Parâmetros arbitrários** (eficiência, %FCmáx, thresholds) | Exigir `version`+`provenance`; definição de zona nomeada |
| 6 | **Dependência de biblioteca de ML** hoje inexistente no núcleo | Adicionar como dependência explícita; preservar determinismo |
| 7 | **Divergência de versão de schema** entre treino e inferência | `CONTRACT_INCOMPATIBLE` já recusa; registrar versão no modelo |
| 8 | **Sobre-ajuste / generalização por ciclista** | Validação por ciclista (decisão 5) |
| 9 | **Confundir "avaliação de rota" com "avaliação de modelo"** | Vocabulário separado (§1.2 da auditoria) |
| 10 | **Regressão física/busca por acoplamento da predição** | Ranking intocado; testes de não-regressão dos ADR-025/026 |
| 11 | **Rede na execução** (download de modelo/dado em tempo de rota ou inferência) | Requisito §0: modelo/dado **locais**; guard offline + **testes com rede desabilitada** (inc. 8, O5/O6) |
| 12 | **Modelo incompatível com a versão de `EffortFeatures`** | Checagem de compatibilidade no **carregamento**; `CONTRACT_INCOMPATIBLE` (O4, inc. 6) |
| 13 | **Artefato de modelo ausente/corrompido no dispositivo** | Tratamento conservador explícito (`UNAVAILABLE`/`MODEL_NOT_CALIBRATED`); nunca baixar |
| 14 | **Dependência de biblioteca de ML que exija rede em build/serviço** | Empacotar como dependência **local**; validar em ambiente sem rede (inc. 8) |
| 15 | **Fonte geográfica local em OSM.PBF ausente** (lacuna O1) | Implementar leitor de OSM.PBF local (inc. 8); até então, ingestão offline depende de grafo já preparado |

---

## 7. Critérios de aceite

A Camada 04 será considerada **implementada** (com ML real) quando:

| Cenário | Comportamento esperado |
| --- | --- |
| Observação real registrada | Alvo + `cyclist_id` pseudonimizado + qualidade/proveniência + `dataset_version` explícitos |
| Alvo ausente | Observação **inutilizável** para aquele alvo; **nunca** imputada como observação |
| Features de treino | Obtidas pelo **mesmo** `extract_effort_features` do processo de inferência local |
| Vazamento | Teste demonstra ausência (alvo não é feature; teste não entra no treino) |
| Baseline | Existe e é superado (ou o modelo é rejeitado) |
| Treino | Reprodutível: mesma entrada ⇒ mesmo `model_version` |
| Registro | `model_version` aponta para dataset, artefato e escala/definição de alvo |
| Validação | Temporal **e** por ciclista, com erro/calibração reportados |
| Critérios mínimos | Definidos antes do treino e aplicados; não satisfeitos ⇒ **não aceito** |
| Preditor calibrado | `predict_effort` retorna `AVAILABLE` só com modelo calibrado injetado |
| Sem modelo calibrado | Estado **explicitamente** `UNAVAILABLE`/`MODEL_NOT_CALIBRATED`; RPE/FC `None` |
| Previsão como medição | **Impossível**: `is_measurement=False` permanece |
| Ranking físico | **Inalterado** pela predição (não-regressão dos baselines 025/026) |
| Previsão indisponível | Classificação **conservadora e explícita** (`PHYSICAL_ONLY` + motivo) |
| Rota tradicional vs ML | **Mesmo** simulador e **mesmo** `EffortFeatures`; predição idêntica a igualdade de features |
| **Operação integralmente offline** | Buscar, simular, **carregar o modelo**, prever e classificar **sem** acesso à rede (testes com rede desabilitada) |
| **Sem download automático** | Nenhum modelo ou dado é baixado durante a execução; tentativa é **falha explícita** (guard offline) |
| **Fonte geográfica local** | Grafo obtido de **OSM.PBF + DEM locais**, sem rede |
| **Modelo local versionado** | Artefato em armazenamento local, com registro `model_version` ↔ `dataset_version` ↔ schema de features ↔ alvo |
| **Modelo incompatível** | Carregamento **recusado** com estado explícito (`CONTRACT_INCOMPATIBLE`); RPE/FC `None` |
| **Modelo ausente/não calibrado** | Estado explícito (`UNAVAILABLE`/`MODEL_NOT_CALIBRATED`); **nunca** imputa nem baixa |

---

## 8. Consequências arquiteturais

**Benefícios.** Os contratos já garantem honestidade (nada é inventado) e
independência do gerador; o seam `EffortPredictor` torna o ML plugável **sem**
tocar em física nem em busca; a separação predição × preferência protege o
ranking físico; o cenário **offline é natural** — a execução já é livre de rede
(§0.2), faltando apenas a infraestrutura de modelo local; e o padrão
`version`+`provenance` já existe para ser reutilizado nos dados e no artefato de
modelo.

**Custos.** O fluxo de **treinamento offline** adiciona uma superfície nova
(dados, treino, registro, avaliação) e possivelmente uma dependência de ML
**local**; o requisito §0 adiciona **leitor de OSM.PBF local**, **guard offline**
e **testes com rede desabilitada**; a qualidade fisiológica exige **dados
reais**, privacidade e critérios de aceitação — sem eles, a Camada 04 permanece
corretamente em `PHYSICAL_ONLY`.

**Decisão de honestidade (vinculante).** Enquanto não houver observações reais,
**nenhum** modelo é treinado; o preditor padrão declara a ausência e a
classificação permanece `PHYSICAL_ONLY`. Isso **não** é uma pendência a
"contornar": é o comportamento correto.

## 9. Fora de escopo e follow-ups registrados

- Reorganização de diretórios das camadas existentes (proibida nesta fase).
- Substituição dos contratos de `EffortFeatures`, `PhysiologicalPrediction` ou
  `predict_effort` (proibida).
- Treino de redes neurais **sem** dados reais e **sem** método de validação
  (proibido).
- Séries de potência por passo na engine física (habilitaria
  `peak_power_w`/`power_variability`) — pertence ao ADR-025 §6, não a este ADR.
- Estimador aproximado (`search/approximate.py`) como fonte de features: **fora**
  do fluxo; o simulador completo permanece a fonte (ADR-025 §10.1).
- Calibração dos pesos/limiares de **preferência** (tolerâncias, `max_similarity`,
  `ScoreReference`): preferência, não fisiologia; reavaliar com dados de uso.
- **Qualquer** dependência de rede na execução: buscar, simular, carregar modelo,
  prever e classificar permanecem **locais** (§0); é proibido introduzir download
  automático de modelo ou dado.
- Manter `OverpassClient`/`OpenTopoDataElevation` como **preparação** da Camada 01
  (fora do caminho de execução); a operação offline completa exige o **leitor de
  OSM.PBF local** e DEM local (inc. 8, lacuna O1), mas **não** exige remover esses
  provedores de preparação.

> **Entregáveis da fase 1:** este ADR e a auditoria
> [`027-auditoria-intelligence.md`](027-auditoria-intelligence.md) —
> inventário, matriz PRESERVAR/ADAPTAR/COMPLEMENTAR/SUBSTITUIR, plano
> incremental e critérios de aceite. **As implementações de ML não devem
> começar antes da revisão desta auditoria.**
