# Guia rápido

Receitas curtas para usar a API. Requer **Python ≥ 3.10**. Rode a partir da raiz
do repositório com `src` no `PYTHONPATH` (ou instale o pacote):

```powershell
$env:PYTHONPATH = "src"
python sua_receita.py
```

A documentação completa dos símbolos públicos está em [`api.md`](api.md). A
especificação do perfil elétrico está em
[`perfil-eletrico.md`](perfil-eletrico.md).

---

## Receita 1 — Do OSM às rotas (grafo → snapping → busca)

O grafo não sabe nada sobre perfis; o snapping apenas conecta origem/destino; a
busca escolhe as rotas.

```python
from router.core.data.graph.serialize import load_json
from router.core.physics import BicycleModel, CyclistModel, PhysicalTraversal
from router.core.search import (
    PhysicalTraversalModel,
    ProfileSearchRequest,
    route_profile_for,
    search_profile,
)
from router.core.data.snapping import build_query_graph

# 1. Grafo ciclavel. Aqui um grafo ja salvo; veja "Construir a partir do OSM".
graph = load_json("data/se.json")

# 2. Modelo fisico: quem pedala + bicicleta + simulacao de travessia.
cyclist = CyclistModel(
    mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
)
traversal = PhysicalTraversalModel(
    traversal=PhysicalTraversal(),
    cyclist=cyclist,
    bicycle=BicycleModel(),
    power_w=200.0,
)

# 3. Conecta origem/destino. O grafo original NAO e alterado.
query = build_query_graph(
    graph,
    origin=(-23.548699, -46.6326332),
    destination=(-23.5485103, -46.6308005),
)
assert query.is_connected()

# 4. Busca + selecao para um perfil ("fastest", "shortest", "flattest",
#    "balanced" ou "electric").
request = ProfileSearchRequest(
    origin=query.origin_id,
    destination=query.destination_id,
    profile=route_profile_for("fastest"),
    max_routes=3,
)
response = search_profile(query.graph, traversal, request)

for route in response.routes:
    metrics = route.metrics
    print(
        f"{route.route_id}: {metrics.time_s / 60:.1f} min, "
        f"{metrics.distance_m / 1000:.2f} km, +{metrics.ascent_m:.0f} m"
    )
```

### Construir o grafo a partir do OSM (offline)

```python
from router.core.data.elevation import TerrariumElevation
from router.core.data.osm.build import attach_elevation, build_graph
from router.core.data.osm.pbf import read_osm_pbf

# Fonte geografica local (arquivo .osm.pbf); sem ingestao online.
data = read_osm_pbf("dados.osm.pbf")
graph, stats = build_graph(data)
print(stats.ways_selected, "vias", graph.node_count, "nos", graph.edge_count, "arestas")

# Elevacao por perfil amostrado a partir de tiles Terrarium locais.
attach_elevation(graph, TerrariumElevation("data/dem", zoom=12))
```

---

## Receita 2 — Perfil elétrico com bateria

O perfil `electric` usa **energia mecânica** e tempo como objetivos Pareto (a
ordem é a mesma da energia elétrica, por um fator `1/η` constante). A conversão
para Wh e a viabilidade de bateria acontecem **depois** da seleção, em
`response.energy`. A bateria é opcional: sem ela, não há autonomia nem
percentual.

```python
from router.core.physics import BatteryConfig, EnergyModel, GenericEnergyConfig
from router.core.search import ProfileSearchRequest, route_profile_for, search_profile

# Modelo energetico: sem configuracao, usa valores de referencia internos.
# Aqui, um modelo personalizado so com a eficiencia da transmissao.
model = EnergyModel.configured(GenericEnergyConfig(drivetrain_efficiency=0.75))

# Bateria: capacidade, carga inicial (padrao = cheia) e reserva desejada.
battery = BatteryConfig(capacity_wh=500.0, reserve_ratio=0.15)

request = ProfileSearchRequest(
    origin=query.origin_id,
    destination=query.destination_id,
    profile=route_profile_for("electric"),
    max_routes=3,
    energy_model=model,
    battery=battery,
)
response = search_profile(query.graph, traversal, request)

for route, energy in zip(response.routes, response.energy):
    print(f"{route.route_id}: {energy.electrical_energy_wh:.1f} Wh")
    if energy.battery is not None:
        print("  ", energy.battery.message())
```

Sem bateria configurada, `energy.battery` é `None` e apenas
`energy.electrical_energy_wh` é informado.

---

## Receita 3 — Explorar a fronteira Pareto

A seleção das N rotas é uma camada posterior. Se você quer a **fronteira
completa** de destino, use o A\* multiobjetivo diretamente.

```python
from router.core.search import MultiObjectiveAStar, profile_for, routes_from_result

search = MultiObjectiveAStar(query.graph, traversal, profile_for("fastest"))
result = search.search(query.origin_id, query.destination_id)

print("alcançou:", result.reached, "| rotas não dominadas:", result.count)

# Cada rota da fronteira, com as metricas finais.
for route in routes_from_result(result):
    print(
        f"{route.time_s / 60:5.1f} min  {route.distance_m / 1000:5.2f} km  "
        f"+{route.ascent_m:4.0f} m  {route.energy_j / 1000:6.1f} kJ"
    )
```

Para ordenar a fronteira segundo a preferência de um perfil (sem escolher N):

```python
from router.core.search import order_routes, route_profile_for

for route in order_routes(routes_from_result(result), route_profile_for("balanced")):
    print(route.time_s, route.distance_m, route.ascent_m)
```

---

## Receita 4 — Consumir o contrato de rota (v1)

`plan_offline_routes` devolve o DTO interno; para transporte use
`RoutePlan.to_contract()` e os serializadores **estritos** de
`router.contracts`. O envelope é versionado (`format`/`version`) e não perde
geometria.

```python
from router.app import plan_offline_routes
from router.contracts import dumps, loads

plan = plan_offline_routes(
    graph, origin, destination, cyclist=cyclist, bicycle=bicycle
)
contract = plan.to_contract()
text = dumps(contract, indent=2)  # allow_nan=False, sem default=str

payload = loads(text)  # rejeita versão desconhecida e payload inválido
for route in payload.routes:
    print(route.id, route.metrics.source, route.ml.mean_rpe)
    for point in route.geometry[:3]:
        print(point.dist_m, point.band, point.rpe, point.watts)
```

Regras que o contrato garante (e que o consumidor pode assumir):

- `format`/`version` sempre presentes; versão desconhecida é erro;
- `dist_m` monotônico ao longo de `geometry`;
- `None` significa **não calculado** (`contra_m`, `ele` sem
  altimetria), nunca zero inventado;
- `ml.calibrated`/`ml.is_measurement` explícitos — a estimativa atual é
  computacional, não calibrada;
- chaves em `snake_case`; o consumidor (futuro porte em TypeScript) traduz para
  `camelCase`.

O mesmo contrato esta exportado em `router.contracts` e na
[referência](api.md#contrato-publico-de-rota-v1). Para arquivo:

```python
from router.contracts import read_plan, write_plan

write_plan("rota.json", contract)
mesmo = read_plan("rota.json")
```

---

## Receita 5 — Múltiplas paradas e superfície de integração

`plan_routes` resolve a rota inteira **no núcleo**: uma busca por perna, na
ordem das paradas, e uma fusão contínua de geometria/métricas. Sem paradas, o
resultado é idêntico ao de uma perna única.

```python
from router.integration import (
    BicycleModel,
    CyclistModel,
    dumps,
    plan_routes,
)

plan = plan_routes(
    graph,
    origin,
    destination,
    stops=[stop_a, stop_b],
    cyclist=CyclistModel(mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0),
    bicycle=BicycleModel(),
    profile="fastest",
)

assert plan.request.stops == (stop_a, stop_b)
route = plan.routes[0]
assert route.provenance.kind == "multi_leg"
print(dumps(plan))
```

Regras da fusão multi-perna:

- a geometria é contínua e a junção não é duplicada; `dist_m` é acumulado e
  monotônico ao longo de todas as pernas;
- distância, tempo, energia e subida/descida são **somados**; `meters_band`
  também; `max_grade_pct` é o maior entre as pernas;
- um valor desconhecido em **qualquer** perna permanece `None` (nunca zero);
- só existe trajeto completo quando **todas** as pernas têm rota elegível —
  uma perna sem rota não vira trajeto parcial silencioso;
- o estado físico não é encadeado entre pernas na v1: cada perna parte do
  estado inicial informado.

`integration` é a superfície **curada** para o aplicativo (`toporide`): apenas
contrato v1, planejamento, modelos de entrada, grafo (`load_graph`/
`save_graph`) e guard offline. A fachada `api` continua ampla por
compatibilidade.

```python
from router import integration

print(integration.PROFILE_IDS)
graph = integration.load_graph("dados.graph.json")
plan = integration.plan_routes(graph, origin, destination, cyclist=c, bicycle=b)
```

---

## Próximos passos

- **Referência da API**: [`api.md`](api.md) — todos os 336 símbolos públicos.
- **Perfil elétrico**: [`perfil-eletrico.md`](perfil-eletrico.md) — decisões e
  mapeamento de implementação.
- **Visão geral do projeto**: [`../README.md`](../README.md).
