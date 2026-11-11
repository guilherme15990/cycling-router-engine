# Baseline de caracterização da busca (Camada 03 — Optimization)

> Documento de apoio ao **ADR-026** (etapa 1 do plano aprovado). Congela o
> comportamento **atual** do `MultiObjectiveAStar` em `0.18.0`, para que qualquer
> mudança futura (bucket, diversidade, guardrails) tenha **contra o que ser
> comparada**. Codificado em `tests/test_search_reference.py` (**13 testes**).

## Método

- Cenários **determinísticos** com um `TraversalModel` **tabelado** (stub): os
  valores são exatos, inteiros e reprodutíveis em qualquer máquina.
- O cenário **S6** usa o modelo físico real e é uma **caracterização de
  integração** (tolerância `rel=1e-6`), no espírito do §7 da auditoria física.
- Atualização **somente** com justificativa e comparação registradas aqui —
  nunca "consertar" o valor no teste.

Todos os cenários partem do nó 1 para o nó 3, com massa total de `87 kg`.

## Cenários e valores congelados

### S1 — diamante com trade-off real

Grafo: `1→2→3` (rota **A**) e `1→4→3` (rota **B**). Modelo tabelado:
A = `time 200 s · dist 2000 m · asc 100 m · E 4000 J · v 5 m/s`;
B = `time 300 s · dist 3000 m · asc 0 m · E 6000 J · v 5 m/s`.

| Perfil (objetivos) | Fronteira (ordem) | Motivo |
| --- | --- | --- |
| `fastest` (time, ascent) | **[A, B]** | A mais rápida, B mais plana — ninguém domina |
| `shortest` (distance, time) | **[A]** | A domina B |
| `flattest` (ascent, distance) | **[B, A]** | B ordena primeiro por subida |
| `balanced` (time, distance, ascent) | **[A, B]** | trade-off preservado |
| `electric` (energy, time) | **[A]** | A domina B |

Métricas de desempenho: `expanded = 3`, `generated = 4`, `truncated = False`
(idênticas em todos os perfis deste grafo).

### S2 — label estritamente dominada é podada

Grafo `1→2→3` (P) e `1→3` direto (D), com D estritamente pior em todos os
objetivos. Fronteira `fastest` = **[P]** (D é podada).
`expanded = 2`, `generated = 3`.

### S3 — buckets de velocidade diferentes não se comparam

Duas rotas com **métricas idênticas** que chegam com **velocidades finais
distintas** (5 m/s → bucket 10; 10 m/s → bucket 20). Fronteira `fastest` =
**[A(v=5), B(v=10)]** — ambas sobrevivem apesar de métricas iguais. Demonstra que
a dominância só compara labels **dentro do mesmo bucket**.

### S4 — limite de expansão interrompe e marca `truncated`

S1 com `max_expansions = 1`: `reached = False`, `count = 0`,
`truncated = True`, `expanded = 1`, `generated = 2`.

### S5 — `max_labels_per_node` corta por chave `(nó, bucket)`

Dois arcos paralelos `1→2` (`a12a`, `a12b`) chegam ao **mesmo nó e bucket** com
trade-off sob (time, ascent): nem um domina o outro.

| Config | Fronteira | expanded / generated |
| --- | --- | --- |
| sem limite | **[A(t=190), B(t=200)]** | 3 / 4 |
| `max_labels_per_node = 1` | **[A(t=190)]** | 2 / 3 |

O corte mantém o melhor **lexicográfico** pela ordem do perfil — controle de
**desempenho**, não garantia de otimalidade (auditoria §3).

### S6 — caracterização com o modelo físico real

Grafo `1→2→3` (2000 m) e `1→4→3` (3000 m), planos; `CyclistModel(75 kg, 800 W,
250 W, 0.5 m²)`, `BicycleModel()`, `Environment()`, `initial_speed = 0`.

- Fronteira `fastest` = **[A]** (`a12, a23`); a rota B é dominada.
- `expanded = 3`, `generated = 4`, `truncated = False`.
- `time ≈ 227.785132 s` · `energy ≈ 55237.895 J` · `v_final ≈ 9.044379 m/s` ·
  `distance = 2000 m` · `ascent = 0 m`.

### S7 — fluxo completo (busca + seleção)

S1 (stub) com `search_profile` e perfil `fastest`, `max_routes = 3`:

- `pareto_route_count = 2`, rotas selecionadas `[A, B]` (rank 0 = A);
- `diversity_relaxed = False`, `dropped_duplicates = 0`, `threshold_used = 0.7`.

### Reprodutibilidade

Duas execuções de S1 produzem fronteiras idênticas (ordem incluída).

## Interpretação

Este baseline fixa três coisas que os guardrails (etapa 2) **não podem** quebrar:

1. a **fronteira Pareto** por perfil (conjunto **e** ordem) nos cenários S1–S3;
2. o efeito **explícito** dos limites de desempenho (S4, S5), sempre sinalizado
   (`truncated`, `dropped_duplicates`);
3. a **não comparabilidade entre buckets** (S3), que os guardrails terão de
   respeitar ao introduzir contexto legal (ver `026-contrato-circulacao.md` §5).

## Validação de inércia (etapa comportamental do ADR-026)

A ligação dos guardrails de circulação (`astar.py`, `dominance.py`) e a
propagação de qualidade/limitação foram feitas **sem tocar** em nenhum valor
congelado acima. Prova:

- `tests/test_search_reference.py` (13 testes) passa **sem alteração** — S1–S7
  idênticos (fronteira, ordem, `expanded`, `generated`, `truncated`).
- O guardrail é **guiado pelos dados**: sem `arc.restrictions` nem
  `graph.turn_restrictions`, `_guardrails_active = False` e `constrained_nodes = {}`
  → a dominância mantém a chave `(nó, bucket)` e a expansão não consulta o contrato.
- `tests/test_search_guardrails.py::test_guardrails_inactive_without_restrictions`
  verifica essa condição explicitamente.

Comparação antes × depois detalhada: `026-contrato-circulacao.md` §11.

## Como atualizar

Qualquer mudança de comportamento que altere um valor congelado exige:

1. justificativa física/arquitetural explícita;
2. comparação **antes × depois** (tabela neste documento);
3. atualização do valor **e** do texto, na mesma entrega.

Sem os três, a mudança é tratada como regressão.
