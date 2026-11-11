# ADR-025 — Auditoria da física (inventário, lacunas e plano)

- **Status:** entregável da **fase 1** do [ADR-025](025-modelo-fisico-e-dinamica-do-ciclista.md).
  Nenhuma alteração de física foi feita. Este documento é o **portão de revisão**
  antes de qualquer implementação.
- **Data:** 2026-11-10
- **Escopo:** auditoria da engine física existente, matriz de compatibilidade,
  análise de lacunas, cenários de referência e plano incremental.
- **Base auditada:** motor `0.14.0`, 269 testes verdes
  (`PYTHONPATH=src python -m pytest`).

> **Regra desta fase:** não recriar a física existente; não introduzir novos
> algoritmos, parâmetros arbitrários ou reorganização de diretórios.

---

## 1. Inventário técnico

### 1.1 Mapa de módulos — `src/physical/`

| Arquivo            | Símbolos principais                                                                                             | Responsabilidade                                                        |
| ------------------ | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `__init__.py`      | 40 nomes em `__all__`                                                                                            | Fachada pública da camada física                                         |
| `_validation.py`   | `positive`, `non_negative`, `finite`                                                                            | Validação numérica compartilhada                                        |
| `arc_profile.py`   | `SegmentProfile`, `ProfileStep`, `ArcProfile`; `from_arc`, `steps`; `coverage_available`, `coverage_observed`   | Deriva passos homogêneos (`slope_rad`, superfície, acesso, limite) do `Arc` |
| `environment.py`   | `Environment`; `DEFAULT_GRAVITY_M_S2`, `DEFAULT_AIR_DENSITY_KG_M3`, `DEFAULT_SAFE_MAX_SPEED_MPS`                | Ar, vento de frente (escalar), gravidade e teto de segurança             |
| `bicycle.py`       | `BicycleModel`                                                                                                   | Massa, `rolling_resistance`, `drag_coefficient`, `drivetrain_efficiency`, `wheel_radius_m` |
| `cyclist.py`       | `CyclistModel`                                                                                                   | Massa, `max_power_w`, `sustainable_power_w`, `frontal_area_m2`          |
| `forces.py`        | `ForceBreakdown`, `gravity_force_n`, `rolling_force_n`, `aerodynamic_force_n`, `forces_at`, `wheel_power_w`, `required_rider_power_w`, `equilibrium_speed_mps` | Equações de força e velocidade de equilíbrio                             |
| `speed.py`         | `SpeedStep`, `advance_speed`, `MIN_SPEED_MPS`                                                                   | Integração por passo espacial (conservação de `ΔE_k`)                    |
| `energy.py`        | `kinetic_energy_j`, `potential_energy_j`, `wheel_energy_from_rider_j`, `rider_energy_from_wheel_j`, `battery_energy_from_wheel_j` | Conversões de energia roda ↔ pedivela ↔ bateria                          |
| `energy_model.py`  | `GenericEnergyConfig`, `EnergyEstimate`, `EnergyModel`, `BatteryConfig`, `BatteryFeasibility`, `assess_battery`, `*_LEVEL`, `JOULES_PER_WH` | Energia **elétrica** estimada e viabilidade de bateria                   |
| `models.py`        | `TraversalResult`, `RouteMetrics`, `TraversalPath`                                                              | Estruturas de saída da física                                            |
| `traversal.py`     | `PhysicalTraversal`, `traverse_arc`, `DEFAULT_STEP_M`, `DEFAULT_ACCELERATION_LIMIT_MPS2`, `DEFAULT_DISMOUNT_SPEED_MPS` | Coordenador arco → perfil → passos → resultado; propaga estado no caminho |

### 1.2 Onde a física é consumida pela busca — `src/search/`

| Arquivo                 | Símbolos                                  | Papel                                                                 |
| ----------------------- | ----------------------------------------- | --------------------------------------------------------------------- |
| `traversal_model.py`    | `TraversalModel` (Protocol), `PhysicalTraversalModel`, `UNKNOWN_SPEED` | Adaptador busca ↔ física; expõe `mass_kg` e `max_speed_mps`           |
| `state.py`              | `PhysicalState`, `SearchState`, `initial_state`, `advance_state`, `route_arcs` | Label de busca: acumula métricas + carrega o estado físico            |
| `route.py`              | `Route`, `route_from_state`, `routes_from_result` | Rota reconstruída (dados finais)                                      |
| `energy.py`             | `RouteEnergyEstimate`, `estimate_route_energy` | Converte energia **mecânica** da rota em **elétrica** (pós-seleção)   |
| `objectives.py`, `profiles.py`, `evaluator.py` | `ObjectiveSet`, `SearchProfile`, `RouteProfile`, `PROFILE_*` | Objetivos (tempo, distância, subida, energia) — **não** reinterpretam física |

### 1.3 Grafo de dependências (física → busca)

```
graph (Node/Segment/Link/Arc, Surface, ElevationQuality)   [ADR-024]
      │  Arc.segments
      ▼
physical.arc_profile.ArcProfile.from_arc ──► ProfileStep[]
      │
      ▼
physical.traversal.PhysicalTraversal.traverse
      └─ physical.speed.advance_speed  ──►  SpeedStep (por passo)
             └─ physical.forces.* (gravity/rolling/aero/equilibrium)
      ▼
physical.models.TraversalResult         (por arco)
      │
      ▼
search.traversal_model.PhysicalTraversalModel.traverse
      ▼
search.state.advance_state ──► SearchState ──► Route ──► RouteEnergyEstimate
```

### 1.4 Parâmetros numéricos atuais

Todos já existem; a auditoria apenas os **registra** (não são para duplicar).

| Constante / default                          | Valor      | Local                          | Observação                                  |
| -------------------------------------------- | ---------- | ------------------------------ | ------------------------------------------- |
| `DEFAULT_STEP_M`                             | 5.0 m      | `traversal.py`                 | Passo espacial de integração                |
| `DEFAULT_ACCELERATION_LIMIT_MPS2`            | 2.0        | `traversal.py`                 | Teto cinemático de aceleração por passo     |
| `DEFAULT_DISMOUNT_SPEED_MPS`                 | 1.4        | `traversal.py`                 | Velocidade ao pé em `DISMOUNT`              |
| `MIN_SPEED_MPS`                              | 0.5        | `speed.py`                     | **Piso numérico** (evita divisão por zero)  |
| `MAX_EQUILIBRIUM_SPEED_MPS`                  | 100.0      | `forces.py`                    | Teto da velocidade terminal                 |
| `DEFAULT_SAFE_MAX_SPEED_MPS`                 | 22.0       | `environment.py`               | Teto global de segurança                    |
| `DEFAULT_GRAVITY_M_S2`                       | 9.80665    | `environment.py`               |                                             |
| `DEFAULT_AIR_DENSITY_KG_M3`                  | 1.225      | `environment.py`               |                                             |
| `BicycleModel` defaults                      | 12 kg / Crr 0.005 / Cd 0.9 / η 0.97 / r 0.35 m | `bicycle.py`     | Parâmetros físicos do veículo               |
| `GenericEnergyConfig` defaults               | 90 kg / Crr 0.008 / Cd 0.9 / A 0.5 / η 0.75 | `energy_model.py` | Referência **do modelo elétrico** (separado) |
| bisseção de equilíbrio                       | 80 iterações | `forces.py`                    | Resolve `P(v) = P_disponível`               |

### 1.5 Testes existentes

| Arquivo                              | Foco                                                                 |
| ------------------------------------ | -------------------------------------------------------------------- |
| `tests/test_physical.py` (523 linhas) | Perfil/passos, forças, equilíbrio, travessia, frenagem, vento, propagação, métricas, CLI |
| `tests/test_physical_energy_model.py` (128 linhas) | Modelo elétrico, níveis, bateria, viabilidade           |
| `tests/test_search_state.py`         | `PhysicalState`/`SearchState`, `advance_state`                       |
| `tests/test_search_profiles.py`, `test_search_electric.py`, `test_search_selection.py`, `test_search_astar.py`, `test_search_candidates.py`, `test_search.py` | Busca, perfis, seleção, energia |

**Nenhum** teste cobre: convergência de passo, balanço energético global, arco
dividido por snapping, incerteza de elevação ausente, features de esforço ou
fisiologia.

---

## 2. Decisão 01 — Estado físico contínuo

### 2.1 Como está hoje

- O balanço é implementado por **conservação de energia cinética por passo
  espacial**: `ΔE_k = (F_drive − F_resist)·ds` (`speed.advance_speed`), e não por
  `dv/dt` explícito. Para o modelo longitudinal é equivalente; `m_eff` = massa
  total (`cyclist.mass_kg + bicycle.mass_kg`), **sem** correção de inércia
  rotacional.
- Forças presentes e com sinal: `F_grade` (gravidade), `F_roll`, `F_aero`
  (quadrática, com sinal: cauda mais rápida que a bicicleta empurra) e `F_prop`
  (de `power·η / v`). `F_brake` **não** existe como força explícita: a frenagem
  aparece como **energia** dissipada quando a velocidade excede o limite.
- **Propagação existe**: `TraversalResult.final_speed_mps` alimenta
  `initial_speed_mps` do próximo arco (`PhysicalTraversal.traverse_path`,
  `search.state.advance_state`). `search.state.PhysicalState` carrega
  `speed_mps` + `kinetic_energy_j` (derivada).
- Momentum: `kinetic_energy_j` existe e o custo do arco **já depende** da
  velocidade de entrada (testado).

### 2.2 Contrato do ADR × código atual

| ADR-025                        | Código atual                                             | Situação        |
| ------------------------------ | -------------------------------------------------------- | --------------- |
| `PhysicalState.elapsed_s`      | `SearchState.time_s` (não no `PhysicalState`)            | Parcial         |
| `PhysicalState.speed_mps`      | `search.state.PhysicalState.speed_mps`                   | Atendido        |
| `PhysicalState.wheel_work_j`   | `TraversalResult.mechanical_energy_j` / `SearchState.energy_j` | Nome difere |
| `PhysicalState.rider_work_j`   | `TraversalResult.rider_energy_j`                         | Nome difere     |
| `PhysicalState.braking_loss_j` | `TraversalResult.braking_energy_j`                       | Nome difere     |
| `simulate_arc(...)`            | `PhysicalTraversal.traverse` / `traverse_arc`            | Nome difere     |
| `ArcSimulationResult`          | `TraversalResult`                                        | Nome difere     |

### 2.3 Invariantes × evidência

| # | Invariante                                   | Hoje                                                                 |
| - | -------------------------------------------- | -------------------------------------------------------------------- |
| 1 | velocidade nunca negativa                    | **Atendido** (`sqrt(max(0,·))`, `max(0,·)`)                          |
| 2 | tempo acumulado não diminui                  | **Atendido** por construção (soma de passos ≥ 0); sem asserção        |
| 3 | trabalho positivo do ciclista não diminui    | **Atendido** (`rider_energy = power·time ≥ 0`)                       |
| 4 | saída de um arco = entrada do próximo        | **Atendido** (`traverse_path`, `advance_state`)                      |
| 5 | respeitar limites físicos/numéricos          | **Atendido** (`_validation`, tetos)                                  |
| 6 | falha de convergência **sinalizada**         | **Parcial** — há `stalled`, mas `MIN_SPEED_MPS=0.5` **mascara** baixa velocidade e a bisseção não reporta convergência |
| 7 | resultado independente do gerador de rota    | **Não verificável hoje** (um único gerador)                          |

> **Lacuna crítica (invariante 6).** O passo `5 m` **não** é independente do
> refinamento (ver §7): `step=5` → 126,82 s; `step=1` → 128,58 s; `step=0,5` →
> 128,75 s (≈1,5%). Não há teste de convergência.

---

## 3. Decisão 02 — Energia separada

### 3.1 Como está hoje

| Grandeza ADR-025      | Equivalente atual                       | Situação                                  |
| --------------------- | --------------------------------------- | ----------------------------------------- |
| `wheel_work_j`        | `TraversalResult.mechanical_energy_j`   | **Atendido** (trabalho **positivo** na roda) |
| `rider_work_j`        | `TraversalResult.rider_energy_j`        | **Atendido** (pedivela = roda/η)          |
| `metabolic_energy_j`  | —                                       | **Ausente**                                |

- Relação de transmissão `P_wheel = η·P_crank`: **atendida**
  (`wheel_energy_from_rider_j`, `rider_energy_from_wheel_j`, η ∈ (0,1]).
- `kinetic_energy_j` e `potential_energy_j` existem, mas **não** há verificação
  de balanço `ΔE_k + ΔE_p = W_signed − perdas`.
- O acumulador é **positivo**; o **trabalho assinado** na roda **não** é exposto
  (a frenagem entra à parte, como `braking_energy_j` positivos).

### 3.2 Invariantes × evidência

| # | Invariante                                             | Hoje                                                       |
| - | ------------------------------------------------------ | ---------------------------------------------------------- |
| 1 | frenagem não cria energia                              | **Atendido** (só dissipa)                                  |
| 2 | transmissão não cria trabalho                          | **Atendido** (η ≤ 1)                                       |
| 3 | descida ganha `E_k` sem pedalada                       | **Atendido** (testado)                                     |
| 4 | frenagem dissipa energia mecânica                      | **Atendido**                                               |
| 5 | metabólico ≠ mecânico                                  | **Ausente** (não há metabólico)                            |
| 6 | unidades explícitas                                    | **Atendido** em geral (sufixos `_j/_m/_mps/_w/_kg`)        |

---

## 4. Decisão 03 — Borg e frequência cardíaca

Busca textual por `borg|rpe|heart|cardiac|metabolic|effort|zone|variability|
physiolog|fatigue|peak_power` em todo o repositório: **apenas** duas menções em
comentários (`search/state.py`, `physical/models.py`). Conclusão:

- `EffortFeatures` — **não existe**.
- `predict_effort` — **não existe**.
- `PhysiologicalPrediction` — **não existe**.

Toda a interface fisiológica é **nova** (COMPLEMENTAR). A física já expõe parte
da matéria-prima (`time_s`, `distance_m`, `ascent_m`/`descent_m`,
`mechanical_energy_j`, `rider_energy_j`), mas **não** potência média/pico,
variabilidade nem cobertura de elevação agregada.

> **Atualização — incremento 6:** `EffortFeatures` passou a existir
> (`physical/effort.py`), agora com **potência média** no pedivela, coberturas
> altimétricas e perdas; pico/`variabilidade` seguem indisponíveis (`None`),
> pois a engine não retém a série por passo (§8.3).
>
> **Atualização — incremento 7:** `predict_effort`, `PhysiologicalPrediction` e
> os demais contratos fisiológicos passaram a existir em `physiology/` + `ml/`
> (§8.4). **Nenhum modelo calibrado** foi introduzido: o preditor padrão
> (`UnavailablePredictor`) declara a ausência; `metabolic_energy_j` só é
> preenchido com `MetabolicParameters` explícitos.

---

## 5. Matriz de compatibilidade

Classificação: **P** = preservar, **A** = adaptar, **C** = complementar,
**S** = substituir (nenhum item em S nesta fase).

| Componente                                          | Classe | Ação mínima proposta                                                                 | Justificativa |
| --------------------------------------------------- | :----: | ------------------------------------------------------------------------------------ | ------------- |
| `graph/*` (Node/Segment/Link/Arc, `Surface`, `ElevationQuality`) | **P** | não tocar | Fonte canônica (ADR-024); física só lê |
| `physical/_validation.py`                           | **P** | — | Já garante limites |
| `physical/arc_profile.py`                           | **P** | — | Deriva passos de elevação corretamente; já separa `coverage_available`/`observed` |
| `physical/forces.py` (equações)                     | **P** | expor trabalho assinado na roda para o balanço (A no wrapper) | Sinais e unidades corretos |
| `physical/environment.py`                           | **A** | (futuro) aceitar **direção** de vento além do escalar `headwind_mps` | §7 exige componente do vento pela geometria orientada |
| `physical/bicycle.py`                               | **A** | (política) `Surface` → `rolling_resistance` | §7 lista `Surface` como entrada; hoje é constante (exige calibração, sem números mágicos) |
| `physical/cyclist.py`                               | **P** | servir de `rider_profile` para a fisiologia | Campos suficientes |
| `physical/speed.py` (`advance_speed`)               | **A** | sinalizar convergência / passo-independente; expor trabalho assinado | Invariante 6 e critério "passo refinado" |
| `physical/energy.py`                                | **P/C** | **C**: modelo metabólico (novo) | Invariante 5 da decisão 02 |
| `physical/energy_model.py`                          | **P** | — | É o elétrico/bateria, **distinto** do metabólico |
| `physical/models.py` (`TraversalResult`…)           | **A** | alinhar nomes (`wheel_work_j`/`rider_work_j`/`braking_loss_j`) e propagar incerteza de elevação | Contrato do ADR + §7 |
| `physical/traversal.py` (`PhysicalTraversal`)       | **A** | alinhar ao formato `simulate_arc`/`PhysicalState` (sem mudar física) | Contrato do ADR |
| `search/traversal_model.py`                         | **P** | — | Adaptador já desacopla a busca |
| `search/state.py` (`PhysicalState`/`SearchState`)   | **A** | unificar no contrato de estado do ADR | Hoje há **nome duplicado** `PhysicalState` com campos diferentes |
| `search/route.py`, `energy.py`, `objectives.py`, `profiles.py`, `evaluator.py` | **P** | — | Já separam preferência de física |
| **Fisiologia** (`EffortFeatures`, `predict_effort`, `PhysiologicalPrediction`) | **C** | novo serviço, separado da simulação | Decisão 03; nada existe hoje |

**Nenhum componente exige SUBSTITUIR.** A física existente é reaproveitável.

---

## 6. Análise de lacunas (mapeada aos critérios de aceite §9)

| Cenário (ADR §9)                        | Cobertura atual                                                        | Lacuna |
| --------------------------------------- | --------------------------------------------------------------------- | ------ |
| Plano, sem vento, velocidade constante  | `test_equilibrium_speed_orders` (ordenação)                           | Falta teste **analítico** "em equilíbrio, `F_total ≈ 0`" / `P_req = P_disp` |
| Subida                                  | `test_uphill_is_slower_than_flat`                                     | Atendido |
| Descida sem pedalada                    | `test_zero_power_downhill_accelerates`                                | Atendido |
| Frenagem                                | `test_speed_limit_brakes_and_reports_energy`                          | Falta verificar **conservação** (não só ≥ 0) |
| Vento contrário/favorável               | `test_force_signs_and_aerodynamic_quadratic`, `test_headwind_...`     | Falta travessia com **cauda** (ganho de velocidade) |
| Arcos consecutivos                      | `test_traverse_path_propagates_speed`                                 | Atendido |
| Arco dividido por snapping              | —                                                                     | **Ausente** (equivalência dentro de tolerância) |
| Passo de integração refinado            | —                                                                     | **Ausente e reprovando** (§7) |
| Elevação ausente                        | `test_profile_without_elevation_is_flat_and_unknown` (perfil)         | **Ausente no resultado físico**: hoje é tratado como plano (slope 0) |
| Mesma rota por métodos diferentes       | —                                                                     | Bloqueado (um gerador) |
| Predição Borg/FC                        | —                                                                     | **Ausente** (decisão 03) |
| Balanço energético global               | —                                                                     | **Ausente** |
| `metabolic_energy_j`                    | —                                                                     | **Ausente** |
| Testes analíticos de valor exato        | Parcial (ordens e sinais)                                             | Falta fixar valores analíticos |

### 6.1 Achado A — passo dependente (resolvido na Decisão 06)

`step=5` subestima o tempo em ≈1,5% vs `step=0,5` (ver §7, linhas 9–11).
**Não é regressão** — é o comportamento atual —, mas contraria o critério "passo
de integração refinado → convergência numérica". Investigado no incremento 4
(§7.1): o esquema é de **primeira ordem** no regime suave; a partida do repouso
é limitada pela regularização `MIN_SPEED_MPS`. **Resolvido na Decisão 06** (§6.1
do ADR): modos `SEARCH`/`VALIDATION` com passos explícitos e exposição do regime
regularizado no resultado — política documentada, não arbitrária.

### 6.2 Achado B — elevação ausente vira terreno plano

`Arc:1000 m sem perfil` produz resultado **idêntico** ao de um plano real
(126,8237 s / 24 603,79 J). O `ArcProfile` registra `unknown_elevation_m`, mas o
`TraversalResult` **não** carrega incerteza. Contraria o ADR-024 (invariante 5) e
o ADR-025 §7 ("devolver resultado parcial, incerteza ou indisponibilidade").

### 6.3 Achado C — trabalho assinado não exposto

O acumulador é só positivo; a frenagem é separada. Sem trabalho **signed** não
há como fechar `ΔE_k + ΔE_p = W_signed − perdas`.

---

## 7. Cenários de referência (baseline atual — `0.14.0`)

Parâmetros: `CyclistModel(75 kg, 600 W, 200 W, 0.5 m²)`, `BicycleModel()`,
`Environment()`, `PhysicalTraversal(step_m=5.0)`. Valores obtidos com a engine
**como está** (nenhuma alteração). Reproduzir com `PYTHONPATH=src`.

| Cenário                                | `v0→v1` (m/s) | tempo (s) | `mech_j`  | `rider_j`  | `brake_j` |
| -------------------------------------- | ------------- | --------- | --------- | ---------- | --------- |
| Plano 1000 m, 200 W, do repouso        | 0 → 8,3161    | 126,8237  | 24 603,79 | 25 364,73  | 0         |
| Subida 500 m (+25 m), 200 W, v₀=5      | 5 → 3,8133    | 129,3223  | 25 088,52 | 25 864,46  | 0         |
| Descida 500 m (−25 m), 200 W, v₀=5     | 5 → 13,6548   | 42,8996   | 8 322,53  | 8 579,93   | 0         |
| Descida 500 m, **0 W**, v₀=2           | 2 → 11,5637   | 56,2662   | 0         | 0          | 0         |
| Frenagem 500 m, limite 18 km/h, v₀=4   | 4 → 5,0       | 100,1111  | 215,56    | 222,22     | 36 742,95 |
| Plano 1000 m, vento de frente 5 m/s    | 0 → 5,5509    | 182,7178  | 35 447,26 | 36 543,56  | 0         |
| Plano 1000 m, vento de cauda 5 m/s     | 0 → 11,6616   | 98,0474   | 19 021,19 | 19 609,47  | 0         |
| Plano 1000 m, **elevação ausente**     | 0 → 8,3161    | 126,8237  | 24 603,79 | 25 364,73  | 0         |
| Plano 1000 m, `step=1,0`               | 0 → 8,3161    | 128,5754  | 24 943,63 | 25 715,08  | 0         |
| Plano 1000 m, `step=0,5`               | 0 → 8,3161    | 128,7451  | 24 976,55 | 25 749,02  | 0         |
| Continuidade 2×200 m, v₀=0             | arco A→8,01499 = arco B v₀  | — | — | — | — |

Forças analíticas (`mass=87 kg`, `slope=0,1 rad`, `v=5 m/s`):
`gravity=85,17573 N`, `rolling=4,244581 N`, `aero=6,890625 N`, `total=96,310936 N`.
Conferência: `87·9,80665·sin(0,1)=85,17573`; `0,005·87·9,80665=4,265893` (plano);
`0,5·1,225·0,9·0,5·25=6,890625`.
Energias de referência: `Ek(87 kg, 5 m/s)=1087,5 J`; `Ep(87 kg, 25 m)=21 329,46 J`.

> Estes valores devem virar **testes de referência** (caracterização) no
> incremento 1, com tolerâncias justificadas.

### 7.1 Estudo de refinamento e balanço energético (incremento 4)

Investigação executada sobre a engine **real** (sem alterar a dinâmica), para
responder à decisão 6. Plano 1000 m @200 W.

**Caso suave (`v₀ = 5 m/s`, sem singularidade de baixa velocidade):**

| passo (m) | tempo (s) | erro vs ref. 0,02 m | razão |
| --------- | --------- | ------------------- | ----- |
| 5,0       | 124,5867  | 1,67e-1             | —     |
| 2,0       | 124,6874  | 6,61e-2             | 2,52  |
| 1,0       | 124,7208  | 3,27e-2             | 2,02  |
| 0,5       | 124,7375  | 1,60e-2             | 2,04  |
| 0,25      | 124,7459  | 7,67e-3             | 2,09  |
| 0,125     | 124,7500  | 3,50e-3             | 2,19  |

Conclusão: o esquema espacial atual é **de primeira ordem** (razão → 2). O erro
no passo padrão (5 m) é ~0,13% e cai para ~0,013% em 0,5 m.

**Caso a partir do repouso (`v₀ = 0`):** o erro é **dominado pelo modelo de
força a baixa velocidade** (`MIN_SPEED_MPS = 0,5`), não pelo integrador. A
sequência **não converge monotonicamente**: com referência fina (0,02 m) os
erros oscilam numa banda de ~0,05% do tempo; o passo 0,25 m chega a ser pior que
0,5 m. Isso é uma **limitação do modelo**, registrada em teste
(`test_rest_start_convergence_is_not_monotone`) — **não mascarada**.

**Corretor de ponto médio (Heun):** prototipado fielmente à engine. No regime
suave eleva a ordem para **2** (razão → 4; erro em 5 m cai de 1,67e-1 para
6,90e-3). Porém, **piora a partida do repouso** (erro em 5 m: 2,37 vs 1,94;
em 0,5 m: 1,11e-1 vs 1,54e-2), porque o corretor amostra a força através do
"joelho" em `v = MIN_SPEED`. Um híbrido (corretor só acima do piso) fica
**inconsistente** e de primeira ordem. **Decisão: manter o esquema de primeira
ordem atual.** Trocar o integrador sem resolver o modelo de baixa velocidade
seria pior para o caso mais comum (partir do repouso).

**Balanço energético** (`ΔE_k + ΔE_p = W_wheel − perdas − frenagem`), `v₀=5`:

| passo (m) | resíduo (J) | % de `W_wheel` |
| --------- | ----------- | -------------- |
| 5,0       | 39,21       | 0,162%         |
| 1,0       | 7,76        | 0,032%         |
| 0,5       | 3,87        | 0,016%         |
| 0,25      | 1,94        | 0,008%         |
| 0,125     | 0,97        | 0,004%         |

O resíduo é de primeira ordem e tende a zero. Está coberto por
`tests/test_physical_convergence.py`, com a identidade exposta em
`TraversalResult.energy_balance(mass_kg)` (`EnergyBalance`).

**Política de passo e baixa velocidade — Decisão 06 (aceita, com limitação
conhecida):** o integrador de primeira ordem é mantido. Foram adotados **dois
modos de execução** com passos explícitos (configurações, não constantes de
precisão):

| modo         | passo padrão | uso                                  |
| ------------ | ------------ | ------------------------------------ |
| `SEARCH`     | 5,0 m        | busca (padrão), priorizando desempenho |
| `VALIDATION` | 1,0 m        | validação/relatório, refinamento      |

Tolerância de comparação de rotas: **0,5%** no tempo; o passo grosso só a viola
na partida do repouso. A ordenação de candidatos claramente separados é estável
entre os modos (coberta por `tests/test_physical_modes.py`).

A regularização `MIN_SPEED_MPS = 0,5` permanece como **hipótese numérica** e
limitação física conhecida (não um limite de força/torque calibrado). Quando ela
é usada, o resultado o **declara** (`TraversalResult.used_min_speed_regularization`,
propagado a `TraversalPath` e presente em `to_dict`; o CLI avisa), para que a
precisão não seja superestimada. O modo `VALIDATION` **não** garante alta
fidelidade na partida do repouso enquanto persistir essa limitação. A substituição
por um modelo explícito de partida/baixa velocidade (Decisão 06, item 3) fica
condicionada a evidência de melhoria.

---

## 8. Plano incremental de implementação

Regras de progresso (herdadas do ADR-024): implementar por incremento, **testar
cada incremento**, rodar a suíte a cada passo, registrar mudanças de
comportamento, **sem** novos números mágicos/pesos/heurísticas.

Ordem **revista na revisão técnica de 2026-11-10** (prioriza confiabilidade do
simulador antes de ampliar capacidades fisiológicas).

| # | Incremento | Entregável | Arquivos (previstos) | Status |
| - | ---------- | ---------- | -------------------- | ------ |
| 0 | ADR + auditoria | este documento + `025-...md` | `docs/adr/*` | **concluído** |
| 1 | **Testes de referência** (analíticos + caracterização) | baseline do §7 fixado; Achados A/B registrados | `tests/test_physical_reference.py` | **concluído** (14 testes, 283 na suíte) |
| 2 | **Estado físico canônico** | `SimulationState` + propagação entre arcos, **sem** alterar a física | `physical/models.py`, `physical/traversal.py`, `physical/__init__.py`, `api.py`, `search/traversal_model.py`, `cli.py` | **concluído** (números idênticos, 283 na suíte) |
| 3 | **Incerteza altimétrica** | `ElevationUncertainty` + `SimulationStatus` até `TraversalResult`; elevação ausente ≠ plano silencioso | `physical/models.py`, `traversal.py`, `arc_profile.py` | **concluído** (resultados numéricos inalterados; 308 na suíte) |
| 4 | **Convergência + balanço energético** | estudo de refinamento, ordem de convergência, trabalho assinado + perdas, teste `ΔE_k+ΔE_p = W − perdas`, tolerâncias justificadas | `physical/speed.py`, `forces.py`, `models.py`, `traversal.py` | **concluído** (dinâmica inalterada; novas perdas/balanço; 308 na suíte) |
| 4b | **Resolução da Decisão 06** | modos `SEARCH`/`VALIDATION` (passos explícitos) + exposição de `used_min_speed_regularization` + testes de modos/ordenação | `physical/traversal.py`, `physical/speed.py`, `physical/models.py`, `cli.py`, `tests/test_physical_modes.py` | **concluído** (dinâmica inalterada; 328 na suíte) |
| 5 | **`Crr` por superfície + vento vetorial** | contratos (documentados agora; comportamento calibrado adiado) — **não** bloqueia o incremento 6 | `physical/bicycle.py`, `forces.py`, `environment.py` | **contratos documentados** (§8.2); implementação adiada |
| 6 | **`EffortFeatures`** | contrato imutável/versionado + extrator determinístico + propagação de qualidade/status/limitação | novo `physical/effort.py` | **concluído** (aditivo, dinâmica inalterada; 347 na suíte) |
| 7 | **Fisiologia + ML** | `physiology/` (escalas, zonas, previsão) e `ml/` (inferência); `EffortFeatures` alimenta | novos pacotes `physiology/` e `ml/` | **concluído** (contratos + seam; aditivo, física inalterada; 401 na suíte) |
| 8 | **Estimador aproximado vs simulador completo** | estimador por equilíbrio + relatório de erro/custo + impacto na ordenação; **sem** tratá-lo como heurística admissível do A\* | novo `search/approximate.py` | **concluído** (aditivo, física/busca inalteradas; 426 na suíte — ver §8.5); **aceito como experimental/opcional, fora do fluxo principal** (ADR-025 §10.1) |

Incrementos **2–4** devem **provar ausência de regressão** contra o incremento 1
(ou justificar cada mudança numérica); os incrementos **5–8** são **aditivos**,
salvo mudança calibrada explicitamente documentada.

### 8.1 Incremento 1 — cenários que viram teste

Fixar (tolerâncias relativas estreitas, ex. `rel=1e-6` para igualdade,
`abs` justificado para magnitude): plano/analítico, subida, descida (com e sem
pedalada), frenagem, vento frente/cauda, arcos consecutivos, elevação ausente,
e **`step=5/1/0,5` como caracterização explícita** (documentando que ainda não
convergem).

### 8.2 Incremento 5 — contratos de `Crr` por superfície e vento vetorial

**Status: contratos documentados; implementação adiada.** Nenhuma equação física
foi alterada e **nenhum coeficiente não calibrado** foi introduzido (decisões 4
e 5 do ADR-025).

**`Surface` → `Crr` (decisão 4).** Hoje `BicycleModel.rolling_resistance` é um
escalar único (0,005), independente de `Surface`; `rolling_force_n` já recebe o
coeficiente como parâmetro e `SegmentProfile.surface` já carrega a categoria
observada. O contrato a formalizar é:

- um mapeamento explícito e **versionado** `Surface → Crr`, com proveniência e
  nível de calibração (nos moldes de `GenericEnergyConfig`), aplicado por passo
  via `ProfileStep.surface`;
- superfície **desconhecida** (`Surface.UNKNOWN`) **não** pode ser mapeada em
  silêncio para um valor calibrado: deve recair no padrão do modelo com marcação
  explícita de hipótese (análoga a `SimulationStatus.DEGRADED`);
- a equação de rolamento permanece `Crr · m · g · cos(θ)`; muda apenas a origem
  do `Crr`. A calibração fica condicionada a dados; até então o comportamento
  atual é **preservado**.

**Vento vetorial (decisão 5).** Hoje `Environment.headwind_mps` é escalar
(`> 0` = vento de frente) e entra em `aerodynamic_force_n` como
`speed + headwind`. O contrato a formalizar é:

- vento como **vetor local** (componentes, ex. leste/norte) mais o **rumo** do
  arco direcionado (azimute derivado da geometria do ADR-024);
- projeção do vetor na direção de deslocamento para obter a componente
  **ao longo da trajetória**; a convenção "de onde vem" × "para onde sopra"
  deve ficar explícita e normalizada;
- o escalar projetado (equivalente a `headwind_mps`) é o que entra na equação
  atual — **a equação não muda**;
- até existir rumo/vento calibrados, o vento permanece escalar e nulo por
  padrão.

### 8.3 Incremento 6 — `EffortFeatures`: mapeamento e relatório de compatibilidade

**Módulo:** `physical/effort.py` (novo). **Extrator:**
`extract_effort_features(path: TraversalPath) -> EffortFeatures`, determinístico
e dependente **apenas** de `physical.models` — sem `search`, sem `ml`.

**Mapeamento feature → fonte concreta na engine:**

| Feature | Fonte | Observação |
| ------- | ----- | ---------- |
| `duration_s` | `RouteMetrics.time_s` | direto |
| `distance_m` | `RouteMetrics.distance_m` | direto |
| `average_speed_mps` | `distance_m / duration_s` | ≡ `RouteMetrics.average_speed_mps` |
| `mechanical_work_j` | `RouteMetrics.mechanical_energy_j` | trabalho na **roda** |
| `rider_work_j` | `RouteMetrics.energy_j` | trabalho no **pedivela** |
| `mean_power_w` | `rider_work_j / duration_s` | potência média no pedivela |
| `climbing_m` / `descending_m` | `RouteMetrics.ascent_m` / `descent_m` | direto |
| `elevation_coverage` | `ElevationUncertainty.coverage` | 1 − missing/total |
| `observed_elevation_coverage` | `observed_distance_m / total_distance_m` | qualidade estrita |
| `braking_energy_j` | `RouteMetrics.braking_energy_j` | direto |
| `aero_loss_j` / `rolling_loss_j` | `RouteMetrics.aero_loss_j` / `rolling_loss_j` | perdas **assinadas** |
| `result_status` | `TraversalPath.result_status` | propagado |
| `used_min_speed_regularization` | `TraversalPath.used_min_speed_regularization` | limitação numérica |
| `elevation_uncertainty` | `TraversalPath.elevation_uncertainty` | qualidade estruturada |
| `peak_power_w` | — | **`None`**: potência constante por arco; série por passo não retida |
| `power_variability` | — | **`None`**: mesma razão |
| `metabolic_energy_j` | — | **`None`** (decisão 3; nunca 0) |
| `battery_energy_j` | `RouteMetrics.battery_energy_j` | `None` (bicicleta convencional) |

**Relatório de compatibilidade com o ADR-025:**

- **§6 (contrato de features):** `EffortFeatures` é imutável (`frozen`) e
  versionado (`schema_version = "1"`, `EFFORT_FEATURES_VERSION`), com unidades
  explícitas. A ausência de dados é explícita (`None` + `unavailable_fields`);
  nenhum campo fisiológico (Borg, FC, fadiga) pertence ao contrato.
- **Invariante "mesma representação para todas as rotas":** o extrator depende só
  do resultado físico; rotas tradicionais e geradas por ML produzem o mesmo
  contrato. Coberto por teste de independência (AST: sem import de `search`/`ml`).
- **Invariante "não apresentar previsão como medição":** não há previsão neste
  contrato; `peak_power_w`/`power_variability`/`metabolic_energy_j` **não** são
  estimados.
- **Decisão 2.** Qualidade altimétrica, `SimulationStatus` e o uso do regime
  regularizado (`MIN_SPEED_MPS`) são propagados; a precisão não é
  superestimada.
- **Decisão 3.** `metabolic_energy_j = None` (não calibrado).
- **Escopo negativo respeitado:** o integrador, o modelo de baixa velocidade e
  os algoritmos de busca **não** foram modificados; a física numérica permanece
  idêntica (aditivo).
- **Testes:** `tests/test_effort_features.py` (19) — unidades, consistência
  energia/potência, dados ausentes, cobertura/status/regularização,
  reprodutibilidade, independência do gerador, versionamento/serialização e
  imutabilidade/validação.

### 8.4 Incremento 7 — Fisiologia + ML: contratos e fronteira de inferência

**Pacotes:** `physiology/` (contratos) e `ml/` (inferência). Nenhum modelo
**calibrado** é incluído — sem dados nem método de validação, um preditor não
pode produzir Borg/FC sem inventar. Entrega-se o **contrato** e o **seam**.

**`physiology/`**

| Módulo | Conteúdo | Invariante do ADR |
| ------ | -------- | ----------------- |
| `scales.py` | `BorgScale` (`RPE_6_20` = 6–20, **padrão**; `CR10` = 0–10), `DEFAULT_BORG_SCALE`, `contains`/`clamp`/`require_valid` | escala **explícita** (decisão 8) |
| `zones.py` | `HeartRateZoneDefinition`, `HeartRateZoneModel` (definição + versão identificadas), `validate_distribution`/`is_normalized`, `DISTRIBUTION_TOLERANCE` | zonas por **definição identificada**, sem percentuais implícitos |
| `metabolic.py` | `MetabolicParameters` (eficiência bruta + `version` + `provenance`), `metabolic_energy_j` | só com calibração; `None` sem parâmetros (**nunca 0**, decisão 3) |
| `prediction.py` | `RiderProfile`, `PredictionContext`, `PhysiologicalPrediction` (`PHYSIOLOGY_VERSION = "1"`), `PredictionStatus` | previsão **nunca** medição; estado explícito |

**`ml/`** — `predictor.py`:

- `EffortPredictor` (Protocol `runtime_checkable`): `model_version` + `predict`;
  pode declarar `required_inputs`.
- `UnavailablePredictor` (**padrão**): devolve `predicted_rpe`,
  `heart_rate_zone_distribution`, `confidence` e `metabolic_energy_j` como
  `None`; estado `MODEL_NOT_CALIBRATED` (ausência explícita).
- `MetabolicEnergyPredictor`: preenche **apenas** `metabolic_energy_j` a partir
  de `MetabolicParameters` explícitos; Borg/FC/confiança continuam `None`
  (estado fisiológico `MODEL_NOT_CALIBRATED`).
- `predict_effort(features, rider, context, *, predictor=None)`: ponto de
  entrada **guardado** que diferencia, sem inventar valores:
  `CONTRACT_INCOMPATIBLE` (schema de `EffortFeatures` não suportado),
  `UNAVAILABLE` (sem preditor), `INSUFFICIENT_DATA` (faltam `required_inputs`,
  com `missing_inputs`) e, por delegação, `MODEL_NOT_CALIBRATED`/`AVAILABLE`.

**Relatório de compatibilidade com o ADR-025:**

- **§2.1, decisão 8.** Escala Borg explícita e **6–20 original como padrão**
  (`DEFAULT_BORG_SCALE = BorgScale.RPE_6_20`); CR10 continua suportada. Zonas
  cardíacas exigem `definition` + `version` e bordas explícitas em bpm — sem
  percentuais de FC máxima codificados.
- **§6, invariantes.** `predicted_rpe` é validado contra os limites da escala;
  a distribuição de zonas é não negativa e soma 1 (tolerância
  `DISTRIBUTION_TOLERANCE`); a ausência é explícita (`status` +
  `unavailable_fields`); `source="prediction"` e `is_measurement=False`
  impedem apresentar previsão como medição; `confidence` numérica exige
  `confidence_method`. Quando `status != AVAILABLE`, RPE e zonas são
  obrigatoriamente `None` — a indisponibilidade é classificada, não inventada.
- **Fronteira (decisão 7).** `EffortFeatures` (incremento 6) é a **única** entrada
  física; o preditor não lê roteamento nem recalcula física, logo não altera
  geometria/restrições. Teste por **AST**: `ml/predictor.py` não importa
  `search` e depende só de `physical.effort`/`physiology` (stdlib) — portanto
  vale para rotas tradicionais ou geradas por ML.
- **Escopo negativo respeitado:** física, integrador, modelo de baixa velocidade
  e algoritmos de busca **intactos**; incremento puramente aditivo.
- **Testes:** `tests/test_physiology.py` (36) e `tests/test_ml_predictor.py` (18)
  — escalas/limites, zonas/distribuição, metabólico (`None` vs presente),
  invariantes de previsão (RPE, definição, confiança, imutabilidade,
  serialização), estados de indisponibilidade (as cinco categorias),
  protocolo, ausência explícita, reprodutibilidade, independência do gerador e
  integração física → features → previsão.

### 8.5 Incremento 8 — Estimador aproximado vs simulador completo: relatório

**Módulo:** `search/approximate.py` (novo, aditivo). **Testes:**
`tests/test_search_approximation.py` (25).

**O estimador.** `estimate_arc` percorre um arco por **equilíbrio de
velocidade** (um passo por arco): inclinação **média**
`atan2(ascent − descent, distance)`, velocidade final = velocidade de
equilíbrio, `tempo = distância / max(velocidade, MIN_SPEED_MPS)`, energia do
pedivela `= potência × tempo` (só enquanto há pedalada), perdas de
aero/rolamento de `forces_at`. Reutiliza **apenas** primitivos da engine
(`equilibrium_speed_mps`, `forces_at`, `ArcProfile.from_arc`,
`classify_status`, `MIN_SPEED_MPS`): **nenhum parâmetro físico novo**. A
diferença em relação ao simulador é de **resolução** (1 passo por arco),
não de física. `SteadyStateTraversalModel` expõe o estimador como
`TraversalModel`.

**Admissibilidade não é presumida.** O estimador **não** declara
`max_speed_mps`; a busca o vê como teto **desconhecido** (`UNKNOWN_SPEED`) e o
limite de tempo da heurística padrão cai para `0` (admissível por construção).
Isso é necessário porque o custo do estimador **não** é um limite inferior
garantido — ele pode **subestimar** (partida do repouso: ignora a aceleração)
ou **superestimar** (entrada em alta velocidade: é independente do estado).
Casos determinísticos cobertos por teste:

| Caso (`pytest`) | Simulador | Estimador | Veredito |
| --------------- | --------- | --------- | -------- |
| Plano 200 m, 200 W, repouso | 30,497 s | 24,146 s | **subestima** ~20,8% |
| Plano 300 m, 100 W, v₀=14 m/s | 32,292 s | 47,376 s | **superestima** ~46,7% |
| Idêntico estado (v₀=0 vs 14 m/s) | tempos **diferentes** | tempos **iguais** | estimador **ignora o estado** |
| Descida 400 m, limite 20 km/h | 5,556 m/s | 5,556 m/s | limite **respeitado** |
| Subida íngreme, 5 W | 100,0 s (piso) | 100,0 s (piso) | regime regularizado **exposto** |

**Erro, custo e ordenação (varredura reprodutível).** 3000 grafos aleatórios
(semente `20261111`; 5–7 nós, 6–10 arcos com elevação e limites; 4 perfis),
`CyclistModel(75 kg, 800 W, 250 W, 0,5 m²)`, `BicycleModel()`,
`Environment()`; custo em **avaliações de passo**:

| Métrica | Resultado |
| ------- | --------- |
| Grafos com fronteira alcançada | 2208 |
| Fronteira com ≥ 2 rotas | 1502 |
| Conjunto de rotas **difere** | **269** (17,9% dos multi-rota) |
| **Rota preferida** muda | **34** (2,3%) |
| ≥ 1 **inversão** de ordem | **60** (4,0%) |
| Custo: passos simulador → estimador | 911 512 → 23 973 (**fator 38,0×**) |
| Erro relativo de **tempo** | média 13,5% · mediana 13,0% · p90 21,0% · máx 37,6% |
| Erro relativo de **energia** | média 43,6% · máx 100% |
| Erro absoluto de **velocidade final** | média 2,05 · p90 4,11 · máx 6,57 m/s |

Pares determinísticos que viram teste (`compare_search_rankings`):

- **contração de fronteira** — o estimador reduz 3 rotas para 2 (mantém a
  preferida);
- **inversão + troca da preferida** — mesmo conjunto de 3 rotas, mas 1 par
  invertido (`order_agreement = 2/3`) e a rota preferida muda.

**Limitações identificadas (registradas, não mascaradas):**

1. **Independência do estado.** A velocidade de equilíbrio ignora a velocidade
   de entrada: o estimador erra para mais ou para menos conforme o estado —
   por isso **não** é heurística admissível.
2. **Partida do repouso.** Ignora a aceleração; subestima o tempo no início de
   rotas (o caso mais comum).
3. **Arcos mistos.** Subida seguida de descida com ganho líquido ≈ 0 vira um
   único “plano” — perde o efeito de subir devagar e descer rápido.
4. **Limite de velocidade por passo.** Usa `min` dos limites do arco e assume
   velocidade constante; a dinâmica de frenagem/aceleração em torno do limite
   não é integrada (a energia do pedivela pode divergir muito: máx 100%).
5. **Erro de energia acompanha o de tempo** em pedalada constante
   (`E = P·t`); onde a pedalada liga/desliga (limite), os erros se descolam.
6. **Status/limitação propagados** (`result_status`,
   `used_min_speed_regularization`), mas a **precisão não é superestimada**:
   o relatório acompanha cada resultado.

**Relatório de compatibilidade com o ADR-025:**

- **Decisão de desempenho (§10 do ADR).** Estimador aproximado para busca +
  simulador completo para avaliação/validação; comparado ao simulador e
  **não** tratado automaticamente como heurística admissível. Atendido: o
  estimador não anuncia teto de velocidade e a comparação é explícita.
- **Escopo negativo respeitado.** Física, integrador, modelo de baixa
  velocidade e algoritmos de busca **intactos**; nenhum parâmetro físico novo.
  Teste de não-regressão confirma que estimador/relatório **não** alteram o
  resultado do simulador.
- **Entrega exigida pelo incremento.** Erro de tempo, energia e velocidade
  final, custo computacional e impacto na ordenação — quantificados acima,
  antes de considerar o ADR-025 concluído.
- **Decisão de aceitação (ADR-025 §10.1).** O incremento é **aceito como
  experimental/opcional**: o estimador **não** entra no fluxo principal de
  classificação, o **simulador completo** continua como avaliador/validador das
  rotas e o algoritmo é **preservado** em `search/approximate.py` para
  otimizações futuras. **Admissibilidade e otimalidade não são presumidas** e
  devem ser verificadas **separadamente** quando custos aproximados forem
  usados.

---

## 9. Riscos e questões abertas (resolvidas na revisão de 2026-11-10)

> As oito questões abaixo foram **decididas** na revisão técnica de 2026-11-10;
> ver ADR-025 §2.1 para a decisão vinculante de cada uma. O texto abaixo é o
> registro original da auditoria.

1. **Nome `PhysicalState` duplicado.** O ADR propõe `PhysicalState` com
   `elapsed_s/speed_mps/wheel_work_j/rider_work_j/braking_loss_j`; já existe
   `search.state.PhysicalState(speed_mps, kinetic_energy_j)`. Qual vira o
   contrato canônico? (proposta: o do ADR, com `kinetic_energy_j` **derivado**.)
   → **Decisão 1:** `SimulationState` é o estado da dinâmica; o da busca é
   preservado como estado específico da busca.
2. **Incerteza de elevação.** Representar como campo (`unknown_elevation_m`,
   `coverage`) no resultado, ou como enum de qualidade? Qual a política
   (resultado parcial × incerteza × indisponibilidade)?
   → **Decisão 2:** contrato estruturado + `SimulationStatus`.
3. **Modelo metabólico.** Forma e parâmetros exigem calibração e **não** podem
   ser números mágicos — devem viver em configuração (nos moldes de
   `GenericEnergyConfig`). Fonte dos parâmetros?
   → **Decisão 3:** `MetabolicParameters` versionado com proveniência;
   indisponível ⇒ `None`.
4. **`Surface` → `rolling_resistance`.** O ADR §7 lista `Surface` como entrada de
   rolamento; hoje é constante em `BicycleModel`. Introduzir o mapeamento exige
   calibração; **adiar** ou incluir?
   → **Decisão 4:** formalizar o contrato agora; preservar o comportamento.
5. **Vento vetorial.** Hoje `headwind_mps` é escalar. §7 pede componente pela
   geometria orientada → requer direção. Modelo a definir.
   → **Decisão 5:** vetor local com projeção na trajetória.
6. **Política de passo/convergência.** Qual tolerância é aceitável entre passos?
   Parametrizar o passo ou fixar um padrão justificado?
   → **Decisão 6 (resolvida, §6.1 do ADR):** primeira ordem caracterizada;
   manter o integrador; modos `SEARCH` (5 m) / `VALIDATION` (1 m); manter a
   regularização `MIN_SPEED_MPS` como hipótese **exposta** no resultado; modelo
   explícito de baixa velocidade só com evidência de melhoria.
7. **Fronteira ML.** A fisiologia pertence a `physical/` (produz features) ou a um
   pacote de ML separado que **consome** `EffortFeatures`? (proposta: features em
   `physical/`, predição fora.)
   → **Decisão 7:** features na saída da física; fisiologia em `physiology/`;
   inferência em `ml/`.
8. **Escala Borg e zonas FC.** Definir CR10 × 6–20 e a definição de zonas antes de
   qualquer `confidence`.
   → **Decisão 8:** zonas por definição identificada; escala Borg explícita.
   **Atualização:** a escala **padrão** passou a ser a **6–20 original**
   (`DEFAULT_BORG_SCALE`), com CR10 ainda suportada (ADR-025 §2.1).

## 10. Conclusão

A engine física **já atende** a maior parte das decisões 01 e 02 (estado
contínuo, propagação, separação roda/pedivela, transmissão). As lacunas são
**específicas e localizadas**: convergência de passo, incerteza de elevação,
trabalho assinado/balanço energético, `metabolic_energy_j`, features de esforço e
a interface fisiológica. **Nada exige substituir a física existente.**

**Progresso:** incrementos **1–4, 4b, 6, 7 e 8 concluídos** — `SimulationState`,
incerteza altimétrica com `SimulationStatus`, estudo de convergência, perdas
assinadas e balanço energético, modos `SEARCH`/`VALIDATION`, `EffortFeatures`,
os contratos de fisiologia + fronteira de inferência e o **estimador aproximado
vs simulador completo** (relatório e limitações em §8.5). **426 testes verdes.**
A dinâmica numérica foi **preservada**: todas as mudanças dos incrementos 3, 4,
6, 7 e 8 são de contrato/metadado ou aditivas, não de física. O incremento 5
(`Crr` por superfície e vento vetorial) tem **contratos documentados** e
implementação adiada (§8.2).

**Estado do ADR-025:** com o incremento 8 entregue (estimador + relatório de
erro/custo/ordenação e limitações identificadas), **todos os incrementos
planejados estão concluídos**. O estimador é **aceito como experimental/opcional
e permanece fora do fluxo principal** de classificação de rotas (ADR-025 §10.1):
o **simulador completo** segue como avaliador/validador e a **admissibilidade/
otimalidade** do custo aproximado não é presumida — deve ser verificada
**separadamente** quando usada. Permanecem como follow-ups dependentes de dados
ou decisão (ADR-025 §11): calibração de modelo metabólico/Borg/FC, `Crr` por
superfície e vento vetorial, modelo explícito de baixa velocidade e inércia
rotacional.
