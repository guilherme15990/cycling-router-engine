# Busca progressiva

`router.integration.plan_routes_progressive` executa uma consulta offline e
publica eventos por callback. Cada rota publicada passou pela simulação física
completa, estimativa de intensidade e avaliação de segurança. Uma rota pode ser
substituída ou dominada por uma descoberta posterior.

```python
from threading import Event
from router.integration import (
    BicycleModel, CyclistModel, load_graph, plan_routes_progressive,
)

graph = load_graph("graph_v4.json")
cancel = Event()

def on_event(event):
    print(event.status, event.leg, event.legs, event.details)
    if event.plan is not None:
        print(event.plan.recommended_route_id, len(event.plan.routes))

result = plan_routes_progressive(
    graph, (-17.7818, -50.9035), (-17.7711, -50.9197),
    cyclist=CyclistModel(mass_kg=75, max_power_w=600,
                         sustainable_power_w=200, frontal_area_m2=0.5),
    bicycle=BicycleModel(),
    profile="fastest",
    on_event=on_event,
    should_cancel=cancel.is_set,
)
```

Estados de `RouteProgress.status`:

* `searching`: busca em andamento; `details` identifica a zona e a perna.
* `route_found`: plano parcial validado; pode mudar enquanto a busca continua.
* `completed`: a busca terminou na cobertura do grafo fornecido.
* `no_route`: não foi retornada rota elegível nessa cobertura.
* `interrupted`: a busca foi cancelada; `plan` preserva o último plano válido,
  ou é `None` quando nenhuma rota completa foi publicada.

A busca ocorre **uma única vez**, dentro do **corredor** da consulta: o box dos
pinos com as margens automáticas por eixo (`use_full_graph=False`, padrão) ou o
extent do grafo carregado (`use_full_graph=True`). O box é definido **antes** da
busca e a limita — como no `toporide` (`solveAll`), não há ampliação de zona nem
segunda busca. Um corredor que não conecte origem e destino resulta em
`no_route`, sem crescer. O plano informa `search_bbox` (o corredor buscado) e
`bbox` (o box final de exibição/altimetria, união do corredor com o traçado das
rotas devolvidas, que **pode** exceder o corredor quando um trecho desvia para
fora). Não há busca ou download automático fora do grafo fornecido e não há
promessa de otimalidade global: `details["optimality_proven"]` é `False`.

Em múltiplas pernas, as buscas independentes rodam em threads e só é publicado
um plano após todas as pernas terem rota elegível. A fisiologia/fadiga não é
encadeada entre pernas no contrato v1. Chamadas de `on_event` são serializadas,
mas podem ocorrer em threads de busca: consumidores de interface devem usar uma
fila ou IPC, sem manipular widgets diretamente no callback.

A busca multiobjetivo dentro do corredor pode ser muito longa e a terminação
geral para estados físicos contínuos ainda não está provada. `should_cancel`
permite cancelamento cooperativo; não se deve interpretar tempo esgotado como
prova de ausência de rota. A API aceita o mesmo perfil, parâmetros de física,
preditor e limites de rotas de `plan_routes`, mas `growth` e `max_iterations`
são **legados e inertes** (mantidos por compatibilidade de assinatura: a busca
não cresce).
