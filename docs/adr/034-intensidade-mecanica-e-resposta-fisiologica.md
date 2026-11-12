# ADR-034 — Intensidade mecânica e resposta fisiológica: separação conceitual

- **Status:** **implementado** em 2026-11-12. O estimador legado foi
  **substituído** (não mantido em paralelo), por decisão explícita de produto:
  “não manter compatibilidade, refazer”. Ver §0.1 para o que foi executado e
  para as divergências deliberadas em relação ao plano original (D9 e D11).
- **Data:** 2026-11-12
- **Arquivo auditado:** `src/router/core/prediction/intensity.py` (468 linhas).
- **Relação:** revisa parcialmente o [ADR-028](028-intensidade-e-ordem-ml.md)
  (D3 e D6) à luz do compromisso de honestidade científica do
  [`README.md`](../../README.md) e da auditoria
  [`028-auditoria-rpe-toporide.md`](028-auditoria-rpe-toporide.md). **Não
  revoga** o ADR-028 D1 (contrato fisiológico intocado), D2 (proibição de rede
  fisiológica com rótulos artificiais), D4 (faixas de intensidade, não zonas
  cardíacas), D5 (distribuição por distância e duração) nem D7 (estimativa
  sempre disponível para toda rota).
- **Depende de:** [ADR-024](024-modelo-canonico-de-grafo-e-ambiente.md),
  [ADR-025](025-modelo-fisico-e-dinamica-do-ciclista.md),
  [ADR-027](027-intelligence-ml-prediction.md),
  [ADR-028](028-intensidade-e-ordem-ml.md),
  [ADR-029](029-evaluation-guardrails.md).
- **Consumidores verificados:** `core/evaluation/assessment.py`,
  `core/evaluation/ports.py`, `app/planning.py`, `app/plan_view.py`,
  `contracts/route.py` (`RouteML`), `app/legacy.py` e
  `tests/test_intelligence_intensity.py`.

> **Resumo.** O módulo atual colapsa três grandezas de naturezas diferentes —
> **demanda mecânica**, **demanda fisiológica estimada** e **resposta individual
> (Borg/FC)** — em uma única fórmula de RPE. O resultado é determinístico e
> auditável, mas não é uma estimativa fisiológica defensável: depende de uma
> massa de referência artificial (75 kg), paga a inclinação duas vezes, trata
> tráfego como esforço metabólico, acumula fadiga sem distinguir esforço leve de
> intenso e agrega RPE por distância. A decisão proposta é separar as camadas,
> tornar a intensidade mecânica relativa o artefato primário e manter Borg/FC
> como camada condicionada a calibração.

---

## 0.1. Status de implementação (2026-11-12)

Executado conforme as decisões D1–D8 e D10, com as divergências abaixo:

| Camada | Artefato | Observação |
| --- | --- | --- |
| Ciclista fisiológico | `core/physiology/rider.py` | `CapacityProfile` (6 perfis), `ActivityLevel` (6 níveis), `PowerReference` com proveniência, `HeartRateParameters`, Tanaka como equação populacional |
| Física da rota | `core/prediction/mechanical.py` | `RoutePhysics`, `PropulsionModel` (assistência opcional, padrão nenhuma), `MechanicalIntensity` |
| Resposta provisória | `core/prediction/response.py` | RPE por âncoras em `P/P_ref`, fadiga CP/W′ só quando ambos existem, FC de primeira ordem, zonas `%HRmax` e Karvonen |
| Tráfego | `core/prediction/traffic.py` | Eixo ordinal separado; não altera `I_mech` nem RPE |
| Orquestração | `core/prediction/intensity.py` | `model_id="mechanical-response"`, `model_version="2"` |

**Divergências deliberadas**

- **D9 (legado preservado):** revogada por decisão de produto. O estimador
  antigo não é mantido; `IntensityCoefficients`, `DEFAULT_INTENSITY_COEFFICIENTS`
  e `IntensitySegment` permanecem apenas como aliases para não romper a
  superfície congelada de `router.interfaces.api`.
- **D11 (semântica de `mean_rpe`):** `mean_rpe` passa a ser **ponderado por
  tempo**; `mean_rpe_distance_weighted` expõe o descritor antigo. O contrato
  `v1` continua aceitando o campo, mas sua semântica numérica mudou de forma
  explícita.
- **P2 (massa de referência):** `P_ref = massa_do_ciclista × W/kg`; a massa do
  sistema continua sendo usada apenas na física da rota.
- **P8 (RMS):** exposto como `rms_power_w`, documentado como média quadrática e
  **não** como Normalized Power.
- **Provisório:** a resposta RPE/FC é rotulada como provisória e **não**
  preenche `PhysiologicalPrediction`, que segue `UNAVAILABLE` sem calibração.

---

## 0. Contexto

O [`README.md`](../../README.md) declara que a engine não deve inventar valores de
percepção de esforço (Borg) ou frequência cardíaca quando não houver modelo
calibrado com dados reais; nesse caso, o estado explícito é
`UNAVAILABLE`/`MODEL_NOT_CALIBRATED`, e a operação é puramente física
(`PHYSICAL_ONLY`).

O `intensity.py` foi criado no ADR-028 como uma **estimativa computacional não
calibrada** (`calibrated = False`, `is_measurement = False`), espelhando o
preditor `borgRpe` do `toporide`. A rotulagem é honesta, mas o **conteúdo** da
estimativa mistura níveis que a própria arquitetura científica separa. O
problema não é ter constantes — modelos fisiológicos sempre têm —, e sim **não
distinguir o estatuto epistêmico de cada constante** e **derivar uma resposta
subjetiva de uma grandeza mecânica sem função de transferência calibrada**.

Este ADR registra o diagnóstico e a direção de correção. Ele não altera a
física, a busca, o contrato `v1` nem o comportamento atual das rotas.

---

## 1. O problema conceitual

A fórmula atual, em `segment_rpe` (`intensity.py:314-346`), é essencialmente:

```text
I_s(P, m, F) = clamp(6, 20,
    6
  + 6 * (P/m) / max(0.5, F/75)
  + G(grade)
  + 1.5 * traffic
  + 1.5 * (fatigue - 1)
)
```

com:

- `P = E_pedivela / Δt` (`rider_energy_j / duration_s`);
- `m` = massa total do sistema (ciclista + bicicleta), passada por
  `assessment.py:309-310`;
- `F` = `cyclist.sustainable_power_w`, chamado de `ftp_w` na assinatura;
- `G = max(0, grade - 5)^1.3`;
- `traffic` = estresse de tráfego da classe viária;
- `fatigue = 1 + tanh(accumulated_j / (F * 1200)) * 1.5`.

O resultado é apresentado como `mean_rpe`/`peak_rpe` em Borg e propagado até o
contrato público `RouteML` (`contracts/route.py:205-245`). A cadeia de consumo é:

```
física (P, E, Δt, grade)
        │
        ▼
segment_rpe ──► estimate_intensity ──► RouteAssessment.intensity
                                              │
                                              ├──► classificação por faixas
                                              ├──► RouteML (mean_rpe, peak_rpe)
                                              └──► segment_rpes/segment_bands
```

Esse desenho trata a **potência mecânica** como se ela já fosse uma **dose
fisiológica** e, depois, converte essa dose em **Borg** por uma soma de termos
heurísticos. São três operações conceitualmente distintas sendo feitas de uma
vez.

---

## 2. Os três níveis que não podem ser colapsados

| Nível | Grandeza | Depende de | Calibração | Saída |
| --- | --- | --- | --- | --- |
| **1. Demanda mecânica** | potência, energia, velocidade, duração, inclinação | física longitudinal, massa, arrasto, rolamento, gravidade | **não** | W, W/kg, J, s |
| **2. Demanda fisiológica estimada** | esforço relativo à capacidade | potência relativa, duração, recuperação, CP/W′ | **individual** (FTP, CP, W′) | fração da capacidade, W′bal |
| **3. Resposta individual** | FC, zonas, percepção (Borg) | nível 2 + calibração individual/populacional | **sim** | bpm, Borg, zonas |

O erro central do `intensity.py` é produzir o nível 3 diretamente do nível 1,
sem passar explicitamente pelo nível 2 e sem uma função de transferência
calibrada. A inclinação e o tráfego agravam o problema porque também entram como
termos aditivos de Borg, misturando **exigência mecânica**, **estresse
ambiental** e **percepção subjetiva** na mesma escala.

A separação não elimina constantes. Ela apenas exige que cada constante declare
**o que é**: constante física, parâmetro de literatura, parâmetro individual ou
heurística provisória.

---

## 3. Constatações no código

| # | Problema | Implementação atual | Consequência |
| - | --- | --- | --- |
| P1 | Normalização de potência | `reference = max(0.5, ftp_w / 75)` e `P/m / reference` | massa de referência artificial; dupla normalização |
| P2 | Ganho de intensidade | `scale_gain = 6.0` | sem calibração; fixa a escala de Borg |
| P3 | Penalidade de subida | `(grade - 5)^1.3` acima de 5% | dupla contagem da gravidade já presente em `rider_energy_j` |
| P4 | Tráfego | `traffic_stress * 1.5` somado ao Borg | mistura esforço metabólico e estresse ambiental |
| P5 | Fadiga | `tanh(accumulated_j / (F·1200))` | não distingue esforço leve de intenso; não recupera |
| P6 | Média de RPE | ponderação por distância | distorce a intensidade temporal |
| P7 | Pico de RPE | máximo entre trechos | sensível à segmentação da malha |
| P8 | Potência equivalente | RMS temporal proposto | não é Normalized Power; depende da resolução |
| P9 | Referência de potência | `ftp_w` recebe `sustainable_power_w` | FTP ≠ potência sustentável; nome enganoso |
| P10 | Rotulagem | `mean_rpe` no contrato `v1` | número de Borg sem calibração, ainda que rotulado |

### 3.1 P1 — A normalização introduce uma massa de referência artificial

O código faz, em `intensity.py:316-321`:

```python
watts_per_kg = segment.rider_energy_j / duration / mass_kg
reference = max(0.5, ftp_w / coefficients.ftp_reference_kg)
intensity_term = watts_per_kg / reference * coefficients.scale_gain
```

Fora do piso de `0.5`, isso equivale a:

```text
6 * (P/m) / (F/75) = 6 * (P/F) * (75/m)
```

Há duas inconsistências:

1. **Bases de massa diferentes.** O numerador usa a massa total do sistema
   (`cyclist.mass_kg + bicycle.mass_kg`, ver `assessment.py:309-310`); o
   denominador usa 75 kg, que a documentação chama de “massa de referência” sem
   dizer se é massa de ciclista ou de sistema. Bicicleta e bagagem entram no
   numerador, mas não no denominador.
2. **Normalização dupla.** `P/F` já é adimensional. Multiplicar por `75/m`
   introduz uma segunda normalização por massa que não corresponde à capacidade
   do ciclista. A razão correta entre potência específica e capacidade específica
   seria `(P/m) / (F/m) = P/F`; a razão entre potências absolutas seria `P/F`.
   Em nenhum dos dois casos aparece `75/m`.

**Exemplo.** Com `F = 200 W` e `P = 140 W` (mesma fração `P/F = 0.70`):

| Massa total | `P/m` | `reference` | termo de intensidade |
| --- | --- | --- | --- |
| 60 kg | 2,333 W/kg | 2,667 W/kg | 5,25 |
| 100 kg | 1,400 W/kg | 2,667 W/kg | 3,15 |

A diferença de **2,1 pontos de Borg** decorre apenas da massa. A mudança de
potência que o ciclista mais pesado precisa produzir para subir a mesma rampa é
um efeito **físico**, já calculado pela física; não deve ser reintroduzida como
normalização arbitrária.

Além disso, o piso `max(0.5, ...)` cria uma descontinuidade: para
`F <= 37.5 W`, a referência passa a ser 0,5 W/kg e a fórmula vira
aproximadamente `12 * P/m`, sem depender de `F`. O código também não valida
`ftp_w > 0`; valores nulos ou negativos são aceitos e caem no piso.

### 3.2 P3 — A inclinação é penalizada duas vezes

`rider_energy_j` é o trabalho no pedivela (`models.py:288-290`), derivado do
trabalho na roda, que já inclui a componente gravitacional
`m * g * Δh` e os termos de arrasto e rolamento. A inclinação, portanto, já
está dentro de `P`. O termo adicional:

```python
grade_term = (grade - 5.0) ** 1.3 if grade > 5.0 else 0.0
```

penaliza novamente a mesma subida. Para uma rampa de 10%, o termo vale
`5^1.3 ≈ 8.1` Borg; somado ao piso 6, chega a 14,1 antes de
intensidade, tráfego e fadiga. A partir de aproximadamente **12,6%**, o termo
sozinho satura o Borg em 20 e torna os demais termos irrelevantes. Isso não é
uma correção fisiológica: é uma segunda contagem da gravidade.

A inclusão de um termo de rampa só se justifica se representar um fenômeno
**não capturado** pela física longitudinal — por exemplo, exigência muscular
associada a torque/cadência, ou perda de eficiência em rampas extremas. Nesse
caso, o termo precisa de:

- mecanismo explícito;
- entradas próprias (cadência, torque, relação de marchas);
- parâmetro com origem na literatura ou calibração;
- testes que separem o efeito mecânico do efeito adicional.

Sem isso, o termo deve sair da intensidade mecânica.

### 3.3 P5 — A fadiga não distingue esforço leve de intenso e não recupera

O estado de fadiga é:

```python
work_window = max(1.0, ftp_w * 1200.0)
fatigue_state = (1.0 + tanh(accumulated_j / work_window) * 1.5) * fatigue_sensitivity
```

`accumulated_j` é a soma de `rider_energy_j` na ordem dos trechos
(`intensity.py:401`). O modelo tem três limitações:

1. **Só o trabalho total importa.** Uma pedalada leve de 5 h a 100 W acumula
   1,8 MJ; um treino de 1 h a 250 W acumula 0,9 MJ. Como o `tanh` satura, ambos
   chegam perto do mesmo acréscimo máximo (cerca de +2,25 Borg), apesar de
   demandas fisiológicas muito diferentes.
2. **Não há recuperação.** O trabalho acumulado nunca diminui; um trecho de
   recuperação continua somando fadiga em vez de restaurar capacidade.
3. **A saturação é rápida.** Com `F = 200 W`, a janela é 240 kJ; depois de
   poucas janelas o termo já está praticamente no máximo. O resultado é quase
   binário (“descansado” × “acabado”), não uma curva de fadiga.

O modelo preferencial é **CP/W′**: manter um saldo de trabalho acima da potência
crítica,

```text
W'bal(t) = W' - integral_0^t max(0, P(tau) - CP) dtau
```

com recuperação quando `P < CP`. Isso distingue intensidade, duração e
recuperação — exatamente o que o `tanh` do trabalho bruto não faz. Enquanto CP e
W′ não estiverem disponíveis, a alternativa honesta é **não aplicar fadiga** ou
rotulá-la como heurística legada, nunca apresentá-la como modelo fisiológico.

### 3.4 P4 — Tráfego é estresse ambiental, não esforço metabólico

`traffic_stress` entra como `traffic_stress * 1.5` no mesmo Borg. Uma via
movimentada pode aumentar o **estresse percebido** e o risco, mas não aumenta a
potência necessária para vencer a rota. Somar tráfego ao Borg:

- torna a “intensidade” dependente de uma escala ordinal de engenharia
  (`_HW_STRESS`, `intensity.py:86-98`) sem base fisiológica;
- faz uma rota plana e fácil em via arterial parecer metabolicamente mais
  intensa que a mesma rota em ciclovia;
- mistura dois eixos que o produto precisa separar: **esforço** e
  **conforto/segurança**.

O tráfego deve ser um eixo próprio (exposição/segurança) ou, se um dia entrar em
RPE calibrado, entrar como **feature calibrada** com coeficiente e limitação
declarados.

### 3.5 P6/P7 — Agregação por distância e pico sensível à segmentação

A média é calculada como:

```python
weighted_rpe += rpe * segment.distance_m
mean_rpe = weighted_rpe / total_distance
```

RPE é um estado/ritmo **por tempo**, não por metro. A ponderação por distância
subrepresenta trechos lentos e difíceis. Exemplo: 1 km a 10 km/h (6 min) e 9 km
a 30 km/h (18 min); com RPE 16 na subida e 8 no plano, a média por distância é
8,8 e a média por tempo é 10,0 — uma diferença de 1,2 Borg.

O pico é `max(segment_rpes)`. Como o RPE de cada trecho depende do trabalho
acumulado **naquele ponto** e de `grade_pct` médio do arco, dividir um arco em
sub-arcos altera o pico. O mesmo vale para a rampa: `segments_from_traversal`
calcula `grade_pct = 100 * elevation_gain_m / distance` por arco
(`intensity.py:456-458`); subdividir a malha muda a rampa média de cada trecho.

A média e o pico devem ser definidos de forma **invariante à segmentação** (ou
com tolerância declarada). Frações por distância e por duração podem continuar
como descritores da rota, mas não devem ser confundidas com a média temporal.

### 3.6 P8 — RMS não é Normalized Power

A implementação proposta na análise usa:

```text
P_eq = sqrt( integral(P^2 dt) / integral(dt) )
```

Isso é a **média quadrática** (RMS) da potência. Não é Normalized Power (NP) de
Coggan, que usa média móvel de 30 s e média de quarta potência. O RMS:

- captura variabilidade, mas com peso diferente do NP;
- depende da resolução temporal dos segmentos: um surto curto dentro de um arco
  longo é diluído; se o arco for subdividido, o surto aparece;
- não tem interpretação fisiológica validada.

Portanto, `equivalent_power_w` deve ser chamado de `rms_power_w` (ou
`variability_index` derivado) e rotulado como descritor matemático. Se o produto
quiser NP, isso é uma decisão separada, com janela e implementação explícitas.

### 3.7 P9 — `ftp_w` não é FTP

A assinatura usa `ftp_w`, mas o consumidor passa
`cyclist.sustainable_power_w` (`assessment.py:310`). O próprio docstring do
módulo registra a diferença (`intensity.py:52-55`): FTP é potência de limiar
(~1 h) e potência sustentável de travessia não são o mesmo conceito. O nome
`ftp_w` sugere uma interpretação que o valor não tem.

A correção é declarar a **natureza da referência**:

- `FTP` (limiar funcional, se realmente medido);
- `critical_power` (CP, do modelo CP/W′);
- `sustainable_power` (potência sustentável de travessia);
- `reference_power` (genérica, com `kind` explícito).

O resultado de `P/P_ref` só pode ser chamado de **fator de intensidade** se
`P_ref` for FTP; caso contrário, é uma **razão de potência relativa** àquela
referência, com outro nome e outra interpretação.

### 3.8 P10 — O contrato público expõe Borg não calibrado

`RouteML.mean_rpe` e `peak_rpe` (`contracts/route.py:214-216`) levam o valor até
o usuário. Os campos `calibrated=False`, `is_measurement=False`, `method_id` e
`model_version` são salvaguardas importantes, mas não mudam o fato de que o
número é lido como Borg. O README promete não inventar Borg; a implementação
entrega um Borg heurístico, ainda que rotulado.

Isso não exige quebrar o contrato agora. Exige:

- manter `calibrated=False` e o `method_id` explícito;
- adicionar, de forma aditiva, o **tipo de estimativa** e a **confiança**;
- decidir, com o produto, se a apresentação padrão deve ser “Borg não
  calibrado” ou “intensidade mecânica + resposta fisiológica indisponível”;
- se o contrato `v1` for evoluído, preservar a compatibilidade dos campos
  existentes e versionar a mudança.

---

## 4. Invariantes e o que o código atual viola

| Invariante | Estado atual | Causa |
| --- | --- | --- |
| **Segmentação:** dividir um trecho não deve mudar a intensidade agregada | **viola** | `tanh` do trabalho acumulado + pico + rampa média por arco |
| **Massa:** a mesma razão `P/P_ref` deve dar a mesma intensidade relativa | **viola** | fator `75/m` |
| **Monotonicidade:** mais potência relativa não deve reduzir a intensidade | **parcial** | o termo cresce, mas o clamp e a massa podem mascarar |
| **Recuperação:** um trecho de recuperação deve reduzir a fadiga | **viola** | trabalho acumulado só cresce |
| **Inclinação:** a mesma potência não deve ser penalizada duas vezes pela rampa | **viola** | termo `G` aditivo |
| **Tráfego:** mudar a via não deve mudar a demanda mecânica | **viola** | `traffic_term` somado ao Borg |
| **Determinismo:** mesma entrada, mesma saída | **cumpre** | sem estado oculto nem aleatoriedade |
| **Finitude:** entradas inválidas não devem produzir NaN/inf | **parcial** | `ftp_w <= 0` não é rejeitado; `duration=0` é tratado |
| **Contrato:** `calibrated` e `is_measurement` sempre falsos | **cumpre** | `ClassVar` e `IntensityEstimate` |

Essas invariantes são a base dos testes de regressão propostos na §9.

---

## 5. Decisão

### D1 — Separar os três níveis

A engine deve calcular **demanda mecânica** para todas as rotas. A **demanda
fisiológica** é uma camada distinta, parametrizada por capacidade individual. A
**resposta individual** (Borg, FC, zonas) é uma terceira camada, disponível
apenas com calibração. Nenhuma camada deve ser derivada da anterior por soma de
constantes sem função de transferência explícita.

### D2 — Intensidade mecânica relativa é o artefato primário

O artefato sempre disponível é a **intensidade mecânica relativa**:

```text
I_mech = P_eq / P_ref
```

com `P_ref` declarado (FTP, CP ou potência sustentável). Não há
`75 kg`, não há massa de referência artificial e não há conversão automática
para Borg. Se a variante específica for desejada, usa-se
`(P/m) / (P_ref/m)`, com a **mesma massa** no numerador e no
denominador — o que é algebricamente igual a `P/P_ref`.

`P_eq` pode ser a média temporal ou o RMS, mas o nome deve dizer qual é. O RMS
não deve ser chamado de NP nem de “potência fisiológica”.

### D3 — Não penalizar a inclinação duas vezes

A intensidade mecânica não deve conter um termo aditivo de rampa. A gravidade já
está em `rider_energy_j`. Um termo adicional só pode existir se modelar um
mecanismo **não capturado** pela física longitudinal (torque/cadência, perda de
eficiência, exigência muscular), com entradas, origem e testes próprios.

### D4 — Fadiga baseada em intensidade, duração e recuperação

O modelo preferencial é **CP/W′**, com saldo de trabalho acima da potência
crítica e recuperação abaixo dela. Enquanto CP e W′ não estiverem disponíveis:

- a fadiga pode ser **desligada** (modelo `none`), ou
- o modelo de trabalho acumulado pode ser mantido como **legado explícito**,
  com `model_id` próprio e limitações declaradas.

O trabalho acumulado bruto não deve ser apresentado como modelo fisiológico.

### D5 — Tráfego é um eixo separado

`traffic_stress` não entra na intensidade mecânica nem no Borg por soma direta.
Ele pertence a um eixo de **exposição/segurança/conforto**. Se um dia entrar em
um RPE calibrado, entra como feature calibrada, com coeficiente e limitação
declarados.

### D6 — Agregar por tempo; distância e duração como descritores

A média de RPE (quando existir) deve ser **ponderada por tempo**, porque RPE é um
estado temporal. As frações por distância e por duração continuam válidas como
descritores da rota, mas não substituem a média temporal. O pico deve ser
definido de forma robusta à segmentação (por exemplo, máximo de uma média móvel
em janela fixa, ou máximo com tolerância declarada), não como máximo de RPEs de
arcos arbitrários.

Na migração, o campo `mean_rpe` do contrato `v1` **permanece com a semântica
atual** (ponderação por distância) até que exista uma versão nova; a média
temporal entra como campo aditivo (`mean_rpe_time_weighted`) ou na `v2`. Não se
reinterpreta o campo existente sem versionamento (D11).

### D7 — Borg/FC só com função de transferência calibrada

A engine pode sempre produzir intensidade mecânica. Ela **não** deve sempre
produzir Borg/FC calibrados. Sem calibração:

- campos fisiológicos ficam `UNAVAILABLE`/`MODEL_NOT_CALIBRATED`;
- se o produto exigir um número provisório, ele é rotulado como
  `estimate_kind="provisional_heuristic"`, com confiança baixa e método
  explícito;
- zonas cardíacas continuam separadas das faixas de intensidade e só existem
  com modelo individual/populacional identificado.

### D8 — Taxonomia de coeficientes

Todo coeficiente entra com:

- **classe:** constante física, parâmetro de literatura, parâmetro individual ou
  heurística provisória;
- **valor e unidade;**
- **origem** (arquivo/linha ou citação);
- **validade** (faixa de aplicação);
- **status de calibração** (`calibrated`, `literature`, `provisional`).

`ftp_reference_kg = 75` **não** é parâmetro de literatura; é uma normalização
arbitrária e deve ser removida, não reclassificada.

### D9 — Legado preservado, novo modelo versionado e aditivo

O estimador atual permanece como **legado** (`toporide-heuristic`), com
comportamento congelado e testes próprios. O novo estimador mecânico entra em
módulo separado, com `model_id`/`model_version` próprios. A substituição do
default só ocorre após comparação em rotas planas, montanhosas, curtas, longas e
intermitentes. Nenhum consumidor é quebrado durante a migração.

### D10 — Testes de invariância são obrigatórios

Nenhum estimador substitui o atual sem passar pelos testes da §9. Em particular:
segmentação, massa, monotonicidade, recuperação, inclinação, tráfego e
agregação.

### D11 — Contrato `v1` congelado

`RouteML.mean_rpe`/`peak_rpe` continuam com a semântica atual na versão `1`.
Campos novos (`estimate_kind`, `confidence`, `mechanical_intensity`,
`reference_kind`, `mean_rpe_time_weighted`) entram de forma **aditiva** ou em uma
versão futura do contrato. Não se renomeia nem se reinterpreta um campo existente
sem versionar.

---

## 6. Arquitetura proposta

```
Física longitudinal (P, E, Δt, grade, velocidade)
        │
        ▼
┌─────────────────────────────────────────────┐
│ Nível 1 — Intensidade mecânica              │
│  mean_power_w, rms_power_w, P/P_ref,        │
│  variability_index, total_work_j, duration  │
│  SEMPRE DISPONÍVEL, SEM BORG                │
└─────────────────────────────────────────────┘
        │
        ├──► Nível 2 — Fadiga (opcional)
        │      CP/W′ (individual) ou
        │      none / legado explícito
        │
        ▼
┌─────────────────────────────────────────────┐
│ Nível 3 — Resposta fisiológica              │
│  RPE, FC, zonas                             │
│  SÓ COM CALIBRAÇÃO; senão UNAVAILABLE       │
└─────────────────────────────────────────────┘
```

Esboço de contrato interno (a ser implementado na fase F1, não neste ADR):

```python
@dataclass(frozen=True, slots=True)
class PowerReference:
    power_w: float
    kind: Literal["ftp", "critical_power", "sustainable_power", "reference"]
    source: str

@dataclass(frozen=True, slots=True)
class MechanicalIntensity:
    mean_power_w: float
    rms_power_w: float          # média quadrática; NÃO é Normalized Power
    relative_intensity: float   # P_eq / P_ref
    variability_index: float    # P_eq / P_mean
    total_work_j: float
    duration_s: float
    reference: PowerReference
```

Observações:

- `relative_intensity` é adimensional e não usa massa;
- `rms_power_w` é descritor matemático, não fisiológico;
- a massa só aparece se o consumidor quiser W/kg, e nesse caso a referência
  também é específica (W/kg);
- a fadiga é um estágio separado, com interface própria;
- a conversão para Borg/FC é um adaptador de calibração, não uma função do
  módulo mecânico.

---

## 7. Taxonomia de coeficientes

| Classe | Exemplos no código atual | Tratamento |
| --- | --- | --- |
| **Constante física** | `g`, eficiência de transmissão, densidade do ar | pertence à física; não entra em `IntensityCoefficients` |
| **Parâmetro de literatura** | limites da escala de Borg 6–20; parâmetros de CP/W′ quando citados | `source` bibliográfico + validade |
| **Parâmetro individual** | FTP, CP, W′, massa, FC máx/repouso | fornecido pelo `CyclistModel`/`RiderProfile`; nunca fixo |
| **Heurística provisória** | `scale_gain=6`, `grade_free_pct=5`, `grade_exponent=1.3`, `traffic_weight=1.5`, `fatigue_weight=1.5` | `provisional`; não pode ser apresentada como fisiologia |
| **Normalização arbitrária** | `ftp_reference_kg=75` | **remover**, não reclassificar |

A tabela de proveniência do topo de `intensity.py` já registra origem e
limitação; o que falta é a **classe** e a **decisão de uso**. Um coeficiente
`provisional` pode existir para compatibilidade, mas não deve determinar a
semântica de um campo fisiológico.

---

## 8. Plano de migração incremental

| Fase | Entrega | Compatibilidade | Critério de saída |
| --- | --- | --- | --- |
| **F0** | Este ADR | nenhuma alteração | revisão e aceite |
| **F1** | `mechanical_intensity.py` + `MechanicalIntensity` + testes | aditivo; nenhum consumidor alterado | testes de invariância §9 verdes |
| **F2** | `FatigueModel` (`none`, `legacy`, `cp_w_prime`) + testes | aditivo; default continua legado | recuperação e intensidade testadas |
| **F3** | `PhysiologicalTransfer` (`unavailable`, `legacy_heuristic`, `calibrated`) + testes | aditivo | `UNAVAILABLE` explícito; legado rotulado |
| **F4** | `IntensityEstimate` ganha campos aditivos (`mechanical`, `estimate_kind`, `confidence`, `reference_kind`, `mean_rpe_time_weighted`) | contrato `v1` preservado | serialização antiga continua válida |
| **F5** | Relatório de comparação legado × novo em rotas planas, montanhosas, curtas, longas e intermitentes | nenhuma mudança de default | diferenças explicadas; ranking avaliado |
| **F6** | Decisão de default e, se necessário, contrato `v2` | migração versionada | aceite do produto e da suíte |

**Portão de qualidade:** a fase F6 não começa sem F1–F5 concluídas e sem os
testes da §9. A classificação por faixas (`intelligence/classification.py`) usa a
intensidade para preferências; qualquer mudança de escala pode mudar a ordem das
rotas. O relatório da F5 deve medir esse impacto explicitamente.

---

## 9. Testes obrigatórios

1. **Invariância à segmentação.** A mesma trajetória, dividida em 1, 2 e N
   arcos com a mesma distância, tempo e energia totais, deve produzir a mesma
   intensidade mecânica agregada dentro de uma tolerância declarada. Para o
   legado, documentar a diferença em vez de exigi-la zero.
2. **Consistência de massa.** Para `P` e `P_ref` fixos, a
   intensidade relativa não pode depender de `m`. Se a variante W/kg for
   usada, `P/m` e `P_ref/m` devem usar a mesma massa.
3. **Monotonicidade.** Com `P_ref` fixo, aumentar `P` não pode
   reduzir a intensidade relativa; aumentar a duração com a mesma potência não
   pode reduzir a carga (quando houver modelo de carga).
4. **Inclinação sem dupla contagem.** Dois trechos com a mesma potência e rampas
   diferentes devem ter a mesma intensidade mecânica. Se houver termo de rampa,
   ele deve ser justificado por um mecanismo separado e testado isoladamente.
5. **Recuperação.** No modelo CP/W′, um trecho com `P < CP` deve aumentar
   `W'bal`; no modelo legado, a ausência de recuperação deve estar
   documentada e testada como limitação.
6. **Intensidade da fadiga.** Com o mesmo trabalho total, uma distribuição
   intermitente intensa deve produzir fadiga diferente de uma distribuição
   constante leve, quando o modelo CP/W′ estiver ativo.
7. **Tráfego isolado.** Alterar apenas `traffic_stress` não pode alterar a
   intensidade mecânica. Se o RPE calibrado usar tráfego, o teste deve verificar
   que o coeficiente é explícito e calibrado.
8. **Agregação.** A média de RPE deve ser temporal; as frações por distância e
   por duração devem somar 1 quando houver base positiva; o pico deve ser
   robusto à segmentação.
9. **Casos-limite.** Duração zero, energia zero, ausência de trechos,
   `P_ref <= 0`, valores não finitos, massa zero/negativa e escala CR10.
10. **Determinismo e serialização.** Mesma entrada → mesma saída;
    `to_dict()` continua serializável em JSON; campos novos são aditivos.
11. **Contrato.** `RouteML` da versão `1` continua serializando e desserializando
    com os mesmos campos; `calibrated=False` e `is_measurement=False`
    permanecem; `method_id`/`model_version` identificam o estimador.

---

## 10. Alternativas consideradas

| Alternativa | Descrição | Decisão |
| --- | --- | --- |
| **A. Status quo documentado** | manter a fórmula e apenas reforçar a rotulagem | rejeitada: o problema conceitual e as invariantes violadas permanecem |
| **B. Substituição imediata** | trocar `segment_rpe` por `P/P_ref` e remover Borg | rejeitada: quebra contrato/consumidores e elimina um artefato que o produto usa |
| **C. Calibração com rótulos sintéticos** | ajustar coeficientes para produzir Borg “plausível” | rejeitada: viola o ADR-028 D2 e a honestidade científica |
| **D. Migração aditiva em camadas** | manter legado, introduzir mecânica, fadiga e transferência fisiológica separadas, comparar antes de trocar | **adotada** |

---

## 11. Consequências

**Positivas**

- A intensidade mecânica passa a ser derivável da física, sem massa de
  referência artificial.
- A inclinação deixa de ser contada duas vezes.
- Fadiga, tráfego e resposta individual ganham camadas e nomes próprios.
- As invariantes ficam testáveis; a origem de cada coeficiente fica explícita.
- O produto pode oferecer “intensidade mecânica” sempre e “Borg/FC” apenas
  quando houver calibração, sem inventar fisiologia.

**Negativas**

- Haverá dois estimadores temporariamente (legado e novo), com custo de
  manutenção e documentação.
- A mudança de escala pode alterar a classificação e a ordem das rotas; isso
  exige comparação e comunicação.
- CP/W′ depende de dados individuais que talvez não existam em produção.
- O contrato `v1` não pode ser reinterpretado; campos novos exigem cuidado.

**Riscos**

- Usuários podem interpretar a ausência de Borg como regressão. A comunicação
  deve explicar que a engine está separando medição de estimativa.
- O RMS pode ser confundido com NP; o nome e a documentação devem ser
  explícitos.
- A comparação legado × novo pode mostrar que o legado “funciona” em alguns
  casos; isso não valida a fórmula, apenas indica coincidência empírica.

---

## 12. Não objetivos

- Calibrar fisiologia sem observações reais.
- Produzir FC, zonas cardíacas ou Borg calibrados sem modelo validado.
- Alterar física, busca, A\* Pareto ou contrato `v1` neste ADR.
- Eliminar todas as constantes — o objetivo é classificá-las e justificá-las.
- Implementar CP/W′ sem CP/W′ individuais disponíveis.
- Usar rótulos sintéticos para treinar um modelo fisiológico.

---

## 13. Questões abertas

1. **Referência de potência:** em produção, existe apenas
   `sustainable_power_w`, ou `threshold_power_w`/CP também estarão disponíveis?
   Qual é o `kind` padrão?
2. **Produto:** a apresentação padrão deve mostrar “Borg não calibrado” ou
   “intensidade mecânica + resposta fisiológica indisponível”?
3. **Contrato:** os campos novos entram de forma aditiva na `v1` ou exigem uma
   `v2`? Qual é a política de compatibilidade para `mean_rpe`?
4. **Resolução:** a intensidade mecânica deve ser calculada por arco, por janela
   temporal fixa ou por ambos? Qual é a tolerância de invariância à segmentação?
5. **Fadiga:** CP/W′ será alimentado por dados individuais reais? Se não, o
   default deve ser `none` ou `legacy`?
6. **Tráfego:** o eixo de exposição/segurança já existe no contrato ou precisa
   de um campo novo?
7. **E-bikes:** a intensidade deve usar `rider_energy_j` (trabalho no pedivela)
   ou incluir assistência? Hoje `rider_energy_j` já é o trabalho do ciclista;
   falta explicitar no contrato.
8. **Incerteza:** o modelo deve propagar intervalos/confiança ou apenas um
   `estimate_kind`?

---

## 14. O que este ADR não autoriza

- Apresentar a heurística atual como fisiologia calibrada.
- Preencher `heart_rate_zone_distribution` com faixas de Borg.
- Remover o estimador legado sem comparação e sem os testes da §9.
- Renomear ou reinterpretar campos do contrato `v1` sem versionamento.
- Introduzir coeficientes arbitrários sem classe, origem, validade e status.
- Treinar modelo fisiológico com rótulos sintéticos de FC/Borg.
- Usar `75 kg` como proxy de capacidade individual em qualquer modelo novo.
- Somar tráfego, rampa e fadiga na mesma escala sem declarar o que cada termo
  representa.

---

## 15. Referências

**Código auditado**

- `src/router/core/prediction/intensity.py` — fórmula, coeficientes, agregação e
  ponte com a física.
- `src/router/core/evaluation/assessment.py:303-311` — chamada com
  `cyclist.sustainable_power_w` e massa total.
- `src/router/app/planning.py:575-582` — uso de `segment_rpes`/`segment_bands`.
- `src/router/app/plan_view.py:75-89` — projeção para `RouteML`.
- `src/router/contracts/route.py:205-245` — contrato público `v1`.
- `src/router/core/physics/models.py:284-313` — `rider_energy_j` como trabalho
  no pedivela.
- `tests/test_intelligence_intensity.py` — 22 testes atuais.
- `tests/test_evaluation_assessment.py:373` — verificação de
  `calibrated is False`.

**ADRs**

- [`028-auditoria-rpe-toporide.md`](028-auditoria-rpe-toporide.md) — proveniência
  dos coeficientes e diferença entre faixas de intensidade e zonas cardíacas.
- [`028-intensidade-e-ordem-ml.md`](028-intensidade-e-ordem-ml.md) — decisão
  original da estimativa não calibrada.
- [`025-modelo-fisico-e-dinamica-do-ciclista.md`](025-modelo-fisico-e-dinamica-do-ciclista.md)
  — modelo físico e grandezas disponíveis.
- [`027-intelligence-ml-prediction.md`](027-intelligence-ml-prediction.md) —
  contrato fisiológico e estado de indisponibilidade.
- [`029-evaluation-guardrails.md`](029-evaluation-guardrails.md) — avaliação e
  guardrails.

**Literatura de referência (a confirmar na fase de calibração)**

- Escala de Borg 6–20 — convenção adotada; não é calibração.
- Normalized Power (Coggan) — média móvel de 30 s e média de quarta potência;
  **não** é o RMS proposto.
- Modelo CP/W′ — potência crítica e capacidade de trabalho acima da CP;
  requer parâmetros individuais.
