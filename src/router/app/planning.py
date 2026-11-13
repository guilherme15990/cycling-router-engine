"""Consulta offline completa: zona -> busca -> fisica -> ML -> box.

Este modulo e a **orquestracao** do fluxo offline declarado nos ADRs 024-028. Ele
nao reimplementa nada: junta componentes ja existentes numa unica chamada,
**espelhando a ordem de eventos do `toporide`** (ADR-028 D7): a ML roda em
**toda** rota, e o **box e calculado por ultimo**, a partir das rotas
**devolvidas** (recomendada + alternativas).

Ordem de eventos (espelhada de `toporide/.../routePipeline.ts` +
`routeWorker.ts`):

0. **MODELO (roteamento)** — opcional; carregaria o modelo de **roteamento** do
   cache local e, na ausencia, o treinaria de dados **computacionalmente
   gerados**. Hoje a busca usa apenas o A\\* Pareto (nenhum roteador neural
   implementado), entao este passo e um ponto de extensao documentado;
1. **CANDIDATAS** — ``_search_zone`` define **uma vez** o box do corredor
   (pins + margens automaticas) e recorta a zona; a busca
   ``search.MultiObjectiveAStar`` (perfil) roda **uma unica vez** nessa zona,
   devolvendo o tracado real de **todas** as rotas da fronteira. Nao ha
   crescimento iterativo nem segunda busca (como no ``toporide``); um corredor
   que nao conecte origem e destino resulta em ``reached=False``;
2. **GUARDRAILS** — validacao de circulacao (``search.circulation``);
3. **FISICA COMPLETA** — ``evaluation.assess_candidates`` reavalia cada
   candidata no **simulador completo** e extrai ``EffortFeatures``;
4. **ML (sempre)** — ``intelligence.intensity`` estima RPE + faixas de
   intensidade (por distancia e por duracao) para **cada** rota. A estimativa e
   **computacional e nao calibrada** (ADR-028): ela **nao** preenche o contrato
   fisiologico (``PhysiologicalPrediction``), que permanece para evolucao futura
   com modelo calibrado;
5. **CLASSIFICACAO** — ``intelligence.classify_routes`` (fisica + intensidade +
   preferencia declarada);
6. **BOX** — ``geo.query_bbox(pins, tracado das rotas DEVOLVIDAS)``. O box
   final limita a altimetria/exibicao e cobre a rota **recomendada** ("pick" da
   ML = primeira na ordem do perfil) **e** as alternativas do A\\*.

Honestidade (ADR-027/ADR-028):

* a **inferencia e local**: o preditor carrega um artefato **do disco** e nunca
  acessa a rede; ``plan_offline_routes`` **nao** baixa nada;
* as **zonas cardiacas nao** sao previstas — ``LocalModelPredictor`` so produz
  RPE; a distribuicao de zonas permanece ``None`` e a ausencia e declarada;
* a **estimativa de intensidade** e separada, deterministica, **nao calibrada** e
  **nao** e apresentada como medicao nem como zona cardiaca real;
* as metricas da busca (modo busca) e as metricas da avaliacao (simulador
  completo) sao mantidas **separadas** e ambas expostas.

Ref.: ADR-025 §10, ADR-026 §3/§5, ADR-027 §0/§3/§6, ADR-028 §D7.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, replace
from collections.abc import Callable

from router.core.data.envelope import graph_for_bbox
from router.core.evaluation.assessment import (
    AssessmentOutcome,
    RouteAssessment,
    assess_candidates,
)
from router.core.evaluation.safety import (
    RouteSafetyAssessment,
    RouteSafetyState,
    evaluate_candidates,
)
from router.core.data.geo import (
    Coord,
    GeoBBox,
    cumulative_distances,
    expand_bbox,
    query_bbox,
)
from router.core.data.graph.model import RoutingGraph
from router.core.evaluation.classification import (
    EffortPreference,
    RouteClassification,
    classify_routes,
)
from router.core.prediction.intensity import (
    DEMAND_BAND_NAMES,
    IntensityEstimate,
    band_for_rpe,
    demand_band_for_ratio,
    rpe_label,
)
from router.core.prediction.ml import EffortPredictor
from router.core.prediction.rider import rider_model_from_cyclist
from router.core.prediction.traffic import arterial_entries
from router.core.physics.bicycle import BicycleModel
from router.core.physics.cyclist import CyclistModel
from router.core.physics.demand import (
    DEMAND_MODEL_VERSION,
    ArcDemandField,
    DemandScenario,
)
from router.core.physics.environment import Environment
from router.core.physics.models import SimulationState
from router.core.physics.pacing import (
    PACING_POLICY,
    DEFAULT_W_PRIME_J,
    PacingParameterSource,
    ReferencePacing,
)
from router.core.physics.traversal import PhysicalTraversal
from router.core.physiology import (
    ParameterSource,
    PhysiologicalPrediction,
    PredictionContext,
    PredictionStatus,
    RiderProfile,
)
from router.core.search import (
    PhysicalTraversalModel,
    RouteCandidate,
    RouteCandidatePool,
    RouteProfile,
    RouteDeduplicator,
    arc_jaccard,
    route_profile_for,
    routes_from_result,
    select_distinct_routes,
)
from router.core.search.candidate_search import search_candidates
from router.core.search.route_set import (
    IncrementalSimilarityMatrix,
    similarity_cache_scope,
)
from router.contracts import (
    DemandScenarioRef,
    Route,
    RouteDemand,
    RoutePlan,
    RoutePoint,
    RouteTelemetry,
)
from router.core.data.snapping import QueryGraph, SnapError, SnapOptions, build_query_graph

#: Ritmo planejado padrao (km/h) quando o chamador nao declara outro.
DEFAULT_REFERENCE_SPEED_KMH = 15.0


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """Uma rota descoberta, com fisica completa, previsao e classificacao.

    ``candidate`` traz a identidade e as metricas da **busca** (modo busca);
    ``assessment`` traz a fisica **completa** (simulador autoritativo), as
    ``EffortFeatures``, a previsao, a **estimativa de intensidade** (ADR-028) e
    a validacao de circulacao; ``classification`` e a classificacao predicao x
    preferencia. ``safety`` e o **estado de seguranca** operacional (ADR-029 D2):
    ``REJECTED`` nunca e recomendada nem apresentada como viavel. Nada foi
    reordenado. ``recommended`` marca o **"pick"** — a rota recomendada (primeira
    **elegivel** na ordem do perfil), que e **sempre** incluida no box junto das
    demais.
    """

    candidate: RouteCandidate
    assessment: RouteAssessment
    classification: RouteClassification
    safety: RouteSafetyAssessment
    recommended: bool = False
    #: Perfil de apresentacao/contrato (``RoutePoint``), preenchido pela
    #: orquestracao a partir da geometria do grafo de consulta.
    geometry: tuple[RoutePoint, ...] = ()
    #: Custo do terreno (``d1``..``d5`` + declividade), quando ha politica de
    #: ritmo. ``None`` no modo legado de potencia constante.
    demand: RouteDemand | None = None
    #: Observaveis do esforco e do tracado para o cartao de rota.
    telemetry: RouteTelemetry | None = None

    @property
    def route_id(self) -> str:
        return self.candidate.route_id

    @property
    def safety_state(self) -> RouteSafetyState:
        return self.safety.state

    @property
    def eligible(self) -> bool:
        return self.safety.eligible

    @property
    def outcome(self) -> AssessmentOutcome:
        return self.assessment.outcome

    @property
    def prediction(self) -> PhysiologicalPrediction:
        return self.assessment.prediction

    @property
    def intensity(self) -> IntensityEstimate:
        """Estimativa de intensidade computacional (**nao calibrada**)."""
        return self.assessment.intensity

    @property
    def predicted_rpe(self) -> float | None:
        """RPE previsto por modelo **calibrado**, ou ``None`` (nunca inventado)."""
        return self.assessment.prediction.predicted_rpe

    @property
    def mean_rpe(self) -> float:
        """RPE medio da **estimativa de intensidade** (nao calibrada)."""
        return self.assessment.intensity.mean_rpe

    @property
    def prediction_status(self) -> PredictionStatus:
        return self.assessment.prediction.status

    def to_dict(self) -> dict[str, object]:
        return {
            "route_id": self.candidate.route_id,
            "label_id": self.candidate.label_id,
            "recommended": self.recommended,
            "safety": self.safety.to_dict(),
            "search_metrics": self.candidate.metrics.to_dict(),
            "assessment": self.assessment.to_dict(),
            "classification": self.classification.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RoutePlanResult:
    """Plano de consulta offline: box, zona e rotas descobertas.

    ``search_bbox`` e o corredor **que limitou a busca** — o box dos pinos com as
    margens automaticas por eixo (ou o extent do grafo, em ``use_full_graph``) — e
    e **fixo** numa consulta: a busca ocorre uma unica vez dentro dele. ``bbox`` e
    o box **final** — uniao do box dos pinos com o tracado de **todas as rotas
    devolvidas** (recomendada + alternativas), calculado **depois** da ML
    (ADR-028 D7) — que limita a altimetria e e o que deve ser exibido; ele **pode**
    exceder o corredor quando um trecho desvia para fora (como no ``toporide``, o
    box cresce so para exibicao, sem nova busca). Ambos sao derivados da origem, do
    destino e das rotas calculadas.

    Seguranca (ADR-029 D4): ``routes`` contem apenas as rotas **elegiveis**
    (``VALIDATED`` ou ``DEGRADED``), na ordem de classificacao; ``rejected``
    preserva as rotas ``REJECTED`` e seus motivos para **auditoria**.
    ``recommended`` e o "pick" (primeira rota **elegivel** na ordem do perfil) —
    **nunca** uma rota rejeitada; e ``None`` quando nao ha nenhuma elegivel.
    ``warnings`` explicitam degradacao da recomendada e ausencia de rota elegivel.

    Completude da busca (observabilidade, ADR-026/ADR-029): ``truncated`` sinaliza
    interrupcao por ``max_expansions``; ``frontier_truncated``/``dropped_labels``
    sinalizam poda da fronteira por ``max_labels_per_key`` (labels nao dominadas
    removidas). Nao se confundem com deduplicacao de candidatas.
    """

    origin: Coord
    destination: Coord
    profile_id: str
    search_bbox: GeoBBox
    bbox: GeoBBox
    reached: bool
    zone_nodes: int
    zone_arcs: int
    graph_nodes: int
    graph_arcs: int
    iterations: int
    routes: tuple[PlannedRoute, ...] = ()
    rejected: tuple[PlannedRoute, ...] = ()
    warnings: tuple[str, ...] = ()
    pareto_route_count: int = 0
    truncated: bool = False
    frontier_truncated: bool = False
    dropped_labels: int = 0
    preference: EffortPreference | None = None
    search_mode: str = "exact"
    search_complete: bool = True
    optimality_proven: bool = True

    @property
    def count(self) -> int:
        """Numero de rotas **elegiveis** (as rejeitadas ficam em ``rejected``)."""
        return len(self.routes)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def recommended(self) -> PlannedRoute | None:
        """Rota recomendada: primeira **elegivel** na ordem, ou ``None``.

        Nunca uma rota ``REJECTED`` (ADR-029 D4). Preserva o baseline
        ``routes[0]`` quando todas as rotas sao validas.
        """
        return self.routes[0] if self.routes else None

    @property
    def physiological_count(self) -> int:
        """Rotas com previsao fisiologica **calibrada** disponivel."""
        return sum(1 for route in self.routes if route.prediction.status.is_available)

    def to_contract(self) -> RoutePlan:
        """Projeta o resultado interno no contrato publico versionado.

        A projecao vive em ``router.app.plan_view`` (ADR-033, E3); este metodo
        apenas delega. O contrato e a **unica** representacao de transporte.
        """
        from router.app.plan_view import to_contract

        return to_contract(self)

    def to_dict(self) -> dict[str, object]:
        """Atalho para ``to_contract().to_dict()`` (payload versionado)."""
        return self.to_contract().to_dict()


def _search_zone(
    graph: RoutingGraph,
    pins: tuple[Coord, ...],
    *,
    use_full_graph: bool,
    pad_m: float,
) -> tuple[GeoBBox, RoutingGraph]:
    """Box de busca **definido uma vez** e a zona que ele recorta.

    Semana de corredor: com ``use_full_graph=False`` (padrao), o box e o
    envelope dos pinos com as margens automaticas por eixo (``query_bbox``) e a
    busca roda apenas na zona correspondente. Com ``use_full_graph=True``, o box
    e o extent do grafo carregado (mais ``pad_m``) e a busca roda no grafo
    inteiro. Em nenhum caso ha crescimento nem segunda busca.
    """
    if use_full_graph:
        nodes = tuple(graph.nodes.values())
        if not nodes:
            box = query_bbox(pins)
            assert box is not None
            return box, graph
        south = min(node.lat for node in nodes)
        north = max(node.lat for node in nodes)
        west = min(node.lon for node in nodes)
        east = max(node.lon for node in nodes)
        box = expand_bbox((south, west, north, east), pad_m, (south + north) / 2.0)
        return box, graph
    box = query_bbox(pins)
    assert box is not None
    return box, graph_for_bbox(graph, box)


def plan_offline_routes(
    graph: RoutingGraph,
    origin: Coord,
    destination: Coord,
    *,
    cyclist: CyclistModel,
    bicycle: BicycleModel,
    profile: str | RouteProfile = "fastest",
    snap_options: SnapOptions | None = None,
    traversal: PhysicalTraversal | None = None,
    environment: Environment | None = None,
    initial_speed_mps: float = 0.0,
    initial_state: SimulationState | None = None,
    power_w: float | None = None,
    pacing: ReferencePacing | None = None,
    demand_field: ArcDemandField | None = None,
    rider: RiderProfile | None = None,
    context: PredictionContext | None = None,
    predictor: EffortPredictor | None = None,
    preference: EffortPreference | None = None,
    deduplicator: RouteDeduplicator | None = None,
    max_routes: int | None = None,
    collapse_similar: bool = True,
    search_mode: str = "exact",
    progressive_max_similarity: float | None = None,
    growth: float = 1.6,
    max_iterations: int | None = 8,
    pad_m: float = 80.0,
    on_progress: Callable[[str, RoutePlan | None, dict], None] | None = None,
    progress_event_every: int = 1,
    explore_full_coverage: bool = False,
    use_full_graph: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> RoutePlanResult:
    """Descobre rotas offline entre ``origin`` e ``destination`` e as classifica.

    Ordem de eventos espelhada do `toporide` (ADR-028 D7): candidatas (A\\*) ->
    guardrails de circulacao -> fisica completa -> **ML em toda rota** (estimativa
    de intensidade) -> classificacao -> **avaliacao final de seguranca** (ADR-029
    D4, sem recalculo) -> **box por ultimo**, a partir das rotas **devolvidas**. A
    busca roda **uma unica vez** na zona do corredor (pins + margens automaticas,
    nao o grafo inteiro); a avaliacao fisica usa o **simulador completo**. O
    preditor, quando informado, e sempre **local** (artefato em disco); a funcao
    nunca toca a rede.

    Args:
        graph: Grafo carregado (a cidade inteira). Nao e alterado.
        origin: Coordenada ``(lat, lon)`` de origem.
        destination: Coordenada ``(lat, lon)`` de destino.
        cyclist: Modelo do ciclista (fisica). Obrigatorio — nao ha padrao.
        bicycle: Modelo da bicicleta (fisica). Obrigatorio — nao ha padrao.
        profile: Id do perfil (``PROFILE_IDS``) ou um ``RouteProfile``.
        snap_options: Parametros do snapping. Padrao ``SnapOptions()``.
        traversal: Simulador fisico (busca **e** avaliacao). Padrao
            ``PhysicalTraversal()`` (modo busca).
        environment: Ambiente fisico; padrao do simulador.
        initial_speed_mps: Velocidade inicial na origem (m/s) da busca.
        initial_state: Estado dinamico inicial da avaliacao; se omitido, deriva
            de ``initial_speed_mps``.
        power_w: Potencia no pedivela; padrao do ciclista. Ignorado quando
            ``pacing`` e informado.
        pacing: Parametros da politica de ritmo (``hold-reference-speed@2``).
            Quando informado, a potencia varia por arco e o RPE pode exceder
            17; quando ``None``, mantem a potencia constante (legado).
        demand_field: Cache de demanda por arco, reutilizado entre rotas e
            entre a busca parcial e a avaliacao final.
        rider: Perfil fisiologico (para a predicao), opcional.
        context: Contexto da predicao, opcional.
        predictor: Preditor fisiologico **local**; ``None`` => estado explicito
            ``UNAVAILABLE`` (nenhum RPE inventado).
        preference: Preferencia de esforco declarada, para a classificacao.
        deduplicator: Estrategia de deduplicacao das candidatas.
        max_routes: ``None`` devolve o conjunto **estruturalmente distinto** —
            uma rota por grupo de quase-duplicatas da fronteira (corte no maior
            salto, ``route_set``); um inteiro positivo ``N`` devolve as ``N``
            rotas **mais dissimiles** (farthest-first, max-min Jaccard). Em
            ambos os modos a rota de indice 0 (a "pick") e preservada e nenhum
            limiar de similaridade e escolhido a mao.
        collapse_similar: quando ``True`` (padrao), aplica a selecao
            estrutural acima (``route_set``). Quando ``False``, mantem a
            **fronteira inteira** apos a deduplicacao PATH e ``max_routes``
            limita apenas as ``N`` primeiras na ordem do perfil — modo de
            comparacao/observabilidade, sem colapso.
        search_mode: ``"exact"`` (padrao) esgota a busca Pareto; ``"progressive"``
            interrompe explicitamente quando ``max_routes`` candidatas elegiveis
            atingem o limite estrutural declarado em ``progressive_max_similarity``.
            O modo progressivo relaxa a completude e nunca e ativado por padrao.
        progressive_max_similarity: maior Jaccard de arcos permitido entre as
            candidatas progressivas selecionadas. Obrigatorio em ``progressive``
            e deve estar em ``[0, 1]``.
        growth: **Inerte** (compatibilidade de assinatura). A busca atual e unica:
            nao ha crescimento da zona (ver ``_search_zone``).
        max_iterations: **Inerte** (compatibilidade de assinatura).
        explore_full_coverage: **Inerte** (compatibilidade de assinatura); use
            ``use_full_graph`` para buscar no grafo inteiro.
        pad_m: Margem (m) somada ao envelope das rotas no box final.
        progress_event_every: publica/avalia o preview a cada N candidatas no
            modo exato com ``on_progress``. A primeira candidata sempre pode
            ser publicada. O modo ``search_mode="progressive"`` não aplica
            esse intervalo, pois usa cada candidata para seu critério de parada.

    Returns:
        ``RoutePlanResult`` com o box (calculado **depois** da ML, a partir das rotas
        devolvidas), a zona e as rotas (fisica + estimativa de intensidade +
        classificacao + **estado de seguranca**). ``routes`` traz apenas as rotas
        **elegiveis**; as ``REJECTED`` ficam em ``rejected`` (auditoria). A rota
        **recomendada** (``recommended``) e a primeira **elegivel** na ordem do
        perfil — **nunca** uma rota rejeitada — ou ``None`` quando nao ha nenhuma
        elegivel. Sem rota conectada, ``reached`` e ``False`` e ``routes`` fica
        vazio — nada e inventado.

    Raises:
        ValueError: perfil desconhecido ou ``max_routes`` invalido.
        snapping.SnapError: nunca — o snapping e tentado na zona; uma zona
            desconectada devolve ``reached=False``, sem crescimento.
    """
    if search_mode not in ("exact", "progressive"):
        raise ValueError("search_mode deve ser 'exact' ou 'progressive'")
    if search_mode == "progressive":
        if max_routes is None:
            raise ValueError(
                "search_mode='progressive' exige max_routes explícito"
            )
        if progressive_max_similarity is None or not 0.0 <= progressive_max_similarity <= 1.0:
            raise ValueError(
                "progressive_max_similarity deve estar em [0, 1] no modo progressivo"
            )
    elif progressive_max_similarity is not None:
        raise ValueError(
            "progressive_max_similarity só pode ser usado no modo progressivo"
        )
    if (
        isinstance(progress_event_every, bool)
        or not isinstance(progress_event_every, int)
        or progress_event_every <= 0
    ):
        raise ValueError("progress_event_every deve ser um inteiro positivo")

    if max_routes is not None and (
        isinstance(max_routes, bool) or not isinstance(max_routes, int) or max_routes <= 0
    ):
        raise ValueError(
            f"max_routes deve ser um inteiro positivo ou None (recebido {max_routes!r})"
        )

    route_profile = profile if isinstance(profile, RouteProfile) else route_profile_for(profile)
    search_profile = route_profile.to_search_profile()
    options = snap_options if snap_options is not None else SnapOptions()
    simulator = traversal if traversal is not None else PhysicalTraversal()
    resolved_demand_field = (
        demand_field
        if demand_field is not None
        else (ArcDemandField(simulator) if pacing is not None else None)
    )
    start_state = (
        initial_state
        if initial_state is not None
        else SimulationState.from_speed(initial_speed_mps)
    )

    search_model = PhysicalTraversalModel(
        simulator, cyclist, bicycle, environment=environment, power_w=power_w,
    )

    pins: tuple[Coord, ...] = (origin, destination)
    captured: dict[str, object] = {"calls": 0}
    # Preview incremental. Guardamos apenas os DTOs públicos compactos e a
    # ``SearchRoute`` (arcos) de cada candidata descoberta — barato. A física
    # completa + ML roda **só** nas rotas que sobrevivem à seleção estrutural
    # (as distintas), e o resultado é memorizado por traçado (``path``): assim o
    # preview avalia poucas candidatas em vez de todas as da fronteira, e vê a
    # **mesma** seleção do resultado final.
    discovered: list[tuple[tuple[float, ...], RouteCandidate, tuple[str | None, ...]]] = []
    preview_contracts: dict[
        tuple[str | None, ...], tuple[Route, bool] | None
    ] = {}
    preview_assessments: dict[tuple[str | None, ...], RouteAssessment] = {}
    preview_elevation_cache: dict[str, _PreparedElevationProfile] = {}
    preview_similarity_cache = IncrementalSimilarityMatrix()
    seen_paths: set[tuple[str | None, ...]] = set()
    valid_candidate_found = False
    progressive_stop_ready = False
    current_box = query_bbox(pins)
    assert current_box is not None

    def zone_started(box: GeoBBox, zone: RoutingGraph, iteration: int) -> None:
        nonlocal current_box
        from router.core.search.astar import SearchCancelled
        if should_cancel is not None and should_cancel():
            raise SearchCancelled("Busca cancelada pelo usuário")
        current_box = box
        if on_progress is not None:
            on_progress("searching", None, {"bbox": box, "iteration": iteration,
                        "zone_nodes": zone.node_count, "zone_arcs": zone.arc_count})

    def probe(zone: RoutingGraph) -> tuple[tuple[Coord, ...], ...]:
        """Roda busca na zona e devolve o tracado real de cada rota."""
        captured["calls"] = int(captured["calls"]) + 1
        if zone.node_count < 2 or zone.arc_count == 0:
            return ()
        try:
            query = build_query_graph(zone, origin, destination, options)
        except SnapError:
            return ()
        def build_partial(candidate: RouteCandidate) -> tuple[Route, bool] | None:
            """Avalia UMA candidata (física completa + ML) e devolve seu contrato.

            Só é chamado para as candidatas que **sobrevivem** à seleção
            estrutural do preview — não para toda label que chega ao destino.
            """
            assessment = assess_candidates(
                query.graph, (candidate,), cyclist=cyclist, bicycle=bicycle,
                traversal=simulator, environment=environment, initial_state=start_state,
                power_w=power_w, rider=rider, context=context, predictor=predictor,
                pacing=pacing, demand_field=resolved_demand_field,
            )[0]
            preview_assessments[tuple(candidate.route.arc_ids)] = assessment
            classification = classify_routes((assessment,), preference)[0]
            report = evaluate_candidates((candidate,), assessments={candidate.route_id: assessment})
            if not report.routes:
                return None
            item = report.routes[0]
            partial_demand, partial_telemetry = _route_demand_and_telemetry(
                query, candidate, assessment
            )
            planned = PlannedRoute(candidate, assessment, classification, item.safety,
                                   geometry=_route_points(
                                       query,
                                       candidate,
                                       assessment,
                                       elevation_cache=preview_elevation_cache,
                                   ),
                                   demand=partial_demand, telemetry=partial_telemetry)
            from router.app.plan_view import _to_route_contract
            return (_to_route_contract(planned, profile_id=route_profile.id),
                    assessment.prediction.status.is_available)

        def publish_candidate(label, search_engine) -> None:
            nonlocal valid_candidate_found, progressive_stop_ready
            if on_progress is None and search_mode != "progressive":
                return
            from router.core.search.astar import SearchResult
            from router.core.search.state import route_arcs
            path = tuple(route_arcs(label))
            if path in seen_paths:
                return
            seen_paths.add(path)
            candidate_result = SearchResult(query.origin_id, query.destination_id,
                                            search_profile, (label,))
            pool = RouteCandidatePool.from_result(candidate_result, deduplicator=deduplicator)
            if not pool.candidates:
                return
            candidate = replace(pool.candidates[0], route_id=f"partial-{len(seen_paths)}")
            discovered.append((search_profile.values(label), candidate, path))
            discovered.sort(key=lambda item: item[0])
            if (
                search_mode != "progressive"
                and len(discovered) > 1
                and len(discovered) % progress_event_every != 0
            ):
                return
            # O preview aplica a MESMA seleção estrutural do resultado final
            # (``collapse_similar``), porém **antes** da física/ML: seleciona
            # sobre os arcos (barato) e avalia só as distintas. Sem isso, a lista
            # mostrada durante a busca avaliaria toda a fronteira (quase-duplicatas).
            if collapse_similar:
                ordered = tuple(item[1].route for item in discovered)
                with similarity_cache_scope(preview_similarity_cache):
                    picked = select_distinct_routes(ordered, max_routes=max_routes)
                chosen = [discovered[index] for index in picked.indices]
            else:
                chosen = discovered if max_routes is None else discovered[:max_routes]
            contracts: list[Route] = []
            available_flags: list[bool] = []
            for _, chosen_candidate, chosen_path in chosen:
                if chosen_path not in preview_contracts:
                    cached = build_partial(chosen_candidate)
                    preview_contracts[chosen_path] = cached
                else:
                    cached = preview_contracts[chosen_path]
                if cached is None:
                    continue
                contracts.append(cached[0])
                available_flags.append(cached[1])
            if not contracts:
                return
            valid_candidate_found = True
            if search_mode == "progressive" and max_routes is not None:
                selected_routes = [item[1].route for item in chosen]
                progressive_stop_ready = (
                    len(contracts) >= max_routes
                    and len(selected_routes) >= max_routes
                    and all(
                        arc_jaccard(selected_routes[i], selected_routes[j])
                        <= float(progressive_max_similarity)
                        for i in range(len(selected_routes))
                        for j in range(i + 1, len(selected_routes))
                    )
                )
            from router.app.plan_view import partial_contract
            routes = tuple(replace(route, recommended=index == 0)
                           for index, route in enumerate(contracts))
            lines = [[(point.lat, point.lon) for point in route.geometry] for route in routes]
            box = query_bbox(pins, lines, pad_m=pad_m)
            assert box is not None
            interim = partial_contract(
                origin=origin, destination=destination, profile_id=route_profile.id,
                search_bbox=current_box, bbox=box,
                zone_nodes=zone.node_count, zone_arcs=zone.arc_count,
                graph_nodes=graph.node_count, graph_arcs=graph.arc_count,
                iterations=int(captured["calls"]), routes=routes,
                pareto_route_count=(
                    len(search_engine.frontier.labels_at(query.destination_id))
                    if search_engine.frontier
                    else 0
                ),
                physiological_count=sum(available_flags),
                search_mode=search_mode,
                search_complete=False,
                optimality_proven=False,
            )
            if on_progress is not None:
                on_progress("route_found", interim,
                            {"bbox": current_box, "iteration": int(captured["calls"])})

        if search_mode == "progressive":
            stop_when_candidate = lambda: progressive_stop_ready
        elif use_full_graph:
            stop_when_candidate = lambda: valid_candidate_found
        else:
            stop_when_candidate = None

        search = search_candidates(
            query.graph,
            search_model,
            search_profile,
            query.origin_id,
            query.destination_id,
            initial_speed_mps=initial_speed_mps,
            on_candidate_state=(
                publish_candidate
                if on_progress is not None or search_mode == "progressive"
                else None
            ),
            should_cancel=should_cancel,
            stop_when_candidate=stop_when_candidate,
        )
        result = search.result
        if not result.reached:
            return ()
        captured["zone"] = zone
        captured["query"] = query
        captured["result"] = result
        captured["pool"] = search.pool
        return _route_geometries(query, result)

    # 1) CANDIDATAS: **uma** busca. O box do corredor (pins + margens
    #    automaticas) e definido **uma vez** e limita a busca — como no
    #    ``toporide`` (``solveAll``): uma geracao de candidatas, nao um ciclo de
    #    crescimento. Nao ha segunda busca: se o corredor nao conectar origem e
    #    destino, o resultado e ``reached=False`` (honesto), sem crescer a zona.
    #    (Os parametros legados ``growth``/``max_iterations``/
    #    ``explore_full_coverage`` ficam inertes — ver a docstring.)
    search_bbox, zone = _search_zone(
        graph, pins, use_full_graph=use_full_graph, pad_m=pad_m
    )
    assert search_bbox is not None
    if on_progress is not None or should_cancel is not None:
        zone_started(search_bbox, zone, 1)
    frontier_geometries = probe(zone)

    query = captured.get("query")
    result = captured.get("result")
    reusable = captured.get("zone") is zone and isinstance(query, QueryGraph)

    base = dict(
        origin=origin,
        destination=destination,
        profile_id=route_profile.id,
        search_bbox=search_bbox,
        zone_nodes=zone.node_count,
        zone_arcs=zone.arc_count,
        graph_nodes=graph.node_count,
        graph_arcs=graph.arc_count,
        iterations=int(captured["calls"]),
        preference=preference,
        search_mode=search_mode,
    )

    # Sem rota conectada: box apenas dos pinos; nada e inventado.
    if not reusable or not frontier_geometries:
        pins_bbox = query_bbox(pins, (), pad_m=pad_m)
        assert pins_bbox is not None
        return RoutePlanResult(reached=False, routes=(), bbox=pins_bbox, **base)

    pool = captured.get("pool")
    if not isinstance(pool, RouteCandidatePool):
        pool = RouteCandidatePool.from_result(result, deduplicator=deduplicator)
    if collapse_similar:
        # 1b) DISTINTAS: a fronteira Pareto multiplica quase-duplicatas
        #     (pequenos desvios trocam segundos por metros). Colapsa-as antes da
        #     fisica/ML, **sem limiar arbitrario**: sem ``max_routes``, corta no
        #     maior salto (natural break); com ``N``, escolhe as N mais
        #     dissimiles (farthest-first). A rota de indice 0 (a "pick") nunca e
        #     descartada.
        selection = select_distinct_routes(pool.routes, max_routes=max_routes)
        candidates = tuple(pool.candidates[index] for index in selection.indices)
    else:
        # Comparacao/observabilidade: a fronteira inteira, sem colapso
        # estrutural. ``max_routes`` limita as N primeiras na ordem do perfil.
        candidates = pool.candidates
        if max_routes is not None:
            candidates = candidates[:max_routes]

    # 3) FISICA COMPLETA + 4) ML (sempre): a avaliacao traz, para CADA rota, a
    #    estimativa de intensidade (RPE + faixas; nao calibrada) ADR-028.
    # 5) CLASSIFICACAO: fisica + intensidade + preferencia declarada.
    assessments_by_path: dict[tuple[str | None, ...], RouteAssessment] = {}
    missing_candidates: list[RouteCandidate] = []
    for candidate in candidates:
        path_key = tuple(candidate.route.arc_ids)
        cached = preview_assessments.get(path_key)
        if cached is None:
            missing_candidates.append(candidate)
        else:
            assessments_by_path[path_key] = cached
    if missing_candidates:
        fresh_assessments = assess_candidates(
            query.graph,
            missing_candidates,
            cyclist=cyclist,
            bicycle=bicycle,
            traversal=simulator,
            environment=environment,
            initial_state=start_state,
            power_w=power_w,
            pacing=pacing,
            demand_field=resolved_demand_field,
            rider=rider,
            context=context,
            predictor=predictor,
        )
        assessments_by_path.update(
            {
                tuple(candidate.route.arc_ids): assessment
                for candidate, assessment in zip(
                    missing_candidates, fresh_assessments
                )
            }
        )
    assessments = tuple(
        assessments_by_path[tuple(candidate.route.arc_ids)]
        for candidate in candidates
    )
    classifications = classify_routes(assessments, preference)

    # 5b) AVALIACAO FINAL (ADR-029 D4): reutiliza as avaliacoes JA calculadas
    #     (sem repetir fisica nem fisiologia) e atribui o estado de seguranca.
    #     O estado governa a recomendacao: REJECTED nunca e recomendada.
    assessment_by_id = {
        candidate.route_id: assessment
        for candidate, assessment in zip(candidates, assessments)
    }
    report = evaluate_candidates(candidates, assessments=assessment_by_id)
    prepared = {
        candidate.route_id: (candidate, assessment, classification)
        for candidate, assessment, classification in zip(
            candidates, assessments, classifications
        )
    }
    # Geometria de apresentacao/contrato: usa o grafo de CONSULTA (com arcos
    # virtuais do snapping) e os resultados fisicos/estimativa ja calculados.
    geometry_by_id: dict[str, tuple[RoutePoint, ...]] = {}
    emitted_geometries_list: list[tuple[Coord, ...]] = []
    elevation_cache: dict[str, _PreparedElevationProfile] = {}
    for candidate, assessment in zip(candidates, assessments):
        geometry = _route_points(
            query,
            candidate,
            assessment,
            elevation_cache=elevation_cache,
        )
        geometry_by_id[candidate.route_id] = geometry
        emitted_geometries_list.append(
            tuple((point.lat, point.lon) for point in geometry)
        )
    demand_by_id: dict[str, RouteDemand | None] = {}
    telemetry_by_id: dict[str, RouteTelemetry | None] = {}
    for candidate, assessment in zip(candidates, assessments):
        demand, telemetry = _route_demand_and_telemetry(query, candidate, assessment)
        demand_by_id[candidate.route_id] = demand
        telemetry_by_id[candidate.route_id] = telemetry

    def _planned(item) -> PlannedRoute:
        candidate, assessment, classification = prepared[item.route_id]
        return PlannedRoute(
            candidate,
            assessment,
            classification,
            safety=item.safety,
            recommended=item.recommended,
            geometry=geometry_by_id[item.route_id],
            demand=demand_by_id[item.route_id],
            telemetry=telemetry_by_id[item.route_id],
        )

    routes = tuple(_planned(item) for item in report.routes)
    rejected = tuple(_planned(item) for item in report.excluded)

    # 6) BOX por ultimo, a partir do tracado das rotas DEVOLVIDAS (a recomendada
    #    — primeira ELEGIVEL, nunca REJECTED — mais as alternativas do A*). A
    #    busca rodou no corredor (``search_bbox``); o box de exibicao/altimetria
    #    e a uniao do corredor com o envelope das rotas, e **pode** exceder o
    #    corredor quando um trecho desvia para fora dele — como no toporide, o
    #    box cresce so para a exibicao, sem nova busca.
    emitted_geometries = tuple(emitted_geometries_list)
    final_bbox = query_bbox(pins, emitted_geometries, pad_m=pad_m)
    assert final_bbox is not None  # pins sempre presentes -> base existe

    return RoutePlanResult(
        reached=result.reached,
        routes=routes,
        rejected=rejected,
        warnings=report.warnings,
        bbox=final_bbox,
        pareto_route_count=len(result.labels),
        truncated=bool(result.truncated),
        frontier_truncated=bool(result.frontier_truncated),
        dropped_labels=int(result.dropped_labels),
        search_complete=not result.stopped_early and not result.truncated,
        optimality_proven=(
            search_mode == "exact"
            and not result.stopped_early
            and not result.truncated
            and not result.frontier_truncated
        ),
        **base,
    )


#: Faixa de 3 cores do contrato a partir das faixas de intensidade (Borg).
#: ``i1``/``i2`` sao verdes, ``i3`` laranja, ``i4``/``i5`` vermelhas.
_BAND_TO_COLOR: dict[str, int] = {
    "i1": 0,
    "i2": 0,
    "i3": 1,
    "i4": 2,
    "i5": 2,
}


def _band_index(band: str | None) -> int | None:
    return None if band is None else _BAND_TO_COLOR.get(band)


@dataclass(frozen=True, slots=True)
class _PreparedElevationProfile:
    distances: tuple[float, ...]
    elevations: tuple[float, ...]


def _prepare_elevation_profile(profile) -> _PreparedElevationProfile:
    """Ordena e materializa as amostras uma vez por arco."""
    samples = sorted(
        (
            (float(sample.distance_m), float(sample.elevation_m))
            for sample in profile
            if sample.elevation_m is not None
        ),
        key=lambda item: item[0],
    )
    return _PreparedElevationProfile(
        distances=tuple(item[0] for item in samples),
        elevations=tuple(item[1] for item in samples),
    )


def _elevation_at(profile, distance_m: float) -> float | None:
    """Interpola a elevacao do arco na distancia dada.

    Retorna ``None`` quando o perfil nao cobre a distancia (nunca inventa
    altitude zero). Fora do intervalo por mais de 0,5 m, a ausencia e explicita.
    """
    if not isinstance(profile, _PreparedElevationProfile):
        profile = _prepare_elevation_profile(profile)
    distances = profile.distances
    elevations = profile.elevations
    if not distances:
        return None
    first_distance, first_elevation = distances[0], elevations[0]
    last_distance, last_elevation = distances[-1], elevations[-1]
    if distance_m < first_distance - 0.5 or distance_m > last_distance + 0.5:
        return None
    if distance_m <= first_distance:
        return first_elevation
    if distance_m >= last_distance:
        return last_elevation
    end_index = bisect_left(distances, distance_m)
    start_index = max(0, end_index - 1)
    start_d, end_d = distances[start_index], distances[end_index]
    start_e, end_e = elevations[start_index], elevations[end_index]
    if end_d <= start_d:
        return end_e
    fraction = (distance_m - start_d) / (end_d - start_d)
    return start_e + fraction * (end_e - start_e)


def reference_pacing_for(
    cyclist: CyclistModel,
    rider: RiderProfile | None = None,
    *,
    reference_speed_kmh: float = DEFAULT_REFERENCE_SPEED_KMH,
    calibrated_reference_power_w: float | None = None,
) -> ReferencePacing:
    """Monta a politica de ritmo a partir do ciclista e do perfil.

    ``P_ref`` vem da mesma ponte usada pela intensidade
    (``rider_model_from_cyclist``), de modo que ritmo e RPE compartilham a
    referencia. Sem CP/W' declarados, usa ``DEFAULT_W_PRIME_J`` com
    proveniencia ``PROVISIONAL_DEFAULT`` — nunca apresentado como medicao.

    Raises:
        ValueError: ``reference_speed_kmh`` nao positivo ou ``max_power_w`` do
            ciclista indisponivel (sem teto de potencia a politica nao roda).
    """
    if not (reference_speed_kmh > 0.0):
        raise ValueError("reference_speed_kmh deve ser positivo")
    if not (cyclist.max_power_w > 0.0):
        raise ValueError(
            "cyclist.max_power_w deve ser positivo para a politica de ritmo"
        )
    rider_model = rider_model_from_cyclist(
        cyclist, rider, calibrated_reference_power_w=calibrated_reference_power_w
    )
    reference_w = rider_model.reference_power_w
    if rider_model.has_critical_power_model:
        assert rider_model.critical_power_w is not None
        assert rider_model.w_prime_j is not None
        return ReferencePacing(
            reference_speed_mps=reference_speed_kmh / 3.6,
            reference_power_w=reference_w,
            critical_power_w=rider_model.critical_power_w,
            w_prime_j=rider_model.w_prime_j,
            max_power_w=cyclist.max_power_w,
            critical_power_source=PacingParameterSource.MEASURED,
            w_prime_source=PacingParameterSource.MEASURED,
        )
    return ReferencePacing(
        reference_speed_mps=reference_speed_kmh / 3.6,
        reference_power_w=reference_w,
        critical_power_w=reference_w,
        w_prime_j=DEFAULT_W_PRIME_J,
        max_power_w=cyclist.max_power_w,
        critical_power_source=_provenance_source(rider_model.reference.source),
        w_prime_source=PacingParameterSource.PROVISIONAL_DEFAULT,
    )


def _provenance_source(source: ParameterSource) -> PacingParameterSource:
    """Reprojeta a proveniencia fisiologica no vocabulario da fisica."""
    try:
        return PacingParameterSource(source.value)
    except ValueError:  # pragma: no cover - vocabularios espelhados
        return PacingParameterSource.PROVISIONAL_DEFAULT


def _route_demand_and_telemetry(
    query: QueryGraph,
    candidate: RouteCandidate,
    assessment: RouteAssessment,
) -> tuple[RouteDemand | None, RouteTelemetry | None]:
    """Monta demanda (custo do terreno) e telemetria do cartao de rota.

    A demanda so existe quando a politica de ritmo rodou (``assessment.paced``).
    A telemetria deriva dos resultados ja calculados: RPE estimado, inercia,
    entradas em via arterial e saldo de ``W'``. Zonas cardiacas ficam ``None``:
    nao ha metodo fisiologico individual, e inventar zonas seria mentir.
    """
    paced = assessment.paced
    intensity = assessment.intensity
    results = assessment.path.results
    total_distance = sum(float(result.distance_m) for result in results)

    demand: RouteDemand | None = None
    if paced is not None:
        by_band_distance = {name: 0.0 for name in DEMAND_BAND_NAMES}
        for arc_pacing, arc_demand in zip(paced.pacings, paced.demands):
            band = demand_band_for_ratio(arc_pacing.demand_ratio)
            by_band_distance[band] += arc_demand.distance_m
        fractions = (
            {
                name: value / total_distance
                for name, value in by_band_distance.items()
            }
            if total_distance > 0.0
            else {}
        )
        reference = paced.pacing
        demand = RouteDemand(
            scenario=DemandScenarioRef(
                reference_speed_kmh=3.6 * reference.reference_speed_mps,
                reference_power_w=reference.reference_power_w,
                critical_power_w=reference.critical_power_w,
                w_prime_j=reference.w_prime_j,
                policy=paced.policy,
                demand_model=DEMAND_MODEL_VERSION,
                critical_power_source=reference.critical_power_source.value,
                w_prime_source=reference.w_prime_source.value,
            ),
            mean_demand_power_w=paced.mean_demand_power_w,
            distance_fraction_by_band=fractions,
            distance_by_band_m=by_band_distance,
            distance_by_grade_bin_m=paced.distance_by_grade_bin_m,
        )

    preserved = sum(
        float(result.distance_m)
        for result in results
        if result.final_speed_mps + 0.2 >= result.initial_speed_mps
    )
    inertia_fraction = (
        preserved / total_distance if total_distance > 0.0 else 0.0
    )
    highways = tuple(
        query.graph.get_arc(arc_id).highway
        for arc_id in candidate.route.arc_ids
        if arc_id is not None
    )

    telemetry = RouteTelemetry(
        policy=paced.policy if paced is not None else "constant-power",
        mean_rpe=intensity.mean_rpe,
        peak_rpe=intensity.peak_rpe,
        rpe_band=band_for_rpe(intensity.mean_rpe),
        rpe_label=rpe_label(intensity.mean_rpe),
        inertia_fraction=inertia_fraction,
        arterial_entries=arterial_entries(highways),
        w_prime_start_j=None if paced is None else paced.w_prime_start_j,
        w_prime_end_j=None if paced is None else paced.w_prime_end_j,
        w_prime_is_provisional=(
            paced is None or paced.pacing.w_prime_is_provisional
        ),
        heart_rate_zones=None,
    )
    return demand, telemetry


def _route_points(
    query: QueryGraph,
    candidate: RouteCandidate,
    assessment: RouteAssessment,
    *,
    elevation_cache: dict[str, _PreparedElevationProfile] | None = None,
) -> tuple[RoutePoint, ...]:
    """Constroi os pontos do contrato para uma rota avaliada.

    Usa a geometria do grafo de consulta e os dados **ja calculados**:
    ``TraversalResult`` (velocidade/energia por arco) e
    ``IntensityEstimate.segment_rpes/segment_bands`` (RPE/faixa por arco).
    Nenhuma equacao nova e introduzida.
    """
    points: list[RoutePoint] = []
    base_distance = 0.0
    intensity = assessment.intensity
    rpes = intensity.segment_rpes
    bands = intensity.segment_bands
    paced = assessment.paced
    pacings = paced.pacings if paced is not None else ()
    demands = paced.demands if paced is not None else ()

    for index, (arc_id, result) in enumerate(
        zip(candidate.route.arc_ids, assessment.path.results)
    ):
        if arc_id is None:
            continue
        arc = query.graph.get_arc(arc_id)
        coords = query.graph.arc_geometry(arc_id)
        if not coords:
            continue
        arc_distance = (
            float(result.distance_m)
            if result.distance_m > 0.0
            else float(arc.distance_m)
        )
        cumulative = cumulative_distances(coords)
        geometry_total = cumulative[-1] if cumulative else 0.0
        if elevation_cache is None:
            elevation_profile = _prepare_elevation_profile(arc.elevation_profile)
        else:
            elevation_profile = elevation_cache.get(arc_id)
            if elevation_profile is None:
                elevation_profile = _prepare_elevation_profile(arc.elevation_profile)
                elevation_cache[arc_id] = elevation_profile
        speed_kmh = float(result.average_speed_kmh)
        grade_pct = (
            100.0 * (float(result.ascent_m) - float(result.descent_m)) / arc_distance
            if arc_distance > 0.0
            else 0.0
        )
        watts = (
            float(result.rider_energy_j) / float(result.time_s)
            if result.time_s > 0.0
            else None
        )
        rpe = rpes[index] if index < len(rpes) else None
        band = _band_index(bands[index] if index < len(bands) else None)
        arc_pacing = pacings[index] if index < len(pacings) else None
        arc_demand = demands[index] if index < len(demands) else None

        for position, (lat, lon) in enumerate(coords):
            if (
                points
                and abs(points[-1].lat - lat) < 1e-9
                and abs(points[-1].lon - lon) < 1e-9
            ):
                continue
            fraction = (
                cumulative[position] / geometry_total if geometry_total > 0.0 else 0.0
            )
            points.append(
                RoutePoint(
                    lat=lat,
                    lon=lon,
                    ele=_elevation_at(elevation_profile, fraction * arc.distance_m),
                    dist_m=base_distance + fraction * arc_distance,
                    speed_kmh=speed_kmh,
                    grade_pct=grade_pct,
                    band=band,
                    rpe=rpe,
                    watts=watts,
                    arc_demand_w=(
                        None if arc_demand is None else arc_demand.mean_power_w
                    ),
                    arc_demand_ratio=(
                        None if arc_pacing is None else arc_pacing.demand_ratio
                    ),
                    demand_band=(
                        None
                        if arc_pacing is None
                        else demand_band_for_ratio(arc_pacing.demand_ratio)
                    ),
                )
            )
        base_distance += arc_distance

    return tuple(points)


def _route_geometries(
    query: QueryGraph, result
) -> tuple[tuple[Coord, ...], ...]:
    """Tracado real de cada rota da fronteira, no grafo de consulta.

    Usa o grafo de **consulta** (com arcos virtuais do snapping), de modo que
    nenhum trecho perto da origem/destino fique sem geometria.
    """
    geometries: list[tuple[Coord, ...]] = []
    for route in routes_from_result(result):
        points: list[Coord] = []
        for arc_id in route.arc_ids:
            if arc_id is None:
                continue
            points.extend(query.graph.arc_geometry(arc_id))
        geometries.append(tuple(points))
    return tuple(geometries)
