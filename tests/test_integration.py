"""Superficie de integracao (ADR-030, Fase 3).

A fachada ``api`` e ampla por compatibilidade; ``integration`` e a superficie
curada para o consumidor externo. Cobrem:

* tamanho e unicidade da superficie (a curadoria e o ponto do modulo);
* todos os nomes existem e apontam para o ponto de entrada eleito;
* o contrato reexportado e subconjunto da fachada ampla (os dois helpers de
  grafo sao a excecao declarada: a fachada ampla nao expoe serializacao);
* ``api.integration`` e o mesmo modulo.
"""

from __future__ import annotations

from router import integration
from router.app import multi_leg
from router.contracts import RoutePlan
from router.interfaces import api


def test_integration_surface_is_curated():
    names = list(integration.__all__)
    assert 20 <= len(names) <= 30, f"superficie fora da faixa curada: {len(names)}"
    assert len(names) == len(set(names)), "integration.__all__ tem duplicatas"


def test_every_integration_symbol_exists():
    missing = [name for name in integration.__all__ if not hasattr(integration, name)]
    assert missing == [], f"simbolos de integration.__all__ ausentes: {missing}"


def test_integration_surface_is_subset_of_wide_facade():
    # Excecao declarada: persistencia do grafo nao esta na fachada ampla (F6).
    graph_helpers = {"load_graph", "save_graph"}
    outside = [
        name
        for name in integration.__all__
        if name not in graph_helpers and name not in api.__all__
    ]
    assert outside == [], f"nomes fora da fachada e sem justificativa: {outside}"


def test_api_exposes_integration_module():
    assert api.integration is integration
    assert "integration" in api.__all__


def test_plan_routes_is_the_elected_multi_leg_entry_point():
    assert integration.plan_routes is multi_leg.plan_routes
    assert "plan_routes" in api.__all__


def test_integration_exposes_the_public_contract_not_internal_dto():
    assert integration.RoutePlan is RoutePlan
    assert not hasattr(integration, "PlannedRoute")
    assert not hasattr(integration, "RoutePlanResult")


def test_public_router_integration_is_the_same_surface():
    from router import integration as public

    assert public.__all__ == integration.__all__
    assert public.plan_routes is integration.plan_routes
