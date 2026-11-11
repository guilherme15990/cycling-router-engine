# Documentação

Índice da documentação do motor de roteamento ciclável.

## Para desenvolvedores

- [`quickstart.md`](quickstart.md) — guia rápido com 5 receitas: do OSM às
  rotas, perfil elétrico com bateria, exploração da fronteira Pareto, contrato
  de rota v1 e múltiplas paradas/superfície de integração.
- [`api.md`](api.md) — referência da **fachada ampla congelada**
  (`router.interfaces.api`, 336 símbolos), mantida por compatibilidade; **não** é
  a API recomendada (prefira a superfície curada `router.integration`).
  Assinaturas geradas automaticamente por
  [`../scripts/generate_api_reference.py`](../scripts/generate_api_reference.py).
  Não edite à mão: regenere com
  `python scripts/generate_api_reference.py`.
- [`perfil-eletrico.md`](perfil-eletrico.md) — especificação do perfil
  `Elétrico`: modelo energético separado, bateria opcional e o mapeamento de
  implementação.

## Arquitetura (ADR-031; ADR-032/033)

O motor vive em um namespace unico, `router`:

- `router.contracts` - contrato publico `v1` (dados versionados);
- `router.integration` - **unica superficie publica de nivel B** (curada para o
  aplicativo);
- `router.core.data` / `router.core.physics` / `router.core.physiology` /
  `router.core.search` / `router.core.prediction` / `router.core.evaluation` -
  nucleo interno;
- `router.app` - orquestracao; `router.interfaces` - adaptadores (API/CLI).

`router.core.*` nao oferece estabilidade. `router.interfaces.api` e **legado
congelado por compatibilidade** (336 nomes), nao a API recomendada; ela ainda
reexporta `router.integration` como `api.integration`. As fronteiras sao
verificadas por `tests/test_architecture.py`.

A estrutura-alvo do
[ADR-032](../adr/032-estrutura-alvo-e-identidade-de-pacote.md) (achatamento de
`physical`/`intelligence`, `physiology` como irmao da fisica, `router.integration`
como fonte unica e identidade de pacote) foi **executada**; o registro esta na
secao 14 do ADR-032 e na secao 14 do
[ADR-033](../adr/033-arquitetura-por-componentes-de-pipeline.md).

## Projeto

- [`../README.md`](../README.md) — visão geral, instalação, uso, estrutura e
  limitações.
- [`../CHANGELOG.md`](../CHANGELOG.md) — histórico de versões e política de
  estabilidade (SemVer).

## Diagramas

As camadas do pipeline, por módulo:

- [`modulo 1.png`](modulo%201.png) — OSM e grafo ciclável
- [`modulo 2.png`](modulo%202.png) — enriquecimento (superfície, elevação,
  restrições)
- [`modulo 3.png`](modulo%203.png), [`modulo 3.1.png`](modulo%203.1.png),
  [`modulo 3.2.png`](modulo%203.2.png) — busca multiobjetivo e seleção por
  perfil
- [`modulo 4.png`](modulo%204.png) — snapping
- [`modulo 5.png`](modulo%205.png) — modelo físico de travessia
- [`engine.png`](engine.png) — visão geral da engine
