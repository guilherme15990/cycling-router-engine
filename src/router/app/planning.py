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

from dataclasses import dataclass

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
from router.core.prediction.intensity import IntensityEstimate
from router.core.prediction.ml import EffortPredictor
from router.core.physics.bicycle import BicycleModel
from router.core.physics.cyclist import CyclistModel
from router.core.physics.environment import Environment
from router.core.physics.models import SimulationState
from router.core.physics.traversal import PhysicalTraversal
from router.core.physiology import (
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
    RoutePlan,
    RoutePoint,
)
from router.core.data.snapping import QueryGraph, SnapError, SnapOptions, build_query_graph


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
    rider: RiderProfile | None = None,
    context: PredictionContext | None = None,
    predictor: EffortPredictor | None = None,
    preference: EffortPreference | None = None,
    deduplicator: RouteDeduplicator | None = None,
    max_routes: int | None = None,
    growth: float = 1.6,
    max_iterations: int = 8,
    pad_m: float = 80.0,
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
        power_w: Potencia no pedivela; padrao do ciclista.
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
    start_state = (
        initial_state
        if initial_state is not None
        else SimulationState.from_speed(initial_speed_mps)
    )

    search_model = PhysicalTraversalModel(
        simulator,
        cyclist,
        bicycle,
        environment=environment,
        power_w=power_w,
    )

    pins: tuple[Coord, ...] = (origin, destination)
    captured: dict[str, object] = {"calls": 0}

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
        result = astar.search(query.origin_id, query.destination_id)
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

    def _planned(item) -> PlannedRoute:
        candidate, assessment, classification = prepared[item.route_id]
        return PlannedRoute(
            candidate,
            assessment,
            classification,
            safety=item.safety,
            recommended=item.recommended,
            geometry=geometry_by_id[item.route_id],
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
    if not bbox_contains(search_bbox, final_bbox):  # pragma: no cover - defensivo
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
