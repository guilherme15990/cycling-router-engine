# Cycling Routing Engine

Motor de roteamento cicloviário multiobjetivo, offline e auditável. O núcleo é
Python puro, combinando modelo físico longitudinal, busca Pareto, regras de
circulação OSM, elevação e uma camada opcional de Machine Learning.

O projeto separa quatro decisões: a física calcula as métricas; a busca encontra
alternativas não dominadas; o ML pode gerar uma candidata independente; e a
política do perfil ordena e limita o que será apresentado. A candidata ML passa
pelas mesmas validações físicas e de circulação. Se coincidir com uma rota
Pareto, o resultado preserva as duas origens.

## Recursos principais

- Física longitudinal com gravidade, rolamento, arrasto, vento, aceleração,
  frenagem e continuidade da velocidade entre trechos.
- Busca multiobjetivo com estado físico, heurísticas, dominância por velocidade
  e fronteira Pareto.
- Perfis `fastest`, `shortest`, `flattest`, `balanced` e `electric`.
- Importação local de OSM PBF, elevação Terrarium e guard de rede offline.
- Restrições de acesso, mão única, contramão configurável e restrições de
  conversão quando fornecidas pelo grafo.
- Presets de ciclista manual para estimar intensidade/RPE sem sensores.
- Rede neural determinística opcional para produzir uma rota ML baseada nos
  objetivos físicos do perfil.
- Contrato de rota versionado, serialização estrita e múltiplas paradas.

## Requisitos e instalação

Requer Python 3.10 ou mais recente. O núcleo não possui dependências
obrigatórias.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Para recursos opcionais de DEM:

```powershell
python -m pip install -e ".[dem]"
```

Também é possível executar diretamente a partir da raiz:

```powershell
$env:PYTHONPATH = "src"
```

## Uso mínimo

A superfície recomendada para integrações é `router.integration`.

```python
from router.integration import MobilityEngine

engine = MobilityEngine.from_pbf(
    "data/sources/regiao.osm.pbf",
    elevation_tiles="data/dem",
)

plan = engine.plan(
    origin=(-17.804347455072403, -50.92253569751691),
    destination=(-17.770388956200584, -50.920569302992085),
    vehicle="bicycle",
    propulsion_type="manual",
    profile="flattest",
)

print(plan.to_json(indent=2))
```

O grafo e a elevação são carregados localmente. Nenhuma etapa de planejamento
baixa dados da internet. Para reutilizar uma rede preparada:

```python
from router.integration import MobilityEngine, load_graph, save_graph

prepared = engine.prepare(vehicle="bicycle", propulsion_type="manual")
save_graph(prepared, "cache/bicycle.json")
engine = MobilityEngine(load_graph("cache/bicycle.json"))
```

## Perfis de rota

| Perfil | Objetivos principais |
| --- | --- |
| `fastest` | tempo e ganho de elevação |
| `shortest` | distância e tempo |
| `flattest` | ganho de elevação e distância |
| `balanced` | tempo, distância e subida normalizados |
| `electric` | energia mecânica e tempo, com bateria opcional |

O perfil escolhe a política de ordenação; ele não altera a física da travessia.
O perfil elétrico usa trabalho mecânico. Não inventa autonomia ou descarga de
bateria quando esses dados não foram fornecidos.

## Presets manuais e RPE

Para bicicletas manuais, o cálculo mínimo do RPE estimado usa a potência humana
produzida pela simulação e a potência de referência do perfil. Peso corporal e
idade podem ser informados; a idade é registrada como contexto e não altera o
baseline atual.

| Perfil | Potência de referência |
| --- | ---: |
| Sedentário | 75 W |
| Iniciante | 112,5 W |
| Recreativo | 150 W |
| Treinado | 225 W |
| Competitivo | 315 W |
| Elite | 412,5 W |

São presets provisórios, versionados como `manual-rider-presets@1`. Não são FTP
ou medições individuais, não escalam com a massa e não representam potência
aplicada continuamente. O ritmo automático busca aproximadamente 18 km/h,
limitado pela demanda física, pela via e pelo preset; descidas podem exigir
potência humana zero.

```python
from router.integration import ManualRiderProfile

rider = ManualRiderProfile(
    profile="recreational",
    mass_kg=75,
    age_years=35,
)

plan = engine.plan(origin, destination, rider=rider)
for route in plan.routes:
    effort = route.effort_estimate
    print(effort["mean_rpe"], effort["peak_rpe"])
```

O RPE segue a escala Borg 6–20 e é uma estimativa computacional não calibrada.
O contrato informa `calibrated=False` e `is_measurement=False`. Sem observações
de ciclistas, o motor não afirma previsão fisiológica de frequência cardíaca ou
fadiga acumulada.

## Geração de uma rota por ML

O ML tem uma função específica: gerar uma rota candidata independente. Ele não
apenas classifica as rotas retornadas.

```python
plan = engine.plan(
    origin,
    destination,
    profile="flattest",
    rider=rider,
    ml_route=True,
)

for route in plan.routes:
    print(route.origins, route.recommended, route.generation_model_version)

model_dict = plan.diagnostics["neural_routing"]["model"]
```

O fluxo é treinar ou reutilizar a rede, executar a busca neural independente,
executar a busca física Pareto, validar todas as candidatas e então ordenar pela
política física do perfil. A rede atual tem sete entradas, oito neurônios ocultos,
uma saída por objetivo, 180 épocas e seed determinística 42. Os alvos vêm da
simulação física local, portanto o diagnóstico identifica a fonte como
`simulation_surrogate` e `calibrated=False`.

O diagnóstico inclui modelo, versão, amostras, MSE, variáveis e percentuais de
influência derivados dos caminhos absolutos dos pesos. Esses percentuais explicam
a rede; não são pesos adicionais do score e não constituem atribuição causal.

## Quantidade e semelhança das rotas

`max_routes` limita apenas a apresentação; a busca ainda pode encontrar a
fronteira completa.

- `max_routes=None` ou `0` mantém todas as candidatas Pareto.
- Um limite positivo mantém até N candidatas Pareto e preserva a candidata ML,
  podendo resultar em N+1 rotas.
- `collapse_similar=True` remove duplicatas exatas e agrupa rotas semelhantes
  por Jaccard dos arcos direcionados.
- Sem limite explícito, o agrupamento usa `structural-natural-break@1`: o corte
  vem do maior salto na árvore de similaridade, sem percentual fixo.
- Sem separação estrutural clara, nenhuma rota é removida.

```python
plan = engine.plan(
    origin,
    destination,
    profile="balanced",
    max_routes=3,
    collapse_similar=True,
    ml_route=True,
)
```

`max_expansions` e `max_labels_per_state` são limites internos da exploração,
distintos do número de rotas exibidas. Quando atingidos, o diagnóstico marca a
busca como truncada; isso não é apresentado como ótimo global.

## Múltiplas paradas e contrato

`plan_routes` resolve as pernas na ordem das paradas, usa o mesmo grafo e funde a
geometria e as métricas. Uma rota só é considerada completa quando todas as
pernas têm uma candidata elegível.

```python
from router.integration import plan_routes

plan = plan_routes(
    graph,
    origin,
    destination,
    stops=[stop_a, stop_b],
    cyclist=cyclist,
    bicycle=bicycle,
    profile="fastest",
)
```

Os contratos públicos estão em `router.contracts`:

```python
from router.integration import dumps, loads

payload = loads(dumps(plan.to_contract(), indent=2))
```

O envelope é versionado, rejeita versões desconhecidas e preserva geometria,
fontes de parâmetros, incerteza de elevação, origem, RPE e métricas físicas.
`None` significa não calculado; não é convertido silenciosamente em zero.

## CLI e documentação complementar

```powershell
python scripts/mobility_route.py `
  --pbf data/sources/regiao.osm.pbf `
  --dem-dir data/dem `
  --origin=-17.804347455072403,-50.92253569751691 `
  --destination=-17.770388956200584,-50.920569302992085 `
  --vehicle bicycle `
  --propulsion manual `
  --output debug/.cache/mobility-plan.json
```

Referências úteis:

- [`docs/mobility-toolkit.md`](docs/mobility-toolkit.md): mobilidade, energia,
  categorias e propulsão.
- [`docs/physical-search.md`](docs/physical-search.md): busca física e
  aproximações numéricas.
- [`docs/ml-candidate-design.md`](docs/ml-candidate-design.md): candidata neural
  e limites de calibração.
- [`docs/reference/quickstart.md`](docs/reference/quickstart.md): receitas da
  API e do contrato.
- [`docs/reference/api.md`](docs/reference/api.md): referência pública gerada.

## Testes

```powershell
pytest
```

Os testes cobrem física, busca, Pareto, circulação, OSM PBF, DEM, energia,
presets manuais, intensidade/RPE, rede neural, contratos, múltiplas paradas e
guardrails offline.

## Licença

MIT. Consulte `pyproject.toml` para os metadados do pacote.
