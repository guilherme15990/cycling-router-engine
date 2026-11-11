# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)
e [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

ADR-026 — Optimização, custo e busca (Camada 03) + Camada 05 (avaliação e
classificação) + **ADR-027 — Camada 04 (Intelligence, ML & Prediction)** e o
**caminho de dados offline** + **ADR-028 — Estimativa de intensidade e ordem de
eventos de ML** + **ADR-029 — Camada 05 (Evaluation & Guardrails)**. **Sem**
física nova, **sem** algoritmo novo e **sem** parâmetro arbitrário. Sem
restrições registradas, a busca é **idêntica** ao baseline S1–S7
(`tests/test_search_reference.py` inalterado). A Camada 04 opera **integralmente
offline** no dispositivo: treinamento offline e inferência local, ambos sem rede;
a fonte geográfica é o **OSM.PBF local** e a altimetria é o **Terrarium local**
(PNG `{z}/{x}/{y}.png`). O ADR-029 acrescenta um **estado de segurança**
operacional (`VALIDATED`/`DEGRADED`/`REJECTED`) e governa a **recomendação** por
ele — **nenhuma** rota inelegível pode ser recomendada.

### Adicionado

- `offline.py` — guard de rede (`offline()`, `is_network_disabled()`,
  `OfflineViolation`): **proíbe** conexões de saída durante a execução e falha de
  forma **explícita** (nunca silenciosa); reentrante; opção `allow_loopback` para
  serviço local. É a garantia executável do requisito mandatório do ADR-027 §0.
- `osm/pbf.py` — leitor **local** de `OpenStreetMap PBF` (`.osm.pbf`) em Python
  puro (sem protobuf/osmium), leitura em duas passagens de memória limitada,
  `read_osm_pbf`/`read_header`/`PbfStats`, com a **mesma semântica** de
  `OverpassClient.fetch` (filtro `highway=*`, `area != yes`, apenas nós
  referenciados). É a **fonte geográfica local** exigida pelo ADR-027 §0.
- `elevation_tiles.py` — preparo de rede dos tiles **Terrarium** (download
  idempotente para `{z}/{x}/{y}.png`, gravação atômica); a leitura é feita
  **offline** por `elevation.TerrariumElevation` (decodificação PNG em Python
  puro, `h = R*256 + G + B/256 - 32768`).
- `envelope.py` + `geo.py` (**envelope de consulta**) — box **dinâmico** de uma
  consulta, portado de `toporide` (`src/domain/geo.ts`): parte do retângulo dos
  pinos (origem + paradas + destino) com margem **por eixo** e cresce para
  cobrir o **traçado real de todas** as rotas (`corridor_bbox_of`,
  `auto_box_pads`, `bbox_with_routes`, `query_bbox`). Como o aplicativo é
  **integralmente offline**, este box limita a **busca** (`graph_for_bbox`,
  `adaptive_zone`) e a **altimetria** — o grafo carregado é a cidade inteira,
  mas a busca nunca o percorre por completo.
- `planning.py` (**consulta offline completa**) — orquestra todo o fluxo numa
  chamada (`plan_offline_routes` / `RoutePlan` / `PlannedRoute`): zona dinâmica
  (`envelope.adaptive_zone`) → snapping (`build_query_graph`) → busca
  (`search.MultiObjectiveAStar`) → **avaliação física completa**
  (`evaluation.assess_candidates`) → previsão (`ml.predict_effort`) →
  classificação (`intelligence.classify_routes`). Nenhuma física, algoritmo ou
  parâmetro novo: apenas composição. As métricas da **busca** e da **avaliação**
  ficam **separadas** (a reavaliação pelo simulador completo pode diferir) e
  ambas são expostas. Sem modelo calibrado, cada rota é `physical_only` com RPE
  `None` — nunca um valor inventado. Comando de CLI `plan` (texto e `--json`),
  com `--model-store`/`--model-version` (preditor **local**) e `--rpe-min`/
  `--rpe-max` (preferência declarada).
- `intelligence/` (Camada 04, ADR-027) — **dois processos ambos offline**:
  - `observations.py` — contrato de **observação real** (alvo observado,
    identidade pseudonimizada `pseudonymize`/`CyclistId`, `Provenance`,
    `MeasurementQuality`, `dataset_version`), persistência **local** versionada
    (`save_dataset`/`load_dataset`). Sem alvo observado, a observação é
    inutilizável — nunca imputada.
  - `targets.py` — contrato de **alvos** (`TargetKind`, RPE na escala Borg
    identificada; zonas cardíacas por `HeartRateZoneDerivationMethod`
    **nomeado e versionado**, `register_zone_derivation`/`derive_heart_rate_zones`).
  - `features.py` — montagem de features de **treino e de serviço as mesmas**
    (`feature_row`, `select_row`, `missing_features`), sem alvo e sem vazamento.
  - `models.py` — modelos base auditáveis (`ConstantModel`, `LinearModel`,
    `fit_constant`, `fit_linear`, `model_from_payload`).
  - `model_store.py` — **artefato de modelo local versionado** + registro
    (`ModelArtifact`, `ModelStore`, `check_compatibility`): liga
    `model_version` ↔ `dataset_version` ↔ schema de `EffortFeatures` ↔ alvo;
    carregamento **do disco** com verificação de compatibilidade. Modelo
    ausente/corrompido/incompatível é tratado de forma **explícita**
    (`ModelNotFoundError`/`ModelFormatError`/`ModelIncompatibleError`) — nunca
    por download nem coerção silenciosa.
  - `training.py` — treino **reproduzível** (mesma entrada ⇒ mesmo
    `model_version`) com **baseline obrigatório** (`train_constant_baseline`,
    `train_linear`, `train_and_save`, `build_training_matrix`); descartes
    reportados, nada imputado.
  - `model_eval.py` — avaliação em conjunto **separado** com validação
    **temporal** (`temporal_split`) e **por ciclista** (`cyclist_split`),
    erro/víés e critérios mínimos definidos **antes** do treino
    (`MinimalCriteria`, `evaluate_model`).
  - `predictor.py` — `LocalModelPredictor` + `load_predictor`: implementa
    `ml.EffortPredictor` sem tocar física/roteamento; só declara `AVAILABLE`
    com artefato **compatível**, alvo **RPE** e **método de calibração**
    declarado; senão estado **explícito**
    (`CONTRACT_INCOMPATIBLE`/`MODEL_NOT_CALIBRATED`/`INSUFFICIENT_DATA`), RPE/FC
    permanecem `None`; RPE fora da escala é **limitado** com motivo explícito.
  - `classification.py` — `classify_route`/`classify_routes`: combina o
    resultado físico (`AssessmentOutcome`), a disponibilidade da previsão e a
    **preferência declarada** (`EffortPreference`) em veredito tri-estado
    conservador (`PreferenceVerdict`), **preservando a ordem** de entrada
    (ranking físico **intocado**). Os códigos de limitação seguem a convenção
    documentada (ADR-026 §5 / ADR-027 decisão 6): `circulation_violation`,
    `physiology_<status>` e `preference_unknown` — os **mesmos** de
    `evaluation.assessment` para as parcelas comuns.
  - `intensity.py` (**ADR-028**) — `estimate_intensity` / `IntensityEstimate`:
    estimativa **computacional e não calibrada** de intensidade de uma rota, com
    RPE (Borg) + cinco **faixas de intensidade** (`i1`..`i5`, definição
    versionada `borg-intensity-bands@1`, **não** zonas cardíacas), distribuição
    por **distância** e por **duração**. Espelha o preditor `borgRpe` do
    `toporide` (auditado em `docs/adr/028-auditoria-rpe-toporide.md`), com
    **proveniência** de cada coeficiente (`IntensityCoefficients`). É
    `calibrated=False` e `is_measurement=False`, **não** preenche
    `PhysiologicalPrediction` e **nunca** é apresentada como zona cardíaca real.
- `evaluation/` (Camada 05) — avaliação física **completa** + classificação:
  `assess_route`/`assess_search_route`/`assess_candidate`/`assess_candidates`,
  `RouteAssessment`, `AssessmentOutcome` (`REJECTED`/`PHYSICAL_ONLY`/
  `PHYSIOLOGICAL`) e `RouteAssessmentError`. Se não houver preditor **calibrado**,
  a classificação é `PHYSICAL_ONLY` e a ausência é declarada
  (`PredictionStatus`) — nunca inventada.
- `evaluation/safety.py` + `evaluation/diagnostics.py` (**ADR-029**, Camada 05) —
  **estado de segurança** operacional (`RouteSafetyState`: `VALIDATED`/`DEGRADED`/
  `REJECTED`, **independente** de `AssessmentOutcome`), `Reason`/`ReasonKind`
  (motivos **estruturados** e auditáveis), `classify_safety`, `RouteSafety`,
  `EvaluatedRoute`, `EvaluationReport` e `evaluate_candidates` (**reutiliza** as
  avaliações já calculadas — sem nova física nem fisiologia — e escolhe a
  primeira rota **elegível**). Toda rejeição carrega um motivo; ausência de
  modelo fisiológico calibrado e a estimativa de intensidade entram como
  **advertências** que **não** alteram o estado. `EnergyConsistency`/
  `energy_consistency` agregam o balanço energético **já calculado** pelo
  simulador (sem equação nova).
- CLI — `build --pbf CAMINHO.osm.pbf` (fonte **offline**; dispensa `--bbox` e
  Overpass), `build --elevation terrarium --tiles-dir DIR [--tiles-zoom Z]` e o
  comando `fetch-dem` (preparo **de rede** dos tiles Terrarium).

### Alterado

- `osm/__init__.py` — exporta o leitor PBF (`PbfError`, `PbfHeader`,
  `PbfStats`, `read_header`, `read_osm_pbf`).
- `cli.py` — `--bbox` deixa de ser obrigatório (exigido apenas sem `--pbf`);
  `--elevation` ganha `terrarium`; novo comando `fetch-dem`.
- `api.py` — reexporta offline guard, OSM.PBF, Terrarium, `elevation_tiles`, o
  envelope de consulta (`geo`/`envelope`), a consulta offline completa
  (`planning`), os 69 símbolos da Camada 04 (`intelligence`), os 14 da
  **estimativa de intensidade** (ADR-028) e os 10 do **estado de segurança**
  (ADR-029). `scripts/generate_api_reference.py` e `docs/reference/api.md`
  sincronizados (**315 símbolos**).
- `planning.py` (**ADR-028 D7** + **ADR-029 D4**) — a ordem de eventos passa a
  espelhar o `toporide`: candidatas (A\*) → guardrails → física completa → **ML
  em toda rota** (estimativa de intensidade) → classificação → **avaliação final
  de segurança** → **box por último**, a partir do traçado das rotas
  **devolvidas**. `PlannedRoute` ganha o estado `safety`; `RoutePlan.routes` traz
  apenas as rotas **elegíveis** e `RoutePlan.rejected` preserva as `REJECTED`
  para auditoria. `RoutePlan.recommended` é a **primeira elegível** (nunca uma
  rejeitada) ou `None`; `RoutePlan.to_dict` inclui `recommended_route_id`,
  `rejected`, `rejected_count` e `warnings`.
- `evaluation/assessment.py` — `RouteAssessment.intensity` (estimativa de
  intensidade **não calibrada**, por rota) e `RouteAssessment.energy`
  (diagnóstico energético **opcional**, ADR-029 D7); `limitations` passa a
  declarar `intensity_not_calibrated`. A intensidade nunca preenche a previsão
  fisiológica.
- `cli.py` — o comando `plan` marca a rota **`[recomendada]`** (recomendação
  governada pelo **estado de segurança**, não pela ML), exibe `seguranca:
  <estado>` por rota, separa **viáveis** de **excluídas** (com motivos
  estruturados) e imprime os **avisos** (rota degradada recomendada; nenhuma
  elegível). Exibe a **intensidade (não calibrada)**: RPE médio e faixas por
  distância. Novas opções `--prefer-bands`/`--min-band-fraction`/`--band-basis`
  avaliam a preferência por **faixas de intensidade** (distância ou duração).
- `intelligence/classification.py` (**ADR-028, incremento 4**) —
  `EffortPreference` ganha `preferred_intensity_bands`,
  `min_intensity_band_fraction` e `intensity_band_basis`; novo critério
  `intensity_bands` avalia a **estimativa de intensidade** (não calibrada) por
  **distância** ou **duração** (`UNKNOWN` quando não há distribuição na base
  escolhida). Não toca a previsão fisiológica nem reordena nada.
- `search/astar.py` — a expansão aplica os guardrails (acesso → conversão)
  **antes** de gastar física; ativação **guiada pelos dados** (inertes sem
  restrições).
- `search/dominance.py` — `SpeedBucketDominance` inclui a **classe de chegada**
  (`arrival_class = incoming_arc_id`) apenas em nós com restrição de conversão.
- `search/state.py`, `search/route.py`, `search/candidates.py` — propagação
  monotônica de `result_status`, incerteza altimétrica, regularização, `stalled`
  e `dismount_used` até `RouteCandidate`.

### Documentação

- `docs/adr/026-optimization-custo-e-busca.md`,
  `docs/adr/026-contrato-circulacao.md`, `docs/adr/026-baseline-busca.md` e
  `docs/adr/025-modelo-fisico-e-dinamica-do-ciclista.md` atualizados.
- **ADR-027 — Camada 04 (Intelligence, ML & Prediction)**:
  `docs/adr/027-auditoria-intelligence.md` (inventário, as seis decisões,
  matriz PRESERVAR/ADAPTAR/COMPLEMENTAR/SUBSTITUIR, análise de lacunas,
  baseline e riscos) e `docs/adr/027-intelligence-ml-prediction.md` (decisões,
  requisito mandatório de operação **integralmente offline** no dispositivo —
  treinamento offline + inferência local, ambos sem rede —, contratos
  compartilhados, plano incremental, riscos, critérios de aceite). Preserva os
  contratos de `EffortFeatures`, `PhysiologicalPrediction` e `predict_effort`;
  **não** cria modelo fictício, target sintético nem parâmetro de calibração
  arbitrário; **não** introduz dependência de rede na execução.
- **ADR-028 — Estimativa de intensidade e ordem de eventos de ML**:
  `docs/adr/028-auditoria-rpe-toporide.md` (auditoria das fórmulas de RPE do
  `toporide` contra as variáveis da engine, com **tabela de proveniência** dos
  coeficientes — só os limites da escala de Borg são rastreáveis; o resto é
  heurística de engenharia — e o achado de que as "zonas cardíacas" do
  `toporide` são **faixas de intensidade por distância**, **não** bpm) e
  `docs/adr/028-intensidade-e-ordem-ml.md` (decisões D1–D8 e incrementos).
  Preserva `EffortFeatures`, `PhysiologicalPrediction`, `predict_effort`, a
  física e o A\* Pareto; **proíbe** rede fisiológica com rótulos artificiais de
  FC e mantém o contrato fisiológico para evolução futura.
- **ADR-029 — Camada 05 (Evaluation & Guardrails)**:
  `docs/adr/029-auditoria-evaluation-guardrails.md` (inventário, contrato ×
  código, invariantes × evidência, matriz PRESERVAR/ADAPTAR/COMPLEMENTAR/
  SUBSTITUIR e análise de lacunas) e `docs/adr/029-evaluation-guardrails.md`
  (decisões D1–D8, critérios de aceite e incrementos). Preserva as Camadas
  01–04, o `MultiObjectiveAStar`, o simulador físico e o ML; **não** cria outro
  roteador nem outro motor físico. Numeração: o ADR-028 original (intensidade e
  ordem de ML) é **preservado**; Evaluation & Guardrails passa a **029**.
- `docs/reference/api.md` regenerado.

### Testes

- Camada 03/05: `tests/test_search_circulation.py` (34),
  `tests/test_search_guardrails.py` (16) e `tests/test_evaluation_assessment.py`
  (23).
- Caminho offline e Camada 04: `tests/test_offline.py` (11),
  `tests/test_osm_pbf.py` (19), `tests/test_elevation_terrarium.py`,
  `tests/test_cli_offline.py` (8), `tests/_intelligence_fixtures.py`,
  `tests/_pbf_fixtures.py`, `tests/test_intelligence_observations.py` (21),
  `tests/test_intelligence_model_store.py` (13),
  `tests/test_intelligence_training.py` (16),
  `tests/test_intelligence_evaluation.py`,
  `tests/test_intelligence_predictor.py` (15) e
  `tests/test_intelligence_classification.py` (20).
- ADR-028: `tests/test_intelligence_intensity.py` (22, RPE + faixas, distância e
  duração, proveniência) e a ordem de eventos / box pós-ML em
  `tests/test_planning.py` (25).
- ADR-029: `tests/test_evaluation_safety.py` (25, estado de segurança, motivos
  estruturados, `evaluate_candidates`, energia), `tests/test_evaluation_
  consistency.py` (16, busca × avaliação final e estabilidade de ranking) e
  `tests/test_evaluation_regression.py` (10, baselines R-a..R-f, operação offline
  e reprodutibilidade). `tests/test_api_surface.py` (4) trava a sincronia entre
  `api.__all__`, as camadas do gerador e `docs/reference/api.md`.
- Envelope de consulta (zona de busca offline): `tests/test_geo_envelope.py`
  (25). Suíte: **798 testes**.

## [0.18.0] - 2026-11-11

Busca — estimador aproximado versus simulador completo (ADR-025, **incremento
8**, decisão de desempenho). Entrega um `TraversalModel` **aproximado** (mais
barato) para a busca e o **relatório de erro/custo** contra o simulador
completo. Nenhum parâmetro físico novo é introduzido: o estimador reutiliza
apenas primitivos da engine (`equilibrium_speed_mps`, `forces_at`,
`ArcProfile`, `classify_status`, `MIN_SPEED_MPS`).

### Adicionado

- `search/approximate.py`:
  - `estimate_arc(...)` — travessia aproximada de um arco por **equilíbrio de
    velocidade** (inclinação média `atan2(ascent − descent, distance)`;
    `tempo = distância / max(velocidade, MIN_SPEED_MPS)`; energia do pedivela
    `= potência × tempo` só enquanto há pedalada). Mesma assinatura e mesmo
    `TraversalResult` de `PhysicalTraversal.traverse`.
  - `SteadyStateTraversalModel` — adaptador `TraversalModel` do estimador.
    **Não** declara `max_speed_mps`: a busca o trata como teto **desconhecido**
    (`UNKNOWN_SPEED`) e o limite de tempo da heurística padrão cai para `0`
    (admissível por construção, sem presumir a qualidade do estimador).
  - `compare_traversals(...)` + `ComparisonReport` + `ArcEstimateError` —
    erro de **tempo**, **energia** e **velocidade final**, mais **custo
    computacional** medido em avaliações de passo (`reference_steps` do
    simulador vs `1` por arco no estimador; `step_reduction_factor`).
  - `compare_search_rankings(...)` + `SearchRankingComparison` —
    impacto sobre a **ordenação das rotas**: roda A* com os dois modelos e
    compara a fronteira (mesmo conjunto de rotas, rota preferida, inversões).
  - `compare_rankings(...)` + `RankingComparison` — métrica de ordenação por
    **chave** (rota) e por **valor** (objetivos), com `inverted_pairs`,
    `order_agreement` e `max_objective_delta`.
- Testes `tests/test_search_approximation.py` — contrato `TraversalModel`,
  reuso da engine (limite de velocidade, regime regularizado, status),
  caracterização do erro, custo, métrica de ordenação e não-regressão do
  simulador.

### Notas

- **Nenhum parâmetro físico novo.** As diferenças em relação ao simulador são
  de **resolução** (um passo por arco), não de física.
- **Admissibilidade não é presumida.** O estimador pode **subestimar** o tempo
  (partida do repouso; ignora a aceleração) **ou superestimar** (entrada rápido;
  o estimador é **independente do estado**). Isso está coberto por testes. Como
  ele **não** declara teto de velocidade, a busca permanece admissível sem
  tratá-lo como heurística admissível (ADR-025).
- **Impacto medido na ordenação:** em grafos de teste, o estimador pode
  **contrair** a fronteira (3 → 2 rotas), **inverter** pares de rotas e até
  **trocar a rota preferida** — quantificado por `SearchRankingComparison`, não
  escondido.
- **Limitações registradas:** velocidade constante ignora aceleração e partida
  do repouso; arcos mistos (sobe-e-desce com ganho líquido ≈ 0) viram
  “planos”; o limite de velocidade por passo não é integrado; o erro de energia
  acompanha o de tempo em pedalada constante. Ver o relatório no ADR-025
  (auditoria §8.5).
- **A física e a busca não foram alteradas.** O incremento é aditivo.

## [0.17.0] - 2026-11-10

Camada 03 — Physiology + ML (ADR-025, **incremento 7**). Entrega os **contratos**
de fisiologia e a **fronteira de inferência**, consumindo `EffortFeatures` do
incremento 6. Nenhum modelo calibrado é incluído (não há dados nem método de
validação): o preditor padrão declara a ausência de forma explícita.

### Adicionado

- `physiology/` — pacote de contratos imutáveis e versionados:
  - `BorgScale` com limites explícitos (`RPE_6_20` = 6–20, **padrão**;
    `CR10` = 0–10) e `DEFAULT_BORG_SCALE = BorgScale.RPE_6_20`;
    `contains`/`clamp`/`require_valid`.
  - `HeartRateZoneDefinition` e `HeartRateZoneModel` (definição + versão
    identificadas, bordas explícitas em bpm; sem percentuais implícitos) e
    `DISTRIBUTION_TOLERANCE` (distribuição não negativa que soma 1). Os
    auxiliares `validate_distribution`/`is_normalized` ficam **internos**
    (`physiology.zones`), fora da fachada pública.
  - `MetabolicParameters` (eficiência bruta + `version` + `provenance`) e
    `metabolic_energy_j` — `None` sem parâmetros calibrados (**nunca 0**).
  - `RiderProfile`, `PredictionContext` e `PhysiologicalPrediction`
    (`PHYSIOLOGY_VERSION`): RPE respeita os limites da escala; distribuição
    exige definição de zonas; `confidence` numérica exige `confidence_method`;
    `is_measurement` é sempre `False`; `source = "prediction"`.
  - `PredictionStatus` — estado explícito da previsão fisiológica:
    `AVAILABLE`, `UNAVAILABLE`, `MODEL_NOT_CALIBRATED`, `INSUFFICIENT_DATA`,
    `CONTRACT_INCOMPATIBLE`.
- `ml/` — fronteira de inferência: protocolo `EffortPredictor`;
  `UnavailablePredictor` (padrão — tudo `None`, sem inventar);
  `MetabolicEnergyPredictor` (preenche **apenas** a energia metabólica a partir
  de parâmetros explícitos); atalho `predict_effort`, que **diferencia** ausência
  de preditor, ausência de modelo calibrado, dados insuficientes e
  incompatibilidade de contrato antes de delegar.
- Testes `tests/test_physiology.py` e `tests/test_ml_predictor.py`.

### Notas

- **Borg**: a escala inicial do projeto passa a ser a **6–20 original**; a CR10
  continua suportada. A escala é sempre explícita no contrato.
- **Previsão nunca é medição**: `PhysiologicalPrediction` marca
  `source="prediction"` e `is_measurement=False`; Borg/FC só existem com
  calibração própria — hoje ficam `None`.
- **Indisponibilidade é explícita e classificada**: quando `status` não é
  `AVAILABLE`, `predicted_rpe`/`heart_rate_zone_distribution` são obrigatoriamente
  `None` (nada é inventado). `predict_effort` distingue `UNAVAILABLE` (sem
  preditor), `MODEL_NOT_CALIBRATED` (preditor sem modelo fisiológico calibrado),
  `INSUFFICIENT_DATA` (faltam entradas) e `CONTRACT_INCOMPATIBLE` (schema de
  `EffortFeatures` não suportado).
- **Invariantes do ADR-025**: valores previstos respeitam os limites da escala;
  a distribuição de zonas é não negativa e soma 1 (dentro da tolerância); o
  preditor **não** altera a geometria nem as restrições da rota.
- **A física não foi tocada.** O incremento é puramente aditivo sobre a fachada.

## [0.16.0] - 2026-11-10

Camada 02 — Physical Model (ADR-025), **incremento 6** (`EffortFeatures`). O
incremento 5 (`Crr` por superfície e vento vetorial) fica **documentado, porém
adiado**: os contratos foram registrados sem alterar as equações físicas nem
introduzir coeficientes não calibrados.

### Adicionado

- `EffortFeatures` — contrato **imutável** e **versionado**
  (`EFFORT_FEATURES_VERSION`, `schema_version`) com unidades explícitas:
  duração, distância, velocidade média, trabalho na roda/pedivela, potência
  média, subida/descida, coberturas altimétricas, perdas assinadas, status e
  limitação numérica.
- `extract_effort_features(path: TraversalPath) -> EffortFeatures` — extrator
  **determinístico**, dependente apenas de `physical.models` (sem `search`, sem
  `ml`), portanto válido para qualquer rota (tradicional ou gerada por ML).
- `EffortFeatures.unavailable_fields` e `to_dict()` — representam
  explicitamente os dados ausentes.
- Testes `tests/test_effort_features.py`.

### Notas

- **Não há inferência inventada.** `peak_power_w` e `power_variability` são
  `None` (o modelo usa potência constante e não retém a série por passo);
  `metabolic_energy_j` é `None` (não calibrado, nunca 0); `battery_energy_j` é
  `None` na bicicleta convencional. Borg, FC e fadiga **não** pertencem a este
  contrato (camada fisiológica futura).
- Qualidade altimétrica (`ElevationUncertainty`), status (`SimulationStatus`) e
  o uso do regime regularizado (`MIN_SPEED_MPS`) são propagados.
- **O integrador, o modelo de baixa velocidade e os algoritmos de busca não
  foram modificados.** A física numérica permanece idêntica.
- Contratos adiados do incremento 5 documentados em
  `docs/adr/025-auditoria-fisica.md` (§8.2) e no ADR-025.

## [0.15.0] - 2026-11-10

Camada 02 — Physical Model (ADR-025, `docs/adr/025-modelo-fisico-e-dinamica-do-ciclista.md`),
incrementos 1–4. Prepara a engine física para as camadas de features e
fisiologia, **sem alterar os resultados numéricos da dinâmica**.

### Adicionado

- `SimulationState` — estado canônico da **dinâmica** (`elapsed_s`, `speed_mps`,
  `wheel_work_j`, `rider_work_j`, `braking_loss_j`), com validação de
  não-negatividade, `from_speed` e `to_dict`. Não carrega posição geográfica.
- `ElevationUncertainty` — incerteza altimétrica estruturada por origem
  (`observed`/`interpolated`/`estimated`/`missing`) com `coverage`.
- `SimulationStatus` (`valid`/`degraded`/`unavailable`), `classify_status` e
  `worst_status` — política de confiabilidade do resultado.
- `EnergyBalance` e `TraversalResult.energy_balance(mass_kg)` — balanço
  `ΔE_k + ΔE_p = W_wheel − aero − rolamento − frenagem`, com resíduo auditável.
- Campos de perdas assinadas: `TraversalResult.aero_loss_j`/`rolling_loss_j`,
  `SpeedStep.aero_loss_j`/`rolling_loss_j` e agregados em `RouteMetrics`.
- `TraversalResult.state`/`simulation_state` e `TraversalResult.result_status`.
- `SimulationMode` (`SEARCH`/`VALIDATION`) e `DEFAULT_STEP_M_BY_MODE` — modos de
  execução com passos explícitos (5 m / 1 m); o CLI ganha `--mode`.
- `used_min_speed_regularization` em `TraversalResult` e `TraversalPath`, mais
  `SpeedStep.regularized` — expõe o uso do regime regularizado de baixa
  velocidade (`MIN_SPEED_MPS`) no resultado e em `to_dict`.
- Testes `tests/test_physical_reference.py` (14), `tests/test_physical_uncertainty.py`
  (15), `tests/test_physical_convergence.py` (10) e `tests/test_physical_modes.py`
  (20).

### Alterado

- `PhysicalTraversal.traverse`/`traverse_path` e `traverse_arc` passam a receber
  `initial_state: SimulationState` em vez de `initial_speed_mps` (sem camada de
  compatibilidade, ADR-024). `PhysicalTraversalModel` (busca) converte a
  velocidade do contrato `TraversalModel` para `SimulationState.from_speed`.
- `PhysicalTraversal.step_m` e `traverse_arc(step_m=...)` tornam-se opcionais: se
  omitidos, o passo vem do modo (`SEARCH` 5 m, `VALIDATION` 1 m); um valor
  explícito sempre prevalece. O `--step` do CLI passa a ser opcional.
- Promoção de `PhysicalTraversal(require_elevation=True)` faz um trecho sem
  elevação virar `UNAVAILABLE` em vez de `DEGRADED`.
- `TraversalResult` agora carrega `result_status`/`elevation_uncertainty` e os
  campos de perdas; `TraversalPath` agrega incerteza e status (pior caso).
- A validação de velocidade inicial negativa passa a pertencer a
  `SimulationState` (não mais ao argumento do simulador).
- CLI (`cmd_physical`) e `api.__version__` atualizados.

### Notas

- **A dinâmica numérica foi preservada.** Os incrementos 3 e 4 adicionam
  contrato/metadado e verificação energética; os cenários de referência seguem
  idênticos. O estudo de convergência (`025-auditoria-fisica.md` §7.1) conclui
  que o esquema é de primeira ordem no regime suave e que a partida do repouso é
  limitada pelo modelo de baixa velocidade — lacuna registrada, não mascarada.
- **Decisão 06 resolvida (aceita, com limitação conhecida).** Mantido o
  integrador de primeira ordem; adotados os modos `SEARCH`/`VALIDATION`. A
  regularização `MIN_SPEED_MPS` permanece como hipótese numérica **exposta** em
  `used_min_speed_regularization` (não apresentada como precisão validada). A
  ordenação de candidatos claramente separados é estável entre os modos.
  Substituir a regularização por um modelo explícito de partida/baixa velocidade
  fica condicionada a evidência de melhoria.
- Migração de chamadas: substituir `initial_speed_mps=x` por
  `initial_state=SimulationState.from_speed(x)`. A busca continua transportando
  apenas a velocidade (contrato `TraversalModel` e `search.state.PhysicalState`
  preservados).

## [0.14.0] - 2026-11-10

Remoção da **camada de compatibilidade**. O projeto é um algoritmo novo que
consome os dados do motor; não há consumidor da API/formato antigos, então não
se mantêm aliases nem leitura de versões anteriores.

### Removido

- `AccessRestriction` (alias) — usar `AccessRule`.
- `ElevationSample.source` (propriedade) — usar `source_id`.
- `ArcProfile.coverage` (alias) — usar `coverage_available`.
- `ElevationSample.quality_inferred` — existia só para a migração v3.
- Leitura de payloads de serialização **v2/v3**; `graph_from_dict` aceita apenas
  **v4**.

### Alterado

- `graph_from_dict` recusa qualquer versão diferente da atual.
- Gerador de referência (`scripts/generate_api_reference.py`) atualizado para a
  API atual e para o caminho `docs/reference/api.md`.

### Notas de migração

- Dados legados (`data/*.json`, formato v2) e payloads v3 devem ser
  **regenerados** a partir do OSM.

## [0.13.0] - 2026-11-10

Camada 01 — Data Model (ADR-024, `docs/adr/024-modelo-canonico-de-grafo-e-ambiente.md`).
Implementa os contratos das três decisões sem alterar a lógica de roteamento.

### Adicionado

- `ElevationQuality` (`measured`, `dem_sampled`, `source_reported`,
  `interpolated`, `estimated`, `missing`) e validação de invariantes em
  `ElevationSample` (MISSING exige `elevation_m=None`; demais estados exigem
  altitude finita; distância ≥ 0 e resolução > 0).
- `OBSERVED_ELEVATION_QUALITIES` e `ElevationSample.is_observed`.
- Campos `ElevationSample.quality_inferred` (categoria deduzida) e
  `SegmentProfile.observed_elevation_m`; `ArcProfile.coverage_available`,
  `coverage_observed` e `ArcProfile.observed_elevation_m`.
- Contratos de restrição: `AccessRule` (novo nome canônico de
  `AccessRestriction`), `TurnRestriction` e `RestrictionSource`; helper
  `turn_restriction_from_relation`.
- Registro de restrições de conversão em `RoutingGraph`
  (`add_turn_restriction`, `get_turn_restriction`, `turn_restrictions`,
  `turn_restrictions_at`), preservado em `copy()`, `simplify_graph()` e na
  serialização.
- `concat_geometry` e grade de geometria derivada: `_Passage.geometry`,
  `RoutingGraph.arc_geometry` com cache amarrado à identidade dos segmentos.
- `ArcProfile.coverage` (fração de distância com elevação conhecida).
- Provedores de elevação expõem `quality`; `ele=*` recebe `SOURCE_REPORTED`;
  amostras preenchidas por interpolação recebem `INTERPOLATED`.

### Alterado

- **`Arc` não persiste mais geometria**: `Arc.geometry`/`Link.geometry` passam a
  ser derivadas dos `Segment` (fonte única de verdade geométrica). `Segment` é
  `frozen`; `concat_geometry` só remove pontos consecutivos repetidos.
- **`ElevationSample`**: `source` → `source_id` (alias de leitura `source`
  mantido); `elevation_m` passa a ser `float | None`; novos campos `quality` e
  `quality_inferred`.
- Formato serializado **v4** (perfil com qualidade e `quality_inferred`;
  `arcs[].geometry` removido; nova seção `turn_restrictions`). Migração de
  payloads **v3** é conservadora e registrada (`ESTIMATED`/`MISSING` +
  `quality_inferred=True`).
- `summarize` inclui `turn_restrictions`.

### Notas de compatibilidade

- `Arc(geometry=...)` deixa de existir (a geometria é derivada).
- `ElevationSample(source=...)` deixa de existir (usar `source_id=`).
- `AccessRestriction` permanece como alias de `AccessRule`.
- **Restrições de conversão são representadas/preservadas, mas ainda NÃO são
  avaliadas** na busca nem no guardrail (o parse de `relation` no build entra no
  próximo incremento). Ao implementar, validar rotas tradicionais **e** ML.

## [0.12.0] - 2026-11-10

Fork do motor (`roteamento engine` 0.11.0) para o projeto **roteamento com ml**,
com a nomenclatura padronizada (norma **N-01**, ver `docs/nomenclatura.md`).

### Adicionado

- **`Link`**: entidade de ligação física não-direcionada entre dois `Node`,
  agrupando um ou dois `Arc`. Registro no grafo via `links`, `get_link`,
  `link_of`, `incident_links`, `add_link`, `rebuild_links` e `link_count`.
- `add_bidirectional` passa a registrar o `Link` físico dos dois arcos.
- Seção `links` no formato serializado (**v3**); `rebuild_links()` reconstrói os
  links quando a seção está ausente.
- `link_id` nas propriedades das feições de arco no GeoJSON e contagem `links`
  em `summarize`.
- Testes dedicados a `Link` (`tests/test_links.py`).

### Alterado

- **Nomenclatura (norma N-01)**, aplicada em todo o código, testes e formato:
  - `EdgeSegment` → `Segment`
  - `Edge` (direcionado) → `Arc`
  - `CyclingGraph` → `RoutingGraph`
  - `EdgeProfile` → `ArcProfile`; módulo `physical/edge_profile.py` →
    `physical/arc_profile.py`
  - `aresta`/`arestas` (pt-BR) → `arco`/`arcos`
- Formato serializado **v3**: a chave `edges` deu lugar a `arcs` (+ `links`).
- `pyproject.toml`: nome `roteamento-com-ml`, versão `0.12.0`.
