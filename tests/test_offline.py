"""Guard de rede: operacao **offline** obrigatoria (ADR-027 §0, lacunas O5/O6).

Cobrem:

* o guard **desabilita** conexoes de saida e falha de forma **explicita**
  (``OfflineViolation``) — nunca silenciosa;
* restauracao correta (o guard e reentrante e nao deixa efeito colateral);
* opcao ``allow_loopback`` (servico local) sem liberar o restante;
* o **caminho de execucao** (treinar, carregar modelo, simular, prever e
  classificar) roda **inteiro** com a rede desabilitada — prova de ausencia de
  rede na execucao.
"""

import socket
import pytest
from router.offline import OfflineViolation, is_network_disabled, offline


# ==========================================================================
# Guard: bloqueio explicito
# ==========================================================================
def test_network_is_enabled_by_default():
    assert is_network_disabled() is False


def test_guard_toggles_state():
    with offline():
        assert is_network_disabled() is True
    assert is_network_disabled() is False


def test_guard_bloqueia_connect():
    with offline():
        client = socket.socket()
        try:
            with pytest.raises(OfflineViolation):
                client.connect(("93.184.216.34", 80))
        finally:
            client.close()


def test_guard_bloqueia_create_connection():
    with offline():
        with pytest.raises(OfflineViolation):
            socket.create_connection(("93.184.216.34", 80), timeout=1.0)


def test_guard_bloqueia_connect_ex():
    with offline():
        client = socket.socket()
        try:
            with pytest.raises(OfflineViolation):
                client.connect_ex(("93.184.216.34", 80))
        finally:
            client.close()


def test_guard_bloqueia_ate_loopback_por_padrao():
    with offline():
        client = socket.socket()
        try:
            with pytest.raises(OfflineViolation):
                client.connect(("127.0.0.1", 9))
        finally:
            client.close()


# ==========================================================================
# Positivo: mecanismo nao quebra sockets fora do guard / loopback permitido
# ==========================================================================
def _listening_socket():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server, server.getsockname()[1]


def test_loopback_connection_works_without_guard():
    server, port = _listening_socket()
    try:
        client = socket.socket()
        client.connect(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()
        client.close()
    finally:
        server.close()


def test_allow_loopback_permits_local_service():
    server, port = _listening_socket()
    try:
        with offline(allow_loopback=True):
            client = socket.socket()
            client.connect(("127.0.0.1", port))
            accepted, _ = server.accept()
            accepted.close()
            client.close()
        # O restante continua bloqueado.
    finally:
        server.close()


def test_state_restored_after_exception():
    original = socket.socket.connect
    with pytest.raises(RuntimeError):
        with offline():
            raise RuntimeError("boom")
    assert is_network_disabled() is False
    assert socket.socket.connect is original


def test_guard_is_reentrant():
    with offline():
        assert is_network_disabled() is True
        with offline():
            assert is_network_disabled() is True
        # Ainda desabilitado apos o contexto interno sair.
        assert is_network_disabled() is True
    assert is_network_disabled() is False


# ==========================================================================
# Caminho de execucao inteiro offline
# ==========================================================================
def test_full_local_pipeline_runs_offline(tmp_path):
    """Treinar -> carregar modelo -> simular -> prever -> classificar, sem rede."""
    from router.core.evaluation import (
        EffortPreference,
        assess_route,
        classify_route,
    )
    from router.core.prediction.intelligence import (
        CyclistId,
        MeasurementQuality,
        ModelStore,
        ObservedEffort,
        Provenance,
        TargetKind,
        build_dataset,
        load_predictor,
        train_and_save,
    )
    from router.core.data.graph.model import Arc, BicycleAccess, ElevationSample, Node, RoutingGraph, Segment
    from router.core.physics.physical import BicycleModel, CyclistModel
    from router.core.physics.physiology import PredictionStatus

    provenance = Provenance(source="test", version="1")
    observations = [
        ObservedEffort(
            observation_id=f"o{i}",
            cyclist_id=CyclistId("c1"),
            duration_s=600.0,
            distance_m=3000.0,
            provenance=provenance,
            observed_rpe=10.0 + 0.02 * (100.0 + 10.0 * i),
            quality=MeasurementQuality.DEVICE_MEASURED,
            feature_row={"mean_power_w": 100.0 + 10.0 * i},
        )
        for i in range(6)
    ]
    dataset = build_dataset("v1", observations, provenance=provenance)
    store = ModelStore(tmp_path / "models")

    cyclist = CyclistModel(
        mass_kg=75.0, max_power_w=600.0, sustainable_power_w=200.0, frontal_area_m2=0.5
    )
    bicycle = BicycleModel()
    graph = RoutingGraph()
    for node_id in (1, 2):
        graph.add_node(Node(id=node_id, lat=0.0, lon=0.0))
    graph.add_arc(
        Arc(
            id="a12",
            source=1,
            target=2,
            segments=(
                Segment(
                    id="a12:0",
                    distance_m=500.0,
                    elevation_profile=(
                        ElevationSample(distance_m=0.0, elevation_m=0.0),
                        ElevationSample(distance_m=500.0, elevation_m=0.0),
                    ),
                    elevation_gain_m=0.0,
                    elevation_loss_m=0.0,
                    bicycle_access=BicycleAccess.YES,
                ),
            ),
        )
    )

    with offline():
        trained = train_and_save(
            dataset,
            store,
            target=TargetKind.RPE,
            feature_names=("mean_power_w",),
            calibration_method="ols@1",
        )
        predictor = load_predictor(store, trained.artifact.model_version)
        assessment = assess_route(
            graph, ("a12",), cyclist=cyclist, bicycle=bicycle, predictor=predictor
        )
        classification = classify_route(
            assessment, EffortPreference(acceptable_rpe=(6.0, 20.0))
        )

    assert assessment.prediction.status is PredictionStatus.AVAILABLE
    assert classification.prediction_status == "available"
    assert classification.fits_preference is True
