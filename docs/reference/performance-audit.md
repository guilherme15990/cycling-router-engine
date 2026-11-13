# Auditoria de compatibilidade de desempenho: API x TopoRide

## Resultado inicial

No grafo real usado pela bancada, a API atual publicou a primeira rota validada
em aproximadamente 29,5 s. Antes do encerramento antecipado do A*, a interface
continuava mostrando a consulta por centenas de segundos porque a busca
multiobjetivo esgotava a fronteira depois que já existia uma rota elegível.

## Diferenças de arquitetura

O TopoRide usa `search()` como uma busca A* best-first de objetivo único. Cada
estratégia termina assim que o destino é retirado do heap. O limite de segurança
é `ML_SEARCH.MAX_ITERATIONS = 600_000`, e a dominância é feita por
`(nó, faixa de velocidade, arco de entrada)`.

A API atual usa `MultiObjectiveAStar`. Cada transição executa o simulador físico
completo, mantém múltiplos labels Pareto por estado e, originalmente, explorava
toda a fronteira do destino. Depois a rota ainda é reavaliada com física completa,
ML, classificação e segurança. Isso faz a busca pagar física pesada em todos os
labels e novamente nos candidatos emitidos.

## Causas prováveis do custo

1. Física completa dentro do loop principal do A*.
2. Mais de um label por estado físico e fronteira Pareto aberta.
3. Avaliação física repetida após a busca.
4. Busca no grafo inteiro da cidade quando o objetivo é uma rota curta.
5. Instrumentação de camadas adicionando custo ao caminho crítico.
6. O contrato atual tenta publicar alternativas antes de separar o caminho
   rápido de produção do caminho de auditoria.

## Compatibilidade necessária para portar ao TopoRide

O caminho de produção precisa reproduzir a ordem do TopoRide:

```text
grafo local + DEM em cache
  -> snapping
  -> A* objetivo único por estratégia
  -> conjunto pequeno de candidatas
  -> física/ML apenas nas candidatas
  -> seleção e segurança
  -> contrato + camadas do mapa
```

O fluxo público mantém um único modelo físico; a principal otimização é limitar a
zona inicial à BBox e expandi-la somente quando a conectividade exigir. A próxima
etapa da auditoria deve medir cada estágio separadamente e comparar, no mesmo
fixture, `nodesExpanded`, chamadas de travessia, tempo de snapping, tempo do A*,
tempo de avaliação e tempo de ML.

## Critério de aceite

Para pontos próximos em uma reta, a rota deve aparecer rapidamente, a consulta
deve terminar sem esgotar a fronteira global e as métricas devem indicar se a
rota foi encontrada pelo caminho TopoRide ou pelo modo de auditoria.
