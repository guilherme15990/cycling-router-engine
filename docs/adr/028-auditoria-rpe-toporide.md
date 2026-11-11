# ADR-028 (auditoria) — Fórmulas de RPE do `toporide` × variáveis da engine atual

- **Status:** auditoria concluída (fase 1). Nenhuma implementação feita ainda —
  ver as decisões propostas em
  [`028-intensidade-e-ordem-ml.md`](028-intensidade-e-ordem-ml.md).
- **Data:** 2026-11-11
- **Contexto:** o usuário pediu para espelhar a **ordem de eventos** do
  `toporide` (ML → achar rotas → devolver dados → dimensionar o box a partir das
  rotas devolvidas) e para **toda rota receber RPE e faixas de intensidade,
  sempre**, com "carrega o modelo do cache; senão treina e gera". Antes de
  implementar, foi exigido: **auditar as fórmulas de RPE do `toporide`** e
  **verificar se seus parâmetros são compatíveis com as variáveis disponíveis na
  engine atual**, **sem copiar coeficientes arbitrários sem registrar origem e
  limitações**.
- **Depende de:** [ADR-024](024-modelo-canonico-de-grafo-e-ambiente.md),
  [ADR-025](025-modelo-fisico-e-dinamica-do-ciclista.md),
  [ADR-026](026-optimization-custo-e-busca.md),
  [ADR-027](027-intelligence-ml-prediction.md).
- **Não substitui:** os contratos de `EffortFeatures`, `PhysiologicalPrediction`
  e `predict_effort`, que permanecem **inalterados**.
- **Regras vinculantes (do usuário):**
  1. **não** criar rede neural fisiológica treinada com rótulos artificiais de
     frequência cardíaca;
  2. **manter** o contrato de previsão fisiológica para evolução futura, com
     status explícito de indisponibilidade quando não existir modelo calibrado;
  3. **não** copiar coeficientes arbitrários sem registrar **origem** e
     **limitações**;
  4. **preservar** a física, o A\* Pareto e os contratos existentes;
  5. implementar de forma **incremental**, com **testes de regressão** e **sem
     dependência de internet**.

---

## 0. Escopo da auditoria

Auditar **como o `toporide` gera RPE e "zonas cardíacas"** e responder:

1. Qual é, **exatamente**, a fórmula de RPE e quais são seus **coeficientes**?
2. Cada coeficiente tem **origem rastreável** (fonte fisiológica citada) ou é
   uma **escolha de engenharia** local?
3. Cada **entrada** da fórmula tem variável correspondente na **engine atual**
   (Camadas 01–03 deste projeto)?
4. O que pode ser **reutilizado com honestidade** e o que **não pode** ser
   copiado sem registro de origem/limitações?

Fontes auditadas no `toporide` (caminhos relativos a
`C:\Users\sus\Documents\$apps\toporide`):

| Arquivo | Conteúdo relevante |
| --- | --- |
| `src/domain/ml/rpeEdge.ts` | `borgRpe`, `arcRpe`, `fatigueStateFrom`, `greenFraction`, `GREEN_MAX_RPE` |
| `src/domain/ml/neuralSearch.ts` | `scoreRouteArcs` — bucketing de "zonas de FC" por distância |
| `src/domain/ml/features.ts` | `hwStress` — classe viária → estresse de tráfego |
| `src/domain/ml/trainingData.ts` | `buildTrainingSamplesFromSys` — alvo RPE via `borgRpe` |
| `src/domain/ml/riderProfiles.ts` | `RIDER_PROFILES` (FTP por nível) |
| `src/config/tuning.ts` | `GRADE`, `FTP_WORK_WINDOW_S`, `ME.RPE_MIN/SPAN` |
| `src/domain/routing/cost.ts` | `arcCost` — unidades de `e` (J) e `t` (s) |

---

## 1. Como o `toporide` gera RPE

### 1.1 Preditor por arco (`rpeEdge.ts`)

O RPE é produzido por um **modelo físico transparente** (`borgRpe`), o mesmo que
rotula o treino da rede — a rede **não** produz RPE; ela estima **custo** de
arco. "O RPE — que decide 'verde × duro' — é o teacher físico" (comentário do
próprio arquivo).

Fórmula (`rpeEdge.ts:55-65`):

```
RPE = clamp(6, 20,
            6
          + (W/kg) / max(0.5, FTP/75) * 6
          + (gradePct > 5 ? (gradePct - 5)^1.3 : 0)
          + hwScore * 1.5
          + (fatigueState - 1) * 1.5)
```

com, em `fatigueStateFrom` (`rpeEdge.ts:27-35`):

```
fatigueState = (1 + tanh(accumJ / (FTP * 1200)) * 1.5) * fatigueSensitivity
```

- `W/kg = c.e / max(1, c.t) / max(1, ctx.m)`, onde `c.e` é a **energia** (J), `c.t`
  o **tempo** (s) e `ctx.m` a **massa** (kg) — `arcCost` devolve `{e, t, vOutKmh}`
  (`cost.ts:46-94`).
- `FTP` vem de `RIDER_PROFILES` (`110/175/190/260 W` por nível).
- `gradePct` é o **pico** de subida no sentido do arco (`arcMaxPct`).
- `hwScore = hwStress(classe viária)` (`features.ts:16-24`).

### 1.2 "Zonas cardíacas" (`neuralSearch.ts:341-415`)

Em `scoreRouteArcs`, as cinco "zonas" são **faixas de RPE aplicadas por
distância**, **não** zonas de frequência cardíaca:

```
zone = r <= 10 ? 0 : r <= 12 ? 1 : r <= 14 ? 2 : r <= 16 ? 3 : 4   // linhas 361
zoneM[zone] += metros                                              // 362
zonePct = round(zoneM[i] / totalM * 100)                           // 391
hrZoneDist = { z1..z5: zonePct }                                   // 409-415
```

O corte das bandas é `10, 12, 14, 16` (Borg); `GREEN_MAX_RPE = 12` define
"zona verde" (Z1+Z2). Ou seja: **a "zona cardíaca" do `toporide` é uma faixa de
intensidade derivada do Borg 6–20, medida em % de distância** — não há FC (bpm)
envolvida.

### 1.3 Treino da rede (`trainingData.ts`)

Se não há modelo em cache, `buildTrainingSamplesFromSys` gera amostras **do
próprio grafo**; o alvo de RPE é **`borgRpe`** (`trainingData.ts:111-117`), e o
alvo de custo vem da **física real** (`arcCost`). Portanto:

- o **RPE** (e, por consequência, as faixas) é **determinístico e físico**, não
  aprendido;
- a **rede** aprende **custo**,. treina com **rótulos derivados da física**,
  e não com observações fisiológicas reais.

---

## 2. Proveniência dos coeficientes

"Origem declarada" = o que o **código-fonte** afirma. "Rastreável" = existe
**fonte fisiológica citada** no repositório para aquele número.

| # | Coeficiente | Valor | Local | Origem declarada | Rastreável? |
| - | --- | --- | --- | --- | --- |
| C1 | Piso Borg | `6` | `rpeEdge.ts:57` | escala de Borg 6–20 | **sim** (convenção) |
| C2 | Teto Borg | `20` | `rpeEdge.ts:56` | escala de Borg 6–20 | **sim** (convenção) |
| C3 | Ganho de intensidade | `6` | `rpeEdge.ts:60` | — | **não** |
| C4 | Referência de normalização do FTP | `75` | `rpeEdge.ts:60` | massa de referência (~75 kg) | **não** (não documentado) |
| C5 | Limiar de rampa | `5` | `rpeEdge.ts:61` | — | **não** |
| C6 | Expoente da rampa | `1.3` | `rpeEdge.ts:61` | — | **não** |
| C7 | Peso do tráfego | `1.5` | `rpeEdge.ts:62` | — | **não** |
| C8 | Peso da fadiga | `1.5` | `rpeEdge.ts:63` | comentário "Banister/Borg" | **parcial** (forma não fiel ao modelo) |
| C9 | Amplitude da fadiga | `1.5` | `rpeEdge.ts:34` | — | **não** |
| C10 | Janela de trabalho FTP | `1200 s` | `tuning.ts:60` | "20 min em FTP" | **plausível**, não citado |
| C11 | Cortes de faixa | `10/12/14/16` | `neuralSearch.ts:361` | — | **não** |
| C12 | Limite "verde" | `12` | `rpeEdge.ts:21` | topo da Z2 | **não** |
| C13 | Escala de tráfego por classe | `0.05 … 1.2` | `features.ts:16-24` | ordinal | **não** |
| C14 | `RPE_MIN`/`RPE_SPAN` | `6`/`14` | `tuning.ts:41-43` | Borg 6–20 | **sim** (convenção) |

**Conclusão da §2:** apenas os limites da **escala de Borg** (C1, C2, C14) têm
origem fisiológica clara. Todo o **mapeamento intensidade/rampa/tráfego/fadiga →
RPE** (C3–C13) é uma **heurística de engenharia local**, sem fonte citada. Copiar
esses coeficientes **como se fossem fisiologia** violaria a regra de "nenhum
parâmetro arbitrário" (ADR-025/027).

---

## 3. Compatibilidade com as variáveis da engine atual

Variáveis disponíveis nas Camadas 01–03 deste projeto:

- `physical/models.py` → `TraversalResult`: `arc_id`, `time_s`, `distance_m`,
  `ascent_m`, `descent_m`, `rider_energy_j`, `mechanical_energy_j`,
  `average_speed_kmh`.
- `physical/effort.py` → `EffortFeatures`: `mean_power_w`, `duration_s`,
  `distance_m`, `climbing_m`, `descending_m`, `average_speed_mps`, …
- `physical/cyclist.py` → `CyclistModel`: `mass_kg`, `max_power_w`,
  `sustainable_power_w`, `frontal_area_m2`.
- `graph/model.py` → `Arc`: `highway`, `elevation_gain_m`/`elevation_loss_m`,
  `elevation_profile`, `distance_m` (e `Segment.highway`).
- `physiology/prediction.py` → `RiderProfile`: `threshold_power_w`,
  `weight_kg`, `max_heart_rate_bpm`, `resting_heart_rate_bpm` (todos opcionais).

Mapeamento entrada × engine:

| Entrada `toporide` | Definição lá | Equivalente atual | Disponibilidade | Observação |
| --- | --- | --- | --- | --- |
| `W/kg` | `c.e/c.t/m` | `rider_energy_j/time_s`, `mean_power_w`, `mass_kg` | **disponível** | J/s/kg = W/kg |
| `FTP` | tier fixo | `CyclistModel.sustainable_power_w` / `RiderProfile.threshold_power_w` | **parcial** | FTP (limiar ~1 h) ≠ potência sustentável de travessia; exige mapeamento explícito |
| `gradePct` | pico do arco | `Arc.elevation_profile` (pico = maior gradiente por passo) | **disponível** | `ArcProfile.steps` já expõe `slope_rad` por passo |
| `hwScore` | `hwStress(hwClass)` | `Arc.highway` (classe OSM) | **disponível** | mapeamento ordinal portável, porém sem fonte |
| `fatigueState` | `tanh(accumJ/(FTP·1200))` | acumular `rider_energy_j` ao longo da rota | **derivável** | constantes arbitrárias (C9, C10) |
| `m` (massa) | `ctx.m` | `cyclist.mass_kg + bicycle.mass_kg` | **disponível** | a engine já soma as duas massas na simulação |

**Conclusão da §3:** as **entradas** são, em sua maioria, **reproduzíveis** na
engine atual. A única lacuna conceitual é o **FTP**: não temos "FTP" e sim
**potência sustentável** (`sustainable_power_w`) e **potência de limiar**
(`threshold_power_w`); qualquer uso precisa declarar qual noção está sendo usada
e por quê. As **bordas de banda** (C11) e os **coeficientes** (C3–C9) continuam
sem fonte.

---

## 4. Achados

1. **As "zonas cardíacas" do `toporide` não são zonas cardíacas.** São **faixas
   de intensidade** (Borg) medidas em **% de distância** (`neuralSearch.ts`).
   Elas **não** devem preencher `PhysiologicalPrediction.heart_rate_zone_distribution`,
   cujo contrato exige uma **definição em bpm** (`physiology/prediction.py:170-181`).
2. **O RPE do `toporide` é físico e determinístico, não aprendido.** A rede
   estima custo; o RPE vem de `borgRpe`. Isso é compatível com "sempre rodar a
   ML": o passo "ML" pode produzir uma **estimativa de intensidade** sempre, sem
   depender de um modelo fisiológico calibrado.
3. **Os coeficientes de `borgRpe` são heurísticas locais** (C3–C13), sem fonte
   citada — exceto a escala de Borg.
4. **As entradas são majoritariamente reproduzíveis** na engine (§3); a exceção
   conceitual é o **FTP**.
5. **O treino "se não houver cache" do `toporide` usa alvos derivados da física**
   (`trainingData.ts`), **não** observações fisiológicas — ou seja, é dado
   **computacionalmente gerado**, exatamente como o usuário autorizou, **desde
   que identificado como tal** e **nunca** como zona cardíaca real.

---

## 5. Recomendação

Caminho honesto, respeitando as cinco regras vinculantes:

- **R1 — Contrato fisiológico intocado.** `EffortFeatures`,
  `PhysiologicalPrediction` e `predict_effort` **não mudam**. Sem modelo
  **calibrado**, o status permanece o explícito de indisponibilidade
  (`UNAVAILABLE`/`MODEL_NOT_CALIBRATED`). **Não** preencher
  `heart_rate_zone_distribution` com faixas de intensidade.
- **R2 — Nada de rede fisiológica com rótulo artificial de FC.** Proibido criar
  um modelo neural que produza FC/zonas a partir de rótulos sintéticos.
- **R3 — Estimativa de intensidade separada e rotulada.** Se implementada, a
  estimativa Borg/faixas deve ser um artefato **novo e distinto**
  ("estimativa computacional **não calibrada fisiologicamente**"), com **cada
  coeficiente com origem + limitação registradas**, e **jamais** tratada como
  medição nem como previsão fisiológica calibrada.
- **R4 — Coeficientes com proveniência.** Se algum coeficiente do `toporide` for
  reutilizado, ele entra **citado** (valor, local de origem, natureza
  "heurística de engenharia", limitação) em tabela versionada — ou **não entra**.
- **R5 — Ordem de eventos espelhada.** O fluxo passa a ser, por rota:
  candidatas (A\* Pareto e, opcionalmente, um modelo de roteamento carregado do
  cache/treinado de dados computacionais) → guardrails de circulação → física
  completa → **estimativa de intensidade (sempre)** → classificação → **box
  calculado a partir das rotas devolvidas**.
- **R6 — Incremental e testável.** Cada incremento vem com testes de regressão,
  offline (rede desabilitada), sem alterar física, A\* Pareto nem contratos.

As **decisões** decorrentes (com a alternativa de **não** implementar a
estimativa, mantendo apenas indisponibilidade explícita) estão em
[`028-intensidade-e-ordem-ml.md`](028-intensidade-e-ordem-ml.md).

---

## 6. O que esta auditoria NÃO autoriza

- Copiar `borgRpe` "como está" e apresentá-lo como fisiologia calibrada.
- Encher `heart_rate_zone_distribution` com faixas de Borg.
- Treinar qualquer rede fisiológica (RPE/FC/zonas) com rótulos artificiais.
- Introduzir parâmetros arbitrários **sem** origem e limitação registradas.
- Tocar física, A\* Pareto ou os contratos existentes.
