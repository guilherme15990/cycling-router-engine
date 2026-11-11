# ADR-024 — Modelo canônico de grafo e ambiente (Camada 01 — Data Model)

- **Status:** aceito
- **Data:** 2026-11-10
- **Contexto:** Camada 01 (Data Model) do projeto `roteamento com ml`. A engine
  Python é a base de organização; o `toporide` (TypeScript) é a **referência
  funcional**. Ver `docs/nomenclatura.md` (norm N-01).
- **Relacionadas:** norm N-01 (nomenclatura), ADR-022 do `toporide` (fronteira
  A\*/ML), ADR-014 do `toporide` (corretude: restrições de conversão ainda não
  consumidas), ADR-016 do `toporide` (provedores de altimetria).

## Contexto

O grafo deve representar a infraestrutura ciclável observada, sem misturar
propriedades geográficas com esforço, otimização ou preferência do ciclista.
Antes de implementar a camada, três decisões ficaram em aberto e são fechadas
aqui como contratos arquiteturais.

## Decisão 1 — Separação `Segment → Link → Arc`

Aprovada. Uma única fonte de verdade geométrica e de atributos físicos.

| Entidade  | Responsabilidade                                             |
| --------- | ------------------------------------------------------------ |
| `Node`    | Identificar vértices e suas coordenadas                      |
| `Segment` | Representar trecho físico homogêneo (**fonte geométrica**)   |
| `Link`    | Conectar dois nós por uma sequência ordenada de segmentos    |
| `Arc`     | Representar travessia permitida/candidata em um sentido      |
| `Graph`   | Organizar entidades, adjacências e índices                   |

Um `Link` contém um ou mais `Segment`. Um `Arc` percorre um `Link` em um
sentido; um link bidirecional tem dois arcos.

### Invariantes

- Segmentos de um `Link` formam uma geometria contínua.
- **A geometria canônica pertence aos `Segment`**; `Link` organiza e `Arc`
  determina a direção. Não há geometria persistida no arco.
- O sentido inverso reutiliza os segmentos na ordem inversa
  (`Segment.reversed_segment`, `Arc.reversed_arc`).
- IDs de segmentos e links são estáveis dentro da versão do grafo.
- A simplificação topológica não elimina mudanças relevantes de atributos.
- O snapping só divide links no `QueryGraph`, sem alterar o grafo original.

### Refinamento — `Arc.geometry` (geometria derivada)

`Arc.geometry` deixa de ser campo persistido e fonte independente. Passa a ser
**propriedade derivada** dos `Segment`, com pontos de junção desduplicados
(`concat_geometry`). Exposições:

- `Arc.geometry` / `Link.geometry` — propriedade derivada (conveniência).
- `RoutingGraph.arc_geometry(arc_id)` — reconstrução com **cache interno**.
  O cache é amarrado à **identidade da tupla de segmentos** que o gerou: se um
  arco for trocado por outro com segmentos diferentes — mesmo por um caminho que
  não passe por `_invalidate_geometry` (ex.: mutação direta de `_arcs`) — a
  identidade muda e a geometria é recomputada. As operações controladas
  (`_insert_arc`, `replace_arc`, `remove_arc`) ainda limpam o cache.
- **`Segment` é `frozen`**: após a construção do grafo não há mutação in-place de
  segmento; alterações passam por `replace`/`reversed_segment`, que produzem
  novas tuplas e, portanto, nova identidade de cache.
- `concat_geometry` remove **apenas** coordenadas consecutivas repetidas (junção
  entre segmentos). Repetições não consecutivas são preservadas, pois podem
  representar um percurso que retorna ao mesmo local (laço).
- na serialização, persiste-se apenas a geometria canônica dos `Segment`; a do
  arco é reconstruída ao carregar.
- o A\* não materializa geometria durante a expansão.

**Invariante de orientação:** `geometry(Arc_reverso) == reverse(geometry(Arc))`,
verificado também após simplificação e snapping (testes dedicados).

### Tipo fundamental pendente (fora desta decisão)

`GeoPoint(latitude, longitude)` com conversão GeoJSON `[lon, lat]` restrita aos
adaptadores fica registrado como follow-up (ver "Fora de escopo").

## Decisão 2 — Preservação das restrições OSM

Aprovada. Preservar o dado original e normalizar em estruturas próprias, sem
confundir restrição observada, interpretação normativa e preferência.

| Entidade                                                    | Papel                                                              |
| ---------------------------------------------------------- | ------------------------------------------------------------------ |
| `AccessRule`                                               | Permissão/proibição de uso de uma via ou sentido                   |
| `TurnRestriction`                                          | Restrição de transição entre arcos (via nó ou sequência de vias)   |
| `RestrictionSource`                                        | Proveniência: ID de relação OSM, tags e versão da fonte            |

### Decisões específicas

| Questão                  | Decisão                                                      |
| ------------------------ | ----------------------------------------------------------- |
| `oneway`                 | Interpretar com exceções para bicicletas                    |
| `bicycle=no`             | Preservar como restrição de acesso                          |
| `oneway:bicycle`         | Interpretar independentemente do `oneway` geral             |
| Restrições de conversão  | Preservar e avaliar na transição entre arcos                |
| Restrições condicionais  | Representar condição e contexto                             |
| Tags desconhecidas       | Preservar sem inventar interpretação                        |
| Contraflow               | Política explícita, nunca liberação silenciosa de proibição |

Preservar integralmente os dados **não** significa que toda condição
condicional já esteja no interpretador. Condição não avaliável ⇒ o sistema
sinaliza a limitação e aplica **política conservadora documentada**, nunca
"permitido por padrão".

`AccessRule` representa permissões ou proibições de uso de uma via ou sentido.
`TurnRestriction` representa restrições de transição entre arcos. O caminho
intermediário é **ordenado**: `via_node_id` (um nó) ou `via_way_ids` (sequência
ordenada de vias de uma restrição `only_*`), nunca um conjunto — a ordem de
travessia é significativa.

### Estado da implementação (representação × cumprimento)

Esta entrega **representa e preserva** as restrições. Quando este ADR foi
escrito, ela **não as aplicava**. Estado **atualizado**:

- **Provisão — fechada.** `turn_restriction_from_relation()` passou a ser
  integrado ao `osm/build.py`: o leitor `osm.pbf` lê relações `type=restriction`
  e o `build_graph` as anexa a `RoutingGraph.turn_restrictions`. A ingestão
  online via Overpass foi **removida** (fonte: arquivo `.osm.pbf` local).
- **Cumprimento — fechado no ADR-026.** Os guardrails de circulação avaliam
  `TurnRestriction` **e** condicionais durante a geração de caminhos
  (`026-contrato-circulacao.md` §4-§6), tanto nas rotas tradicionais quanto na
  geração orientada por ML. Condição não avaliável ⇒ **política conservadora
  explícita** (nunca "permitido por padrão").

> **Registro histórico.** No estado original deste ADR, a representação existia
> mas **nada a aplicava**: `turn_restriction_from_relation()` não era integrado
> ao build e nenhuma lógica de busca/validação avaliava `TurnRestriction`. A
> ausência de avaliação era uma **lacuna declarada**, não uma garantia
> implícita.

### Invariantes

- Ausência de restrição não é permissão absoluta.
- Restrição legal não é penalidade ajustável de custo.
- Arco proibido não se torna válido por rota mais curta.
- O guardrail também valida transições entre arcos.
- Simplificação e snapping preservam as restrições aplicáveis.

**Escopo desta entrega:** contratos (`AccessRule`, `TurnRestriction`,
`RestrictionSource`), registro no grafo, serialização e **preservação** das
restrições através de `copy()` e `simplify_graph()` (o snapping sempre opera
sobre uma cópia do grafo). A **avaliação durante a transição** e o parse de
`relation` no build entram em incremento seguinte; nenhuma lógica de roteamento
é alterada agora.

## Decisão 3 — Qualidade e proveniência da elevação

Aprovada. Elevação é dado medido ou estimado, com qualidade explícita.

```python
@dataclass(frozen=True, slots=True)
class ElevationSample:
    distance_m: float
    elevation_m: float | None
    source_id: str | None = None
    resolution_m: float | None = None
    quality: ElevationQuality | None = None
```

Categorias (`ElevationQuality`): `MEASURED`, `DEM_SAMPLED`, `SOURCE_REPORTED`,
`INTERPOLATED`, `ESTIMATED`, `MISSING`. A resolução espacial e a origem são
preservadas independentemente do estado de qualidade.

**`MEASURED` é reservado a fontes que documentam uma medição instrumental.**
`ele=*` do OSM é uma altitude **declarada pela fonte** e por isso recebe
`SOURCE_REPORTED` — não `MEASURED` (semântica corrigida no endurecimento).
`OBSERVED_ELEVATION_QUALITIES = {MEASURED, DEM_SAMPLED, SOURCE_REPORTED}`
identifica os estados sustentados por fonte, em oposição a preenchimentos
posteriores (interpolação/estimação).

### Invariantes

1. `MISSING` exige `elevation_m is None`.
2. Qualquer outro estado exige altitude numérica finita.
3. Distâncias e resoluções são validadas (`distance_m ≥ 0`, `resolution_m > 0`).
4. Proveniência preservada na ingestão, serialização e reconstrução.
5. A camada física não interpreta elevação ausente como zero nem como plano.
6. Interpolação e estimativa são operações explícitas, rastreáveis e
   configuráveis, distintas dos dados originais.
7. Sem limites arbitrários de interpolação: pertencem a uma política de
   processamento de elevação (parâmetros futuros, não magic numbers).

### Cobertura (numérica × qualidade)

`ArcProfile.unknown_elevation_m` (já existente) mede a distância sem elevação.
Duas métricas **separadas** evitam confundir cobertura numérica com
confiabilidade:

- `coverage_available = 1 − unknown_elevation_m / distance_m` — fração com
  **alguma** elevação conhecida.
- `coverage_observed = observed_elevation_m / distance_m` — fração sustentada
  por amostras **observadas** (`MEASURED`/`DEM_SAMPLED`/`SOURCE_REPORTED`), sem
  contar interpolações nem estimativas.

Um perfil 100% preenchido por interpolação/estimação tem
`coverage_available == 1.0` mas `coverage_observed` baixo — a camada física
recebe a distinção e não pode tratar cobertura cheia como confiabilidade plena.

### Serialização (v4, sem versões anteriores)

Este projeto é um **novo** algoritmo que consome os dados do motor. Não há
consumidor da API antiga, então **não há camada de compatibilidade**:

- a leitura aceita **somente a versão atual (v4)**; payloads v2/v3 são recusados;
- `AccessRestriction`, `ElevationSample.source`, `ArcProfile.coverage` e
  `quality_inferred` foram **removidos** (não há aliases), pois só existiam para
  a API/formato antigos.

## Consequências

- ML e rota tradicional compartilham o mesmo grafo, as mesmas restrições e os
  mesmos perfis (ver ADR-022 do `toporide`): o ML prevê RPE/zonas e propõe rota,
  sem duplicar nem reinterpretar o dado geográfico.
- **Incompatibilidades assumidas (sem shims):**
  - `ElevationSample.source` → `source_id` (o nome antigo foi removido).
  - `Arc` não aceita `geometry=` no construtor (a geometria é derivada).
  - serialização apenas **v4** (v2/v3 recusados).
- Os dados legados (`data/*.json`, v2) e payloads v3 devem ser **regenerados**.

## Endurecimento (revisão da auditoria ADR-024)

Rodada focada em cache, orientação de geometria, proveniência de elevação e
migração v3 — **sem novas funcionalidades**:

1. `ele=*` reclassificado de `MEASURED` para `SOURCE_REPORTED`.
2. Cache geométrico amarrado à identidade dos segmentos (não sobrevive a
   alteração de segmento, mesmo por mutação direta); `Segment` é `frozen`.
3. Cobertura separada em `coverage_available` × `coverage_observed`.
4. Serialização **v4-only**; aliases de compatibilidade removidos.
5. `via_way_ids` é sequência ordenada.
6. Documentado explicitamente que restrições são **representadas, não impostas**;
   o guardrail (quando implementado) valida rotas tradicionais **e** ML.
7. Testes de invariante adicionados (v4 round-trip, v2/v3 recusados, geometria
   reversa pós-simplificação/snapping, elevação ausente persistente, ordem de
   via, cópia preserva restrições+geometria, snapping não muta o original, cache
   após mutação, `concat_geometry` só desduplica consecutivos).

## Fora de escopo (follow-ups registrados)

- `GeoPoint(latitude, longitude)` + conversão GeoJSON só em adaptadores.
- Remover `SURFACE_PRIORITY` do modelo geográfico.
- Avaliação de `TurnRestriction` na transição de arcos e parse de `relation`
  no `osm/build.py`.
- Tipos dedicados `ElevationProfile`/`ElevationSummary`/`GradeProfile` e
  política configurável de interpolação.
