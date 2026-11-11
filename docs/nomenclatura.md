# Nomenclatura padronizada — "roteamento com ml"

> Norma **N-01**. Esta é a fonte da verdade para os nomes das entidades do
> domínio de roteamento. Todo código, dado, métrica e documento deste projeto
> usa estes termos. Sinônimos são proibidos no vocabulário do domínio.

## Entidades

| Entidade | Significado |
| --- | --- |
| **Node** | Interseção ou ponto topológico |
| **Segment** | Trecho homogêneo de uma via |
| **Link** | Ligação física entre dois nós |
| **Arc** | Possibilidade de deslocamento direcionado |

## Hierarquia e cardinalidade

```
Node ──(1:N)── Link ──(1:N)── Segment
                  └──(1:N)── Arc ──(1:N)── Segment   (referência, não posse)
```

- Um **Node** é um ponto único e imutável no espaço (`id`, `lat`, `lon`,
  `elevation_m` opcional).
- Um **Link** é o par físico `(Node origem, Node destino)`. Ele existe **uma
  única vez**, independentemente do sentido de tráfego. Um `Link` é composto por
  um ou mais `Segment`s em ordem.
- Um **Arc** é uma **possibilidade de deslocamento direcionado** sobre um
  `Link`. Cada `Link` gera de 1 a 2 `Arc`s (um por sentido permitido). É a
  unidade que a busca percorre.
- Um **Segment** é um trecho homogêneo (mesma superfície, mesma via, mesmos
  atributos). Os `Arc`s **referenciam** os `Segment`s do seu `Link` (ordem
  direta ou reversa); os `Segment`s não são duplicados por sentido.

### Regras (invariantes)

1. `Segment` pertence a exatamente **um** `Link` (posse física).
2. `Arc` pertence a exatamente **um** `Link` e aponta de um `Node` a outro
   (`Arc.source != Arc.target`).
3. Um `Link` tem **no máximo dois** `Arc`s: `forward` e `backward`.
4. Atributos físicos (superfície, elevação, distância, restrições) vivem no
   `Segment`. Ganho/perda de elevação e geometria **dependem do sentido** e são
   derivados no `Arc`.
5. `Node`/`Link`/`Segment` são **não-direcionados**; apenas `Arc` carrega
   direção.
6. A identidade estável de cada entidade é o seu `id` (string). Nunca usar
   posição em lista como identidade.

## Definições de apoio

- **Grafo de roteamento (RoutingGraph):** conjunto de `Node`s, `Link`s e `Arc`s.
  A busca opera sobre `Arc`s; a adjacência é por `Node` de origem.
- **Ponto topológico:** `Node` que existe por necessidade de topologia
  (curva/forma da via), não por interseção real. Pode ser simplificado
  (contração de grau 2) sem perder atributos, pois estes vivem nos `Segment`s.
- **Aresta/Edge:** termo **proibido**. Substituído por `Link` (físico) e `Arc`
  (direcionado). Nunca usar "edge" no domínio.

## Mapeamento a partir do motor atual (`roteamento engine`)

O motor em `$apps/roteamento engine` é a base de código reutilizada (fork em
`src/`). A padronização renomeou as entidades assim — **já aplicado** no código
deste projeto:

| Nome atual no código (`roteamento engine`) | Nome padronizado aqui |
| --- | --- |
| `Node` (`graph/model.py`) | **Node** (mantido) |
| `EdgeSegment` (`graph/model.py`) | **Segment** |
| `Edge` (direcionado, `graph/model.py`) | **Arc** |
| `CyclingGraph` | **RoutingGraph** |
| `Edge.source` / `Edge.target` | `Arc.source` / `Arc.target` (Nodes) |
| `Edge.reversed_edge()` | `Arc` oposto do mesmo `Link` |
| `add_bidirectional(edge)` | cria `Link` + `Arc` forward/backward |
| *(não existe)* | **Link** (conceito novo: agrupa o par de `Arc`s) |

### Convenções de campo derivadas

Os nomes de campo que mudam de dono:

| Conceito | No `Segment` | No `Arc` |
| --- | --- | --- |
| `distance_m` | comprimento do trecho | soma dos trechos |
| `surface`, `highway`, `name`, `tags`, `restrictions`, `osm_way_id` | valor próprio (fonte) | agregado dominante por comprimento |
| `elevation_gain_m` / `elevation_loss_m` | não se aplica (sem sentido) | derivado do sentido |
| `elevation_profile` | perfil local + proveniência | perfil consolidado no sentido do `Arc` |
| `geometry` | forma do trecho | concatenada e orientada no sentido |

O `Link` reúne as agregações **sem sentido** (`distance_m`, `surface`, `name`,
…). Só o `Arc` carrega ganho/perda e perfil orientados.

## Nomes de código (identificadores)

- Classes: `Node`, `Segment`, `Link`, `Arc`, `RoutingGraph`.
- Coleções: `nodes`, `segments`, `links`, `arcs`; contagens `node_count`,
  `segment_count`, `link_count`, `arc_count`.
- Acesso: `get_node`, `get_link`, `get_arc`, `out_arcs(node)`, `in_arcs(node)`,
  `incident_links(node)`, `add_link`, `link_of(arc)`.
- Arquivo-fonte do modelo: `src/graph/model.py` (um único módulo com as cinco
  entidades; a divisão em `src/domain/` fica como evolução futura opcional).

## Formato serializado

O `graph_to_dict` grava as seções `format`, `version`, `metadata`, `nodes`,
`arcs`, `links` e `turn_restrictions` (**versão 4** do formato). O
`graph_from_dict` aceita **apenas** a versão atual — não há camada de
compatibilidade nem leitura de versões anteriores (ver `CHANGELOG.md`, `0.14.0`).

Versão da norma: **1.0** · aplicada no fork deste projeto (motor `0.18.0`).

