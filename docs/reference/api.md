# Referencia da API

> Gerado automaticamente por `scripts/generate_api_reference.py`. Nao edite a mao.

Versao: **0.18.0** - 336 simbolos publicos (`api.__all__`).

Todas as assinaturas sao extraidas do codigo por introspeccao. A descricao de cada camada e curada; o resumo de cada simbolo vem da propria docstring (use `help(obj)` para o texto completo).

## Sumario

- [Grafo ciclavel: modelo](#grafo-ciclavel-modelo)
- [Grafo ciclavel: superficie](#grafo-ciclavel-superficie)
- [Grafo ciclavel: restricoes de acesso](#grafo-ciclavel-restricoes-de-acesso)
- [Grafo ciclavel: simplificacao](#grafo-ciclavel-simplificacao)
- [Snapping (grafo de consulta)](#snapping-grafo-de-consulta)
- [Fisica: travessia](#fisica-travessia)
- [Fisica: features de esforco](#fisica-features-de-esforco)
- [Fisica: energia e bateria](#fisica-energia-e-bateria)
- [Fisiologia: escala, zonas e metabolico](#fisiologia-escala-zonas-e-metabolico)
- [Fisiologia: contrato de previsao](#fisiologia-contrato-de-previsao)
- [ML: inferencia](#ml-inferencia)
- [Busca: objetivos e perfis](#busca-objetivos-e-perfis)
- [Busca: estado, fronteira e A*](#busca-estado-fronteira-e-a)
- [Busca: estimador aproximado (incremento 8)](#busca-estimador-aproximado-incremento-8)
- [Busca: contrato de circulacao (guardrails)](#busca-contrato-de-circulacao-guardrails)
- [Busca: reconstrucao e avaliacao de rotas](#busca-reconstrucao-e-avaliacao-de-rotas)
- [Selecao: candidatas, ranking e diversidade](#selecao-candidatas-ranking-e-diversidade)
- [Selecao: N rotas, explicacao e pipeline](#selecao-n-rotas-explicacao-e-pipeline)
- [Energia da rota (pos-selecao)](#energia-da-rota-pos-selecao)
- [Avaliacao: rota completa e classificacao (Camada 05)](#avaliacao-rota-completa-e-classificacao-camada-05)
- [Seguranca da rota e avaliacao final (Camada 05, ADR-029)](#seguranca-da-rota-e-avaliacao-final-camada-05-adr-029)
- [Consulta offline completa (planejamento)](#consulta-offline-completa-planejamento)
- [Integracao com o aplicativo (ADR-030, Fase 3)](#integracao-com-o-aplicativo-adr-030-fase-3)
- [Contrato publico de rota (v1)](#contrato-publico-de-rota-v1)
- [Operacao offline (guard de rede)](#operacao-offline-guard-de-rede)
- [Fonte geografica local: leitor OSM.PBF](#fonte-geografica-local-leitor-osm-pbf)
- [Elevacao local: Terrarium (offline)](#elevacao-local-terrarium-offline)
- [Envelope de consulta: zona de busca + altimetria (offline)](#envelope-de-consulta-zona-de-busca-altimetria-offline)
- [Camada 04 (intelligence): alvos e observacoes](#camada-04-intelligence-alvos-e-observacoes)
- [Camada 04 (intelligence): features, modelos e artefato local](#camada-04-intelligence-features-modelos-e-artefato-local)
- [Camada 04 (intelligence): treino, avaliacao e classificacao](#camada-04-intelligence-treino-avaliacao-e-classificacao)
- [Camada 04 (intelligence): estimativa de intensidade (ADR-028)](#camada-04-intelligence-estimativa-de-intensidade-adr-028)

## Grafo ciclavel: modelo

Estruturas centrais do grafo: nos, segmentos, arcos direcionadas e o conteiner ``RoutingGraph``.

### `BicycleAccess`

```python
class BicycleAccess(str, Enum)
```

Permissao de circulacao observada no OSM.

Valores: `YES`, `DISMOUNT`.

### `RoutingGraph`

```python
class RoutingGraph
```

Grafo direcionado com adjacencia por origem.

### `Arc`

```python
class Arc(_Passage)(id: str, source: Hashable, target: Hashable, segments: tuple[router.core.data.Segment, ...])
```

Possibilidade de deslocamento **direcionado** sobre um :class:`Link`.

### `Link`

```python
class Link(_Passage)(id: str, source: Hashable, target: Hashable, segments: tuple[router.core.data.Segment, ...], arc_ids: tuple[str, ...] = ())
```

Ligacao fisica e **nao-direcionada** entre dois :class:`Node`.

### `Segment`

```python
class Segment(id: str, distance_m: float, surface: router.core.data.Surface = <Surface.UNKNOWN: 'unknown'>, surface_status: router.core.data.SurfaceStatus = <SurfaceStatus.UNKNOWN: 'unknown'>, elevation_gain_m: float = 0.0, elevation_loss_m: float = 0.0, min_elevation_m: float | None = None, max_elevation_m: float | None = None, elevation_profile: tuple[router.core.data.ElevationSample, ...] = (), geometry: tuple[tuple[float, float], ...] = (), highway: str | None = None, name: str | None = None, osm_way_id: int | None = None, bicycle_access: router.core.data.BicycleAccess = <BicycleAccess.YES: 'yes'>, max_speed_kmh: float | None = None, restrictions: tuple[router.core.data.AccessRule, ...] = (), tags: Mapping[str, str] = <factory>)
```

Trecho homogeneo de uma via, entre dois pontos de forma do OSM.

### `ElevationQuality`

```python
class ElevationQuality(str, Enum)
```

Qualidade explicita de uma amostra de elevacao (ver ADR-024, decisao 3).

Valores: `MEASURED`, `DEM_SAMPLED`, `SOURCE_REPORTED`, `INTERPOLATED`, `ESTIMATED`, `MISSING`.

### `ElevationSample`

```python
class ElevationSample(distance_m: float, elevation_m: float | None, source_id: str | None = None, resolution_m: float | None = None, quality: router.core.data.ElevationQuality | None = None)
```

Altitude a ``distance_m`` metros do inicio do segmento.

### `Node`

```python
class Node(id: Hashable, lat: float, lon: float, elevation_m: float | None = None, osm_id: int | None = None, tags: Mapping[str, str] = <factory>)
```

Posicao geografica (intersecao ou ponto topologico).

### `OBSERVED_ELEVATION_QUALITIES`

```python
OBSERVED_ELEVATION_QUALITIES
```

Estados de elevacao sustentados por fonte (medida, DEM ou declarada).

## Grafo ciclavel: superficie

Classificacao observavel de superficie e a politica de prioridade (ordinal) do roteador.

### `OSM_SURFACE_MAP`

```python
OSM_SURFACE_MAP
```

Mapeia valores de ``surface=*`` do OSM para ``Surface``.

### `OSM_TRACKTYPE_MAP`

```python
OSM_TRACKTYPE_MAP
```

Mapeia ``tracktype=grade1..grade5`` para ``Surface``.

### `SURFACE_PRIORITY`

```python
SURFACE_PRIORITY
```

Prioridade ordinal por superficie (1 melhor ... 6 desconhecida).

### `Surface`

```python
class Surface(str, Enum)
```

Valores: `ASPHALT`, `CONCRETE`, `PAVED`, `COMPACTED_GRAVEL`, `GRAVEL`, `DIRT`, `SAND`, `UNKNOWN`.

### `SurfaceStatus`

```python
class SurfaceStatus(str, Enum)
```

Proveniencia da classificacao de superficie.

Valores: `MAPPED`, `UNMAPPED`, `INFERRED`, `UNKNOWN`.

### `surface_from_tags`

```python
surface_from_tags(tags: Mapping[str, str]) -> router.core.data.Surface
```

Atalho somente da superficie observada (ver ``surface_observation``).

### `surface_observation`

```python
surface_observation(tags: Mapping[str, str]) -> tuple[router.core.data.Surface, router.core.data.SurfaceStatus]
```

Deriva superficie e proveniencia apenas do que esta mapeado no OSM.

### `surface_priority`

```python
surface_priority(surface: router.core.data.Surface) -> int
```

Prioridade ordinal de uma superficie (1 = melhor).

## Grafo ciclavel: restricoes de acesso

Restricoes de acesso preservadas das tags OSM, sem avaliacao temporal.

### `AccessRule`

```python
class AccessRule(scope: str, value: str, condition: str | None, raw: str)
```

Condicao de acesso preservada, ainda nao avaliada.

### `RestrictionSource`

```python
class RestrictionSource(relation_id: int | None = None, tags: Mapping[str, str] = <factory>, version: str | None = None)
```

Proveniencia de uma restricao derivada de relacao OSM.

### `TurnRestriction`

```python
class TurnRestriction(id: str, from_way_id: int, to_way_id: int, restriction_type: str, via_node_id: int | None = None, via_way_ids: tuple[int, ...] = (), condition: str | None = None, source: router.core.data.RestrictionSource | None = None)
```

Restricao de conversao entre dois arcos, preservada do OSM.

### `parse_conditional_restrictions`

```python
parse_conditional_restrictions(tags: Mapping[str, str]) -> tuple[router.core.data.AccessRule, ...]
```

Extrai todas as tags ``*:conditional`` sem interpretar a condicao.

### `turn_restriction_from_relation`

```python
turn_restriction_from_relation(relation: Mapping[str, object], version: str | None = None) -> router.core.data.TurnRestriction | None
```

Normaliza uma relacao ``type=restriction`` do OSM em ``TurnRestriction``.

## Grafo ciclavel: simplificacao

Contracao de nos de grau 2 sem perder atributos fisicos nem de acesso.

### `SimplifyOptions`

```python
class SimplifyOptions(keep_nodes: frozenset[typing.Hashable] = frozenset())
```

``keep_nodes`` protege pontos que precisam existir (ex.: POIs, snapping).

### `SimplifyStats`

```python
class SimplifyStats(nodes_before: int, nodes_after: int, arcs_before: int, arcs_after: int, contracted_nodes: int, chains: int, multi_segment_arcs: int)
```

SimplifyStats(nodes_before: 'int', nodes_after: 'int', arcs_before: 'int', arcs_after: 'int', contracted_nodes: 'int', chains: 'int', multi_segment_arcs: 'int')

### `simplify_graph`

```python
simplify_graph(graph: router.core.data.RoutingGraph, options: router.core.data.SimplifyOptions | None = None) -> tuple[router.core.data.RoutingGraph, router.core.data.SimplifyStats]
```

Retorna um novo grafo com os nos de passagem contraidos.

## Snapping (grafo de consulta)

Conexao de origem/destino a rede: opcoes, candidatos, resultado e o grafo temporario de consulta.

### `QueryGraph`

```python
class QueryGraph(graph: router.core.data.RoutingGraph, origin_id: Hashable, destination_id: Hashable, origin: router.core.data.snapping.SnapCandidate, destination: router.core.data.snapping.SnapCandidate, virtual_nodes: frozenset[typing.Hashable] = <factory>)
```

Grafo temporario pronto para a busca de rotas.

### `SnapCandidate`

```python
class SnapCandidate(label: str, kind: Literal['node', 'arc'], point: tuple[float, float], offset_m: float, node_id: Hashable | None = None, arc_id: str | None = None, segment_id: str | None = None, segment_index: int | None = None, along_m: float | None = None, fraction: float | None = None)
```

Ponto escolhido para conectar a consulta a rede.

### `SnapError`

```python
class SnapError(ValueError)
```

Nao foi possivel conectar o ponto a rede ciclavel.

### `SnapOptions`

```python
class SnapOptions(max_distance_m: float = 100.0, node_preference_m: float = 10.0, node_offset_tolerance_m: float = 2.0, min_split_distance_m: float = 1.0, allow_dismount: bool = True, cell_size_m: float | None = None)
```

Parametros do snapping.

### `SnapResult`

```python
class SnapResult(candidate: router.core.data.snapping.SnapCandidate, node_id: Hashable, virtual: bool, splits: tuple[router.core.data.snapping._ArcSplit, ...] = (), elevation_m: float | None = None)
```

Plano de conexao de um ponto, aplicavel a uma copia do grafo.

### `SpatialSnapper`

```python
class SpatialSnapper(graph: router.core.data.RoutingGraph, options: router.core.data.snapping.SnapOptions | None = None)
```

Indexo arcos e nos e projeta coordenadas na rede.

### `build_query_graph`

```python
build_query_graph(graph: router.core.data.RoutingGraph, origin: tuple[float, float], destination: tuple[float, float], options: router.core.data.snapping.SnapOptions | None = None) -> router.core.data.snapping.QueryGraph
```

Conecta origem e destino e devolve o grafo temporario de consulta.

## Fisica: travessia

Modelos do ciclista e da bicicleta, perfis de arco e a simulacao de travessia (tempo, energia e velocidade).

### `BicycleModel`

```python
class BicycleModel(mass_kg: float = 12.0, rolling_resistance: float = 0.005, drag_coefficient: float = 0.9, drivetrain_efficiency: float = 0.97, wheel_radius_m: float = 0.35)
```

Caracteristicas fisicas da bicicleta.

### `CyclistModel`

```python
class CyclistModel(mass_kg: float, max_power_w: float, sustainable_power_w: float, frontal_area_m2: float)
```

Caracteristicas fisicas do ciclista.

### `ArcProfile`

```python
class ArcProfile(arc_id: str, distance_m: float, segments: tuple[router.core.physics.arc_profile.SegmentProfile, ...], ascent_m: float, descent_m: float, unknown_elevation_m: float)
```

Perfil fisico completo de um arco direcionado.

### `Environment`

```python
class Environment(air_density_kg_m3: float = 1.225, headwind_mps: float = 0.0, gravity_m_s2: float = 9.80665, safe_max_speed_mps: float | None = 22.0)
```

Ambiente fisico da travessia.

### `PhysicalTraversal`

```python
class PhysicalTraversal(step_m: float | None = None, acceleration_limit_mps2: float = 2.0, dismount_speed_mps: float = 1.4, require_elevation: bool = False, *, mode: router.core.physics.traversal.SimulationMode | str = <SimulationMode.SEARCH: 'search'>)
```

Simula a travessia fisica de arcos do grafo.

### `PhysicsRouteMetrics`

```python
class PhysicsRouteMetrics(time_s: float = 0.0, distance_m: float = 0.0, ascent_m: float = 0.0, descent_m: float = 0.0, energy_j: float = 0.0, mechanical_energy_j: float = 0.0, braking_energy_j: float = 0.0, battery_energy_j: float | None = None, aero_loss_j: float = 0.0, rolling_loss_j: float = 0.0)
```

Metricas acumuladas de uma rota.

### `SimulationMode`

```python
class SimulationMode(str, Enum)
```

Modo de execucao da integracao espacial (ADR-025, decisao 6, item 1).

Valores: `SEARCH`, `VALIDATION`.

### `SimulationState`

```python
class SimulationState(elapsed_s: float = 0.0, speed_mps: float = 0.0, wheel_work_j: float = 0.0, rider_work_j: float = 0.0, braking_loss_j: float = 0.0)
```

Estado dinamico canonico de uma simulacao (ADR-025, decisao 1).

### `SimulationStatus`

```python
class SimulationStatus(str, Enum)
```

Confiabilidade de um resultado fisico (ADR-025, decisao 2).

Valores: `VALID`, `DEGRADED`, `UNAVAILABLE`.

### `ElevationUncertainty`

```python
class ElevationUncertainty(total_distance_m: float, missing_distance_m: float = 0.0, interpolated_distance_m: float = 0.0, estimated_distance_m: float = 0.0, observed_distance_m: float = 0.0)
```

Incerteza altimetrica estruturada de um trecho (ADR-025, decisao 2).

### `EnergyBalance`

```python
class EnergyBalance(delta_kinetic_j: float, delta_potential_j: float, wheel_work_j: float, aero_loss_j: float, rolling_loss_j: float, braking_loss_j: float)
```

Balanco energetico longitudinal de um trecho (ADR-025, decisao 2).

### `TraversalPath`

```python
class TraversalPath(results: tuple[router.core.physics.models.TraversalResult, ...], metrics: router.core.physics.models.PhysicsRouteMetrics, initial_speed_mps: float, final_speed_mps: float, elevation_uncertainty: 'ElevationUncertainty | None' = None, result_status: router.core.physics.models.SimulationStatus = <SimulationStatus.VALID: 'valid'>, used_min_speed_regularization: bool = False)
```

Resultado de percorrer arcos em continuidade.

### `TraversalResult`

```python
class TraversalResult(time_s: float, distance_m: float, ascent_m: float, descent_m: float, initial_speed_mps: float, final_speed_mps: float, mechanical_energy_j: float, rider_energy_j: float, battery_energy_j: float | None = None, braking_energy_j: float = 0.0, stalled: bool = False, arc_id: str | None = None, state: 'SimulationState | None' = None, elevation_uncertainty: 'ElevationUncertainty | None' = None, result_status: router.core.physics.models.SimulationStatus = <SimulationStatus.VALID: 'valid'>, aero_loss_j: float = 0.0, rolling_loss_j: float = 0.0, used_min_speed_regularization: bool = False)
```

Resultado fisico de percorrer um arco.

### `traverse_arc`

```python
traverse_arc(arc: router.core.data.Arc, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, initial_state: router.core.physics.models.SimulationState | None = None, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None, *, step_m: float | None = None, mode: router.core.physics.traversal.SimulationMode | str = <SimulationMode.SEARCH: 'search'>) -> router.core.physics.models.TraversalResult
```

Atalho para uma travessia isolada, sem reutilizar cache.

### `classify_status`

```python
classify_status(uncertainty: router.core.physics.models.ElevationUncertainty | None, *, require_elevation: bool = False) -> router.core.physics.models.SimulationStatus
```

Politica de status a partir da incerteza altimetrica (ADR-025, decisao 2).

### `worst_status`

```python
worst_status(*statuses: router.core.physics.models.SimulationStatus) -> router.core.physics.models.SimulationStatus
```

Status mais restritivo entre varios trechos (pior caso).

## Fisica: features de esforco

Contrato imutavel e versionado de caracteristicas de esforco derivadas da fisica (a partir do resultado de travessia), sem ML nem roteamento.

### `EFFORT_FEATURES_VERSION`

```python
EFFORT_FEATURES_VERSION
```

### `EffortFeatures`

```python
class EffortFeatures(duration_s: float, distance_m: float, average_speed_mps: float, mechanical_work_j: float, rider_work_j: float, mean_power_w: float, peak_power_w: float | None, power_variability: float | None, climbing_m: float, descending_m: float, elevation_coverage: float | None, observed_elevation_coverage: float | None, braking_energy_j: float, aero_loss_j: float, rolling_loss_j: float, battery_energy_j: float | None, metabolic_energy_j: float | None, result_status: router.core.physics.models.SimulationStatus, used_min_speed_regularization: bool, elevation_uncertainty: router.core.physics.models.ElevationUncertainty | None)
```

Caracteristicas de esforco de uma rota, derivadas da fisica.

### `extract_effort_features`

```python
extract_effort_features(path: router.core.physics.models.TraversalPath) -> router.core.physics.effort.EffortFeatures
```

Extrai ``EffortFeatures`` de um resultado fisico de rota.

## Fisica: energia e bateria

Modelo energetico (energia mecanica -> eletrica em Wh) e viabilidade de bateria. O perfil eletrico usa energia **mecanica** na fronteira Pareto; a conversao para Wh e um estagio posterior a selecao.

### `BatteryConfig`

```python
class BatteryConfig(capacity_wh: float, initial_charge_wh: float | None = None, reserve_ratio: float = 0.1)
```

Bateria opcional: capacidade, carga inicial e reserva desejada.

### `BatteryFeasibility`

```python
class BatteryFeasibility(capacity_wh: float, available_energy_wh: float, estimated_consumption_wh: float, reserve_energy_wh: float)
```

Viabilidade de uma rota com a bateria configurada.

### `CALIBRATED_LEVEL`

```python
CALIBRATED_LEVEL
```

Nivel de estimativa calibrado com dados reais.

### `EnergyEstimate`

```python
class EnergyEstimate(mechanical_energy_j: float, electrical_energy_j: float, efficiency: float, level: str = 'generic')
```

Estimativa de energia de um trecho, rota ou veiculo.

### `EnergyModel`

```python
class EnergyModel(config: router.core.physics.energy_model.GenericEnergyConfig = <factory>, level: str = 'generic')
```

Converte energia mecanica em energia eletrica estimada.

### `GENERIC_LEVEL`

```python
GENERIC_LEVEL
```

Nivel de estimativa com valores de referencia internos.

### `GenericEnergyConfig`

```python
class GenericEnergyConfig(mass_kg: float = 90.0, rolling_resistance: float = 0.008, drag_coefficient: float = 0.9, frontal_area_m2: float = 0.5, drivetrain_efficiency: float = 0.75)
```

Parametros de referencia do modelo energetico generico.

### `JOULES_PER_WH`

```python
JOULES_PER_WH
```

Fator de conversao de energia (1 Wh = 3600 J).

### `PERSONALIZED_LEVEL`

```python
PERSONALIZED_LEVEL
```

Nivel de estimativa com parametros do veiculo informados.

### `assess_battery`

```python
assess_battery(config: router.core.physics.energy_model.BatteryConfig, consumption_wh: float) -> router.core.physics.energy_model.BatteryFeasibility
```

Avalia se a bateria cobre o consumo estimado respeitando a reserva.

## Fisiologia: escala, zonas e metabolico

Contratos fisiológicos explícitos: escala de Borg (padrão 6–20; CR10 suportada), zonas cardíacas por definição identificada (sem percentuais implícitos) e energia metabólica só com parâmetros calibrados.

### `PHYSIOLOGY_VERSION`

```python
PHYSIOLOGY_VERSION
```

Versao do contrato de previsao fisiologica.

### `BorgScale`

```python
class BorgScale(str, Enum)
```

Escala de Borg/RPE, com limites explicitos.

Valores: `RPE_6_20`, `CR10`.

### `DEFAULT_BORG_SCALE`

```python
DEFAULT_BORG_SCALE
```

Escala de Borg padrao do projeto: **6-20** (original).

### `DISTRIBUTION_TOLERANCE`

```python
DISTRIBUTION_TOLERANCE
```

Tolerancia numerica para "a distribuicao soma 1".

### `HeartRateZoneDefinition`

```python
class HeartRateZoneDefinition(name: str, lower_bpm: float, upper_bpm: float)
```

Uma zona cardiaca: nome e intervalo explicito de bpm.

### `HeartRateZoneModel`

```python
class HeartRateZoneModel(definition: str, version: str, zones: tuple[router.core.HeartRateZoneDefinition, ...])
```

Modelo de zonas cardiacas com definicao e versao identificadas.

### `MetabolicParameters`

```python
class MetabolicParameters(gross_efficiency: float, version: str, provenance: str)
```

Parametros metabolicos explicitos, versionados e com proveniencia.

### `metabolic_energy_j`

```python
metabolic_energy_j(rider_work_j: float, params: router.core.MetabolicParameters | None) -> float | None
```

Energia metabolica estimada, ou ``None`` sem parametros calibrados.

## Fisiologia: contrato de previsao

Contrato de previsão fisiológica — sempre identificada como estimativa (`source="prediction"`, `is_measurement=False`), nunca como medição. `PredictionStatus` distingue ausência de preditor, modelo não calibrado, dados insuficientes e contrato incompatível.

### `PhysiologicalPrediction`

```python
class PhysiologicalPrediction(borg_scale: router.core.BorgScale, predicted_rpe: float | None, heart_rate_zone_distribution: dict[str, float] | None, model_version: str, status: router.core.PredictionStatus, confidence: float | None = None, confidence_method: str | None = None, heart_rate_zone_definition: str | None = None, prediction_interval_s: float | None = None, metabolic_energy_j: float | None = None, reason: str | None = None, missing_inputs: tuple[str, ...] = ())
```

Estimativa fisiologica identificada — nunca uma medicao.

### `PredictionContext`

```python
class PredictionContext(temperature_c: float | None = None, duration_s: float | None = None, notes: str | None = None)
```

Contexto opcional da predicao.

### `PredictionStatus`

```python
class PredictionStatus(str, Enum)
```

Estado da previsao **fisiologica** (RPE e zonas cardiacas).

Valores: `AVAILABLE`, `UNAVAILABLE`, `MODEL_NOT_CALIBRATED`, `INSUFFICIENT_DATA`, `CONTRACT_INCOMPATIBLE`.

### `RiderProfile`

```python
class RiderProfile(max_heart_rate_bpm: float | None = None, resting_heart_rate_bpm: float | None = None, threshold_power_w: float | None = None, weight_kg: float | None = None)
```

Perfil fisiologico do ciclista, **parcial e opcional**.

## ML: inferencia

Fronteira de inferência: consome `EffortFeatures` e devolve `PhysiologicalPrediction`. O preditor padrão declara a ausência de modelo calibrado em vez de inventar valores.

### `EffortPredictor`

```python
class EffortPredictor(Protocol)
```

Contrato minimo de um preditor fisiologico.

### `MetabolicEnergyPredictor`

```python
class MetabolicEnergyPredictor(metabolic: router.core.MetabolicParameters, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>)
```

Preditor que preenche **apenas** a energia metabolica.

### `UnavailablePredictor`

```python
class UnavailablePredictor(borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, model_version: str = 'unavailable')
```

Preditor padrao: declara explicitamente a ausencia de modelo calibrado.

### `predict_effort`

```python
predict_effort(features: router.core.physics.effort.EffortFeatures, rider: router.core.RiderProfile | None = None, context: router.core.PredictionContext | None = None, *, predictor: router.core.prediction.ml.EffortPredictor | None = None) -> router.core.PhysiologicalPrediction
```

Ponto de entrada guardado para a previsao de esforco.

## Busca: objetivos e perfis

Objetivos, perfis declarativos (fastest/shortest/flattest/balanced/electric), tolerancias e configuracao de diversidade.

### `BalancedWeights`

```python
class BalancedWeights(time_s: float = 0.3333333333333333, distance_m: float = 0.3333333333333333, ascent_m: float = 0.3333333333333333)
```

Pesos do score normalizado. Devem somar 1.

### `DiversityConfig`

```python
class DiversityConfig(metric: router.core.DiversityMetric = <DiversityMetric.SHARED_DISTANCE_RATIO: 'shared_distance_ratio'>, max_similarity: float = 0.7, fallback: router.core.DiversityFallback = <DiversityFallback.RELAX_THRESHOLD: 'relax_threshold'>, relaxation: router.core.DiversityRelaxation = <factory>)
```

Configuracao de diversidade do perfil.

### `DiversityFallback`

```python
class DiversityFallback(str, Enum)
```

Politica quando a diversidade impede preencher as N rotas.

Valores: `RETURN_FEWER`, `RELAX_THRESHOLD`, `NONE`.

### `DiversityMetric`

```python
class DiversityMetric(str, Enum)
```

Metricas de semelhanca entre caminhos.

Valores: `ARC_JACCARD`, `SHARED_DISTANCE_RATIO`.

### `DiversityRelaxation`

```python
class DiversityRelaxation(max_threshold: float = 1.0, step: float = 0.1)
```

Limite e passo do relaxamento progressivo da diversidade.

### `MetricSource`

```python
class MetricSource(Protocol)
```

Qualquer objeto com as metricas lidas pelos objetivos.

### `Objective`

```python
class Objective(str, Enum)
```

Metricas que podem participar da dominancia.

Valores: `TIME`, `DISTANCE`, `ASCENT`, `ENERGY`.

### `ObjectiveEvaluator`

```python
class ObjectiveEvaluator(Protocol)
```

Contrato minimo de extracao de valores objetivo.

### `ObjectiveSet`

```python
class ObjectiveSet(name: str, objectives: tuple[str, ...])
```

Conjunto nomeado de objetivos a minimizar.

### `ObjectiveValues`

```python
class ObjectiveValues(time_s: float, distance_m: float, ascent_m: float, energy_j: float)
```

Metricas finais de uma rota, na forma usada pelos objetivos.

### `PROFILE_CONFIG`

```python
PROFILE_CONFIG
```

``RouteProfile`` (selecao) por id de perfil.

### `PROFILE_IDS`

```python
PROFILE_IDS
```

Ids dos perfis de selecao (``profiles.py``).

### `PROFILE_NAMES`

```python
PROFILE_NAMES
```

Ids dos perfis de busca, em ordem estavel (``objectives.py``).

### `PROFILE_OBJECTIVES`

```python
PROFILE_OBJECTIVES
```

``ObjectiveSet`` (objetivos Pareto) por id de perfil.

### `PROFILES`

```python
PROFILES
```

``SearchProfile`` por id de perfil.

### `RankingStrategy`

```python
class RankingStrategy(str, Enum)
```

Como ordenar rotas nao dominadas.

Valores: `LEXICOGRAPHIC`, `WEIGHTED_NORMALIZED`.

### `RankingTolerance`

```python
class RankingTolerance(time_s: float = 0.0, distance_m: float = 0.0, ascent_m: float = 0.0, energy_j: float = 0.0)
```

Tolerancia por objetivo no ranking lexicografico.

### `RouteObjective`

```python
class RouteObjective(str, Enum)
```

Metricas que podem participar da dominancia.

Valores: `TIME`, `DISTANCE`, `ASCENT`, `ENERGY`.

### `RouteObjectiveEvaluator`

```python
class RouteObjectiveEvaluator
```

Implementacao central: le as metricas ja acumuladas na rota.

### `RouteProfile`

```python
class RouteProfile(id: str, objectives: tuple[router.core.Objective, ...], ranking_strategy: router.core.RankingStrategy, title: str = '', tolerance: router.core.RankingTolerance | None = None, weights: router.core.BalancedWeights | None = None, references: router.core.ScoreReference | None = None, diversity: router.core.DiversityConfig = <factory>)
```

Preferencia de rota: objetivos Pareto, ranking e diversidade.

### `ScoreReference`

```python
class ScoreReference(time_s: float = 1800.0, distance_m: float = 10000.0, ascent_m: float = 100.0, energy_j: float = 0.0)
```

Referencias fixas para o score normalizado do perfil equilibrado.

### `SearchProfile`

```python
class SearchProfile(id: str, objectives: tuple[router.core.Objective, ...])
```

Perfil de busca: identificador + objetivos ordenados.

### `dominates`

```python
dominates(values_a: collections.abc.Sequence[float], values_b: collections.abc.Sequence[float]) -> bool
```

Dominancia de Pareto por minimizacao.

### `epsilon_dominates`

```python
epsilon_dominates(values_a: collections.abc.Sequence[float], values_b: collections.abc.Sequence[float], epsilon: float) -> bool
```

Dominancia com tolerancia ``epsilon`` por objetivo.

### `objectives_for`

```python
objectives_for(profile: str) -> router.core.ObjectiveSet
```

Devolve o ``ObjectiveSet`` de um perfil, normalizando o nome.

### `profile_for`

```python
profile_for(profile: str) -> router.core.SearchProfile
```

Devolve o ``SearchProfile`` de um perfil, normalizando o nome.

### `route_profile_for`

```python
route_profile_for(profile: str) -> router.core.RouteProfile
```

Devolve o ``RouteProfile`` de um id, normalizando o nome.

## Busca: estado, fronteira e A*

Nucleo do A* multiobjetivo: estado, dominancia, fronteira Pareto, fila lexicografica e heuristica admissivel.

### `FrontierManager`

```python
class FrontierManager(dominance: router.core.Dominance, *, max_labels_per_key: int | None = None, on_discard: collections.abc.Callable[[Any], None] | None = None)
```

Fronteiras nao dominadas por chave de estado fisico.

### `FrontierMap`

```python
class FrontierMap(objectives: router.core.ObjectiveSet, *, epsilon: float = 0.0, max_labels_per_node: int | None = None, key: collections.abc.Callable[[Any], collections.abc.Sequence[float]] | None = None, on_discard: collections.abc.Callable[[Any], None] | None = None)
```

Fronteiras Pareto separadas por chave.

### `Heuristic`

```python
class Heuristic(graph: router.core.data.RoutingGraph, *, max_speed_mps: float | None = 22.0)
```

Heuristica geometrica admissivel, independente de perfil.

### `HeuristicBounds`

```python
class HeuristicBounds(time_s: float, distance_m: float, ascent_m: float)
```

Limites inferiores por objetivo para alcancar o destino.

### `LexicographicQueue`

```python
class LexicographicQueue
```

Fila de prioridade ordenada por um vetor de prioridade.

### `MultiObjectiveAStar`

```python
class MultiObjectiveAStar(graph: router.core.data.RoutingGraph, traversal: router.core.TraversalModel, profile: router.core.SearchProfile | str, *, heuristic: router.core.Heuristic | None = None, dominance: router.core.Dominance | None = None, config: router.core.SearchConfig | None = None, initial_speed_mps: float = 0.0, condition_evaluator: collections.abc.Callable[[str], bool | None] | None = None, circulation_policy: router.core.CirculationPolicy = CirculationPolicy(unresolved_verdict=<Verdict.FORBID: 'forbid'>))
```

Busca Pareto por perfil sobre um grafo finito.

### `ParetoFrontier`

```python
class ParetoFrontier(objectives: router.core.ObjectiveSet, *, key: collections.abc.Callable[[Any], collections.abc.Sequence[float]] | None = None, epsilon: float = 0.0, max_size: int | None = None, on_discard: collections.abc.Callable[[Any], None] | None = None)
```

Colecao de itens nao dominados segundo um ``ObjectiveSet``.

### `PhysicalState`

```python
class PhysicalState(speed_mps: float, kinetic_energy_j: float)
```

Estado fisico necessario para a proxima travessia.

### `PhysicalTraversalModel`

```python
class PhysicalTraversalModel(traversal: router.core.physics.traversal.PhysicalTraversal, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None)
```

Liga ``PhysicalTraversal`` aos modelos de ciclista, bicicleta e ambiente.

### `SearchConfig`

```python
class SearchConfig(max_labels_per_node: int | None = None, speed_step_mps: float = 0.5, epsilon_dominance: float = 0.0, max_expansions: int | None = None)
```

Parametros de execucao da busca.

### `SearchResult`

```python
class SearchResult(origin: collections.abc.Hashable, destination: collections.abc.Hashable, profile: router.core.SearchProfile, labels: tuple[router.core.SearchState, ...], expanded: int = 0, generated: int = 0, truncated: bool = False, frontier_truncated: bool = False, dropped_labels: int = 0)
```

Fronteira Pareto de destino devolvida por ``MultiObjectiveAStar``.

### `SearchState`

```python
class SearchState(node_id: collections.abc.Hashable, physical_state: router.core.PhysicalState, time_s: float = 0.0, distance_m: float = 0.0, ascent_m: float = 0.0, descent_m: float = 0.0, energy_j: float = 0.0, parent: 'SearchState | None' = None, incoming_arc_id: str | None = None, label_id: int = 0, active: bool = True, result_status: router.core.physics.models.SimulationStatus = <SimulationStatus.VALID: 'valid'>, elevation_uncertainty: 'ElevationUncertainty | None' = None, used_min_speed_regularization: bool = False, stalled: bool = False, dismount_used: bool = False)
```

Label de busca: metricas acumuladas + estado fisico + reconstrucao.

### `SpeedBucketDominance`

```python
class SpeedBucketDominance(objectives: router.core.ObjectiveSet, *, speed_step_mps: float = 0.5, arrival_class_nodes: collections.abc.Iterable[collections.abc.Hashable] | None = None)
```

Dominancia por objetivos, somente dentro do mesmo no e bucket.

### `TraversalModel`

```python
class TraversalModel(Protocol)
```

Contrato minimo que a busca espera do modelo fisico.

### `UNKNOWN_SPEED`

```python
UNKNOWN_SPEED
```

Sentinela de velocidade inicial desconhecida na busca.

### `advance_state`

```python
advance_state(current: router.core.SearchState, arc: router.core.data.Arc, result: router.core.physics.models.TraversalResult, *, mass_kg: float, label_id: int) -> router.core.SearchState
```

Acumula a travessia de ``arc`` em uma nova label.

### `initial_state`

```python
initial_state(node_id: collections.abc.Hashable, mass_kg: float, initial_speed_mps: float = 0.0, *, label_id: int = 0) -> router.core.SearchState
```

Label inicial na origem, sem custo acumulado.

### `model_max_speed`

```python
model_max_speed(traversal: router.core.TraversalModel) -> float | None | router.core._UnknownSpeedSentinel
```

Teto de velocidade **declarado** pelo modelo, se houver.

### `route_arcs`

```python
route_arcs(state: router.core.SearchState) -> list[str | None]
```

Reconstroi a sequencia de arcos da origem ate ``state``.

## Busca: estimador aproximado (incremento 8)

Estimador fisico por equilibrio de velocidade para a busca, com relatorio de erro/custo contra o simulador completo e comparacao de ordenacao de rotas. **Nao** substitui o simulador nem presume admissibilidade.

### `ArcEstimateError`

```python
class ArcEstimateError(arc_id: str, reference_time_s: float, estimate_time_s: float, time_rel_error: float | None, reference_energy_j: float, estimate_energy_j: float, energy_rel_error: float | None, reference_final_speed_mps: float, estimate_final_speed_mps: float, final_speed_error_mps: float, reference_steps: int, estimate_steps: int)
```

Erro do estimador contra o simulador para um arco.

### `ComparisonReport`

```python
class ComparisonReport(arcs: tuple[router.core.ArcEstimateError, ...], max_time_rel_error: float | None, mean_time_rel_error: float | None, max_energy_rel_error: float | None, mean_energy_rel_error: float | None, max_final_speed_abs_error: float, reference_steps: int, estimate_steps: int)
```

Relatorio agregado estimador x simulador.

### `RankingComparison`

```python
class RankingComparison(reference_count: int, estimate_count: int, same_key_set: bool, identical_order: bool, inverted_pairs: int, total_shared_pairs: int, keys_value_changed: int, max_objective_delta: float, tolerance: float)
```

Impacto de uma ordenacao alternativa sobre **as mesmas rotas**.

### `SearchRankingComparison`

```python
class SearchRankingComparison(profile_id: str, ranking: router.core.RankingComparison, same_route_set: bool, same_top_route: bool, reference_expanded: int, estimate_expanded: int, reference_arc_sequences: tuple[tuple[str | None, ...], ...], estimate_arc_sequences: tuple[tuple[str | None, ...], ...])
```

Impacto do estimador sobre a **fronteira de busca** de um perfil.

### `SteadyStateTraversalModel`

```python
class SteadyStateTraversalModel(cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None, require_elevation: bool = False)
```

``TraversalModel`` aproximado por equilibrio (uso em busca).

### `compare_rankings`

```python
compare_rankings(reference: list[tuple[collections.abc.Hashable, tuple[float, ...]]], estimate: list[tuple[collections.abc.Hashable, tuple[float, ...]]], *, tolerance: float = 1e-06) -> router.core.RankingComparison
```

Compara duas ordenacoes de rotas por **chave** e por **valor**.

### `compare_search_rankings`

```python
compare_search_rankings(graph: router.core.data.RoutingGraph, origin: collections.abc.Hashable, destination: collections.abc.Hashable, profile: router.core.SearchProfile | str, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, *, initial_speed_mps: float = 0.0, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None, reference_step_m: float = 5.0, tolerance: float = 1e-06) -> router.core.SearchRankingComparison
```

Compara a fronteira de busca do simulador completo e do estimador.

### `compare_traversals`

```python
compare_traversals(arcs: list[router.core.data.Arc], cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, *, initial_state: router.core.physics.models.SimulationState | None = None, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None, reference_step_m: float = 5.0) -> router.core.ComparisonReport
```

Compara o simulador completo e o estimador arco a arco.

### `estimate_arc`

```python
estimate_arc(arc: router.core.data.Arc, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, initial_state: router.core.physics.models.SimulationState | None = None, environment: router.core.physics.environment.Environment | None = None, power_w: float | None = None, *, require_elevation: bool = False) -> router.core.physics.models.TraversalResult
```

Travessia **aproximada** de ``arc`` por equilibrio de velocidade.

## Busca: contrato de circulacao (guardrails)

Contrato de circulacao (ADR-026): veredito tri-estado (`ALLOW`/`FORBID`/`UNKNOWN`) e politica conservadora (`UNKNOWN -> FORBID`), decisao de acesso do arco, decisao de conversao (via-node e via-way ordenado), validador independente de rota e os nos com restricao de conversao. **Nao** altera a fisica.

### `CirculationPolicy`

```python
class CirculationPolicy(unresolved_verdict: router.core.Verdict = <Verdict.FORBID: 'forbid'>)
```

Politica **declarativa** para vereditos ``UNKNOWN`` (brutos).

### `Decision`

```python
class Decision(verdict: router.core.Verdict, reason: router.core.DecisionReason, raw_verdict: router.core.Verdict = <Verdict.ALLOW: 'allow'>, legality_asserted: bool = False, dismount: bool = False, restriction_id: str | None = None)
```

Resultado de uma consulta de circulacao.

### `DecisionReason`

```python
class DecisionReason(str, Enum)
```

Motivo de uma decisao (nunca silencioso; sempre auditavel).

Valores: `NO_RULE`, `EXPLICIT_FORBIDDEN`, `EXPLICIT_ALLOWED`, `CONDITIONAL_FORBIDDEN`, `CONDITIONAL_ALLOWED`, `CONDITION_UNRESOLVED`, `CONTEXT_INSUFFICIENT`, `DISMOUNT`, `TURN_FORBIDDEN`, `TURN_ONLY_SATISFIED`, `TURN_ONLY_VIOLATED`, `UNSUPPORTED`.

### `RouteValidation`

```python
class RouteValidation(compliant: bool, steps: tuple[router.core.Decision, ...], violations: tuple[router.core.Decision, ...], unresolved: tuple[router.core.Decision, ...], dismount_used: bool)
```

Resultado da validacao de uma rota contra o contrato de circulacao.

### `Verdict`

```python
class Verdict(str, Enum)
```

Veredito tri-estado de uma decisao de circulacao.

Valores: `ALLOW`, `FORBID`, `UNKNOWN`.

### `arc_decision`

```python
arc_decision(arc: router.core.data.Arc, *, evaluator: collections.abc.Callable[[str], bool | None] | None = None, policy: router.core.CirculationPolicy = CirculationPolicy(unresolved_verdict=<Verdict.FORBID: 'forbid'>)) -> router.core.Decision
```

Decide o acesso de ``arc``.

### `constrained_nodes`

```python
constrained_nodes(graph: router.core.data.RoutingGraph) -> frozenset[collections.abc.Hashable]
```

Nos em que a legalidade da proxima conversao depende do arco de chegada.

### `transition_decision`

```python
transition_decision(graph: router.core.data.RoutingGraph, from_arc: router.core.data.Arc, to_arc: router.core.data.Arc, node_id: collections.abc.Hashable | None = None, *, history_way_ids: collections.abc.Sequence[int | None] = (), evaluator: collections.abc.Callable[[str], bool | None] | None = None, policy: router.core.CirculationPolicy = CirculationPolicy(unresolved_verdict=<Verdict.FORBID: 'forbid'>)) -> router.core.Decision
```

Decide a conversao ``from_arc -> to_arc`` no no de transicao.

### `validate_route`

```python
validate_route(graph: router.core.data.RoutingGraph, arc_ids: collections.abc.Sequence[str], *, evaluator: collections.abc.Callable[[str], bool | None] | None = None, policy: router.core.CirculationPolicy = CirculationPolicy(unresolved_verdict=<Verdict.FORBID: 'forbid'>)) -> router.core.RouteValidation
```

Valida uma sequencia de arcos direcionados contra o contrato.

## Busca: reconstrucao e avaliacao de rotas

Reconstrucao da fronteira em rotas e avaliacao de objetivos.

### `SearchRoute`

```python
class SearchRoute(arc_ids: tuple[str | None, ...], time_s: float, distance_m: float, ascent_m: float, descent_m: float, energy_j: float, label_id: int = 0, arc_lengths: tuple[float, ...] = (), result_status: router.core.physics.models.SimulationStatus = <SimulationStatus.VALID: 'valid'>, elevation_uncertainty: router.core.physics.models.ElevationUncertainty | None = None, used_min_speed_regularization: bool = False, stalled: bool = False, dismount_used: bool = False)
```

Rota reconstruida: arcos direcionados, comprimentos e metricas.

### `evaluate`

```python
evaluate(source: router.core.MetricSource, evaluator: router.core.ObjectiveEvaluator | None = None) -> router.core.ObjectiveValues
```

Atalho: avalia ``source`` com o avaliador dado (ou o central).

### `evaluate_metrics`

```python
evaluate_metrics(metrics: router.core.MetricSource, evaluator: router.core.ObjectiveEvaluator | None = None) -> router.core.ObjectiveValues
```

Compatibilidade: avalia um conjunto de metricas.

### `evaluate_route`

```python
evaluate_route(route: router.core.MetricSource, evaluator: router.core.ObjectiveEvaluator | None = None) -> router.core.ObjectiveValues
```

Compatibilidade: avalia uma rota (mesmo que ``evaluate``).

### `route_from_state`

```python
route_from_state(state: router.core.SearchState) -> router.core.SearchRoute
```

Reconstroi uma ``SearchRoute`` de uma label de destino.

### `routes_from_result`

```python
routes_from_result(result: router.core.SearchResult) -> tuple[router.core.SearchRoute, ...]
```

Converte a fronteira de destino de um ``SearchResult`` em rotas.

## Selecao: candidatas, ranking e diversidade

Pool de candidatas (com deduplicacao), ranking por perfil e diversidade.

### `CandidateMetrics`

```python
class CandidateMetrics(time_s: float, distance_m: float, ascent_m: float, descent_m: float, energy_j: float)
```

Metricas finais de uma candidata (objetivos + descida).

### `DeduplicationMode`

```python
class DeduplicationMode(str, Enum)
```

Como decidir que duas candidatas sao a mesma alternativa.

Valores: `PATH`, `METRICS`, `PATH_AND_METRICS`.

### `ProfileRanker`

```python
class ProfileRanker(evaluator: router.core.ObjectiveEvaluator | None = None)
```

Componente de ranking: ordena candidatas segundo o perfil.

### `RankedCandidate`

```python
class RankedCandidate(candidate: router.core.RouteCandidate, rank: int, profile_score: float | None = None)
```

Candidata ordenada: posicao, candidata e score (None se lexicografico).

### `RankedRoute`

```python
class RankedRoute(route: router.core.SearchRoute, rank: int, profile_score: float | None = None)
```

Compatibilidade: rota ordenada (sem a identidade da candidata).

### `RouteCandidate`

```python
class RouteCandidate(route_id: str, route: router.core.SearchRoute, metrics: router.core.CandidateMetrics)
```

Alternativa candidata: identidade + rota reconstruida + metricas.

### `RouteCandidatePool`

```python
class RouteCandidatePool(candidates: tuple[router.core.RouteCandidate, ...] = (), dropped_duplicates: int = 0)
```

Conjunto de candidatas nao dominadas, ja deduplicado.

### `RouteDeduplicator`

```python
class RouteDeduplicator(mode: router.core.DeduplicationMode = <DeduplicationMode.PATH: 'path'>, metric_quantum: float = 0.001)
```

Remove candidatas repetidas mantendo a primeira ocorrencia.

### `RouteDiversity`

```python
class RouteDiversity(metric: router.core.DiversityMetric = <DiversityMetric.SHARED_DISTANCE_RATIO: 'shared_distance_ratio'>, threshold: float = 0.7)
```

Componente de diversidade: metrica + limiar configurados juntos.

### `arc_jaccard`

```python
arc_jaccard(a: router.core.SearchRoute, b: router.core.SearchRoute) -> float
```

Similaridade de Jaccard sobre os arcos direcionados.

### `is_too_similar`

```python
is_too_similar(a: router.core.SearchRoute, b: router.core.SearchRoute, metric: router.core.DiversityMetric, threshold: float) -> bool
```

True quando a semelhanca excede ``threshold``.

### `order_routes`

```python
order_routes(routes: collections.abc.Iterable[router.core.SearchRoute], profile: router.core.RouteProfile, *, evaluator: router.core.ObjectiveEvaluator | None = None) -> list[router.core.SearchRoute]
```

Atalho: apenas as rotas, na ordem de preferencia do perfil.

### `profile_score`

```python
profile_score(route: router.core.SearchRoute, profile: router.core.RouteProfile, *, evaluator: router.core.ObjectiveEvaluator | None = None) -> float | None
```

Score escalar do perfil para uma rota (None se lexicografico).

### `rank_candidates`

```python
rank_candidates(candidates: collections.abc.Iterable[router.core.RouteCandidate], profile: router.core.RouteProfile, *, evaluator: router.core.ObjectiveEvaluator | None = None) -> list[router.core.RankedCandidate]
```

Ordena candidatas pela preferencia do perfil (rank 0 = melhor).

### `rank_routes`

```python
rank_routes(routes: collections.abc.Iterable[router.core.SearchRoute], profile: router.core.RouteProfile, *, evaluator: router.core.ObjectiveEvaluator | None = None) -> list[router.core.RankedRoute]
```

Ordena rotas pela preferencia do perfil (rank 0 = melhor).

### `route_diversity`

```python
route_diversity(a: router.core.SearchRoute, b: router.core.SearchRoute, metric: router.core.DiversityMetric) -> float
```

Semelhanca entre ``a`` e ``b`` segundo ``metric`` (0 = distintas).

### `route_similarity`

```python
route_similarity(a: router.core.SearchRoute, b: router.core.SearchRoute) -> float
```

Alias de ``arc_jaccard`` (compatibilidade com a API anterior).

### `shared_distance_ratio`

```python
shared_distance_ratio(a: router.core.SearchRoute, b: router.core.SearchRoute) -> float
```

Proporcao de distancia compartilhada, em ``[0, 1]``.

## Selecao: N rotas, explicacao e pipeline

Escolha das N rotas (com fallback de diversidade), explicacao e o contrato de alto nivel ``search_profile``.

### `ProfileSearchRequest`

```python
class ProfileSearchRequest(origin: collections.abc.Hashable, destination: collections.abc.Hashable, profile: router.core.RouteProfile, max_routes: int = 3, initial_speed_mps: float = 0.0, energy_model: router.core.physics.energy_model.EnergyModel | None = None, battery: router.core.physics.energy_model.BatteryConfig | None = None)
```

Pedido de busca para um perfil concreto.

### `ProfileSearchResponse`

```python
class ProfileSearchResponse(profile_id: str, routes: tuple[router.core.SelectedRoute, ...], pareto_route_count: int, selection: router.core.SelectionMetadata, explanations: tuple[router.core.RouteExplanation, ...] = (), energy: tuple[router.core.RouteEnergyEstimate, ...] = (), reached: bool = False, truncated: bool = False, frontier_truncated: bool = False, dropped_labels: int = 0)
```

Resposta: rotas selecionadas + explicacoes alinhadas por indice.

### `RouteExplanation`

```python
class RouteExplanation(headline: str, details: tuple[str, ...] = (), tradeoffs: tuple[str, ...] = ())
```

Justificativa textual de uma rota selecionada.

### `RouteExplainer`

```python
class RouteExplainer(evaluator: router.core.ObjectiveEvaluator | None = None)
```

Produz explicacoes derivadas das metricas e do conjunto comparado.

### `RouteSelectionRequest`

```python
class RouteSelectionRequest(max_routes: int, min_routes: int = 1, diversity: router.core.DiversityConfig | None = None)
```

Como escolher as N rotas (limite de apresentacao).

### `RouteSelectionResult`

```python
class RouteSelectionResult(selected: tuple[router.core.SelectedRoute, ...], pareto_route_count: int, rejected_as_similar_count: int, diversity_relaxed: bool, threshold_used: float | None = None, dropped_duplicates: int = 0)
```

Resultado da selecao.

### `RouteSelector`

```python
class RouteSelector(evaluator: router.core.ObjectiveEvaluator | None = None)
```

Componente de selecao: escolhe ate N candidatas diversas.

### `SelectedRoute`

```python
class SelectedRoute(candidate: router.core.RouteCandidate, rank: int, profile_score: float | None, selection_reason: router.core.SelectionReason)
```

Candidata escolhida: posicao, score do perfil e razao da escolha.

### `SelectionConfig`

```python
class SelectionConfig(max_routes: int, min_routes: int = 1, diversity: router.core.DiversityConfig | None = None)
```

Como escolher as N rotas (limite de apresentacao).

### `SelectionMetadata`

```python
class SelectionMetadata(requested_count: int, selected_count: int, diversity_relaxed: bool, selection_strategy: str, threshold_used: float | None = None, dropped_duplicates: int = 0)
```

Metadados da selecao apresentados junto com a resposta.

### `SelectionReason`

```python
class SelectionReason(primary_objective: router.core.Objective, primary_value: float, diversity_accepted: bool)
```

Por que a rota entrou: objetivo primario e aceite de diversidade.

### `SelectionResult`

```python
class SelectionResult(selected: tuple[router.core.SelectedRoute, ...], pareto_route_count: int, rejected_as_similar_count: int, diversity_relaxed: bool, threshold_used: float | None = None, dropped_duplicates: int = 0)
```

Resultado da selecao.

### `explain_route`

```python
explain_route(route: router.core.SearchRoute, profile: router.core.RouteProfile, reason: router.core.SelectionReason, *, candidates: collections.abc.Iterable[router.core.SearchRoute] = (), evaluator: router.core.ObjectiveEvaluator | None = None) -> router.core.RouteExplanation
```

Atalho funcional para ``RouteExplainer().explain``.

### `search_profile`

```python
search_profile(graph: router.core.data.RoutingGraph, traversal: router.core.TraversalModel, request: router.core.ProfileSearchRequest, *, heuristic: router.core.Heuristic | None = None, search_config: router.core.SearchConfig | None = None, selection_config: router.core.SelectionConfig | None = None, deduplicator: router.core.RouteDeduplicator | None = None, explain: bool = True) -> router.core.ProfileSearchResponse
```

Executa busca + selecao para um ``ProfileSearchRequest``.

### `select_candidates`

```python
select_candidates(candidates: collections.abc.Iterable[router.core.RouteCandidate], profile: router.core.RouteProfile, config: router.core.SelectionConfig | None = None, *, evaluator: router.core.ObjectiveEvaluator | None = None) -> router.core.SelectionResult
```

Seleciona ate ``config.max_routes`` candidatas, em ordem de preferencia.

### `select_from_result`

```python
select_from_result(result: SearchResult, profile: RouteProfile | str | None = None, config: SelectionConfig | None = None, *, evaluator: ObjectiveEvaluator | None = None, deduplicator: RouteDeduplicator | None = None) -> SelectionResult
```

Atalho: forma o pool a partir de um ``SearchResult`` e seleciona.

### `select_routes`

```python
select_routes(routes: collections.abc.Iterable[router.core.SearchRoute], profile: router.core.RouteProfile, config: router.core.SelectionConfig | None = None, *, evaluator: router.core.ObjectiveEvaluator | None = None, deduplicator: router.core.RouteDeduplicator | None = None) -> router.core.SelectionResult
```

Atalho: forma o pool de candidatas (com dedup) e seleciona.

## Energia da rota (pos-selecao)

Estimativa de energia eletrica de uma rota, alinhada as rotas selecionadas.

### `RouteEnergyEstimate`

```python
class RouteEnergyEstimate(route_id: str, estimate: router.core.physics.energy_model.EnergyEstimate, battery: router.core.physics.energy_model.BatteryFeasibility | None = None)
```

Energia estimada de uma rota + viabilidade de bateria (opcional).

### `estimate_route_energy`

```python
estimate_route_energy(route: router.core.SearchRoute, model: router.core.physics.energy_model.EnergyModel | None = None, *, battery: router.core.physics.energy_model.BatteryConfig | None = None, route_id: str = '') -> router.core.RouteEnergyEstimate
```

Converte a energia mecanica de ``route`` em estimativa eletrica.

### `estimate_routes_energy`

```python
estimate_routes_energy(routes: collections.abc.Iterable[router.core.SearchRoute], model: router.core.physics.energy_model.EnergyModel | None = None, *, battery: router.core.physics.energy_model.BatteryConfig | None = None, route_ids: collections.abc.Iterable[str] | None = None) -> tuple[router.core.RouteEnergyEstimate, ...]
```

Estima a energia de varias rotas, alinhando ``route_ids`` por indice.

## Avaliacao: rota completa e classificacao (Camada 05)

Avaliacao fisica **completa** de uma rota (simulador, nao o estimador) -> `EffortFeatures` -> previsao fisiologica, com validacao de circulacao e classificacao explicita (`REJECTED`/`PHYSICAL_ONLY`/`PHYSIOLOGICAL`). Sem modelo calibrado, declara a ausencia; nunca inventa valor nem recalcula fisica.

### `AssessmentOutcome`

```python
class AssessmentOutcome(str, Enum)
```

Classificacao explicita de uma rota avaliada.

Valores: `PHYSIOLOGICAL`, `PHYSICAL_ONLY`, `REJECTED`.

### `RouteAssessment`

```python
class RouteAssessment(arc_ids: tuple[str, ...], path: router.core.physics.models.TraversalPath, features: router.core.physics.effort.EffortFeatures, prediction: router.core.PhysiologicalPrediction, circulation: router.core.RouteValidation, intensity: 'IntensityEstimate', energy: router.core.EnergyConsistency | None = None)
```

Resultado da avaliacao fisica completa de uma rota.

### `RouteAssessmentError`

```python
class RouteAssessmentError(ValueError)
```

Erro de **dados** na avaliacao de rota.

### `assess_candidate`

```python
assess_candidate(graph: router.core.data.RoutingGraph, candidate: router.core.RouteCandidate, **kwargs) -> router.core.RouteAssessment
```

Avalia uma ``RouteCandidate`` (identidade + rota) pelo simulador completo.

### `assess_candidates`

```python
assess_candidates(graph: router.core.data.RoutingGraph, candidates: collections.abc.Iterable[router.core.RouteCandidate], **kwargs) -> tuple[router.core.RouteAssessment, ...]
```

Avalia um conjunto de candidatas, preservando a ordem de entrada.

### `assess_route`

```python
assess_route(graph: router.core.data.RoutingGraph, arc_ids: collections.abc.Sequence[str | None], *, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, traversal: router.core.physics.traversal.PhysicalTraversal | None = None, environment: router.core.physics.environment.Environment | None = None, initial_state: router.core.physics.models.SimulationState | None = None, power_w: float | None = None, rider: router.core.RiderProfile | None = None, context: router.core.PredictionContext | None = None, predictor: router.core.prediction.ml.EffortPredictor | None = None, condition_evaluator: collections.abc.Callable[[str], bool | None] | None = None, circulation_policy: router.core.CirculationPolicy = CirculationPolicy(unresolved_verdict=<Verdict.FORBID: 'forbid'>)) -> router.core.RouteAssessment
```

Avalia uma rota com o **simulador fisico completo** e a classifica.

### `assess_search_route`

```python
assess_search_route(graph: router.core.data.RoutingGraph, route: router.core.SearchRoute, **kwargs) -> router.core.RouteAssessment
```

Avalia uma ``SearchRoute`` da busca (Camada 03) pelo simulador completo.

## Seguranca da rota e avaliacao final (Camada 05, ADR-029)

Estado de **seguranca** operacional de uma rota (`VALIDATED`/`DEGRADED`/`REJECTED`), **independente** de `AssessmentOutcome`, e a **avaliacao final** das candidatas. `evaluate_candidates` **reutiliza** as avaliacoes ja calculadas (sem nova fisica nem fisiologia) e escolhe a primeira rota **elegivel**; `REJECTED` nunca e recomendada. Toda rejeicao carrega um motivo auditavel; ausencia de modelo calibrado e a estimativa de intensidade entram como **advertencias** que nao alteram o estado. `energy_consistency` agrega o balanco energetico ja calculado (sem equacao nova).

### `EnergyConsistency`

```python
class EnergyConsistency(wheel_work_j: float, residual_j: float, relative_residual: float | None, mass_kg: float)
```

Consistencia energetica agregada de uma rota (diagnostico, ADR-029 D7).

### `EvaluatedRoute`

```python
class EvaluatedRoute(route_id: str, safety: router.core.RouteSafetyAssessment, assessment: router.core.RouteAssessment | None = None, recommended: bool = False)
```

Rota avaliada com seu estado de seguranca (D4).

### `EvaluationReport`

```python
class EvaluationReport(routes: tuple[router.core.EvaluatedRoute, ...] = (), excluded: tuple[router.core.EvaluatedRoute, ...] = (), recommended: router.core.EvaluatedRoute | None = None, warnings: tuple[str, ...] = ())
```

Relatorio de avaliacao final: elegiveis, excluidas e recomendada (D4).

### `SafetyReason`

```python
class SafetyReason(kind: router.core.ReasonKind, detail: str, arc_id: str | None = None)
```

Motivo estruturado de um estado (auditavel).

### `ReasonKind`

```python
class ReasonKind(str, Enum)
```

Motivo **estruturado** de um estado (nunca silencioso; sempre auditavel).

Valores: `CIRCULATION_VIOLATION`, `CIRCULATION_UNRESOLVED`, `NON_FINITE_METRIC`, `NEGATIVE_SPEED`, `NON_POSITIVE_DISTANCE`, `ELEVATION_MISSING`, `SIMULATION_DEGRADED`, `SIMULATION_UNAVAILABLE`, `MIN_SPEED_REGULARIZATION`, `STALLED`, `DISMOUNT_USED`, `PHYSIOLOGY_UNAVAILABLE`, `PHYSIOLOGY_MODEL_NOT_CALIBRATED`, `PHYSIOLOGY_INSUFFICIENT_DATA`, `PHYSIOLOGY_CONTRACT_INCOMPATIBLE`, `INTENSITY_NOT_CALIBRATED`, `EVALUATION_MISSING`.

### `RouteSafetyAssessment`

```python
class RouteSafetyAssessment(state: router.core.RouteSafetyState, reasons: tuple[router.core.SafetyReason, ...] = (), advisories: tuple[router.core.SafetyReason, ...] = ())
```

Estado de seguranca de uma rota e seus motivos (ADR-029 D2).

### `RouteSafetyState`

```python
class RouteSafetyState(str, Enum)
```

Estado de seguranca operacional de uma rota (ADR-029 D2).

Valores: `VALIDATED`, `DEGRADED`, `REJECTED`.

### `classify_safety`

```python
classify_safety(assessment: router.core.RouteAssessment) -> router.core.RouteSafetyAssessment
```

Classifica a **seguranca** de uma rota ja avaliada (sem recalcular nada).

### `energy_consistency`

```python
energy_consistency(path: router.core.physics.models.TraversalPath, mass_kg: float) -> router.core.EnergyConsistency
```

Agrega o balanco energetico ja calculado de um ``TraversalPath``.

### `evaluate_candidates`

```python
evaluate_candidates(candidates: collections.abc.Iterable, *, assessments: collections.abc.Mapping[str, router.core.RouteAssessment] | None = None) -> router.core.EvaluationReport
```

Avalia a seguranca de candidatas **sem recalcular** o ja validado (D4).

## Consulta offline completa (planejamento)

Orquestracao de todo o fluxo offline numa chamada: **zona dinamica** (`envelope.adaptive_zone`, recorta o grafo da cidade inteira) -> **snapping** -> **busca** (`search.MultiObjectiveAStar`) -> **avaliacao fisica completa** (`evaluation.assess_candidates`) -> **previsao** (`ml.predict_effort`, preditor local) -> **classificacao** (`intelligence.classify_routes`). O box e **dinamico** (origem, destino e tracado de todas as rotas) e limita a busca e a altimetria. Sem modelo calibrado, cada rota fica `physical_only` com RPE `None` — nunca um valor inventado.

### `PlannedRoute`

```python
class PlannedRoute(candidate: router.core.RouteCandidate, assessment: router.core.RouteAssessment, classification: router.core.RouteClassification, safety: router.core.RouteSafetyAssessment, recommended: bool = False, geometry: tuple[router.contracts.route.RoutePoint, ...] = ())
```

Uma rota descoberta, com fisica completa, previsao e classificacao.

### `RoutePlanResult`

```python
class RoutePlanResult(origin: tuple[float, float], destination: tuple[float, float], profile_id: str, search_bbox: tuple[float, float, float, float], bbox: tuple[float, float, float, float], reached: bool, zone_nodes: int, zone_arcs: int, graph_nodes: int, graph_arcs: int, iterations: int, routes: tuple[router.app.planning.PlannedRoute, ...] = (), rejected: tuple[router.app.planning.PlannedRoute, ...] = (), warnings: tuple[str, ...] = (), pareto_route_count: int = 0, truncated: bool = False, frontier_truncated: bool = False, dropped_labels: int = 0, preference: router.core.EffortPreference | None = None)
```

Plano de consulta offline: box, zona e rotas descobertas.

### `plan_offline_routes`

```python
plan_offline_routes(graph: router.core.data.RoutingGraph, origin: tuple[float, float], destination: tuple[float, float], *, cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, profile: str | router.core.RouteProfile = 'fastest', snap_options: router.core.data.snapping.SnapOptions | None = None, traversal: router.core.physics.traversal.PhysicalTraversal | None = None, environment: router.core.physics.environment.Environment | None = None, initial_speed_mps: float = 0.0, initial_state: router.core.physics.models.SimulationState | None = None, power_w: float | None = None, rider: router.core.RiderProfile | None = None, context: router.core.PredictionContext | None = None, predictor: router.core.prediction.ml.EffortPredictor | None = None, preference: router.core.EffortPreference | None = None, deduplicator: router.core.RouteDeduplicator | None = None, max_routes: int | None = None, growth: float = 1.6, max_iterations: int = 8, pad_m: float = 80.0) -> router.app.planning.RoutePlanResult
```

Descobre rotas offline entre ``origin`` e ``destination`` e as classifica.

## Integracao com o aplicativo (ADR-030, Fase 3)

Superficie **curada** para o consumidor externo: `plan_routes` resolve multi-pernas (`stops`) no nucleo e devolve o envelope `RoutePlan`; `integration` reexporta poucos nomes (contrato, grafo, modelos de entrada e guard offline) e preserva um caminho por operacao.

### `plan_routes`

```python
plan_routes(graph: router.core.data.RoutingGraph, origin: tuple[float, float], destination: tuple[float, float], *, stops: collections.abc.Sequence[tuple[float, float]] = (), cyclist: router.core.physics.cyclist.CyclistModel, bicycle: router.core.physics.bicycle.BicycleModel, profile: str = 'fastest', **options: Any) -> router.contracts.route.RoutePlan
```

Planeja uma rota passando por ``stops``, na ordem informada.

### `integration`

```python
integration
```

Modulo de integracao: superficie curada para o app (ADR-030 Fase 3).

## Contrato publico de rota (v1)

Fronteira de **transporte** do motor (ADR-030): tipos versionados, serializaveis e independentes dos DTOs internos. O envelope e **estrito** (`format`/`version` obrigatorios, `allow_nan=False`, sem `default=str`), a ausencia e explicita (`None`) e a geometria vem por ponto (`dist_m` monotonico). `snake_case` no Python/JSON; adaptadores traduzem para `camelCase`.

### `CONTRACT_FORMAT`

```python
CONTRACT_FORMAT
```

### `CONTRACT_VERSION`

```python
CONTRACT_VERSION
```

### `INTENSITY_BANDS`

```python
INTENSITY_BANDS
```

### `RouteContractError`

```python
class RouteContractError(ValueError)
```

Payload de rota invalido, incompleto ou de versao desconhecida.

### `BBox`

```python
class BBox(south: float, west: float, north: float, east: float)
```

Caixa geografica (graus).

### `RouteRequest`

```python
class RouteRequest(origin: tuple[float, float], destination: tuple[float, float], profile: str, stops: tuple[tuple[float, float], ...] = ())
```

Intencao de roteamento ecoada no resultado.

### `RoutePoint`

```python
class RoutePoint(lat: float, lon: float, ele: float | None, dist_m: float, speed_kmh: float, grade_pct: float, band: int | None = None, rpe: float | None = None, watts: float | None = None)
```

Ponto do perfil da rota.

### `RouteMetrics`

```python
class RouteMetrics(source: str, distance_m: float, time_s: float, energy_j: float, ascent_m: float, descent_m: float, max_grade_pct: float | None = None, meters_band: tuple[float, float, float] = (0.0, 0.0, 0.0), contra_m: float | None = None, walk_m: float | None = None)
```

Metricas agregadas da rota.

### `RouteML`

```python
class RouteML(mean_rpe: float, peak_rpe: float, calibrated: bool, is_measurement: bool, band_distance_fraction: dict[str, float] = <factory>, band_duration_fraction: dict[str, float] | None = None, method_id: str = '', model_version: str = '', borg_scale: str = '')
```

Estimativa de intensidade (RPE/faixas) — **nao calibrada**.

### `Reason`

```python
class Reason(kind: str, detail: str, arc_id: str | None = None)
```

Motivo estruturado de seguranca (auditavel).

### `RouteSafety`

```python
class RouteSafety(state: str, eligible: bool, reasons: tuple[router.contracts.route.Reason, ...] = (), advisories: tuple[router.contracts.route.Reason, ...] = ())
```

Estado de seguranca da rota (ADR-029).

### `RouteProvenance`

```python
class RouteProvenance(kind: str, profile: str)
```

Como a rota nasceu (gerador/perfil).

### `Route`

```python
class Route(id: str, profile: str, recommended: bool, geometry: tuple[router.contracts.route.RoutePoint, ...], metrics: router.contracts.route.RouteMetrics, ml: router.contracts.route.RouteML, safety: router.contracts.route.RouteSafety, provenance: router.contracts.route.RouteProvenance)
```

Rota no contrato publico.

### `Zone`

```python
class Zone(nodes: int, arcs: int, graph_nodes: int, graph_arcs: int)
```

Tamanho da zona de busca versus grafo total.

### `RoutePlan`

```python
class RoutePlan(request: router.contracts.route.RouteRequest, reached: bool, search_bbox: router.contracts.geo.BBox, bbox: router.contracts.geo.BBox, zone: router.contracts.route.Zone, iterations: int, pareto_route_count: int, physiological_count: int, routes: tuple[router.contracts.route.Route, ...] = (), rejected: tuple[router.contracts.route.Route, ...] = (), warnings: tuple[str, ...] = (), recommended_route_id: str | None = None, truncated: bool = False, frontier_truncated: bool = False, dropped_labels: int = 0, format: str = 'roteamento.cycling-route-plan', version: int = 1)
```

Envelope versionado de uma consulta de rotas.

### `dumps`

```python
dumps(plan: router.contracts.route.RoutePlan, *, indent: int | None = None) -> str
```

Serializa o contrato em JSON **estrito** (sem ``default=str``).

### `loads`

```python
loads(text: str) -> router.contracts.route.RoutePlan
```

Le e valida um envelope JSON do contrato.

### `write_plan`

```python
write_plan(path: str | pathlib.Path, plan: router.contracts.route.RoutePlan, *, indent: int | None = 2) -> pathlib.Path
```

Grava o contrato em UTF-8 e devolve o caminho.

### `read_plan`

```python
read_plan(path: str | pathlib.Path) -> router.contracts.route.RoutePlan
```

Le um envelope do contrato de um arquivo UTF-8.

## Operacao offline (guard de rede)

Guard que **proibe** acesso a rede durante a execucao (ADR-027 §0): buscar, simular, carregar modelo, prever e classificar rodam **localmente**, sem internet, sem nuvem e sem API externa. Uma tentativa de conexao bloqueada falha de forma explicita (`OfflineViolation`), nunca silenciosa.

### `OfflineViolation`

```python
class OfflineViolation(RuntimeError)
```

Tentativa de acesso a rede durante operacao offline (ADR-027 §0).

### `is_network_disabled`

```python
is_network_disabled() -> bool
```

True enquanto o guard offline estiver ativo.

### `offline`

```python
offline(*, allow_loopback: bool = False) -> Iterator[NoneType]
```

Desabilita conexoes de rede enquanto o contexto estiver ativo.

## Fonte geografica local: leitor OSM.PBF

Leitor **offline** de `OpenStreetMap PBF` (`.osm.pbf`), em Python puro (sem protobuf/osmium), com leitura em duas passagens de memoria limitada. E a unica fonte geografica do motor (ADR-027 §0): le vias, nos e relacoes `type=restriction` de um arquivo local, sem ingestao online.

### `PbfError`

```python
class PbfError(RuntimeError)
```

Falha ao ler um arquivo OpenStreetMap PBF.

### `PbfHeader`

```python
class PbfHeader(bbox: tuple[float, float, float, float] | None, writing_program: str | None, source: str | None, required_features: tuple[str, ...] = (), optional_features: tuple[str, ...] = ())
```

Metadados do bloco ``OSMHeader``.

### `PbfStats`

```python
class PbfStats(blobs: int, ways_seen: int, ways_selected: int, nodes_seen: int, nodes_selected: int, relations_seen: int = 0)
```

Contadores da leitura (auditoria do que foi percorrido/selecionado).

### `read_header`

```python
read_header(path: str | pathlib.Path) -> router.core.data.PbfHeader
```

Le o ``OSMHeader`` do arquivo e devolve seus metadados.

### `read_osm_pbf`

```python
read_osm_pbf(path: str | pathlib.Path, *, highways: tuple[str, ...] | None = None, with_stats: bool = False) -> router.core.data.OsmData | tuple[router.core.data.OsmData, router.core.data.PbfStats]
```

Le um ``.osm.pbf`` e monta um :class:`OsmData`.

## Elevacao local: Terrarium (offline)

Elevacao **offline** por tiles Terrarium locais (`{z}/{x}/{y}.png`), com decodificacao PNG em Python puro (`h = R*256 + G + B/256 - 32768`). `elevation_tiles` faz o **preparo de rede** (download dos tiles para uso offline).

### `TerrariumElevation`

```python
class TerrariumElevation(tiles_dir: str | pathlib.Path, *, zoom: int | None = None, name: str = 'terrarium')
```

Altitudes de tiles Terrarium locais (PNG ``{z}/{x}/{y}.png``).

### `TerrariumTileError`

```python
class TerrariumTileError(RuntimeError)
```

Falha ao baixar um tile Terrarium.

### `fetch_terrarium_tiles`

```python
fetch_terrarium_tiles(bbox: tuple[float, float, float, float], zoom: int, dest_dir: str | pathlib.Path, *, timeout_s: float = 60.0, user_agent: str = 'roteamento-engine/0.1 (cycling graph; terrarium)', progress: Callable[[int, int, pathlib.Path], NoneType] | None = None) -> list[pathlib.Path]
```

Baixa os tiles de ``bbox``/``zoom`` para ``dest_dir`` (idempotente).

### `lonlat_to_tile`

```python
lonlat_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]
```

Converte ``(lat, lon)`` no tile XYZ ``(x, y)`` do web-mercator (OSM).

### `tiles_for_bbox`

```python
tiles_for_bbox(bbox: tuple[float, float, float, float], zoom: int) -> list[tuple[int, int]]
```

Tiles XYZ ``(x, y)`` que cobrem ``bbox`` no ``zoom`` dado.

## Envelope de consulta: zona de busca + altimetria (offline)

Box **dinamico** de uma consulta: parte do retangulo dos pinos (origem + paradas + destino) com margem **por eixo** e cresce para cobrir o **tracado real de todas** as rotas (`bbox_with_routes` / `query_bbox`). Como o aplicativo e **integralmente offline**, este box limita a **busca** (`graph_for_bbox`) e a **altimetria** — o grafo carregado e a cidade inteira, mas a busca nunca o percorre por completo. Portado de `toporide` (`src/domain/geo.ts`).

### `M_PER_DEG_LAT`

```python
M_PER_DEG_LAT
```

### `m_per_deg_lon`

```python
m_per_deg_lon(lat: float) -> float
```

Metros por grau de longitude na latitude dada.

### `expand_bbox`

```python
expand_bbox(bbox: tuple[float, float, float, float], pad_m: float, lat_ref: float) -> tuple[float, float, float, float]
```

Expande o box em ``pad_m`` metros, nos dois sentidos de cada eixo.

### `corridor_bbox_of`

```python
corridor_bbox_of(points: Sequence[tuple[float, float]], pad_x_km: float, pad_y_km: float | None = None) -> tuple[float, float, float, float]
```

Box do corredorenvolvendo **todos** os pontos + margem por eixo (km).

### `auto_box_pads`

```python
auto_box_pads(points: Sequence[tuple[float, float]]) -> tuple[float, float]
```

Margens automaticas do box (km, por lado), uma por eixo.

### `bbox_size_km`

```python
bbox_size_km(bbox: tuple[float, float, float, float]) -> tuple[float, float]
```

Dimensoes ``(largura_x_km, altura_y_km)`` do box.

### `bbox_of_routes`

```python
bbox_of_routes(routes: Sequence[Sequence[tuple[float, float]]], pad_m: float = 0.0) -> tuple[float, float, float, float] | None
```

Box de todas as rotas (tracado real); ``None`` se nao houver pontos.

### `union_bbox`

```python
union_bbox(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]
```

Menor box que contem ``a`` e ``b``.

### `bbox_with_routes`

```python
bbox_with_routes(base: tuple[float, float, float, float] | None, routes: Sequence[Sequence[tuple[float, float]]], pad_m: float = 80.0) -> tuple[float, float, float, float] | None
```

Box efetivo: une o box dos pinos ao envelope do tracado de TODAS as rotas.

### `ensure_min_span`

```python
ensure_min_span(bbox: tuple[float, float, float, float], min_span_deg: float) -> tuple[float, float, float, float]
```

Garante extensao minima (graus) dos dois lados do box.

### `query_bbox`

```python
query_bbox(waypoints: Sequence[tuple[float, float]], routes: Sequence[Sequence[tuple[float, float]]] = (), *, pad_m: float = 80.0) -> tuple[float, float, float, float] | None
```

Box dinamico de uma consulta: pinos (com margens) unido ao tracado real.

### `bbox_contains`

```python
bbox_contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float], eps: float = 1e-09) -> bool
```

``True`` quando ``outer`` cobre todo o ``inner`` (com tolerancia).

### `bbox_intersects`

```python
bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool
```

``True`` quando dois boxes se sobrepoem.

### `graph_for_bbox`

```python
graph_for_bbox(graph: router.core.data.RoutingGraph, bbox: tuple[float, float, float, float]) -> router.core.data.RoutingGraph
```

Subgrafo da zona: nos dentro do box + arcos com ambas as pontas dentro.

### `scale_bbox`

```python
scale_bbox(bbox: tuple[float, float, float, float], factor: float) -> tuple[float, float, float, float]
```

Escala o box sobre o proprio centro por ``factor`` (``>1`` cresce).

### `adaptive_zone`

```python
adaptive_zone(graph: router.core.data.RoutingGraph, waypoints: collections.abc.Sequence[tuple[float, float]], probe: collections.abc.Callable[[router.core.data.RoutingGraph], collections.abc.Sequence[collections.abc.Sequence[tuple[float, float]]]], *, growth: float = 1.6, max_iterations: int = 8, pad_m: float = 80.0) -> tuple[tuple[float, float, float, float], router.core.data.RoutingGraph, tuple[collections.abc.Sequence[tuple[float, float]], ...]]
```

Zona de busca **adaptativa**, dimensionada pelas rotas.

## Camada 04 (intelligence): alvos e observacoes

Contrato de **observacao real** (alvo observado + identidade pseudonimizada + qualidade/proveniencia + `dataset_version`), persistido **localmente**, e o contrato de **alvos** (RPE na escala Borg identificada; zonas cardiacas por definicao individualizada e metodo **nomeado/versionado**). Sem alvo observado, a observacao e inutilizavel — nunca imputada.

### `TARGETS_VERSION`

```python
TARGETS_VERSION
```

Versao do contrato de alvos (Camada 04).

### `TargetKind`

```python
class TargetKind(str, Enum)
```

Natureza do alvo previsto/observado.

Valores: `RPE`, `HEART_RATE_ZONES`, `HEART_RATE_POINT`.

### `ZoneDerivationUnavailable`

```python
class ZoneDerivationUnavailable(RuntimeError)
```

Nao ha metodo de derivacao de zonas registrado/intercambiavel.

### `HeartRateZoneDerivationMethod`

```python
class HeartRateZoneDerivationMethod(Protocol)
```

Metodo **nomeado e versionado** para derivar zonas de um ciclista.

### `ZONE_DERIVATION_METHODS`

```python
ZONE_DERIVATION_METHODS
```

Metodos de derivacao de zonas registrados (vazio por padrao).

### `register_zone_derivation`

```python
register_zone_derivation(method: router.core.prediction.targets.HeartRateZoneDerivationMethod) -> str
```

Registra um metodo de derivacao e devolve o identificador completo.

### `get_zone_derivation`

```python
get_zone_derivation(method_id: str) -> router.core.prediction.targets.HeartRateZoneDerivationMethod
```

Recupera um metodo registrado, ou levanta ``ZoneDerivationUnavailable``.

### `derive_heart_rate_zones`

```python
derive_heart_rate_zones(profile: router.core.RiderProfile, method_id: str) -> router.core.HeartRateZoneModel
```

Deriva zonas do ciclista usando um metodo **explicito** registrado.

### `OBSERVATIONS_VERSION`

```python
OBSERVATIONS_VERSION
```

Versao do contrato de observacao (Camada 04).

### `DATASET_FORMAT`

```python
DATASET_FORMAT
```

Identificador do formato de arquivo do conjunto de observacoes.

### `DATASET_FORMAT_VERSION`

```python
DATASET_FORMAT_VERSION
```

Versao do formato de arquivo do conjunto de observacoes.

### `ObservationError`

```python
class ObservationError(ValueError)
```

Erro de **dados** em uma observacao ou conjunto de observacoes.

### `MeasurementQuality`

```python
class MeasurementQuality(str, Enum)
```

Como o esforco observado foi obtido (nunca inferido).

Valores: `DEVICE_MEASURED`, `SELF_REPORTED`, `UNKNOWN`.

### `Provenance`

```python
class Provenance(source: str, version: str, notes: str | None = None)
```

Origem versionada de um dado (reutiliza o padrao do projeto).

### `CyclistId`

```python
class CyclistId(value: str)
```

Identificador **pseudonimizado** do ciclista.

### `pseudonymize`

```python
pseudonymize(identifier: str, *, salt: str) -> router.core.prediction.observations.CyclistId
```

Pseudonimo **estavel e nao reversivel** a partir de um identificador.

### `ObservedEffort`

```python
class ObservedEffort(observation_id: str, cyclist_id: router.core.prediction.observations.CyclistId, duration_s: float, distance_m: float, provenance: router.core.prediction.observations.Provenance, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, observed_rpe: float | None = None, observed_heart_rate_bpm: float | None = None, heart_rate_zone_durations_s: dict[str, float] | None = None, heart_rate_zone_definition: str | None = None, feature_schema_version: str = '1', feature_row: dict[str, float | None] | None = None, recorded_at: str | None = None, quality: router.core.prediction.observations.MeasurementQuality = <MeasurementQuality.UNKNOWN: 'unknown'>, notes: str | None = None)
```

Um esforco **observado** associado a uma rota percorrida.

### `ObservationDataset`

```python
class ObservationDataset(dataset_version: str, observations: tuple[router.core.prediction.observations.ObservedEffort, ...], provenance: router.core.prediction.observations.Provenance, notes: str | None = None)
```

Conjunto de observacoes reais, versionado e com proveniencia.

### `save_dataset`

```python
save_dataset(dataset: router.core.prediction.observations.ObservationDataset, path: str | pathlib.Path) -> pathlib.Path
```

Persiste o conjunto **localmente** (JSON versionado).

### `load_dataset`

```python
load_dataset(path: str | pathlib.Path) -> router.core.prediction.observations.ObservationDataset
```

Le um conjunto persistido **localmente** (sem rede).

### `build_dataset`

```python
build_dataset(dataset_version: str, observations: Iterable[router.core.prediction.observations.ObservedEffort], *, provenance: router.core.prediction.observations.Provenance, notes: str | None = None) -> router.core.prediction.observations.ObservationDataset
```

Atalho para montar um ``ObservationDataset`` a partir de um iteravel.

## Camada 04 (intelligence): features, modelos e artefato local

Features de treino e de servico **as mesmas** (sem alvo, sem vazamento); modelos base auditaveis; e o **artefato de modelo local** versionado (registro `model_version` <-> `dataset_version` <-> schema de features <-> alvo), carregado **do disco** com **verificacao de compatibilidade**. Modelos ausentes/incompativeis/corrompidos sao tratados de forma explicita — nunca por download.

### `FEATURES_VERSION`

```python
FEATURES_VERSION
```

Versao do contrato de montagem de features (treino/servico).

### `ALL_FEATURE_NAMES`

```python
ALL_FEATURE_NAMES
```

Todas as features que a Camada 04 sabe montar (ordem estavel).

### `DEFAULT_FEATURE_NAMES`

```python
DEFAULT_FEATURE_NAMES
```

Conjunto padrao: apenas features **sempre** presentes na fisica.

### `feature_row`

```python
feature_row(features: router.core.physics.effort.EffortFeatures, rider: router.core.RiderProfile | None = None, context: router.core.PredictionContext | None = None) -> dict[str, float | None]
```

Monta a linha de features (sem alvo) a partir do contrato existente.

### `select_row`

```python
select_row(row: Mapping[str, float | None], names: tuple[str, ...]) -> dict[str, float]
```

Seleciona as features exigidas, garantindo que estao presentes.

### `missing_features`

```python
missing_features(row: Mapping[str, float | None], names: tuple[str, ...]) -> tuple[str, ...]
```

Nomes exigidos que estao ausentes (``None``) na linha.

### `ModelError`

```python
class ModelError(ValueError)
```

Erro de dados/numerico no ajuste ou uso de um modelo.

### `ConstantModel`

```python
class ConstantModel(value: float)
```

Modelo constante: preve sempre o mesmo valor (baseline trivial).

### `LinearModel`

```python
class LinearModel(feature_names: tuple[str, ...], coefficients: tuple[float, ...], intercept: float)
```

Modelo linear ``y = intercept + sum(coef_i * feature_i)``.

### `model_from_payload`

```python
model_from_payload(payload: Mapping[str, Any]) -> router.core.prediction.models.ConstantModel | router.core.prediction.models.LinearModel
```

Reconstroi um modelo a partir do ``payload`` persistido.

### `fit_constant`

```python
fit_constant(targets: Sequence[float]) -> router.core.prediction.models.ConstantModel
```

Baseline constante: media dos alvos observados.

### `fit_linear`

```python
fit_linear(rows: Sequence[Mapping[str, float]], targets: Sequence[float], feature_names: Sequence[str]) -> router.core.prediction.models.LinearModel
```

Ajuste linear por minimos quadrados (equacoes normais + eliminacao).

### `MODEL_ARTIFACT_FORMAT`

```python
MODEL_ARTIFACT_FORMAT
```

Identificador do formato do artefato de modelo local.

### `MODEL_ARTIFACT_VERSION`

```python
MODEL_ARTIFACT_VERSION
```

Versao do formato do artefato de modelo local.

### `REGISTRY_FILENAME`

```python
REGISTRY_FILENAME
```

Nome do arquivo de registro dentro do armazenamento local.

### `ModelArtifactError`

```python
class ModelArtifactError(RuntimeError)
```

Erro base do artefato/armazenamento de modelo.

### `ModelNotFoundError`

```python
class ModelNotFoundError(ModelArtifactError)
```

Modelo ausente no armazenamento local.

### `ModelIncompatibleError`

```python
class ModelIncompatibleError(ModelArtifactError)
```

Modelo incompativel com o schema de features/contrato em uso.

### `ModelFormatError`

```python
class ModelFormatError(ModelArtifactError)
```

Artefato ilegivel/corrompido/em formato desconhecido.

### `ModelArtifact`

```python
class ModelArtifact(model_version: str, dataset_version: str, effort_schema_version: str, target: router.core.prediction.targets.TargetKind, method: str, provenance: router.core.prediction.observations.Provenance, payload: dict[str, typing.Any], is_baseline: bool = False, calibration_method: str | None = None, format: str = 'intelligence-model', format_version: str = '1', notes: str | None = None)
```

Modelo treinado, persistido localmente, com proveniencia completa.

### `check_compatibility`

```python
check_compatibility(artifact: router.core.prediction.model_store.ModelArtifact, effort_schema_version: str) -> None
```

Levanta ``ModelIncompatibleError`` se o modelo nao for compativel.

### `ModelStore`

```python
class ModelStore(root: str | pathlib.Path)
```

Armazenamento **local** de artefatos de modelo + registro.

## Camada 04 (intelligence): treino, avaliacao e classificacao

Treino **reproduzivel** (mesma entrada => mesma `model_version`) com baseline obrigatorio; avaliacao em conjunto separado (**temporal** e **por ciclista**), com erro/vies e criterios minimos definidos **antes** do treino; preditor **local calibrado** (so `AVAILABLE` com modelo compativel e metodo de calibracao) e **classificacao predicao x preferencia** (conservadora quando a previsao esta indisponivel). O ranking fisico **nao** e alterado.

### `TrainingError`

```python
class TrainingError(ValueError)
```

Erro de dados/parametros no treino (nunca mascarado).

### `TrainingReport`

```python
class TrainingReport(target: router.core.prediction.targets.TargetKind, method: str, feature_names: tuple[str, ...], n_total: int, n_used: int, n_dropped_missing_target: int, n_dropped_missing_features: int)
```

Resumo auditavel do que foi usado/descartado no treino.

### `TrainedModel`

```python
class TrainedModel(artifact: router.core.prediction.model_store.ModelArtifact, report: router.core.prediction.training.TrainingReport)
```

Artefato + relatorio do treino que o produziu.

### `build_training_matrix`

```python
build_training_matrix(dataset: router.core.prediction.observations.ObservationDataset, target: router.core.prediction.targets.TargetKind, feature_names: Sequence[str], *, feature_schema_version: str = '1') -> tuple[list[dict[str, float]], list[float], router.core.prediction.training.TrainingReport]
```

Monta ``(linhas, alvos, relatorio)`` a partir das observacoes reais.

### `train_constant_baseline`

```python
train_constant_baseline(dataset: router.core.prediction.observations.ObservationDataset, *, target: router.core.prediction.targets.TargetKind = <TargetKind.RPE: 'rpe'>, feature_schema_version: str = '1') -> router.core.prediction.training.TrainedModel
```

Baseline constante: media do alvo observado (sem features).

### `train_linear`

```python
train_linear(dataset: router.core.prediction.observations.ObservationDataset, *, target: router.core.prediction.targets.TargetKind = <TargetKind.RPE: 'rpe'>, feature_names: Sequence[str] = ('duration_s', 'distance_m', 'average_speed_mps', 'mechanical_work_j', 'rider_work_j', 'mean_power_w', 'climbing_m', 'descending_m', 'braking_energy_j', 'aero_loss_j', 'rolling_loss_j'), feature_schema_version: str = '1', is_baseline: bool = False, calibration_method: str | None = None, notes: str | None = None) -> router.core.prediction.training.TrainedModel
```

Treino linear por minimos quadrados (baseline ou candidato).

### `train_and_save`

```python
train_and_save(dataset: router.core.prediction.observations.ObservationDataset, store, *, target: router.core.prediction.targets.TargetKind = <TargetKind.RPE: 'rpe'>, feature_names: Sequence[str] = ('duration_s', 'distance_m', 'average_speed_mps', 'mechanical_work_j', 'rider_work_j', 'mean_power_w', 'climbing_m', 'descending_m', 'braking_energy_j', 'aero_loss_j', 'rolling_loss_j'), is_baseline: bool = True, feature_schema_version: str = '1', calibration_method: str | None = None, notes: str | None = None) -> router.core.prediction.training.TrainedModel
```

Treina (baseline ou candidato linear) e grava o artefato localmente.

### `EvaluationError`

```python
class EvaluationError(ValueError)
```

Erro de dados/parametros na avaliacao (nunca mascarado).

### `GroupMetrics`

```python
class GroupMetrics(key: str, n: int, mae: float, bias: float)
```

Erro/vies de um subgrupo (ex.: por ciclista).

### `ModelEvaluation`

```python
class ModelEvaluation(target: router.core.prediction.targets.TargetKind, split_scheme: str, n: int, mae: float, rmse: float, bias: float, per_group: tuple[router.core.prediction.model_eval.GroupMetrics, ...] = ())
```

Resultado auditavel da avaliacao de um modelo.

### `MinimalCriteria`

```python
class MinimalCriteria(max_mae: float, max_abs_bias: float, min_n: int = 1)
```

Criterios minimos definidos **antes** do treino.

### `CriteriaResult`

```python
class CriteriaResult(accepted: bool, reasons: tuple[str, ...] = ())
```

CriteriaResult(accepted: 'bool', reasons: 'tuple[str, ...]' = ())

### `evaluate_model`

```python
evaluate_model(model, observations: Sequence[router.core.prediction.observations.ObservedEffort], *, target: router.core.prediction.targets.TargetKind = <TargetKind.RPE: 'rpe'>, feature_names: Sequence[str], split_scheme: str = 'unspecified') -> router.core.prediction.model_eval.ModelEvaluation
```

Avalia MAE/RMSE/vies do modelo nas observacoes fornecidas.

### `temporal_split`

```python
temporal_split(dataset: router.core.prediction.observations.ObservationDataset, *, test_fraction: float = 0.2) -> tuple[router.core.prediction.observations.ObservationDataset, router.core.prediction.observations.ObservationDataset]
```

Separa treino/teste no tempo (teste no **futuro** do treino).

### `cyclist_split`

```python
cyclist_split(dataset: router.core.prediction.observations.ObservationDataset, *, test_fraction: float = 0.2) -> tuple[router.core.prediction.observations.ObservationDataset, router.core.prediction.observations.ObservationDataset]
```

Separa por ciclista: ciclistas de teste **nao** aparecem no treino.

### `LocalModelPredictor`

```python
class LocalModelPredictor(artifact: router.core.prediction.model_store.ModelArtifact, model: router.core.prediction.models.ConstantModel | router.core.prediction.models.LinearModel, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, effort_schema_version: str = '1')
```

``EffortPredictor`` apoiado em um artefato de modelo **local**.

### `load_predictor`

```python
load_predictor(store: router.core.prediction.model_store.ModelStore, model_version: str, *, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, effort_schema_version: str = '1') -> router.core.prediction.predictor.LocalModelPredictor
```

Atalho para :meth:`LocalModelPredictor.from_store`.

### `ClassificationError`

```python
class ClassificationError(ValueError)
```

Erro de **dados/parametros** na classificacao (nunca mascarado).

### `PreferenceVerdict`

```python
class PreferenceVerdict(str, Enum)
```

Veredito de um criterio de preferencia (tri-estado, conservador).

Valores: `SATISFIED`, `NOT_SATISFIED`, `UNKNOWN`.

### `EffortPreference`

```python
class EffortPreference(borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, acceptable_rpe: tuple[float, float] | None = None, preferred_zones: tuple[str, ...] | None = None, min_preferred_fraction: float | None = None, preferred_intensity_bands: tuple[str, ...] | None = None, min_intensity_band_fraction: float | None = None, intensity_band_basis: str = 'distance', notes: str | None = None)
```

Preferencia de esforco **declarada** pelo ciclista.

### `CriterionResult`

```python
class CriterionResult(name: str, verdict: router.core.PreferenceVerdict, reason: str, observed: float | None = None, threshold: float | None = None)
```

Resultado auditavel de **um** criterio de preferencia.

### `RouteClassification`

```python
class RouteClassification(arc_ids: tuple[str, ...], outcome: router.core.AssessmentOutcome, preference: router.core.EffortPreference | None, prediction_status: str, criteria: tuple[router.core.CriterionResult, ...] = ())
```

Classificacao final de uma rota: esforco previsto x preferencia.

### `classify_route`

```python
classify_route(assessment: router.core.RouteAssessmentView, preference: router.core.EffortPreference | None = None) -> router.core.RouteClassification
```

Classifica **uma** rota avaliada: esforco previsto x preferencia.

### `classify_routes`

```python
classify_routes(assessments: collections.abc.Iterable[router.core.RouteAssessmentView], preference: router.core.EffortPreference | None = None) -> tuple[router.core.RouteClassification, ...]
```

Classifica varias rotas **preservando a ordem de entrada**.

## Camada 04 (intelligence): estimativa de intensidade (ADR-028)

Estimativa **computacional e nao calibrada** de intensidade de uma rota (RPE na escala de Borg + cinco **faixas de intensidade**, por **distancia** e por **duracao**), derivada do resultado fisico. Espelha o preditor de RPE do `toporide` (auditado em `docs/adr/028-auditoria-rpe-toporide.md`), com **proveniencia** de cada coeficiente. **Nao** e medicao nem previsao fisiologica calibrada; **nao** preenche `PhysiologicalPrediction` e **nunca** e apresentada como zona cardiaca real.

### `INTENSITY_MODEL_ID`

```python
INTENSITY_MODEL_ID
```

Identificador do metodo de estimativa de intensidade (origem: heuristica do `toporide`).

### `INTENSITY_MODEL_VERSION`

```python
INTENSITY_MODEL_VERSION
```

Versao do metodo de estimativa de intensidade.

### `INTENSITY_BAND_DEFINITION`

```python
INTENSITY_BAND_DEFINITION
```

Definicao versionada das cinco faixas de intensidade (derivadas do Borg 6-20).

### `INTENSITY_BAND_NAMES`

```python
INTENSITY_BAND_NAMES
```

Nomes das cinco faixas de intensidade (`i1`..`i5`); **nao** sao zonas cardiacas.

### `INTENSITY_BAND_EDGES`

```python
INTENSITY_BAND_EDGES
```

Cortes superiores (Borg) das faixas: 10, 12, 14 e 16.

### `DEFAULT_INTENSITY_COEFFICIENTS`

```python
DEFAULT_INTENSITY_COEFFICIENTS
```

Coeficientes padrao da estimativa (origem: `toporide`; ver tabela de proveniencia).

### `IntensityCoefficients`

```python
class IntensityCoefficients(base_rpe: float = 6.0, scale_gain: float = 6.0, ftp_reference_kg: float = 75.0, grade_free_pct: float = 5.0, grade_exponent: float = 1.3, traffic_weight: float = 1.5, fatigue_weight: float = 1.5, fatigue_amplitude: float = 1.5, fatigue_window_s: float = 1200.0, fatigue_sensitivity: float = 1.0)
```

Coeficientes da estimativa de intensidade, com origem declarada.

### `IntensitySegment`

```python
class IntensitySegment(distance_m: float, duration_s: float, rider_energy_j: float, grade_pct: float, traffic_stress: float)
```

Observacao de um trecho (arco) para a estimativa de intensidade.

### `IntensityEstimate`

```python
class IntensityEstimate(borg_scale: router.core.BorgScale, mean_rpe: float, peak_rpe: float, band_distance_fraction: dict[str, float], band_duration_fraction: dict[str, float] | None, distance_m: float, duration_s: float, method_id: str = 'toporide-heuristic', model_version: str = '1', band_definition: str = 'borg-intensity-bands@1', segment_rpes: tuple[float, ...] = (), segment_bands: tuple[str, ...] = ())
```

Estimativa de intensidade de uma rota — **nao calibrada**.

### `hw_stress`

```python
hw_stress(highway: str | None) -> float
```

Estresse de trafego (ordinal) de uma classe de via OSM.

### `band_for_rpe`

```python
band_for_rpe(rpe: float) -> str
```

Nome da faixa de intensidade para um RPE (Borg).

### `segment_rpe`

```python
segment_rpe(segment: router.core.prediction.intensity.IntensitySegment, *, mass_kg: float, ftp_w: float, accumulated_j: float = 0.0, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, coefficients: router.core.prediction.intensity.IntensityCoefficients = IntensityCoefficients(base_rpe=6.0, scale_gain=6.0, ftp_reference_kg=75.0, grade_free_pct=5.0, grade_exponent=1.3, traffic_weight=1.5, fatigue_weight=1.5, fatigue_amplitude=1.5, fatigue_window_s=1200.0, fatigue_sensitivity=1.0)) -> float
```

RPE (Borg) estimado de um trecho, com a fadiga acumulada informada.

### `estimate_intensity`

```python
estimate_intensity(segments: collections.abc.Sequence[router.core.prediction.intensity.IntensitySegment], *, mass_kg: float, ftp_w: float, borg_scale: router.core.BorgScale = <BorgScale.RPE_6_20: '6-20'>, coefficients: router.core.prediction.intensity.IntensityCoefficients = IntensityCoefficients(base_rpe=6.0, scale_gain=6.0, ftp_reference_kg=75.0, grade_free_pct=5.0, grade_exponent=1.3, traffic_weight=1.5, fatigue_weight=1.5, fatigue_amplitude=1.5, fatigue_window_s=1200.0, fatigue_sensitivity=1.0)) -> router.core.prediction.intensity.IntensityEstimate
```

Estima RPE e faixas de intensidade de uma rota (por distancia e duracao).

### `segments_from_traversal`

```python
segments_from_traversal(path: router.core.physics.models.TraversalPath, arcs: collections.abc.Sequence) -> tuple[router.core.prediction.intensity.IntensitySegment, ...]
```

Monta os trechos de intensidade a partir de um ``TraversalPath``.
