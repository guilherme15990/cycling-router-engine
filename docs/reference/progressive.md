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

O bbox inicial cresce até abranger os nós do grafo carregado. A fila e a
fronteira são preservadas entre eventos de uma busca; uma expansão requer nova
busca sobre a zona maior. O plano informa `search_bbox` e `bbox` separadamente.
Não há busca ou download automático fora do grafo fornecido e não há promessa
de otimalidade global: `details["optimality_proven"]` é `False`.

Em múltiplas pernas, as buscas independentes rodam em threads e só é publicado
um plano após todas as pernas terem rota elegível. A fisiologia/fadiga não é
encadeada entre pernas no contrato v1. Chamadas de `on_event` são serializadas,
mas podem ocorrer em threads de busca: consumidores de interface devem usar uma
fila ou IPC, sem manipular widgets diretamente no callback.

Não há limite arbitrário de expansões geográficas: a cobertura do grafo finito
encerra o crescimento do bbox. A busca multiobjetivo dentro de uma zona pode
ser muito longa e a terminação geral para estados físicos contínuos ainda não
está provada. `should_cancel` permite cancelamento cooperativo; não se deve
interpretar tempo esgotado como prova de ausência de rota. A API aceita o mesmo
perfil, parâmetros de física, preditor e limites de rotas de `plan_routes`, mas
`max_iterations` não se aplica; `growth` deve ser finito e maior que 1.
