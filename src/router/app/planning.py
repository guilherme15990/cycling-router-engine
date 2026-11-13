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
1. **CANDIDATAS** — ``envelope.adaptive_zone`` recorta a zona e roda a busca
   ``search.MultiObjectiveAStar`` (perfil), devolvendo o tracado real de **todas**
   as rotas da fronteira;
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

from dataclasses import dataclass, replace
from collections.abc import Callable

from router.core.data.envelope import adaptive_zone
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
from router.core.data.geo import GeoBBox, Coord, bbox_contains, cumulative_distances, query_bbox
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
    MultiObjectiveAStar,
    PhysicalTraversalModel,
    RouteCandidate,
    RouteCandidatePool,
    RouteProfile,
    RouteDeduplicator,
    route_profile_for,
    routes_from_result,
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

    ``search_bbox`` e a zona que a busca percorreu (dimensionada para manter a
    rede conectada); ``bbox`` e o box **final** — uniao do box dos pinos com o
    tracado de **todas as rotas devolvidas** (recomendada + alternativas),
    calculado **depois** da ML (ADR-028 D7) — que limita a altimetria e e o que
    deve ser exibido. Ambos sao dinamicos e derivados apenas da origem, do
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
    growth: float = 1.6,
    max_iterations: int | None = 8,
    pad_m: float = 80.0,
    on_progress: Callable[[str, RoutePlan | None, dict], None] | None = None,
    explore_full_coverage: bool = False,
    use_full_graph: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> RoutePlanResult:
    """Descobre rotas offline entre ``origin`` e ``destination`` e as classifica.

    Ordem de eventos espelhada do `toporide` (ADR-028 D7): candidatas (A\\*) ->
    guardrails de circulacao -> fisica completa -> **ML em toda rota** (estimativa
    de intensidade) -> classificacao -> **avaliacao final de seguranca** (ADR-029
    D4, sem recalculo) -> **box por ultimo**, a partir das rotas **devolvidas**. A
    busca roda sobre uma zona **dinamica** (nao o grafo inteiro); a avaliacao
    fisica usa o **simulador completo**. O preditor, quando informado, e sempre
    **local** (artefato em disco); a funcao nunca toca a rede.

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
        max_routes: Se informado, limita as rotas a N **melhores pela ordem do
            perfil** (a ordem da fronteira). ``None`` mantem todas.
        growth: Fator de crescimento da zona a cada iteracao sem rota (politica
            de conectividade; nao e parametro de fisica).
        max_iterations: Numero maximo de crescimentos da zona.
        pad_m: Margem (m) somada ao envelope das rotas no box final.

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
        snapping.SnapError: nunca — o snapping e tentado por iteracao; uma zona
            desconectada leva ao crescimento, nao a excecao.
    """
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
    # Só guardamos os DTOs públicos compactos, não as simulações completas nem
    # labels que manteriam cadeias inteiras da busca vivas entre expansões.
    partials: list[tuple[tuple[float, ...], Route, bool]] = []
    seen_paths: set[tuple[str | None, ...]] = set()
    valid_candidate_found = False
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
        astar = MultiObjectiveAStar(
            query.graph,
            search_model,
            search_profile,
            initial_speed_mps=initial_speed_mps,
        )

        def publish_candidate(label) -> None:
            nonlocal valid_candidate_found
            if on_progress is None:
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
            assessment = assess_candidates(
                query.graph, (candidate,), cyclist=cyclist, bicycle=bicycle,
                traversal=simulator, environment=environment, initial_state=start_state,
                power_w=power_w, rider=rider, context=context, predictor=predictor,
                pacing=pacing, demand_field=resolved_demand_field,
            )[0]
            classification = classify_routes((assessment,), preference)[0]
            report = evaluate_candidates((candidate,), assessments={candidate.route_id: assessment})
            if not report.routes:
                return
            valid_candidate_found = True
            item = report.routes[0]
            partial_demand, partial_telemetry = _route_demand_and_telemetry(
                query, candidate, assessment
            )
            planned = PlannedRoute(candidate, assessment, classification, item.safety,
                                   geometry=_route_points(query, candidate, assessment),
                                   demand=partial_demand, telemetry=partial_telemetry)
            from router.app.plan_view import _to_route_contract, partial_contract
            partials.append((search_profile.values(label),
                             _to_route_contract(planned, profile_id=route_profile.id),
                             assessment.prediction.status.is_available))
            partials.sort(key=lambda item: item[0])
            chosen = partials if max_routes is None else partials[:max_routes]
            routes = tuple(replace(route, recommended=index == 0)
                           for index, (_, route, _) in enumerate(chosen))
            lines = [[(point.lat, point.lon) for point in route.geometry] for route in routes]
            box = query_bbox(pins, lines, pad_m=pad_m)
            assert box is not None
            interim = partial_contract(
                origin=origin, destination=destination, profile_id=route_profile.id,
                search_bbox=current_box, bbox=box,
                zone_nodes=zone.node_count, zone_arcs=zone.arc_count,
                graph_nodes=graph.node_count, graph_arcs=graph.arc_count,
                iterations=int(captured["calls"]), routes=routes,
                pareto_route_count=len(astar.frontier.labels_at(query.destination_id)) if astar.frontier else 0,
                physiological_count=sum(available for _, _, available in chosen),
            )
            on_progress("route_found", interim,
                        {"bbox": current_box, "iteration": int(captured["calls"])})

        result = astar.search(query.origin_id, query.destination_id,
                              on_candidate_state=publish_candidate if on_progress else None,
                                  should_cancel=should_cancel,
                                  stop_when_candidate=(lambda: valid_candidate_found) if use_full_graph else None)
        if not result.reached:
            return ()
        captured["zone"] = zone
        captured["query"] = query
        captured["result"] = result
        return _route_geometries(query, result)

    # 1) CANDIDATAS: a zona cresce ate envolver o tracado de TODAS as rotas da
    #    fronteira — assim qualquer rota devolvida cabe na zona de busca.
    search_bbox, zone, frontier_geometries = adaptive_zone(
        graph,
        pins,
        probe,
        growth=growth,
        max_iterations=max_iterations,
        pad_m=pad_m,
        explore_full_coverage=explore_full_coverage,
        use_full_graph=use_full_graph,
        on_zone=zone_started if on_progress is not None or should_cancel is not None else None,
    )

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
    )

    # Sem rota conectada: box apenas dos pinos; nada e inventado.
    if not reusable or not frontier_geometries:
        pins_bbox = query_bbox(pins, (), pad_m=pad_m)
        assert pins_bbox is not None
        return RoutePlanResult(reached=False, routes=(), bbox=pins_bbox, **base)

    pool = RouteCandidatePool.from_result(result, deduplicator=deduplicator)
    candidates = pool.candidates
    if max_routes is not None:
        candidates = candidates[:max_routes]

    # 3) FISICA COMPLETA + 4) ML (sempre): a avaliacao traz, para CADA rota, a
    #    estimativa de intensidade (RPE + faixas; nao calibrada) ADR-028.
    # 5) CLASSIFICACAO: fisica + intensidade + preferencia declarada.
    assessments = assess_candidates(
        query.graph,
        candidates,
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
    geometry_by_id = {
        candidate.route_id: _route_points(query, candidate, assessment)
        for candidate, assessment in zip(candidates, assessments)
    }
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
    #    — primeira ELEGIVEL, nunca REJECTED — mais as alternativas do A*). Como
    #    devolvidas ⊆ candidatas ⊆ fronteira, e a zona ja contem toda a fronteira,
    #    o box final cabe na zona de busca.
    emitted_geometries = tuple(
        _candidate_geometry(query, candidate) for candidate in candidates
    )
    final_bbox = query_bbox(pins, emitted_geometries, pad_m=pad_m)
    assert final_bbox is not None  # pins sempre presentes -> base existe
    if not use_full_graph and not bbox_contains(search_bbox, final_bbox):  # pragma: no cover - defensivo
        raise AssertionError(
            "box final saiu da zona de busca: rota devolvida fora da zona"
        )

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
        **base,
    )


def _candidate_geometry(
    query: QueryGraph, candidate: RouteCandidate
) -> tuple[Coord, ...]:
    """Tracado real de uma candidata, no grafo de consulta.

    Usa o grafo de **consulta** (com arcos virtuais do snapping), de modo que
    nenhum trecho perto da origem/destino fique sem geometria.
    """
    points: list[Coord] = []
    for arc_id in candidate.route.arc_ids:
        if arc_id is None:
            continue
        points.extend(query.graph.arc_geometry(arc_id))
    return tuple(points)


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


def _elevation_at(profile, distance_m: float) -> float | None:
    """Interpola a elevacao do arco na distancia dada.

    Retorna ``None`` quando o perfil nao cobre a distancia (nunca inventa
    altitude zero). Fora do intervalo por mais de 0,5 m, a ausencia e explicita.
    """
    samples = [
        (float(sample.distance_m), float(sample.elevation_m))
        for sample in profile
        if sample.elevation_m is not None
    ]
    if not samples:
        return None
    samples.sort(key=lambda item: item[0])
    first_distance, first_elevation = samples[0]
    last_distance, last_elevation = samples[-1]
    if distance_m < first_distance - 0.5 or distance_m > last_distance + 0.5:
        return None
    if distance_m <= first_distance:
        return first_elevation
    if distance_m >= last_distance:
        return last_elevation
    for (start_d, start_e), (end_d, end_e) in zip(samples, samples[1:]):
        if start_d <= distance_m <= end_d:
            if end_d <= start_d:
                return end_e
            fraction = (distance_m - start_d) / (end_d - start_d)
            return start_e + fraction * (end_e - start_e)
    return last_elevation


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
        elevation_profile = arc.elevation_profile
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
