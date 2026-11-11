# ADR-025 — Modelo físico e dinâmica do ciclista (Camada 02 — Physical Model)

- **Status:** aceito — decisões arquiteturais fechadas; **implementação em
  andamento por incrementos** (ver `docs/adr/025-auditoria-fisica.md`, §8).
- **Data:** 2026-11-10
- **Contexto:** Camada 02 (Physical Model) do projeto `roteamento com ml`. A
  engine Python é a base de organização; o `toporide` (TypeScript) é a
  **referência funcional**. Ver `docs/nomenclatura.md` (norm N-01).
- **Depende de:** [ADR-024](024-modelo-canonico-de-grafo-e-ambiente.md) (Camada
  01 — grafo, superfície, altimetria e qualidade dos dados).
- **Relacionadas:** norm N-01 (nomenclatura), ADR-022 do `toporide` (fronteira
  A\*/ML), ADR-016 do `toporide` (provedores de altimetria).

## 1. Contexto e objetivo

A Camada 01 fornece o grafo cicloviário, as características das superfícies, os
perfis altimétricos e a qualidade dos dados.

A Camada 02 transforma essas informações em uma **simulação física** do
deslocamento de um ciclista, considerando bicicleta, ambiente, potência, forças
resistivas e condições iniciais.

O objetivo é estimar:

- velocidade e tempo de percurso;
- aceleração e desaceleração;
- forças aerodinâmicas, gravitacionais e de rolamento;
- trabalho mecânico e demanda de potência;
- energia dissipada em frenagens;
- características de esforço utilizadas para prever Borg/RPE e zonas cardíacas.

**Princípio central:** aproveitar a implementação física existente no projeto
Python. Este ADR define **contratos e invariantes**; **não** autoriza reescrever
a engine nem substituir algoritmos funcionais sem justificativa técnica.

## 2. Decisões aprovadas

| Decisão            | Contrato                                                                            |
| ------------------ | ----------------------------------------------------------------------------------- |
| 01 — Estado físico | Simulação dinâmica com propagação do estado entre arcos                             |
| 02 — Energia       | Separação entre trabalho na roda, trabalho do ciclista e gasto metabólico estimado  |
| 03 — Fisiologia    | Física produz features; ML prevê Borg/RPE e zonas cardíacas                         |

Essas decisões são **independentes do algoritmo gerador da rota**. Uma rota
tradicional e uma rota neural devem utilizar o **mesmo simulador** e as **mesmas
regras físicas**.

## 2.1 Decisões da revisão técnica (2026-11-10)

A revisão aprovou o plano incremental com **oito decisões** que vinculam a
implementação. Elas têm precedência sobre exemplos de contrato meramente
ilustrativos nas seções seguintes.

| # | Questão | Decisão aprovada |
| - | ------- | ---------------- |
| 1 | Estado físico canônico | `SimulationState` é o estado da **dinâmica**; `search.state.PhysicalState` é preservado e avaliado como estado específico da busca (não renomear em massa antes de examinar dependências). **Sem** posição geográfica no contrato. |
| 2 | Incerteza de elevação | Contrato estruturado `ElevationUncertainty` + `SimulationStatus` (`VALID`/`DEGRADED`/`UNAVAILABLE`) propagado até `TraversalResult`. Elevação ausente **nunca** vira terreno plano sem **hipótese explícita** e status degradado. |
| 3 | Modelo metabólico | `MetabolicParameters` explícitos, versionados e com proveniência. Sem calibração suficiente, `metabolic_energy_j = None` (**nunca** 0). |
| 4 | Surface → `Crr` | Formalizar o contrato agora; **preservar** o `Crr` atual até existir calibração. |
| 5 | Vento vetorial | Vetor em coordenadas locais (ex.: leste/norte) com projeção na trajetória; normalizar a convenção "de onde vem" × "para onde sopra". |
| 6 | Passo/convergência | Investigado no incremento 4: esquema de **primeira ordem** no regime suave; a partida do repouso é limitada pela regularização `MIN_SPEED_MPS=0.5`. **Resolvida (aceita com limitação conhecida):** manter o integrador; adotar modos `SEARCH` (5 m) e `VALIDATION` (1 m); manter a regularização como hipótese numérica **exposta** no resultado; evoluir para modelo explícito de baixa velocidade só com evidência de melhoria. Ver §6.1. |
| 7 | Fronteira fisiológica | `EffortFeatures` na **saída** de `physical/`; contratos de fisiologia em `physiology/`; inferência neural em `ml/`. |
| 8 | Borg e zonas FC | Borg **6–20 original** como escala inicial, com suporte explícito a **CR10**. Zonas cardíacas por **definição identificada**, sem percentuais implícitos. |

Prioridades imediatas da revisão: **testes de referência, propagação de
incerteza, correção numérica e balanço energético**. A ordem de execução dos
incrementos foi revista (ver §8 do documento de auditoria).

## 3. Arquitetura lógica

```
ADR-024 · Data Model
  Arc · Segment · Surface · ElevationProfile · Quality
        │
        ▼
Rider & Bicycle            Environment
  Massa, potência,           Vento, ar, superfície,
  aerodinâmica, transmissão  declividade
        │                         │
        └────────────┬────────────┘
                     ▼
Existing Python Physics Engine
  Forças → aceleração → velocidade → tempo → energia
                     │
                     ▼
Physical Results            Effort Features
  Tempo, potência,            Intensidade, duração,
  velocidade, energia         distribuição de carga
                     │
                     ▼
ML · Physiological Prediction
  Borg/RPE · Zonas cardíacas · Incerteza
```

O ML **não** substitui a dinâmica física. Ele consome os resultados e as
características produzidas pela simulação.

## 4. Decisão 01 — Estado físico contínuo

A dinâmica longitudinal deve obedecer ao balanço de forças:

```
m_eff · dv/dt = F_prop − F_roll − F_aero − F_grade − F_brake
```

Onde:

- `m_eff`: massa efetiva, incluindo eventual correção de inércia rotacional.
- `F_prop`: força propulsiva.
- `F_roll`: resistência ao rolamento.
- `F_aero`: força aerodinâmica com sinal.
- `F_grade`: componente gravitacional ao longo da trajetória.
- `F_brake`: força de frenagem.

### Contrato de estado

```python
@dataclass(frozen=True)
class PhysicalState:
    elapsed_s: float
    speed_mps: float
    wheel_work_j: float
    rider_work_j: float
    braking_loss_j: float
```

> Este é o **contrato lógico mínimo proposto**. Não é exigência de substituir
> classes já existentes; a auditoria (`025-auditoria-fisica.md`) mapeia os nomes
> atuais (`TraversalResult`, `SearchState`, `PhysicalTraversal`) para este
> contrato e decide a forma final de adaptação.

A simulação de um arco recebe um estado inicial e devolve um estado final:

```python
simulate_arc(
    arc_profile,
    rider,
    bicycle,
    environment,
    initial_state,
) -> ArcSimulationResult
```

O estado final do arco anterior deve alimentar o arco seguinte, **salvo
transições explícitas** como parada, frenagem ou interrupção.

### Invariantes

1. A velocidade nunca pode ser negativa.
2. O tempo acumulado não pode diminuir.
3. O trabalho positivo acumulado do ciclista não pode diminuir.
4. O estado de saída de um arco é o estado de entrada do próximo, quando não há
   evento intermediário.
5. A simulação deve respeitar limites físicos e numéricos.
6. Uma **falha de convergência** deve ser **sinalizada**, não mascarada por um
   valor plausível inventado.
7. O resultado de uma rota deve ser **independente** de qual algoritmo a gerou.

### Momentum

A energia cinética é `E_k = ½ · m_eff · v²`.

Uma descida pode gerar velocidade que será aproveitada na subida seguinte.
Portanto, o custo físico de um arco **pode depender da velocidade de entrada**.

Consequência: **não** devemos presumir que exista um custo energético fixo e
universal para cada arco.

## 5. Decisão 02 — Energia separada

O modelo deverá distinguir três grandezas:

| Grandeza              | Unidade | Responsabilidade                                   |
| --------------------- | ------- | -------------------------------------------------- |
| `wheel_work_j`        | J       | Trabalho mecânico positivo entregue à roda         |
| `rider_work_j`        | J       | Trabalho mecânico positivo produzido no pedivela   |
| `metabolic_energy_j`  | J       | Estimativa de energia metabólica                   |

A eficiência da transmissão relaciona potência no pedivela e potência na roda:

```
P_wheel = η_drive · P_crank
```

Para o trabalho positivo de propulsão:

```
W_wheel = ∫ P_wheel⁺(t) dt
W_rider = ∫ P_crank⁺(t) dt
```

O **gasto metabólico** será estimado **separadamente**, com um modelo fisiológico
e parâmetros identificados.

Não se deve confundir:

- trabalho mecânico produzido;
- energia cinética armazenada;
- energia potencial gravitacional;
- energia dissipada por frenagem;
- gasto metabólico humano.

### Balanço energético

Uma verificação física importante é:

```
ΔE_k + ΔE_p = W_wheel,signed − W_aero,loss − W_roll,loss − W_brake,loss
```

A expressão assume o modelo longitudinal, **sem outras fontes de energia ou
perdas não representadas**. O trabalho **assinado** na roda deve ser distinguido
do **acumulador** de trabalho positivo.

### Invariantes

1. Energia não pode ser criada por frenagem.
2. A transmissão não pode produzir mais trabalho mecânico do que recebe, quando
   não há outra fonte energética modelada.
3. Descidas podem aumentar a energia cinética sem pedalada.
4. Frenagem dissipa energia mecânica.
5. O gasto metabólico **não** deve ser calculado como se fosse idêntico ao
   trabalho mecânico.
6. Todas as grandezas devem usar **unidades explícitas**.

## 6. Decisão 03 — Borg e frequência cardíaca

A física produz resultados quantitativos, mas **não** determina a percepção
subjetiva de esforço. Por isso, a Camada 02 disponibilizará **características**
para um preditor fisiológico.

### Contrato de features

Contrato **imutável** e **versionado**, implementado no incremento 6 em
`physical/effort.py` (ver `025-auditoria-fisica.md` §8.3):

```python
@dataclass(frozen=True, slots=True)
class EffortFeatures:
    schema_version: str                  # constante da classe: "1"
    duration_s: float
    distance_m: float
    average_speed_mps: float
    mechanical_work_j: float              # trabalho na roda
    rider_work_j: float                   # trabalho no pedivela (crank)
    mean_power_w: float
    peak_power_w: float | None            # None: serie por passo nao retida
    power_variability: float | None       # None: idem
    climbing_m: float
    descending_m: float
    elevation_coverage: float | None
    observed_elevation_coverage: float | None
    braking_energy_j: float
    aero_loss_j: float                    # perda assinada
    rolling_loss_j: float
    battery_energy_j: float | None        # None na bicicleta convencional
    metabolic_energy_j: float | None      # None: nao calibrado (decisao 3)
    result_status: SimulationStatus
    used_min_speed_regularization: bool
    elevation_uncertainty: ElevationUncertainty | None
```

O extrator é determinístico e depende apenas do resultado físico
(`TraversalPath`) — **sem** ML nem roteamento. `peak_power_w` e
`power_variability` permanecem `None` enquanto a engine **não** retiver a série
de potência por passo: não se infere pico nem variabilidade. `unavailable_fields`
e `to_dict()` representam a ausência explicitamente.

O contrato **poderá** ser estendido com tempo por faixa de potência, duração de
esforços intensos e recuperação (quando houver granularidade por passo).

A saída fisiológica é produzida por um **serviço separado**, implementado no
incremento 7 (`ml/`), consumindo `EffortFeatures`:

```python
predict_effort(
    features,          # EffortFeatures (incremento 6)
    rider_profile,     # RiderProfile (opcional)
    context,           # PredictionContext (opcional)
    *, predictor=None, # EffortPredictor; padrao: UnavailablePredictor
) -> PhysiologicalPrediction
```

### Contrato de previsão

Contrato **imutável** e **versionado** (`PHYSIOLOGY_VERSION`, `schema_version`),
implementado em `physiology/prediction.py` (ver `025-auditoria-fisica.md` §8.4):

```python
@dataclass(frozen=True, slots=True)
class PhysiologicalPrediction:
    borg_scale: BorgScale                 # escala explicita (padrao 6-20)
    predicted_rpe: float | None           # respeita os limites da escala
    heart_rate_zone_distribution: dict[str, float] | None
    model_version: str
    status: PredictionStatus              # AVAILABLE | UNAVAILABLE |
                                          # MODEL_NOT_CALIBRATED |
                                          # INSUFFICIENT_DATA |
                                          # CONTRACT_INCOMPATIBLE
    confidence: float | None = None       # so com confidence_method
    confidence_method: str | None = None
    heart_rate_zone_definition: str | None = None
    prediction_interval_s: float | None = None
    metabolic_energy_j: float | None = None
    reason: str | None = None
    missing_inputs: tuple[str, ...] = ()  # so com INSUFFICIENT_DATA
    # classvars: schema_version = "1"; source = "prediction"; is_measurement = False
```

`confidence` **não** é atribuída sem um **método de validação** declarado
(`confidence_method`): o campo é validado e só então aceito. A previsão informa
a **definição das zonas cardíacas** (obrigatória quando há distribuição) e o
**intervalo temporal**. A natureza do dado é fixa: `source="prediction"` e
`is_measurement=False`.

**Estados explícitos.** `prediction status` nunca confunde ausências distintas:

| Estado | Significado |
| ------ | ----------- |
| `AVAILABLE` | um modelo calibrado produziu RPE e/ou distribuição de zonas |
| `UNAVAILABLE` | nenhum preditor foi configurado |
| `MODEL_NOT_CALIBRATED` | há preditor, mas sem modelo fisiológico calibrado |
| `INSUFFICIENT_DATA` | faltam entradas exigidas (`missing_inputs`) |
| `CONTRACT_INCOMPATIBLE` | schema de `EffortFeatures` não suportado |

Quando `status != AVAILABLE`, `predicted_rpe` e `heart_rate_zone_distribution`
são **obrigatoriamente** `None`. Sem modelo calibrado, o preditor padrão
(`UnavailablePredictor`) devolve `None` para tudo que exigiria calibração —
nunca um valor fabricado.

### Invariantes

- O ML recebe a **mesma** representação física para todas as rotas.
- A escala Borg deve ser **explícita**: **6–20 (original, padrão)** ou CR10.
- Valores previstos devem respeitar os limites da escala escolhida.
- A distribuição de tempo por zonas deve ser **não negativa** e **somar 1**,
  dentro da tolerância numérica, quando disponível.
- Ausência de dados fisiológicos deve ser representada **explicitamente**. O
  `status` distingue **por que** não há valor; quando não é `AVAILABLE`, RPE e
  zonas são obrigatoriamente `None`.
- O sistema **não** pode apresentar previsão como medição real.
- O preditor **não** pode alterar a geometria ou as restrições da rota.

## 6.1 Decisão 06 — integração numérica e baixa velocidade (resolvida)

**Status: aceita, com limitação conhecida.** A política aprovada é:

1. **Discretização.** Manter o integrador atual de primeira ordem. Adotar dois
   modos de execução, com passos que são **configurações explícitas**, não
   constantes universais de precisão:
   - `SEARCH` — passo espacial padrão de **5 m**, priorizando desempenho (modo
     padrão);
   - `VALIDATION` — passo espacial padrão de **1 m**, permitindo refinamento
     conforme a necessidade de precisão.
   Preservar testes de convergência, balanço energético e comparação entre
   modos; validar o impacto do passo sobre a **ordenação** das rotas candidatas.
   O modo `VALIDATION` **não** garante alta fidelidade na partida do repouso
   enquanto persistir a limitação do modelo de baixa velocidade.

2. **Modelo de baixa velocidade.** Manter temporariamente a regularização
   `MIN_SPEED_MPS = 0.5`, documentada como **hipótese numérica** e limitação
   física conhecida — **não** como limite de força/torque empiricamente
   calibrado. Não introduzir parâmetros arbitrários de torque, força ou
   potência. Registrar testes reproduzíveis de partida do repouso e caracterizar
   o erro associado. A utilização do regime regularizado é **exposta** no
   resultado (`TraversalResult.used_min_speed_regularization`, propagado a
   `TraversalPath` e presente em `to_dict`), para que a precisão não seja
   superestimada.

3. **Evolução futura.** Investigar um modelo explícito de partida e baixa
   velocidade, com parâmetros físicos identificáveis e calibração documentada. A
   substituição do modelo atual dependerá de evidências de melhoria em partida,
   regime suave, balanço energético e desempenho computacional.

4. **Critério de aceite.** A implementação pode prosseguir para os próximos
   incrementos sem substituir o integrador, desde que a limitação esteja
   documentada, coberta por testes e **não** seja apresentada como precisão
   física validada em todos os regimes.

A caracterização quantitativa (primeira ordem, banda do repouso, resíduo do
balanço) está em `025-auditoria-fisica.md` §7.1; a cobertura de testes em
`tests/test_physical_convergence.py` (incremento 4) e `tests/test_physical_modes.py`.

## 7. Contrato de integração com o ADR-024

A camada física recebe dados geográficos **já normalizados**, mas **não** deve
modificar o grafo.

| Entrada                  | Uso na física                                  |
| ------------------------ | ---------------------------------------------- |
| `ArcProfile`             | Distância, elevação e inclinação               |
| `Surface`                | Resistência ao rolamento                       |
| `ElevationQuality`       | Qualidade e incerteza dos cálculos             |
| Geometria orientada      | Direção do deslocamento e componente do vento  |
| Restrições de circulação | Validação externa à física                     |
| Identificadores de arcos | Rastreabilidade dos resultados                 |

A física **não** deve decidir se uma rua é legalmente permitida para bicicletas.
Essa responsabilidade pertence ao modelo de circulação e à validação de rotas.

Também **não** deve transformar elevação ausente em terreno plano. Quando a
qualidade for insuficiente, deverá devolver resultado parcial, incerteza ou
indisponibilidade explícita, conforme uma política definida.

## 8. Estratégia de reaproveitamento da engine Python

A implementação existente é a **referência inicial**. Nenhum módulo novo deverá
ser criado antes de verificar se a mesma responsabilidade já existe.

| Área                       | Ação                                                                |
| -------------------------- | ------------------------------------------------------------------- |
| `physical/arc_profile.py`  | Preservar contrato e verificar integração com ADR-024               |
| Forças e resistências      | Reaproveitar equações existentes e validar sinais e unidades        |
| Velocidade e integração    | Verificar propagação do estado entre arcos                          |
| Potência e energia         | Identificar grandezas existentes antes de separá-las                |
| Rider/Bicycle              | Estender modelos atuais quando necessário                           |
| Borg e frequência cardíaca | Identificar implementações atuais e avaliar uso como baseline        |
| Serialização               | Versionar somente se houver mudança efetiva no contrato persistido   |

Os nomes exatos dos arquivos estão confirmados no repositório e no inventário
técnico (`025-auditoria-fisica.md`). Não se deve pressupor que uma estrutura
sugerida corresponda à estrutura real.

### Sequência de execução

1. **Auditoria** sem alterações: mapear módulos, equações, funções, parâmetros e
   testes existentes. → *concluída em `025-auditoria-fisica.md`.*
2. **Matriz de compatibilidade**: classificar cada componente como *preservar,
   adaptar, complementar ou substituir* mediante justificativa.
3. **Testes de referência**: registrar resultados físicos atuais em cenários
   reproduzíveis.
4. **Contratos mínimos**: adaptar a API existente para estado contínuo e
   grandezas energéticas distintas.
5. **Interface fisiológica**: disponibilizar features para ML, sem duplicar a
   simulação.
6. **Regressão**: comparar resultados antes e depois, investigando alterações
   físicas e numéricas.

## 9. Critérios de aceite

A Camada 02 somente será considerada implementada quando os seguintes testes
forem satisfeitos:

| Cenário                                  | Comportamento esperado                                              |
| ---------------------------------------- | ------------------------------------------------------------------- |
| Plano, sem vento, velocidade constante   | Potência compensa resistências                                      |
| Subida                                   | Aumento da demanda mecânica nas mesmas condições                    |
| Descida sem pedalada                     | Possibilidade de ganho de velocidade                                |
| Frenagem                                 | Dissipação energética não negativa                                  |
| Vento contrário e favorável              | Força aerodinâmica com direção correta                              |
| Arcos consecutivos                       | Continuidade do estado físico                                       |
| Arco dividido por snapping               | Resultado equivalente dentro da tolerância                          |
| Passo de integração refinado             | Convergência numérica                                               |
| Elevação ausente                         | Ausência ou incerteza explícita                                     |
| Mesma rota gerada por métodos diferentes | Resultados físicos equivalentes                                     |
| Predição Borg/FC                         | Saída identificada como estimativa, com escala e definição de zonas |

Os testes devem incluir cenários com **resultados analíticos conhecidos**, além
de testes de regressão. As tolerâncias numéricas devem ser **justificadas**, não
escolhidas arbitrariamente.

## 10. Consequências arquiteturais

**Benefícios:** física reutilizável, continuidade dinâmica, energia corretamente
separada, interface estável para ML e avaliação consistente entre geradores de
rotas.

**Custos:** o estado de entrada pode influenciar o custo do arco; simulações
completas podem ser computacionalmente caras; resultados fisiológicos exigem
calibração e validação próprias.

**Decisão de desempenho:** permitir um **estimador físico aproximado** para busca
e manter o **simulador completo** para avaliação e validação da rota. O
estimador deverá ser comparado ao simulador, **sem** ser tratado automaticamente
como heurística admissível do A\*.

**Fora do escopo imediato:** treinamento de redes neurais, nova estratégia de
roteamento, reestruturação integral dos módulos Python e calibração individual
de Borg/FC.

### 10.1 Estimador aproximado de busca (incremento 8) — experimental/opcional

O estimador físico aproximado de busca foi implementado em `search/approximate.py`
(`SteadyStateTraversalModel`, `estimate_arc`, `compare_traversals`,
`RankingComparison`/`compare_rankings`, `SearchRankingComparison`/
`compare_search_rankings`) e é **aceito como experimental e opcional**.

Regras vinculantes:

- **Não integrado ao fluxo principal de classificação de rotas.** O modelo não é
  consumido por `pipeline.py`, `selection.py` nem `astar.py`; a busca e a
  avaliação permanecem no **simulador físico completo**.
- O **simulador completo** (`PhysicalTraversalModel`,
  `PhysicalTraversal.traverse_path`) permanece o único responsável por
  **avaliar e validar** cada rota. O estimador **não substitui** o simulador.
- O algoritmo é **preservado** em `search/approximate.py` como implementação
  **disponível** para otimizações futuras, **sem** troca automática.
- **Admissibilidade e otimalidade não são presumidas.** O custo aproximado pode
  **subestimar** ou **sobreestimar** o tempo e é independente do estado de
  entrada. A verificação de admissibilidade e de otimalidade deve ser feita
  **separadamente** sempre que custos aproximados forem usados na busca. O
  estimador não declara `max_speed_mps`, de modo que o limite heurístico padrão
  é 0 (**admissível por construção**, sem presumir admissibilidade).
- **Sem novos parâmetros físicos arbitrários:** o estimador reutiliza apenas
  `equilibrium_speed_mps`, `forces_at`, `ArcProfile.from_arc`,
  `classify_status` e `MIN_SPEED_MPS`.

Evidência experimental: ver §8.5 da auditoria física e os testes em
`tests/test_search_approximation.py` (incluindo casos determinísticos de
contração de fronteira e inversão de ordenação).

**Arquitetura de prioridade preservada (ordem de execução):**

1. algoritmos tradicionais (e, opcionalmente, ML) geram **rotas candidatas**;
2. todas as candidatas passam pelas **mesmas restrições de circulação**;
3. o **simulador físico completo** avalia cada rota;
4. `EffortFeatures` alimenta o **preditor fisiológico** de Borg e zonas de FC;
5. o sistema **classifica** as rotas segundo os resultados e as preferências do
   ciclista.

O estimador aproximado **não altera** nenhum desses passos, enquanto não houver
decisão explícita em contrário com evidência de melhoria.

## 11. Fora de escopo e follow-ups registrados

- Reorganização de diretórios da camada física (proibida nesta etapa).
- Modelo metabólico calibrado (entra com a interface fisiológica, decisão 03).
- ~~Estimador aproximado dedicado à busca (decisão de desempenho).~~
  **Implementado no incremento 8** (`search/approximate.py`): estimador por
  equilíbrio de velocidade + relatório de erro/custo e impacto na ordenação,
  **sem** tratá-lo como heurística admissível (ver `025-auditoria-fisica.md`
  §8.5). **Aceito como experimental/opcional e fora do fluxo principal**; o
  simulador completo segue sendo o avaliador/validador das rotas (ver §10.1).
- Correção de inércia rotacional em `m_eff`.
- ~~Verificação formal de convergência do passo de integração.~~ **Investigada
  no incremento 4 e resolvida na Decisão 06 (§6.1)**: o esquema é de primeira
  ordem no regime suave; a partida do repouso é limitada pelo modelo de força a
  baixa velocidade (`MIN_SPEED_MPS`), que permanece como **lacuna conhecida**
  (ver `025-auditoria-fisica.md` §7.1). Mitigação adotada: modos
  `SEARCH`/`VALIDATION` e exposição de `used_min_speed_regularization`.
- Integrador de ordem superior / controle de erro adaptativo: **rejeitado por
  ora** (o corretor de ponto médio degrada a partida do repouso); reavaliar junto
  com a correção do modelo de baixa velocidade.
- Modelo explícito de partida e baixa velocidade, com parâmetros identificáveis e
  calibração documentada (Decisão 06, item 3): substitui a regularização apenas
  com evidência de melhoria em partida, regime suave, balanço e desempenho.
- ~~Contrato de `EffortFeatures`.~~ **Implementado no incremento 6**
  (`physical/effort.py`): contrato imutável/versionado + extrator determinístico,
  com qualidade/status/limitação propagados e ausência explícita (ver
  `025-auditoria-fisica.md` §8.3).
- **`Surface → Crr` (decisão 4)** e **vento vetorial (decisão 5):** contratos
  **documentados e adiados** (§8.2 da auditoria) — implementação só com
  calibração/rumo; nenhum coeficiente não calibrado foi introduzido.
- Série de potência por passo (para `peak_power_w`/`power_variability` e tempo
  por faixa): hoje indisponível; o contrato marca explicitamente `None`.
- ~~Modelo metabólico, predição de Borg e zonas de FC.~~ **Contratos e fronteira
  implementados no incremento 7** (`physiology/` + `ml/`): escala de Borg
  explícita (**6–20 original, padrão**; CR10 suportada), zonas por definição
  identificada, `MetabolicParameters` explícitos, `PhysiologicalPrediction`
  (nunca medição) e o seam `EffortPredictor` (ver `025-auditoria-fisica.md`
  §8.4). **Pendente:** um modelo **calibrado** (metabólico/Borg/FC) e um método
  de validação — sem dados, o preditor padrão declara a ausência.
- Calibração da energia metabólica (eficiência bruta) e de Borg/FC com dados
  reais, com proveniência e versão — condição para preencher `predicted_rpe`,
  distribuição de zonas e `confidence`.
- ~~Fluxo de avaliação/classificação ponta a ponta.~~ **Implementado**
  (Camada 05, `src/evaluation/`): candidatas → mesmas restrições de circulação →
  **simulador completo** → `EffortFeatures` → preditor fisiológico →
  **classificação explícita** (`REJECTED`/`PHYSICAL_ONLY`/`PHYSIOLOGICAL`). Sem
  modelo calibrado, a classificação é `PHYSICAL_ONLY` e a ausência é declarada
  (`PredictionStatus`), nunca inventada. Ver ADR-026 §5. **Pendente:** o próprio
  modelo calibrado (bullet acima).

## 12. Instrução de implementação (portão de fase)

**ATENÇÃO: a engine Python já possui implementações físicas. Não recrie a
física existente.** Antes de modificar qualquer código:

- inspecionar todos os módulos físicos e seus testes;
- identificar classes, funções, equações, unidades, parâmetros e integrações;
- verificar o tratamento de velocidade, aceleração, momentum, potência, energia
  e esforço;
- identificar quais contratos deste ADR já são atendidos;
- classificar cada componente como **PRESERVAR, ADAPTAR, COMPLEMENTAR** ou
  **SUBSTITUIR**, justificando qualquer substituição;
- produzir a **matriz de compatibilidade** e o **plano mínimo de alterações**;
- registrar os **testes de referência** necessários para detectar regressões.

**Não** introduzir novos algoritmos, parâmetros arbitrários ou reorganizações de
diretórios nesta etapa.

> **Entregáveis da primeira fase:** este documento, o inventário técnico da
> física Python, a análise de lacunas e o plano incremental — consolidados em
> [`025-auditoria-fisica.md`](025-auditoria-fisica.md).
>
> **As alterações físicas não devem ser implementadas antes da revisão desse
> inventário.**
