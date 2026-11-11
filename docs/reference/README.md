# Documentação

Índice da documentação do motor de roteamento ciclável.

## Para desenvolvedores

- [`quickstart.md`](quickstart.md) — guia rápido com 5 receitas: do OSM às
  rotas, perfil elétrico com bateria, exploração da fronteira Pareto, contrato
  de rota v1 e múltiplas paradas/superfície de integração.
- [`api.md`](api.md) — referência da API pública (336 símbolos de `api.__all__`),
  com assinaturas geradas automaticamente por
  [`../scripts/generate_api_reference.py`](../scripts/generate_api_reference.py).
  Não edite à mão: regenere com
  `python scripts/generate_api_reference.py`.
- [`perfil-eletrico.md`](perfil-eletrico.md) — especificação do perfil
  `Elétrico`: modelo energético separado, bateria opcional e o mapeamento de
  implementação.

## Arquitetura (ADR-031)

O motor vive em um namespace unico, `router`:

- `router.contracts` - contrato publico `v1` (dados versionados);
- `router.integration` - superficie curada para o aplicativo;
- `router.core.data` / `router.core.physics` / `router.core.search` /
  `router.core.prediction` / `router.core.evaluation` - nucleo interno;
- `router.app` - orquestracao; `router.interfaces` - adaptadores (API/CLI).

`router.core.*` nao oferece estabilidade. A fachada ampla
`router.interfaces.api` permanece congelada por compatibilidade. As fronteiras
sao verificadas por `tests/test_architecture.py`.

A estrutura-alvo revisada (achatamento de `physical`/`intelligence`,
`physiology` como irmao da fisica e identidade de pacote) esta no
[ADR-032](../adr/032-estrutura-alvo-e-identidade-de-pacote.md), ainda como
proposta; nenhum codigo foi movido.

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
