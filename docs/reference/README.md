# Documentação

Índice da documentação do motor de roteamento ciclável.

## Para desenvolvedores

- [`quickstart.md`](quickstart.md) — guia rápido com 3 receitas: do OSM às
  rotas, perfil elétrico com bateria e exploração da fronteira Pareto.
- [`api.md`](api.md) — referência da API pública (141 símbolos de `api.__all__`),
  com assinaturas geradas automaticamente por
  [`../scripts/generate_api_reference.py`](../scripts/generate_api_reference.py).
  Não edite à mão: regenere com
  `python scripts/generate_api_reference.py`.
- [`perfil-eletrico.md`](perfil-eletrico.md) — especificação do perfil
  `Elétrico`: modelo energético separado, bateria opcional e o mapeamento de
  implementação.

## Projeto

- [`../README.md`](../README.md) — visão geral, instalação, uso, estrutura e
  limitações.
- [`../CHANGELOG.md`](../CHANGELOG.md) — histórico de versões e política de
  estabilidade (SemVer).
- [`../gui/README.md`](../gui/README.md) — interface gráfica (Tkinter).

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
