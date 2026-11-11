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
from graph.serialize import load_json
from physical import BicycleModel, CyclistModel, PhysicalTraversal
from search import (
    PhysicalTraversalModel,
    ProfileSearchRequest,
    route_profile_for,
    search_profile,
)
from snapping import build_query_graph

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
from elevation import TerrariumElevation
from osm.build import attach_elevation, build_graph
from osm.pbf import read_osm_pbf

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
from physical import BatteryConfig, EnergyModel, GenericEnergyConfig
from search import ProfileSearchRequest, route_profile_for, search_profile

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
from search import MultiObjectiveAStar, profile_for, routes_from_result

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
from search import order_routes, route_profile_for

for route in order_routes(routes_from_result(result), route_profile_for("balanced")):
    print(route.time_s, route.distance_m, route.ascent_m)
```

---

## Próximos passos

- **Referência da API**: [`api.md`](api.md) — todos os 141 símbolos públicos.
- **Perfil elétrico**: [`perfil-eletrico.md`](perfil-eletrico.md) — decisões e
  mapeamento de implementação.
- **Visão geral do projeto**: [`../README.md`](../README.md).
- **Interface gráfica**: [`../gui/README.md`](../gui/README.md).
