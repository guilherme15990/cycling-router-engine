# ADR-029 — Camada 05: Evaluation & Guardrails

> Companheiro de decisão do [ADR-029 — Auditoria da Camada 05](029-auditoria-evaluation-guardrails.md).
> Mesmo método dos ADR-025/026/027: decisões numeradas, contrato explícito,
> critérios de aceite, plano incremental e riscos. **Preserva** as Camadas 01–04.

- **Status:** **Aprovado e implementado** (fase 2, por incrementos). A
  autorização da fase 2 fixou as **decisões vinculantes D1–D8** (abaixo, §2.0);
  cada incremento entrou com **teste + justificação + comparação com baseline**.
- **Data:** 2026-11-11
- **Base auditada:** motor `0.18.0`, suíte com **743 testes** (antes) →
  **798 testes** (após a fase 2) → **805 testes** (após o incremento de
  observabilidade da fronteira, §11).
- **Referências:** ADR-024 (grafo/restrições), ADR-025 (física), ADR-026
  (busca/guardrails, `026-contrato-circulacao.md`), ADR-027 (Intelligence/ML),
  ADR-028 (intensidade — `028-intensidade-e-ordem-ml.md`).

> **Fase 1** (este documento, proposto) fixou o desenho; a **fase 2** (aprovada
> por D1–D8) o implementou em incrementos pequenos. As seções §3–§9 foram
> **reconciliadas com a API implementada**; onde a proposta divergiu da
> implementação, prevalece a **implementação** e a divergência é registrada.

---

## 0. Requisito arquitetural obrigatório — operação integralmente offline

A avaliação final **não** acessa rede. Ela **consome** o grafo já carregado
(`OSM.PBF` local), a altimetria local (`Terrarium`) e, quando houver, um preditor
**local** (artefato em disco). A garantia executável é o guard `offline()`
(`offline.py`) e a suíte `tests/test_offline.py`. Toda decisão deste ADR
preserva esse requisito (invariante I7).

---

## 1. Contexto e objetivo

### 1.1 O que já existe (auditoria)

- `evaluation.assessment` avalia a rota no **simulador completo**, extrai
  `EffortFeatures`, chama `predict_effort` e valida a circulação (`validate_route`).
- `AssessmentOutcome` = `PHYSIOLOGICAL` / `PHYSICAL_ONLY` / `REJECTED`, derivado
  de sinais **já existentes** (sem limiar novo).
- O contrato de circulação é **compartilhado** entre a busca e a validação final;
  os guardrails são **opt-in pelos dados** (sem restrições ⇒ baseline idêntico).
- `planning.plan_offline_routes` orquestra zona → busca → física → ML → box, e
  marca `recommended = routes[0]`.
- Incerteza (`result_status`, `elevation_uncertainty`,
  `used_min_speed_regularization`, `stalled`) propaga monotonicamente até a
  candidata; a estimativa de intensidade é **separada e não calibrada**.

### 1.2 O que falta (lacunas L1/P1/O2/U1/C2/R-* — auditoria §10)

1. A **recomendação não é governada** por um estado de segurança (L1/O2).
2. A **inviabilidade física** não é um **estado auditável** (P1).
3. Não há **estado operacional** formal VALIDATED/DEGRADED/REJECTED (O2).
4. Falta **código de limitação** da estimativa de intensidade (U1).
5. Faltam **baselines de regressão** adversos, de reprodutibilidade e de
   **estabilidade de ranking** (C2/R-a..R-f).

### 1.3 Objetivo

Definir um **contrato de avaliação final** que:

- **recebe** as rotas candidatas e seus resultados **físicos e fisiológicos já
  disponíveis**, **sem recalcular** componentes já validados;
- atribui a cada rota um **estado de segurança** (`VALIDATED`/`DEGRADED`/
  `REJECTED`) com **motivos estruturados**;
- impõe **regras explícitas** de **apresentação**, **rejeição** e
  **recomendação**, garantindo as invariantes I1–I7 **independentemente do
  gerador** da rota.

### 1.4 Não objetivos

- Novo algoritmo de busca, nova física ou novo preditor neural.
- Reimplementar `MultiObjectiveAStar`, o simulador ou o estimador de RPE.
- Gerar zonas cardíacas ou apresentar faixas `i1..i5` como medição.

---

## 2. Decisões

### 2.0 Decisões aprovadas na autorização da fase 2 (vinculantes)

| Decisão | Conteúdo | Onde foi implementado |
| --- | --- | --- |
| **D1** | Renumerar os documentos para **ADR-029**; preservar o **ADR-028** original (intensidade / ordem de ML) | `docs/adr/029-*`; `028-intensidade-e-ordem-ml.md` intacto |
| **D2** | `RouteSafetyState` (`VALIDATED`/`DEGRADED`/`REJECTED`), **independente** de `AssessmentOutcome`; ausência de modelo fisiológico **não** rebaixa | `evaluation/safety.py` (`RouteSafetyState`, `classify_safety`) |
| **D3** | Proibição explícita → `REJECTED`; restrição aplicável **não resolvida** → `REJECTED` com `circulation_unresolved`; **ausência** de regra **não** é proibição nem não resolvida | `classify_safety` (`CIRCULATION_VIOLATION` / `CIRCULATION_UNRESOLVED`) |
| **D4** | `evaluate_candidates` reutiliza avaliações existentes (sem recalcular); `recommended` = 1ª **elegível** ou `None`; `REJECTED` **nunca** recomendada/apresentada; `DEGRADED` recomendável **com aviso**; rejeitadas preservadas | `evaluation/safety.py` (`evaluate_candidates`, `EvaluationReport`); `planning.py`; `cli.py` |
| **D5** | Inviabilidade física **comprovada** → `REJECTED`; incerteza não impeditiva → `DEGRADED`; diferenciar falha de simulação / ausência de dados / impossibilidade; **nunca** converter exceção em prova | `classify_safety` (`NON_FINITE_METRIC`/`NEGATIVE_SPEED`/`NON_POSITIVE_DISTANCE` vs limitações) |
| **D6** | `intensity_not_calibrated` **informativo**; **não** altera o estado; RPE nunca apresentado como zona cardíaca | `RouteAssessment.limitations` + `RouteSafety.advisories` |
| **D7** | Diagnóstico energético **opcional** reutilizando resultados já calculados; **sem** equação nova; ausência **não** implica rejeição | `evaluation/diagnostics.py`; `RouteAssessment.energy` |
| **D8** | Baselines adversos, estabilidade de ranking, divergências busca × avaliação final e suíte completa **offline** | `tests/test_evaluation_safety.py`, `tests/test_evaluation_consistency.py`, `tests/test_evaluation_regression.py` |

> As decisões originais deste documento (D1–D8, abaixo) permanecem como
> **justificativa do desenho**. Onde houver conflito de numeração, valem as
> decisões **aprovadas** da fase 2 (esta tabela).

### D1 — Contrato de avaliação final que **não recalcula** o já validado

**Decisão.** A Camada 05 expõe um contrato de avaliação final que **consome**
resultados já produzidos:

```
evaluate_candidates(
    candidates: Iterable[RouteCandidate],                         # ordem de classificação
    *,
    assessments: Mapping[str, RouteAssessment] | None = None,     # já calculados (por route_id)
) -> EvaluationReport
```

- Cada `RouteAssessment` recebido (de `assess_candidates`, que já rodou o
  simulador completo, a fisiologia, a circulação e a estimativa) é **reutilizado
  como está** (mesma identidade de objeto). O contrato **não** re-simula física
  nem re-chama o preditor.
- O que **falta** vira **ausência explícita**: uma candidata sem avaliação entra
  como `REJECTED` por **inelegibilidade** (`evaluation_missing`) — explicitamente
  **não** uma prova de inviabilidade (I4, I5).
- O contrato é o **único** ponto por onde qualquer rota — da busca, de cache ou
  (futuro) de um gerador ML — passa antes de ser apresentada (I6).

**Divergências face à proposta.** O `evidence`/`requirement` da proposta **não**
foram implementados: nenhum resultado avulso é computado (a avaliação final
apenas **consome** `RouteAssessment`); a taxonomia fechou em `ReasonKind` (§3) e
a ausência é tratada como `EVALUATION_MISSING`.

**Justificativa.** Hoje `plan_offline_routes` já produz `RouteAssessment` para
cada candidata; re-simular na "avaliação final" seria desperdício e risco de
divergir. O contrato consome o que existe e adiciona **só** o julgamento de
segurança.

**Classificação:** **COMPLEMENTAR** (não altera física nem busca).

### D2 — Estado de segurança tri-estado, **ortogonal** a `AssessmentOutcome`

**Decisão.** Introduzir `RouteSafetyState = VALIDATED | DEGRADED | REJECTED`,
separado e ortogonal a `AssessmentOutcome`:

| Estado | Condição | `AssessmentOutcome` (inalterado) |
| --- | --- | --- |
| **REJECTED** | proibição de circulação **aplicável** (`circulation_violation`), restrição aplicável **não resolvida** (`circulation_unresolved`) **ou** inviabilidade física **comprovada** (métrica não finita, velocidade negativa, distância não positiva). Inclui **inelegibilidade de avaliação** (`evaluation_missing`, **não** prova de inviabilidade) | `REJECTED` **ou** qualquer |
| **DEGRADED** | fisicamente válida e legal, **com** limitação física relevante (incerteza altimétrica, `stalled`, regularização de baixa velocidade, dismount, `simulation_unavailable`) | `PHYSICAL_ONLY`/`PHYSIOLOGICAL` |
| **VALIDATED** | fisicamente válida, legal, **sem** limitação física material | `PHYSICAL_ONLY`/`PHYSIOLOGICAL` |

Regras:

- O estado de segurança trata **física + legalidade**. A **disponibilidade
  fisiológica** é um eixo **separado** (`physiology_*`): a ausência de modelo
  calibrado é **esperada**, **não** degrada a segurança física.
- **Toda** transição para `REJECTED` carrega **motivo estruturado**
  (`Reason`/`ReasonKind` + evidência) — invariante I5. **Nenhuma** rejeição é
  silenciosa.
- `RouteAssessmentError` (erro de dados de rota) **não** é mascarado:
  `assess_route` o levanta e a avaliação final o vê como avaliação **ausente**
  (`evaluation_missing`) — explicitamente **não** uma prova de inviabilidade
  (decisão aprovada **D5**). Um resultado degradado continua **degradado**
  (propagado), não vira erro.

**Justificativa.** `AssessmentOutcome` **não** é grau de segurança (auditoria §8).
Separar os dois evita que "fisiologia indisponível" seja lido como "rota
insegura" e que "degradado" seja lido como "inviável".

**Classificação:** **COMPLEMENTAR**.

### D3 — Legalidade: a recomendação é **governada** pelo estado

**Decisão.** A rota **recomendada** nunca é `REJECTED` (I1). A escolha segue:

1. conjunto elegível = rotas com estado `VALIDATED` ou `DEGRADED`, **na ordem de
   classificação** (ordem do perfil);
2. `recommended` = **primeira** elegível nessa ordem (o *pick*); **não** se
   reordena por estado — uma `DEGRADED` à frente de uma `VALIDATED` continua
   sendo a recomendada;
3. se a recomendada for `DEGRADED`, o plano emite um **aviso explícito**
   (`warnings`: "rota recomendada … esta DEGRADED …");
4. se o conjunto elegível for **vazio**, **não** há recomendação
   (`recommended=None`) e o plano declara o motivo.

**Divergência face à proposta.** A proposta **preferia** uma `VALIDATED` a uma
`DEGRADED`. A implementação **preserva a ordem de classificação** (decisão
aprovada **D4**): a recomendada é a **primeira elegível**, o que mantém o
baseline `routes[0]` quando todas são válidas e **não** introduz um segundo
critério de ordenação. Promover `VALIDATED` sobre `DEGRADED` fica como evolução
futura, se desejada.

Além disso:

- rotas `REJECTED` **não** são apresentadas como viáveis (I2); aparecem, no
  máximo, em uma seção **"excluídas"** com o motivo auditável;
- a validação final (`validate_route`) permanece **independente** do gerador
  (I6); o contrato a **reutiliza** e não a substitui.

**Justificativa.** Corrige L1/O2 sem alterar física nem busca e sem quebrar o
caso comum (quando todas as rotas são válidas, `routes[0]` continua sendo a
recomendada).

**Classificação:** **ADAPTAR** (`planning` + CLI) **sobre** contrato **COMPLEMENTAR**.

### D4 — Viabilidade física: separar **impossibilidade** de **incerteza**

**Decisão.** O contrato de avaliação distingue explicitamente (decisão aprovada
**D5**):

- **Legalidade** → `REJECTED` com motivo:
  `circulation_violation` (proibição comprovada) ou `circulation_unresolved`
  (restrição aplicável **não resolvida**);
- **Impossibilidade física comprovada** → `REJECTED` com motivo:
  `non_finite_metric`, `negative_speed`, `non_positive_distance`;
- **Inelegibilidade de avaliação** → `REJECTED` com motivo `evaluation_missing`
  (explicitamente **não** uma prova de inviabilidade);
- **Incerteza / limitação não impeditiva** → `DEGRADED` com motivo:
  `elevation_missing`, `simulation_degraded`, `simulation_unavailable`,
  `min_speed_regularization`, `stalled`, `dismount_used`.

Nenhum limiar numérico novo é introduzido: os motivos vêm dos **sinais
existentes** (`SimulationStatus`, `ElevationUncertainty`, flags de regularização/
`stalled`/`dismount`, finitude das métricas, `circulation`). A **qualidade da
simulação** permanece a do simulador completo.

**Divergência face à proposta.** Erros de dados de rota (`empty_route`,
`unknown_arc`, `discontinuous`) **não** são convertidos automaticamente em
`REJECTED`: `assess_route` os levanta como `RouteAssessmentError` (não mascarados)
e a avaliação final os vê como avaliação **ausente** (`evaluation_missing`). Isso
materializa a decisão aprovada **D5** ("não converter qualquer exceção em prova
de inviabilidade"). `power_above_limit` não foi implementado como motivo
estruturado (a física já reporta os sinais correspondentes via limitações).

**Classificação:** **COMPLEMENTAR** (taxonomia sobre sinais já existentes).

### D5 — Comparação: simulador completo é a **referência comum**

**Decisão.**

- Toda comparação de rotas usa as **métricas do simulador completo**
  (`RouteAssessment.metrics`), nunca as métricas do modo busca isoladamente.
- A consistência **busca × avaliação final** é verificada por testes
  (`tests/test_evaluation_consistency.py`): toda rota **devolvida** pela busca é
  **elegível** sob a avaliação final, que usa o **mesmo** contrato de circulação
  (`validate_route`). É uma verificação de **conformidade de guardrail**, não de
  igualdade de métricas — o simulador completo pode diferir legitimamente do modo
  busca, e a avaliação **reusa** os resultados da Camada 05 sem recalcular.
- A **fronteira Pareto** é a referência de diversidade; o *pick* é a **ordem de
  classificação**, e a recomendação é a **primeira elegível** (D3).
- Congela-se um **baseline de estabilidade de ranking** por perfil
  (`tests/test_evaluation_consistency.py`, fronteira S1 congelada).
- A **diversidade intrínseca** da fronteira permanece follow-up declarado
  (ADR-026 §4) — **não** entra aqui.

**Divergência face à proposta.** A verificação de consistência de **métricas**
(`candidate.metrics` × `assessment.metrics`) **não** foi implementada como
runtime; o que se garante é a **conformidade de guardrail** (nenhuma rota
devolvida é rejeitada pela avaliação final).

**Classificação:** **COMPLEMENTAR**.

### D6 — Incerteza: propagação rotulada e **não ambígua**

**Decisão.**

- Os códigos de limitação da física permanecem os de `RouteAssessment.limitations`.
- **Adiciona-se** o código `intensity_not_calibrated` para a estimativa de
  intensidade, de modo que **nenhum** consumidor possa apresentar `i1..i5` como
  zona cardíaca sem um rótulo explícito.
- `simulation_degraded`/`simulation_unavailable` são **documentados** como
  atributos de **qualidade altimétrica**, distintos de inviabilidade física
  (que é `REJECTED` com motivo próprio).
- `IntensityEstimate` mantém `calibrated=False`, `is_measurement=False` e **não**
  preenche `PhysiologicalPrediction.heart_rate_zone_distribution` (I3).

**Classificação:** **COMPLEMENTAR** (código estável) + **PRESERVAR** (contrato).

### D7 — Regressões: determinismo, adversos, baselines e offline

**Decisão.** Consolidar (auditoria §7.2 e §11):

1. baseline da **fronteira com restrições** (adverso);
2. baseline de **incerteza em escala** (rota longa, `stalled`, dismount,
   elevação ausente);
3. **reprodutibilidade** do `RoutePlan.to_dict()` com a rede desabilitada;
4. **consistência busca × avaliação** (métricas);
5. **estabilidade de ranking** por perfil;
6. **benchmark** do fluxo (o A\* no grafo inteiro pendura — sempre recortar zona);
7. **nenhuma rota proibida recomendada** (I1) como teste de aceite.

Todo teste determinístico; tolerâncias **justificadas** e registradas.

**Divergência face à proposta.** O item de **consistência de métricas**
(`candidate.metrics` × `assessment.metrics`) **não** foi implementado (ver D5,
corpo): o que se congela é a **conformidade de guardrail** e a estabilidade de
ranking. Não há **benchmark** de tempo como teste — a preocupação "o A\* no grafo
inteiro pendura" continua endereçada **recortando a zona** (os cenários usam
grafos pequenos/recortados), não por medição de desempenho na suíte.

**Classificação:** **COMPLEMENTAR**.

### D8 — Segurança operacional: apresentação, rejeição e recomendação

**Decisão.** Regras **explícitas** de apresentação:

| Estado | Apresentar? | Recomendar? | Como exibir |
| --- | --- | --- | --- |
| `VALIDATED` | Sim | Sim | rota normal, sem alerta |
| `DEGRADED` | Sim | Sim (se for a **primeira elegível**) | rota normal **+ motivos**; se recomendada, **aviso** explícito |
| `REJECTED` | Não como viável | **Nunca** | seção **"excluídas"** com motivo auditável |

- A CLI (`_print_plan`) separa **"viáveis"** de **"excluídas"** e imprime os
  **motivos estruturados** (`<kind>: <detail>`), não texto solto.
- A recomendação **nunca** recai sobre `REJECTED`; recai sobre a **primeira
  elegível** na ordem de classificação (D3).
- JSON: cada rota carrega `safety` (`state`, `eligible`, `reasons[]`,
  `advisories[]`); o plano carrega `warnings[]` incluindo o aviso de recomendada
  `DEGRADED` e o de "nenhuma rota elegível".

**Classificação:** **ADAPTAR** (apresentação) **sobre** contrato **COMPLEMENTAR**.

---

## 3. Contrato de avaliação final (API implementada)

> Assinaturas **implementadas** (fase 2), correspondentes a
> `evaluation/safety.py`, `evaluation/diagnostics.py`, `evaluation/assessment.py`
> e `planning.py`.

```python
class RouteSafetyState(str, Enum):
    VALIDATED = "validated"
    DEGRADED  = "degraded"
    REJECTED  = "rejected"

class ReasonKind(str, Enum):          # motivo estruturado e auditável
    # legalidade (D3)
    CIRCULATION_VIOLATION = "circulation_violation"
    CIRCULATION_UNRESOLVED = "circulation_unresolved"
    # viabilidade física comprovada (D5)
    NON_FINITE_METRIC = "non_finite_metric"
    NEGATIVE_SPEED = "negative_speed"
    NON_POSITIVE_DISTANCE = "non_positive_distance"
    # incerteza / limitação física não impeditiva (D5)
    ELEVATION_MISSING = "elevation_missing"
    SIMULATION_DEGRADED = "simulation_degraded"
    SIMULATION_UNAVAILABLE = "simulation_unavailable"
    MIN_SPEED_REGULARIZATION = "min_speed_regularization"
    STALLED = "stalled"
    DISMOUNT_USED = "dismount_used"
    # fisiologia / estimativa (eixos separados; NUNCA rebaixam — D2/D6)
    PHYSIOLOGY_UNAVAILABLE = "physiology_unavailable"
    PHYSIOLOGY_MODEL_NOT_CALIBRATED = "physiology_model_not_calibrated"
    PHYSIOLOGY_INSUFFICIENT_DATA = "physiology_insufficient_data"
    PHYSIOLOGY_CONTRACT_INCOMPATIBLE = "physiology_contract_incompatible"
    INTENSITY_NOT_CALIBRATED = "intensity_not_calibrated"
    # inelegibilidade de avaliação (NÃO é prova de inviabilidade — D5)
    EVALUATION_MISSING = "evaluation_missing"

@dataclass(frozen=True)
class Reason:
    kind: ReasonKind
    detail: str                       # evidência textual; nunca vazio
    arc_id: str | None = None

@dataclass(frozen=True)
class RouteSafety:
    state: RouteSafetyState
    reasons: tuple[Reason, ...] = ()      # determinam o estado; vazio só em VALIDATED
    advisories: tuple[Reason, ...] = ()   # informativos; NUNCA alteram o estado
    # eligível = state is not REJECTED; + validated/degraded/rejected

@dataclass(frozen=True)
class EvaluatedRoute:
    route_id: str
    safety: RouteSafety
    assessment: RouteAssessment | None = None   # consumido, NÃO recalculado
    recommended: bool = False

@dataclass(frozen=True)
class EvaluationReport:
    routes: tuple[EvaluatedRoute, ...] = ()       # elegíveis (VALIDATED/DEGRADED)
    excluded: tuple[EvaluatedRoute, ...] = ()     # REJECTED (preservadas p/ auditoria)
    recommended: EvaluatedRoute | None = None     # 1ª elegível, ou None
    warnings: tuple[str, ...] = ()                # ex.: "nenhuma rota elegível"

def classify_safety(assessment: RouteAssessment) -> RouteSafety: ...

def evaluate_candidates(
    candidates, *, assessments: Mapping[str, RouteAssessment] | None = None,
) -> EvaluationReport: ...

# D7 — diagnóstico energético opcional (reuso do balanço já calculado)
@dataclass(frozen=True)
class EnergyConsistency:
    wheel_work_j: float
    residual_j: float
    relative_residual: float | None   # None quando não há trabalho na roda
    mass_kg: float

def energy_consistency(path: TraversalPath, mass_kg: float) -> EnergyConsistency: ...

# Integração (planning) — recomendação governada pelo estado
@dataclass(frozen=True)
class PlannedRoute:
    candidate; assessment; classification
    safety: RouteSafety               # propriedades: safety_state, eligible
    recommended: bool = False

@dataclass(frozen=True)
class RoutePlan:
    ...
    routes: tuple[PlannedRoute, ...] = ()      # elegíveis apenas
    rejected: tuple[PlannedRoute, ...] = ()    # REJECTED (auditoria)
    warnings: tuple[str, ...] = ()
    @property
    def recommended(self) -> PlannedRoute | None: ...   # routes[0] ou None
```

**Garantias do contrato.**

- **Não recalcula** física/fisiologia/estimativa já presentes em `assessment`
  (o objeto é **reutilizado** como está — mesma identidade).
- A **ausência de avaliação** vira `REJECTED` por `EVALUATION_MISSING`
  (inelegibilidade **explícita**, I4) — explicitamente **não** uma prova de
  inviabilidade.
- **Todo** `REJECTED` tem ≥ 1 `Reason` (I5); `VALIDATED` tem `reasons` vazio.
- `advisories` (fisiologia ausente, `intensity_not_calibrated`) **nunca** alteram
  o estado (D2/D6).
- É o **único** caminho de apresentação (I6), usado pela busca e por qualquer
  gerador futuro.

**Divergências face à proposta.** (1) `classify_safety` retorna `RouteSafety`
(estado + motivos + advertências), **não** uma tupla `(estado, motivos)`.
(2) `RouteSafety` carrega `advisories` separados; `EvaluatedRoute` embute
`safety` em vez de campos achatados (`safety_state`/`reasons`). (3) O nome
`RejectionReason` virou `ReasonKind`; `recommendation_degraded` virou um **aviso**
em `EvaluationReport.warnings`. (4) `evaluate_candidates` não recebe
`evidence`/`requirement`.

---

## 4. Critérios de aceite

| # | Cenário | Comportamento esperado |
| --- | --- | --- |
| A1 | Rota viola circulação registrada | `REJECTED` + `Reason(CIRCULATION_VIOLATION)`; **nunca** recomendada; aparece só em "excluídas" |
| A2 | Rota com proibição em 1º lugar na ordem | recomendada é a **próxima elegível** (não a proibida); nenhuma `REJECTED` recomendada, em **nenhuma** posição |
| A3 | Rota com elevação ausente | `DEGRADED` + `Reason(ELEVATION_MISSING)`; status e incerteza **propagados**, não mascarados |
| A4 | Rota com `stalled`/regularização | `DEGRADED` + motivos correspondentes |
| A5 | Sem modelo calibrado | `PHYSICAL_ONLY` **mantido**; `Reason(PHYSIOLOGY_*)` **como advertência** (`advisories`); **não** rebaixa a segurança física; `predicted_rpe is None` |
| A6 | Estimativa de intensidade | `calibrated=False`, `is_measurement=False`, zonas `None`; advertência `INTENSITY_NOT_CALIBRATED` + código `intensity_not_calibrated` |
| A7 | Métrica não finita / velocidade negativa / distância não positiva | `REJECTED` + motivo (`NON_FINITE_METRIC`/`NEGATIVE_SPEED`/`NON_POSITIVE_DISTANCE`); **nunca** apresentada como viável |
| A8 | Rota vazia/descontínua/arco ausente | `assess_route` levanta `RouteAssessmentError` (**não mascarado**); a avaliação final vê `evaluation_missing` — **nunca** convertido automaticamente em `REJECTED` por inviabilidade |
| A9 | Nenhuma rota elegível | `recommended=None` + `warnings`; rejeitadas preservadas em `excluded`/`rejected` (auditoria) |
| A10 | Todas as rotas válidas | `routes[0]` continua sendo a recomendada (baseline preservado) |
| A11 | Consumo sem recálculo | `evaluate_candidates` **não** re-simula; `assessment` é o **mesmo objeto** (`is`) |
| A12 | Mesmo gerador, mesmas checagens | rota de cache/ML passa por `classify_safety` idêntico (I6) |
| A13 | Rede desabilitada | fluxo completo roda com `offline()`; saída reprodutível (I7) |
| A14 | Regressão | baselines S1–S7 **inalterados**; suíte verde (**805 testes**, §11) |

---

## 5. Regras de apresentação / rejeição / recomendação (resumo)

- **Apresentação:** só `VALIDATED`/`DEGRADED` como **viáveis**; `REJECTED` em
  seção "excluídas". Estimativa sempre rotulada "não calibrada".
- **Rejeição:** sempre com `Reason` estruturado (≥ 1); nunca silenciosa; nunca
  mascarando falha numérica. `advisories` (fisiologia, intensidade) **não**
  rejeitam.
- **Recomendação:** a **primeira elegível** (`VALIDATED` **ou** `DEGRADED`) na
  **ordem de classificação**; `REJECTED` **nunca**; `None` se não houver elegível.
  Recomendada `DEGRADED` gera **aviso** explícito (`warnings`).

---

## 6. Plano incremental de implementação e testes

> Cada incremento: **teste + justificação + comparação com baseline**. Os
> incrementos **1–7** foram **implementados**; o resultado é a suíte com
> **798 testes** (743 antes da fase 2). O incremento **complementar** de
> observabilidade da fronteira (§11) leva a suíte a **805 testes**.

| Inc. | Entrega | Testes | Estado |
| --- | --- | --- | --- |
| 1 | `RouteSafetyState`, `Reason`/`ReasonKind`, `RouteSafety`, `classify_safety` (puro, sem física nova) | `test_evaluation_safety.py` | ✅ (25) |
| 2 | `evaluate_candidates` consumindo `RouteAssessment` (sem recálculo) + `EnergyConsistency`/`energy_consistency` (D7) | idem | ✅ |
| 3 | `planning`: `recommended` governado pelo estado; `routes` elegíveis + `rejected`/`warnings`; `PlannedRoute.safety` | `test_planning.py` | ✅ (25) |
| 4 | CLI: seções "viáveis"/"excluídas", motivos estruturados, avisos | `test_planning.py` | ✅ |
| 5 | Consistência busca × avaliação + estabilidade de ranking | `test_evaluation_consistency.py` | ✅ (16) |
| 6 | Regressões adversas + reprodutibilidade offline | `test_evaluation_regression.py` | ✅ (10) |
| 7 | API (`src/api.py` `__all__` + `generate_api_reference.py` `LAYERS`) + CHANGELOG/README + ADR | `test_api_surface.py` + `docs/reference/api.md` | ✅ (315 símbolos, 4) |

**Incrementos 1–2** são o núcleo (contrato + estado). **3–4** fecham I1/I2 na
apresentação. **5–6** blindam regressões. **7** é a fachada.

---

## 7. Riscos e dependências

Ver auditoria §12. Destaques:

- **R1:** preservar `routes[0]` como recomendada quando válida (A10) evita quebrar
  `test_plan_marks_recommended_route`.
- **R2:** manter o estado **ortogonal** a `AssessmentOutcome` (vocabulário estável).
- **R5:** contrato **consome** `RouteAssessment`; nunca re-simula.
- **R6:** numeração — **resolvido** (D1): os documentos passam a **ADR-029**;
  o **ADR-028** original (intensidade / ordem de ML) fica **intacto**.

**Dependências externas:** nenhuma (tudo local). **Dependências internas:**
`physical`, `search.circulation`, `evaluation.assessment`, `ml.predictor`,
`physiology`, `offline`.

---

## 8. Consequências arquiteturais

**Benefícios.** Recomendação auditável e **imune ao gerador**; separação clara
entre **segurança física/legal** e **disponibilidade fisiológica**; rejeições
sempre motivadas; incerteza rotulada de forma não ambígua; regressões cobertas
por baselines.

**Custos.** Um contrato novo e um estado novo (pequenos, puros); adaptação da CLI
e da seleção de recomendada; mais testes de baseline.

**Preservação.** Física, busca, `MultiObjectiveAStar`, contrato de circulação e
ML permanecem **intocados**.

---

## 9. Decisões pendentes — resolvidas na autorização da fase 2

Todas respondidas pelas **decisões vinculantes D1–D8** (§2.0):

1. **Numeração.** **Renumerado para `029`** (D1); o `028` original (intensidade /
   ordem de ML) é **preservado**.
2. **Escopo do eixo fisiológico.** **Informativa** (D2/D6): ausência de modelo
   calibrado vira **advertência** (`advisories`) e **não** rebaixa a segurança.
3. **`unresolved` de circulação.** **`REJECTED`** (D3), com motivo **distinto**
   (`circulation_unresolved`).
4. **Consistência energética por rota (P2).** **Incluída** como diagnóstico
   **opcional** (D7), reutilizando o balanço já calculado.
5. **`recommended=None` sem elegível.** **Confirmado** (D4); a CLI/consumidores
   exibem a seção "excluídas" e os avisos — impacto coberto por
   `test_planning.py`, `test_evaluation_consistency.py` e
   `test_evaluation_regression.py`.

---

## 10. Fora de escopo e follow-ups

- Gerador de rotas por ML (passo 0 de `planning`) — extensão futura.
- Diversidade intrínseca da fronteira (ADR-026 §4).
- Modelo fisiológico calibrado (ADR-027) — mantém-se a contrato de ausência.
- Zonas cardíacas (nunca derivadas da estimativa de intensidade).

---

## 11. Observabilidade complementar — completude da busca (correção ao ADR-026)

Os guardrails do ADR-029 governam a **recomendação** (estado de segurança). Uma
lacuna **complementar** foi encontrada no ADR-026: a poda da fronteira por
`max_labels_per_node` (chave `(nó, bucket)`) removia labels **não dominadas** sem
qualquer sinal no resultado — a mesma classe de risco ("perda de completude
silenciosa") que o ADR-029 combate na apresentação.

Foi implementado um incremento **aditivo e retrocompatível**, sem tocar física,
ML, custo ou política de dominância:

| Efeito | Campo no resultado | Onde nasce |
| --- | --- | --- |
| Busca **interrompida** por `max_expansions` | `truncated` (inalterado) | `MultiObjectiveAStar.search` |
| Fronteira **podada** por `max_labels_per_node` | `frontier_truncated`, `dropped_labels` | `FrontierManager` (contador só em `_truncate`) |
| Candidatas **deduplicadas** após a busca | `dropped_duplicates` (inalterado) | `candidates.py` / `selection.py` |

Os três indicadores são **independentes** e **não** se confundem. Os novos campos
propagam até `SearchResult`, `ProfileSearchResponse`, `RoutePlan` e a CLI
(avisos explícitos). `FrontierManager` (caminho do A\*) e `ParetoFrontier`
(caminho legado via `FrontierMap`) têm contadores **coerentes** e sem dupla
contagem — o descarte normal por dominância **não** é contado como poda.

> **Caveat de leitura.** `truncated=False` **e** `frontier_truncated=False`
> significam apenas que **nenhum dos dois limites atuou** — **não** provam
> completude nem otimalidade **global** (o bucket de velocidade segue sendo uma
> aproximação; a fila esvazia sob os caps e a heurística correntes). São
> condições **necessárias, não suficientes**.

`epsilon_dominance` permanece **inativo** no caminho principal
(`SpeedBucketDominance` não aplica epsilon); sua condição está documentada no
ADR-026 como controle **reservado**, sem alterar a semântica de dominância agora.

Referências: `docs/adr/026-optimization-custo-e-busca.md` (Decisão 2),
`docs/adr/026-baseline-busca.md` (S4/S5 e Interpretação, item 2),
`tests/test_search_reference.py`, `tests/test_search_astar.py`,
`tests/test_search_state.py`, `tests/test_search.py`.

---

## 12. Verificação de fechamento — decisões de segurança (D2–D6)

Checagem de que cada decisão de segurança está **implementada** e **testada**.
Não há decisão dependente de estado externo: o núcleo (`evaluation/safety.py`) é
**puro** e consome apenas `RouteAssessment` já calculado.

| # | Item verificado | Código | Evidência (testes) |
| --- | --- | --- | --- |
| 1 | `RouteSafetyState` **independente** de `AssessmentOutcome` | `RouteSafetyState`, `classify_safety`, `RouteAssessment.outcome` | `test_evaluation_safety.py`: `test_state_is_independent_from_assessment_outcome`, `test_calibrated_physiology_does_not_upgrade_or_downgrade`, `test_proven_physical_infeasibility_rejects`, `test_absence_of_calibrated_physiology_is_advisory_not_state` |
| 2 | `evaluate_candidates` **sem recalcular** física/fisiologia | `evaluate_candidates` recebe `assessments` (mapa) e **não** tem parâmetro de física/fisiologia | `test_evaluation_safety.py::test_evaluate_candidates_reuses_existing_assessment` (identidade `is`), `test_evaluation_consistency.py::test_evaluate_candidates_preserves_eligible_order` |
| 3 | `recommended=None` quando **não há elegível** | `_select_recommended` (primeira `eligible`) e `EvaluationReport` | `test_evaluation_safety.py::test_no_eligible_route_recommends_none`, `test_evaluation_consistency.py::test_all_ineligible_recommends_none`, `test_evaluation_regression.py::test_re_no_eligible_route_keeps_rejected_for_audit`, `::test_rf_no_ineligible_route_is_ever_recommended` |
| 4 | Rejeição **auditável** de circulação proibida/não resolvida | `classify_safety` (`CIRCULATION_VIOLATION`/`CIRCULATION_UNRESOLVED`), preservadas em `rejected` | `test_evaluation_safety.py`: `test_explicit_forbidden_rule_rejects`, `test_unresolved_restriction_rejects_with_distinct_reason`, `test_absence_of_rule_is_not_prohibition_nor_unresolved`, `test_every_rejection_carries_a_reason`; `test_evaluation_regression.py` R-a/R-c |
| 5 | Propagação de **incerteza** e **intensidade não calibrada** | `RouteAssessment.limitations`, `RouteSafety.advisories`, CLI | `test_evaluation_safety.py`: `test_absence_of_calibrated_physiology_is_advisory_not_state`, `test_intensity_estimate_is_always_advisory`, `test_degraded_simulation_degrades_but_stays_eligible`, `test_dismount_degrades_but_stays_eligible`; `test_evaluation_assessment.py` (limitations/intensidade); `test_planning.py::test_plan_every_route_receives_intensity_estimate` e o teste da saída de texto da CLI |

**Separação de relatórios (não confundir).** O `EvaluationReport` conserva os
**diagnósticos de segurança** (`reasons`/`advisories`/`limitations`), enquanto os
**indicadores de completude da busca** (`truncated`/`frontier_truncated`/
`dropped_labels`) vivem em `RoutePlan`/`ProfileSearchResponse` (§11). A interface
(CLI) apresenta ambos: `seguranca:` + `intensidade (nao calibrada)` por rota, e os
avisos de poda/interrupção.

**Suíte:** **805 testes** verdes, totalmente **offline**.
