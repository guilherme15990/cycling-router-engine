# Road-centric Mobility Toolkit

O núcleo combina estrada, perfil de mobilidade, propulsão e ambiente. Todos os
perfis de rota usam o mesmo A* físico. A API existente de bicicletas continua
disponível; `PhysicalTraversal` agora adapta bicicleta/ciclista para o mesmo
`MotionEngine` utilizado pelo toolkit.

## Começar com o mínimo

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

Veículo e propulsão são suficientes para começar. Veículo padrão: bicicleta;
a propulsão também pode ser inferida da categoria. Peso, aro, pressão e potência
não são perguntas obrigatórias. Presets numéricos são hipóteses iniciais, não
fichas técnicas de fabricantes. Cada plano informa `parameter_sources` e
`assumptions`; esses valores não devem ser apresentados como medições pessoais.

Categorias com preset: `bicycle`, `cargo_bike`, `kick_scooter`,
`small_electric_vehicle`, `moped`, `motorcycle`, `motorcar`, `goods`, `hgv`, `bus`.
Propulsões: `manual`, `electric`, `combustion`. Bicicleta elétrica assume
assistência ao pedalar para classificação de acesso, e declara essa hipótese.
Outra classificação pode ser configurada pelo integrador em `access_mode`.

O primeiro cálculo por categoria prepara a rede e, se configurado, amostra o
DEM. O objeto `engine` reutiliza essa rede nas consultas seguintes. Nenhuma
etapa de cálculo baixa dados; o PBF e os tiles devem estar disponíveis localmente.

No app/servidor, prepare a rede antes de aceitar consultas interativas:

```python
from router.integration import save_graph, load_graph

prepared = engine.prepare(vehicle="bicycle", propulsion_type="manual")
save_graph(prepared, "cache/bicycle.json")
# Em outra execução, carregar a rede preparada dispensa refazer o DEM:
engine = MobilityEngine(load_graph("cache/bicycle.json"))
```

`prepare()` também pode anexar um provedor de elevação a uma cópia de um grafo
existente. A rede de entrada é preservada. O cliente de demonstração separa
`setup_s`, `preparation_s` e `planning_s` para distinguir ingestão e consulta.

## Personalização opcional

```python
from router.integration import MobilityProfile

mobility = MobilityProfile.create(
    "bicycle", "manual", rider_mass_kg=82, load_mass_kg=4,
)
plan = engine.plan(origin, destination, mobility=mobility)
```

`MobilityProfile.create()` resolve presets e registra as origens dos parâmetros.
O construtor direto recebe parâmetros físicos explícitos. Para adaptar uma
configuração existente: `MobilityProfile.from_bicycle(cyclist, bicycle)`.
Potência opcional pode ser informada em `power_w` ou `power_cv`, nunca nos dois.
`power_in_watts()` aceita W, kW e cv (cavalo-vapor métrico), sem confundir cv com hp.

## Responsabilidades e extensões

| Componente | Contrato |
| --- | --- |
| `MobilityProfile` | Massa total, CdA, rolamento, transmissão, limites e categoria |
| `RoadProfile` / `RoadStep` | Perfil da estrada e passos alinhados à elevação |
| `RoadLoadModel.evaluate()` | Combina estrada, mobilidade, ambiente e velocidade em forças |
| `PropulsionModel.evaluate()` | Força de tração, potência mecânica e potência da fonte quando conhecida |
| `MotionEngine` | Integra movimento e propaga velocidade, tempo e trabalho |
| `MobilityEngine` | Importação por categoria, snapping, busca, seleção e contrato de saída |

`StandardRoadLoad` calcula gravidade, rolamento e arrasto com vento relativo.
Pode receber coeficientes de rolamento por superfície fornecidos pelo integrador.
Na ausência dessa calibração, usa o coeficiente do perfil e declara
`surface_response_uses_mobility_baseline`; uma categoria de pavimento não é
convertida silenciosamente em coeficiente físico.
Os passos preservam a geometria original do segmento e observações OSM em
`RoadStep.tags`; `road.tag("smoothness")`, por exemplo, consulta o dado observado
sem inventar uma escala física. A resposta a rugosidade/curvatura depende do
modelo fornecido pelo integrador.

Implementações próprias usam composição, sem subclasses de veículo:

```python
from router.integration import (
    MobilityEngine, MotionEngine, PropulsionOutput,
)

class TracaoPersonalizada:
    # Contrato determinístico: depende só da velocidade e dos dados da consulta.
    def evaluate(self, speed_mps, road, mobility, environment):
        force = 30.0  # hipótese ilustrativa; substitua pelo seu modelo
        wheel_power = force * max(speed_mps, 0.5)
        return PropulsionOutput(
            wheel_force_n=force,
            input_power_w=wheel_power / mobility.drive_efficiency,
            wheel_power_w=wheel_power,
            work_mode="force",
        )

plan = engine.plan(origin, destination, propulsion=TracaoPersonalizada())
```

Há modelos prontos `ConstantPower` e `ConstantForce`, com limites opcionais.
O integrador espacial é compartilhado com a API antiga. Equilíbrios analíticos
só são reutilizados para a combinação física conhecida; um modelo próprio não
recebe um teto de equilíbrio inventado. O limite de velocidade e o limite de
aceleração continuam aplicados.

Modelos próprios são determinísticos e sem estado oculto. `cache_key` imutável
habilita reutilização de transições; sem essa declaração, o cache de resultados
fica desligado. Modelos que declaram dependências adicionais, como bateria ou
fadiga, são recusados pelo adaptador baseado somente em velocidade. Essas
dependências exigem um `TraversalModel` e uma regra de dominância que transportem
o estado completo; não podem ser ocultadas numa função de propulsão.

## Energia e precisão

Potência em W/cv representa potência mecânica de entrada, e não consumo de
combustível ou capacidade física medida de uma pessoa. Presets de elétricos
representam propulsão agregada estimada, sem inferir separadamente motor/pedalada.

* `mechanical_energy_j`: trabalho entregue às rodas.
* `drive_input_energy_j`: trabalho mecânico antes das perdas de transmissão.
* `source_energy_j`: energia da fonte, somente se o modelo a fornecer.
* `battery_energy_j`: energia elétrica da fonte para um perfil elétrico, quando conhecida.

`ConstantPower(..., source_efficiency=...)` permite uma eficiência explicitamente
fornecida. Sem esse dado, energia da fonte e bateria ficam `None`. Potência
mecânica não gera uma autonomia, litros de combustível ou esforço metabólico
fictícios. O objetivo `electric` existente continua usando trabalho mecânico e
tempo; não é uma otimização de descarga de bateria com estado de carga.

O integrador mantém o tratamento numérico declarado de baixa velocidade. Saídas
preservam regularização, paralisação, qualidade e incerteza de elevação. Os
presets longitudinais não são modelos calibrados de condução, marcha humana,
conforto lateral ou tráfego. ML e fisiologia continuam opcionais na integração
de bicicletas, sem dependência obrigatória no toolkit genérico.

Por padrão a comparação intermediária usa os buckets de velocidade existentes.
`state_approximated` informa essa aproximação. `SearchConfig(exact_speed=True)`
desliga a discretização de comparação; não muda a integração física. Cortes
explícitos de busca são sinalizados e `max_routes` limita apenas a apresentação.

## OSM e cobertura

Categoria de acesso é separada da fonte de energia: carro elétrico e carro a
combustão usam `motorcar`; bicicleta assistida utiliza sua hierarquia específica.
A importação PBF do toolkit preserva também motorway/trunk antes da seleção por
categoria. Ela respeita as exceções de mão única de cada modalidade e as
restrições de conversão aplicáveis, incluindo `except` e tags específicas.

O acesso é resolvido por especificidade dentro de cada segmento. Um trecho
permitido não libera outro trecho proibido dentro de um arco simplificado.
Snapping e busca usam a mesma política de acesso. Condições não resolvidas são
reportadas e podem ser bloqueadas por uma política configurada pelo integrador.

Os padrões de inclusão por tipo de estrada são genéricos e não certificam a
legislação de um país. Tags desconhecidas, condições e ausência de informação
não viram uma declaração de legalidade. Regras locais podem exigir uma política
de acesso adicional.

Restrições dimensionais detalhadas (altura, largura, peso por eixo etc.) não
são inferidas apenas da categoria. O integrador pode fornecer
`access_filter(arc, mobility) -> bool`, determinístico para a consulta; ele é
aplicado tanto ao snapping quanto à expansão e à validação final das rotas.
Essa extensão permite aplicar dados locais ou restrições específicas sem
adicionar perguntas obrigatórias ao cadastro.

Uma rede já filtrada não recupera as vias descartadas. Para outra categoria,
prefira o PBF/OSM original. Grafos com `transport_mode` incompatível são recusados;
grafos antigos sem identificação têm cobertura ciclável assumida e declarada.
Para usar um grafo externo em categorias motorizadas, o integrador deve declarar
`network_mode` ou importar o OSM original.

O contrato genérico usa `roteamento.mobility-route-plan`, versão 1, com
`to_dict()` e `to_json()`. O contrato ciclável existente e seus `dumps/loads`
continuam preservados.

## Cliente de demonstração

```powershell
python scripts/mobility_route.py --pbf data/sources/regiao.osm.pbf --dem-dir data/dem --origin=-17.804347455072403,-50.92253569751691 --destination=-17.770388956200584,-50.920569302992085 --vehicle bicycle --propulsion manual --output debug/.cache/mobility-plan.json
```

Para uma rede já preparada, use `--graph` em lugar de `--pbf`. A demonstração
chama somente a fachada pública; não contém um segundo algoritmo de roteamento.

Referências OSM utilizadas na classificação e direção:
[acesso](https://wiki.openstreetmap.org/wiki/Key:access),
[mão única](https://wiki.openstreetmap.org/wiki/Key:oneway),
[exceções para bicicletas](https://wiki.openstreetmap.org/wiki/Key:oneway:bicycle).


## Publicação progressiva

`plan(..., on_plan=callback, progress_event_every=4)` publica alternativas
validadas enquanto a fronteira permanece aberta. O primeiro preview é imediato;
os demais respeitam o intervalo de candidatas. Previews declaram
`search_complete=False` e `optimality_proven=False`. Sem callback não há custo
de projeção de previews. O plano final continua sendo o retorno de `plan()`.

`should_cancel` é cooperativo e pode lançar `router.integration.SearchCancelled`.
O consumidor pode preservar o último preview ao cancelar. O debug faz isso e
usa `MobilityPlan.from_dict()` para ler o contrato nativo do worker.
