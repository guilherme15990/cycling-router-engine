"""Gera ``docs/reference/api.md`` a partir de ``api.__all__`` (assinaturas exatas via ``inspect``).

As assinaturas sao extraidas por introspeccao, de modo que o documento nunca
diverge do codigo. O texto de cada camada e curado aqui. Rode com:

    python scripts/generate_api_reference.py

O script falha se algum simbolo de ``api.__all__`` nao estiver em nenhuma
camada, evitando que a referencia fique incompleta ao adicionar nomes novos.
"""

from __future__ import annotations

import inspect
import re
import sys
from enum import EnumMeta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from router.interfaces import api  # noqa: E402

# Ordem e texto das camadas. Os nomes vem exatamente de ``api.__all__``.
LAYERS: list[tuple[str, str, list[str]]] = [
    (
        "Grafo ciclavel: modelo",
        "Estruturas centrais do grafo: nos, segmentos, arcos direcionadas e o "
        "conteiner ``RoutingGraph``.",
        ["BicycleAccess", "RoutingGraph", "Arc", "Link", "Segment", "ElevationQuality", "ElevationSample", "Node", "OBSERVED_ELEVATION_QUALITIES"],
    ),
    (
        "Grafo ciclavel: superficie",
        "Classificacao observavel de superficie e a politica de prioridade "
        "(ordinal) do roteador.",
        [
            "OSM_SURFACE_MAP",
            "OSM_TRACKTYPE_MAP",
            "SURFACE_PRIORITY",
            "Surface",
            "SurfaceStatus",
            "surface_from_tags",
            "surface_observation",
            "surface_priority",
        ],
    ),
    (
        "Grafo ciclavel: restricoes de acesso",
        "Restricoes de acesso preservadas das tags OSM, sem avaliacao temporal.",
        [
            "AccessRule",
            "RestrictionSource",
            "TurnRestriction",
            "parse_conditional_restrictions",
            "turn_restriction_from_relation",
        ],
    ),
    (
        "Grafo ciclavel: simplificacao",
        "Contracao de nos de grau 2 sem perder atributos fisicos nem de acesso.",
        ["SimplifyOptions", "SimplifyStats", "simplify_graph"],
    ),
    (
        "Snapping (grafo de consulta)",
        "Conexao de origem/destino a rede: opcoes, candidatos, resultado e o "
        "grafo temporario de consulta.",
        [
            "QueryGraph",
            "SnapCandidate",
            "SnapError",
            "SnapOptions",
            "SnapResult",
            "SpatialSnapper",
            "build_query_graph",
        ],
    ),
    (
        "Fisica: travessia",
        "Modelos do ciclista e da bicicleta, perfis de arco e a simulacao de "
        "travessia (tempo, energia e velocidade).",
        [
            "BicycleModel",
            "CyclistModel",
            "ArcProfile",
            "Environment",
            "PhysicalTraversal",
            "PhysicsRouteMetrics",
            "SimulationMode",
            "SimulationState",
            "SimulationStatus",
            "ElevationUncertainty",
            "EnergyBalance",
            "TraversalPath",
            "TraversalResult",
            "traverse_arc",
            "classify_status",
            "worst_status",
        ],
    ),
    (
        "Fisica: features de esforco",
        "Contrato imutavel e versionado de caracteristicas de esforco derivadas "
        "da fisica (a partir do resultado de travessia), sem ML nem roteamento.",
        [
            "EFFORT_FEATURES_VERSION",
            "EffortFeatures",
            "extract_effort_features",
        ],
    ),
    (
        "Fisica: energia e bateria",
        "Modelo energetico (energia mecanica -> eletrica em Wh) e viabilidade de "
        "bateria. O perfil eletrico usa energia **mecanica** na fronteira Pareto; a "
        "conversao para Wh e um estagio posterior a selecao.",
        [
            "BatteryConfig",
            "BatteryFeasibility",
            "CALIBRATED_LEVEL",
            "EnergyEstimate",
            "EnergyModel",
            "GENERIC_LEVEL",
            "GenericEnergyConfig",
            "JOULES_PER_WH",
            "PERSONALIZED_LEVEL",
            "assess_battery",
        ],
    ),
    (
        "Fisiologia: escala, zonas e metabolico",
        "Contratos fisiológicos explícitos: escala de Borg (padrão 6–20; CR10 "
        "suportada), zonas cardíacas por definição identificada (sem percentuais "
        "implícitos) e energia metabólica só com parâmetros calibrados.",
        [
            "PHYSIOLOGY_VERSION",
            "BorgScale",
            "DEFAULT_BORG_SCALE",
            "DISTRIBUTION_TOLERANCE",
            "HeartRateZoneDefinition",
            "HeartRateZoneModel",
            "MetabolicParameters",
            "metabolic_energy_j",
        ],
    ),
    (
        "Fisiologia: contrato de previsao",
        "Contrato de previsão fisiológica — sempre identificada como estimativa "
        "(`source=\"prediction\"`, `is_measurement=False`), nunca como medição. "
        "`PredictionStatus` distingue ausência de preditor, modelo não calibrado, "
        "dados insuficientes e contrato incompatível.",
        [
            "PhysiologicalPrediction",
            "PredictionContext",
            "PredictionStatus",
            "RiderProfile",
        ],
    ),
    (
        "ML: inferencia",
        "Fronteira de inferência: consome `EffortFeatures` e devolve "
        "`PhysiologicalPrediction`. O preditor padrão declara a ausência de "
        "modelo calibrado em vez de inventar valores.",
        [
            "EffortPredictor",
            "MetabolicEnergyPredictor",
            "UnavailablePredictor",
            "predict_effort",
        ],
    ),
    (
        "Busca: objetivos e perfis",
        "Objetivos, perfis declarativos (fastest/shortest/flattest/balanced/"
        "electric), tolerancias e configuracao de diversidade.",
        [
            "BalancedWeights",
            "DiversityConfig",
            "DiversityFallback",
            "DiversityMetric",
            "DiversityRelaxation",
            "MetricSource",
            "Objective",
            "ObjectiveEvaluator",
            "ObjectiveSet",
            "ObjectiveValues",
            "PROFILE_CONFIG",
            "PROFILE_IDS",
            "PROFILE_NAMES",
            "PROFILE_OBJECTIVES",
            "PROFILES",
            "RankingStrategy",
            "RankingTolerance",
            "RouteObjective",
            "RouteObjectiveEvaluator",
            "RouteProfile",
            "ScoreReference",
            "SearchProfile",
            "dominates",
            "epsilon_dominates",
            "objectives_for",
            "profile_for",
            "route_profile_for",
        ],
    ),
    (
        "Busca: estado, fronteira e A*",
        "Nucleo do A* multiobjetivo: estado, dominancia, fronteira Pareto, fila "
        "lexicografica e heuristica admissivel.",
        [
            "FrontierManager",
            "FrontierMap",
            "Heuristic",
            "HeuristicBounds",
            "LexicographicQueue",
            "MultiObjectiveAStar",
            "ParetoFrontier",
            "PhysicalState",
            "PhysicalTraversalModel",
            "SearchConfig",
            "SearchResult",
            "SearchState",
            "SpeedBucketDominance",
            "TraversalModel",
            "UNKNOWN_SPEED",
            "advance_state",
            "initial_state",
            "model_max_speed",
            "route_arcs",
        ],
    ),
    (
        "Busca: estimador aproximado (incremento 8)",
        "Estimador fisico por equilibrio de velocidade para a busca, com "
        "relatorio de erro/custo contra o simulador completo e comparacao de "
        "ordenacao de rotas. **Nao** substitui o simulador nem presume "
        "admissibilidade.",
        [
            "ArcEstimateError",
            "ComparisonReport",
            "RankingComparison",
            "SearchRankingComparison",
            "SteadyStateTraversalModel",
            "compare_rankings",
            "compare_search_rankings",
            "compare_traversals",
            "estimate_arc",
        ],
    ),
    (
        "Busca: contrato de circulacao (guardrails)",
        "Contrato de circulacao (ADR-026): veredito tri-estado "
        "(`ALLOW`/`FORBID`/`UNKNOWN`) e politica de produto (`UNKNOWN -> "
        "ALLOW`, ainda reportado), decisao de acesso do arco, decisao de conversao (via-node e "
        "via-way ordenado), validador independente de rota e os nos com "
        "restricao de conversao. **Nao** altera a fisica.",
        [
            "CirculationPolicy",
            "Decision",
            "DecisionReason",
            "RouteValidation",
            "Verdict",
            "arc_decision",
            "constrained_nodes",
            "transition_decision",
            "validate_route",
        ],
    ),
    (
        "Busca: reconstrucao e avaliacao de rotas",
        "Reconstrucao da fronteira em rotas e avaliacao de objetivos.",
        [
            "SearchRoute",
            "evaluate",
            "evaluate_metrics",
            "evaluate_route",
            "route_from_state",
            "routes_from_result",
        ],
    ),
    (
        "Selecao: candidatas, ranking e diversidade",
        "Pool de candidatas (com deduplicacao), ranking por perfil e diversidade.",
        [
            "CandidateMetrics",
            "DeduplicationMode",
            "ProfileRanker",
            "RankedCandidate",
            "RankedRoute",
            "RouteCandidate",
            "RouteCandidatePool",
            "RouteDeduplicator",
            "RouteDiversity",
            "arc_jaccard",
            "is_too_similar",
            "order_routes",
            "profile_score",
            "rank_candidates",
            "rank_routes",
            "route_diversity",
            "route_similarity",
            "shared_distance_ratio",
        ],
    ),
    (
        "Selecao: N rotas, explicacao e pipeline",
        "Escolha das N rotas (com fallback de diversidade), explicacao e o "
        "contrato de alto nivel ``search_profile``.",
        [
            "ProfileSearchRequest",
            "ProfileSearchResponse",
            "RouteExplanation",
            "RouteExplainer",
            "RouteSelectionRequest",
            "RouteSelectionResult",
            "RouteSelector",
            "SelectedRoute",
            "SelectionConfig",
            "SelectionMetadata",
            "SelectionReason",
            "SelectionResult",
            "explain_route",
            "search_profile",
            "select_candidates",
            "select_from_result",
            "select_routes",
        ],
    ),
    (
        "Energia da rota (pos-selecao)",
        "Estimativa de energia eletrica de uma rota, alinhada as rotas "
        "selecionadas.",
        ["RouteEnergyEstimate", "estimate_route_energy", "estimate_routes_energy"],
    ),
    (
        "Avaliacao: rota completa e classificacao (Camada 05)",
        "Avaliacao fisica **completa** de uma rota (simulador, nao o estimador) "
        "-> `EffortFeatures` -> previsao fisiologica, com validacao de "
        "circulacao e classificacao explicita (`REJECTED`/`PHYSICAL_ONLY`/"
        "`PHYSIOLOGICAL`). Sem modelo calibrado, declara a ausencia; nunca "
        "inventa valor nem recalcula fisica.",
        [
            "AssessmentOutcome",
            "RouteAssessment",
            "RouteAssessmentError",
            "assess_candidate",
            "assess_candidates",
            "assess_route",
            "assess_search_route",
        ],
    ),
    (
        "Seguranca da rota e avaliacao final (Camada 05, ADR-029)",
        "Estado de **seguranca** operacional de uma rota (`VALIDATED`/`DEGRADED`/"
        "`REJECTED`), **independente** de `AssessmentOutcome`, e a **avaliacao "
        "final** das candidatas. `evaluate_candidates` **reutiliza** as avaliacoes "
        "ja calculadas (sem nova fisica nem fisiologia) e escolhe a primeira rota "
        "**elegivel**; `REJECTED` nunca e recomendada. Toda rejeicao carrega um "
        "motivo auditavel; ausencia de modelo calibrado e a estimativa de "
        "intensidade entram como **advertencias** que nao alteram o estado. "
        "`energy_consistency` agrega o balanco energetico ja calculado (sem "
        "equacao nova).",
        [
            "EnergyConsistency",
            "EvaluatedRoute",
            "EvaluationReport",
            "SafetyReason",
            "ReasonKind",
            "RouteSafetyAssessment",
            "RouteSafetyState",
            "classify_safety",
            "energy_consistency",
            "evaluate_candidates",
        ],
    ),
    (
        "Consulta offline completa (planejamento)",
        "Orquestracao de todo o fluxo offline numa chamada: **corredor de busca** "
        "(`planning._search_zone`: box dos pinos com margens automaticas, ou o "
        "extent do grafo em `use_full_graph`; recorta a zona via "
        "`envelope.graph_for_bbox`) -> **snapping** -> **busca unica** "
        "(`search.MultiObjectiveAStar`) -> **avaliacao fisica completa** "
        "(`evaluation.assess_candidates`) -> **previsao** "
        "(`ml.predict_effort`, preditor local) -> **classificacao** "
        "(`intelligence.classify_routes`). O corredor e definido **uma vez** e "
        "limita a busca (sem crescimento de zona, como no `toporide`); o box final "
        "cobre os pinos e o tracado das rotas devolvidas e e o que limita a "
        "altimetria. Sem modelo calibrado, cada rota fica `physical_only` com RPE "
        "`None` — nunca um valor inventado.",
        [
            "PlannedRoute",
            "RoutePlanResult",
            "plan_offline_routes",
        ],
    ),
    (
        "Integracao com o aplicativo (ADR-030, Fase 3)",
        "Superficie **curada** para o consumidor externo: `plan_routes` resolve "
        "multi-pernas (`stops`) no nucleo e devolve o envelope `RoutePlan`; "
        "`integration` reexporta poucos nomes (contrato, grafo, modelos de "
        "entrada e guard offline) e preserva um caminho por operacao.",
        [
            "plan_routes",
            "integration",
        ],
    ),
    (
        "Contrato publico de rota (v1)",
        "Fronteira de **transporte** do motor (ADR-030): tipos versionados, "
        "serializaveis e independentes dos DTOs internos. O envelope e "
        "**estrito** (`format`/`version` obrigatorios, `allow_nan=False`, sem "
        "`default=str`), a ausencia e explicita (`None`) e a geometria vem por "
        "ponto (`dist_m` monotonico). `snake_case` no Python/JSON; adaptadores "
        "traduzem para `camelCase`.",
        [
            "CONTRACT_FORMAT",
            "CONTRACT_VERSION",
            "INTENSITY_BANDS",
            "RouteContractError",
            "BBox",
            "RouteRequest",
            "RoutePoint",
            "RouteMetrics",
            "RouteML",
            "Reason",
            "RouteSafety",
            "RouteProvenance",
            "Route",
            "Zone",
            "RoutePlan",
            "dumps",
            "loads",
            "write_plan",
            "read_plan",
        ],
    ),
    (
        "Operacao offline (guard de rede)",
        "Guard que **proibe** acesso a rede durante a execucao (ADR-027 §0): "
        "buscar, simular, carregar modelo, prever e classificar rodam "
        "**localmente**, sem internet, sem nuvem e sem API externa. Uma "
        "tentativa de conexao bloqueada falha de forma explicita "
        "(`OfflineViolation`), nunca silenciosa.",
        [
            "OfflineViolation",
            "is_network_disabled",
            "offline",
        ],
    ),
    (
        "Fonte geografica local: leitor OSM.PBF",
        "Leitor **offline** de `OpenStreetMap PBF` (`.osm.pbf`), em Python puro "
        "(sem protobuf/osmium), com leitura em duas passagens de memoria "
        "limitada. E a unica fonte geografica do motor (ADR-027 §0): le vias, "
        "nos e relacoes `type=restriction` de um arquivo local, sem ingestao "
        "online.",
        [
            "PbfError",
            "PbfHeader",
            "PbfStats",
            "read_header",
            "read_osm_pbf",
        ],
    ),
    (
        "Elevacao local: Terrarium (offline)",
        "Elevacao **offline** por tiles Terrarium locais "
        "(`{z}/{x}/{y}.png`), com decodificacao PNG em Python puro "
        "(`h = R*256 + G + B/256 - 32768`). `elevation_tiles` faz o "
        "**preparo de rede** (download dos tiles para uso offline).",
        [
            "TerrariumElevation",
            "TerrariumTileError",
            "fetch_terrarium_tiles",
            "lonlat_to_tile",
            "tiles_for_bbox",
        ],
    ),
    (
        "Envelope de consulta: zona de busca + altimetria (offline)",
        "Box **dinamico** de uma consulta: parte do retangulo dos pinos "
        "(origem + paradas + destino) com margem **por eixo** e cresce para "
        "cobrir o **tracado real de todas** as rotas (`bbox_with_routes` / "
        "`query_bbox`). Como o aplicativo e **integralmente offline**, este "
        "box limita a **busca** (`graph_for_bbox`) e a **altimetria** — o "
        "grafo carregado e a cidade inteira, mas a busca nunca o percorre "
        "por completo. Portado de `toporide` (`src/domain/geo.ts`).",
        [
            "M_PER_DEG_LAT",
            "m_per_deg_lon",
            "expand_bbox",
            "corridor_bbox_of",
            "auto_box_pads",
            "bbox_size_km",
            "bbox_of_routes",
            "union_bbox",
            "bbox_with_routes",
            "ensure_min_span",
            "query_bbox",
            "bbox_contains",
            "bbox_intersects",
            "graph_for_bbox",
            "scale_bbox",
            "adaptive_zone",
        ],
    ),
    (
        "Camada 04 (intelligence): alvos e observacoes",
        "Contrato de **observacao real** (alvo observado + identidade "
        "pseudonimizada + qualidade/proveniencia + `dataset_version`), "
        "persistido **localmente**, e o contrato de **alvos** (RPE na escala "
        "Borg identificada; zonas cardiacas por definicao individualizada e "
        "metodo **nomeado/versionado**). Sem alvo observado, a observacao e "
        "inutilizavel — nunca imputada.",
        [
            "TARGETS_VERSION",
            "TargetKind",
            "ZoneDerivationUnavailable",
            "HeartRateZoneDerivationMethod",
            "ZONE_DERIVATION_METHODS",
            "register_zone_derivation",
            "get_zone_derivation",
            "derive_heart_rate_zones",
            "OBSERVATIONS_VERSION",
            "DATASET_FORMAT",
            "DATASET_FORMAT_VERSION",
            "ObservationError",
            "MeasurementQuality",
            "Provenance",
            "CyclistId",
            "pseudonymize",
            "ObservedEffort",
            "ObservationDataset",
            "save_dataset",
            "load_dataset",
            "build_dataset",
        ],
    ),
    (
        "Camada 04 (intelligence): features, modelos e artefato local",
        "Features de treino e de servico **as mesmas** (sem alvo, sem "
        "vazamento); modelos base auditaveis; e o **artefato de modelo local** "
        "versionado (registro `model_version` <-> `dataset_version` <-> schema "
        "de features <-> alvo), carregado **do disco** com **verificacao de "
        "compatibilidade**. Modelos ausentes/incompativeis/corrompidos sao "
        "tratados de forma explicita — nunca por download.",
        [
            "FEATURES_VERSION",
            "ALL_FEATURE_NAMES",
            "DEFAULT_FEATURE_NAMES",
            "feature_row",
            "select_row",
            "missing_features",
            "ModelError",
            "ConstantModel",
            "LinearModel",
            "model_from_payload",
            "fit_constant",
            "fit_linear",
            "MODEL_ARTIFACT_FORMAT",
            "MODEL_ARTIFACT_VERSION",
            "REGISTRY_FILENAME",
            "ModelArtifactError",
            "ModelNotFoundError",
            "ModelIncompatibleError",
            "ModelFormatError",
            "ModelArtifact",
            "check_compatibility",
            "ModelStore",
        ],
    ),
    (
        "Camada 04 (intelligence): treino, avaliacao e classificacao",
        "Treino **reproduzivel** (mesma entrada => mesma `model_version`) com "
        "baseline obrigatorio; avaliacao em conjunto separado (**temporal** e "
        "**por ciclista**), com erro/vies e criterios minimos definidos "
        "**antes** do treino; preditor **local calibrado** (so `AVAILABLE` com "
        "modelo compativel e metodo de calibracao) e **classificacao predicao "
        "x preferencia** (conservadora quando a previsao esta indisponivel). O "
        "ranking fisico **nao** e alterado.",
        [
            "TrainingError",
            "TrainingReport",
            "TrainedModel",
            "build_training_matrix",
            "train_constant_baseline",
            "train_linear",
            "train_and_save",
            "EvaluationError",
            "GroupMetrics",
            "ModelEvaluation",
            "MinimalCriteria",
            "CriteriaResult",
            "evaluate_model",
            "temporal_split",
            "cyclist_split",
            "LocalModelPredictor",
            "load_predictor",
            "ClassificationError",
            "PreferenceVerdict",
            "EffortPreference",
            "CriterionResult",
            "RouteClassification",
            "classify_route",
            "classify_routes",
        ],
    ),
    (
        "Camada 04 (intelligence): estimativa de intensidade (ADR-028)",
        "Estimativa **computacional e nao calibrada** de intensidade de uma rota "
        "(RPE na escala de Borg + cinco **faixas de intensidade**, por "
        "**distancia** e por **duracao**), derivada do resultado fisico. Espelha "
        "o preditor de RPE do `toporide` (auditado em `docs/adr/"
        "028-auditoria-rpe-toporide.md`), com **proveniencia** de cada "
        "coeficiente. **Nao** e medicao nem previsao fisiologica calibrada; "
        "**nao** preenche `PhysiologicalPrediction` e **nunca** e apresentada "
        "como zona cardiaca real.",
        [
            "INTENSITY_MODEL_ID",
            "INTENSITY_MODEL_VERSION",
            "INTENSITY_BAND_DEFINITION",
            "INTENSITY_BAND_NAMES",
            "INTENSITY_BAND_EDGES",
            "DEFAULT_INTENSITY_COEFFICIENTS",
            "IntensityCoefficients",
            "IntensitySegment",
            "IntensityEstimate",
            "hw_stress",
            "band_for_rpe",
            "segment_rpe",
            "estimate_intensity",
            "segments_from_traversal",
        ],
    ),
]

# Resumos curtos para constantes de modulo (valores, nao classes/funcoes).
CONSTANT_NOTES: dict[str, str] = {
    "integration": "Modulo de integracao: superficie curada para o app (ADR-030 Fase 3).",
    "OSM_SURFACE_MAP": "Mapeia valores de ``surface=*`` do OSM para ``Surface``.",
    "OSM_TRACKTYPE_MAP": "Mapeia ``tracktype=grade1..grade5`` para ``Surface``.",
    "OBSERVED_ELEVATION_QUALITIES": "Estados de elevacao sustentados por fonte (medida, DEM ou declarada).",
    "SURFACE_PRIORITY": "Prioridade ordinal por superficie (1 melhor ... 6 desconhecida).",
    "JOULES_PER_WH": "Fator de conversao de energia (1 Wh = 3600 J).",
    "GENERIC_LEVEL": "Nivel de estimativa com valores de referencia internos.",
    "PERSONALIZED_LEVEL": "Nivel de estimativa com parametros do veiculo informados.",
    "CALIBRATED_LEVEL": "Nivel de estimativa calibrado com dados reais.",
    "PROFILE_IDS": "Ids dos perfis de selecao (``profiles.py``).",
    "PROFILE_NAMES": "Ids dos perfis de busca, em ordem estavel (``objectives.py``).",
    "PROFILE_OBJECTIVES": "``ObjectiveSet`` (objetivos Pareto) por id de perfil.",
    "PROFILES": "``SearchProfile`` por id de perfil.",
    "PROFILE_CONFIG": "``RouteProfile`` (selecao) por id de perfil.",
    "UNKNOWN_SPEED": "Sentinela de velocidade inicial desconhecida na busca.",
    "PHYSIOLOGY_VERSION": "Versao do contrato de previsao fisiologica.",
    "DEFAULT_BORG_SCALE": "Escala de Borg padrao do projeto: **6-20** (original).",
    "DISTRIBUTION_TOLERANCE": "Tolerancia numerica para \"a distribuicao soma 1\".",
    "TARGETS_VERSION": "Versao do contrato de alvos (Camada 04).",
    "OBSERVATIONS_VERSION": "Versao do contrato de observacao (Camada 04).",
    "DATASET_FORMAT": "Identificador do formato de arquivo do conjunto de observacoes.",
    "DATASET_FORMAT_VERSION": "Versao do formato de arquivo do conjunto de observacoes.",
    "FEATURES_VERSION": "Versao do contrato de montagem de features (treino/servico).",
    "MODEL_ARTIFACT_FORMAT": "Identificador do formato do artefato de modelo local.",
    "MODEL_ARTIFACT_VERSION": "Versao do formato do artefato de modelo local.",
    "REGISTRY_FILENAME": "Nome do arquivo de registro dentro do armazenamento local.",
    "ALL_FEATURE_NAMES": "Todas as features que a Camada 04 sabe montar (ordem estavel).",
    "DEFAULT_FEATURE_NAMES": "Conjunto padrao: apenas features **sempre** presentes na fisica.",
    "ZONE_DERIVATION_METHODS": "Metodos de derivacao de zonas registrados (vazio por padrao).",
    "INTENSITY_MODEL_ID": "Identificador do metodo de estimativa de intensidade (origem: heuristica do `toporide`).",
    "INTENSITY_MODEL_VERSION": "Versao do metodo de estimativa de intensidade.",
    "INTENSITY_BAND_DEFINITION": "Definicao versionada das cinco faixas de intensidade (derivadas do Borg 6-20).",
    "INTENSITY_BAND_NAMES": "Nomes das cinco faixas de intensidade (`i1`..`i5`); **nao** sao zonas cardiacas.",
    "INTENSITY_BAND_EDGES": "Cortes superiores (Borg) das faixas: 9, 12, 14 e 16.",
    "DEFAULT_INTENSITY_COEFFICIENTS": "Coeficientes padrao da estimativa (origem: `toporide`; ver tabela de proveniencia).",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Prefixos de modulo interno que poluem as assinaturas (a fachada reexporta os
# nomes curtos, entao ``graph.surface.Surface`` sai como ``Surface``).
_MODULE_PREFIX = re.compile(
    r"\b(?:graph|search|physical|osm|physiology|ml|evaluation|intelligence"
    r"|elevation|elevation_tiles|offline)(?:\.[a-z_]+)+\."
)


def tidy(text: str) -> str:
    return _MODULE_PREFIX.sub("", text)


def own_doc(obj: object) -> str:
    """Docstring propria (nao herdada), limpa; vazio se ausente."""
    doc = getattr(obj, "__doc__", None)
    return inspect.cleandoc(doc) if doc else ""


def _params(obj: object) -> str:
    """Assinatura de parametros, com anotacoes resolvidas quando possivel."""
    try:
        signature = str(inspect.signature(obj, eval_str=True))
    except (NameError, TypeError, ValueError):
        try:
            signature = str(inspect.signature(obj))
        except (TypeError, ValueError):
            return ""
        # Remove as aspas das anotacoes quando nao foi possivel resolve-las.
        signature = re.sub(r"'([^']*)'", r"\1", signature)
    # Remove uma anotacao de retorno residual (ex.: ``-> None`` em classes).
    return tidy(re.sub(r"\s*->.*$", "", signature).strip())


def render_signature(name: str, obj: object) -> str:
    if inspect.isclass(obj):
        bases = [base for base in obj.__bases__ if base is not object]
        base_text = f"({', '.join(base.__name__ for base in bases)})" if bases else ""
        if isinstance(obj, EnumMeta):
            return f"class {name}{base_text}"
        params = _params(obj)
        params = "" if params in {"()", "(*args, **kwargs)"} else params
        return f"class {name}{base_text}{params}"
    if inspect.isroutine(obj):
        try:
            return tidy(f"{name}{inspect.signature(obj, eval_str=True)}")
        except (NameError, TypeError, ValueError):
            signature = str(inspect.signature(obj))
            return tidy(re.sub(r"'([^']*)'", r"\1", f"{name}{signature}"))
    return name


def render_symbol(name: str, obj: object) -> list[str]:
    lines = [f"### `{name}`", "", "```python", render_signature(name, obj), "```", ""]

    if inspect.isclass(obj) or inspect.isroutine(obj):
        text = own_doc(obj).split("\n\n", 1)[0].replace("\n", " ").strip()
    else:
        text = CONSTANT_NOTES.get(name, "")
    if text:
        lines.append(text)
        lines.append("")
    if isinstance(obj, EnumMeta):
        members = ", ".join(f"`{member.name}`" for member in obj)
        lines.append(f"Valores: {members}.")
        lines.append("")
    return lines


def main() -> int:
    declared = {name for _, _, names in LAYERS for name in names}
    exported = set(api.__all__)
    if declared != exported:
        missing = sorted(exported - declared)
        extra = sorted(declared - exported)
        raise SystemExit(f"camadas desatualizadas; faltam={missing} sobram={extra}")

    out: list[str] = [
        "# Referencia da API",
        "",
        "> Gerado automaticamente por `scripts/generate_api_reference.py`. Nao edite a mao.",
        "",
        f"Versao: **{api.__version__}** - {len(api.__all__)} simbolos publicos "
        "(`api.__all__`).",
        "",
        "Todas as assinaturas sao extraidas do codigo por introspeccao. A "
        "descricao de cada camada e curada; o resumo de cada simbolo vem da "
        "propria docstring (use `help(obj)` para o texto completo).",
        "",
        "## Sumario",
        "",
    ]
    for title, _, _ in LAYERS:
        out.append(f"- [{title}](#{slug(title)})")
    out.append("")

    for title, intro, names in LAYERS:
        out.append(f"## {title}")
        out.append("")
        out.append(intro)
        out.append("")
        for name in names:
            out.extend(render_symbol(name, getattr(api, name)))

    destination = ROOT / "docs" / "reference" / "api.md"
    destination.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    print(f"escrito {destination} ({len(api.__all__)} simbolos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
