import pytest
from router.core.data.graph.model import Arc, Segment
from router.core.physics import TraversalResult
from router.core.physics.energy import kinetic_energy_j
from router.core.search import (
    Objective,
    ObjectiveSet,
    PhysicalState,
    SearchProfile,
    SpeedBucketDominance,
    advance_state,
    initial_state,
    profile_for,
    route_arcs,
    speed_bucket,
)
from router.core.search import FrontierManager

MASS_KG = 87.0


def make_arc(arc_id="e0", target=2, distance_m=100.0):
    segment = Segment(id=f"{arc_id}:0", distance_m=distance_m)
    return Arc(id=arc_id, source=1, target=target, segments=(segment,))


def make_result(
    *,
    time_s=10.0,
    distance_m=100.0,
    ascent_m=1.0,
    descent_m=2.0,
    final_speed_mps=5.0,
    mechanical_energy_j=1000.0,
):
    return TraversalResult(
        time_s=time_s,
        distance_m=distance_m,
        ascent_m=ascent_m,
        descent_m=descent_m,
        initial_speed_mps=0.0,
        final_speed_mps=final_speed_mps,
        mechanical_energy_j=mechanical_energy_j,
        rider_energy_j=mechanical_energy_j / 0.97,
    )


def make_state(node_id, *, speed_mps, time_s, distance_m, label_id=0):
    from router.core.search import SearchState

    return SearchState(
        node_id=node_id,
        physical_state=PhysicalState.from_speed(MASS_KG, speed_mps),
        time_s=time_s,
        distance_m=distance_m,
        label_id=label_id,
    )


# --------------------------------------------------------------------------
# Estado fisico e label
# --------------------------------------------------------------------------
def test_physical_state_derives_kinetic_energy():
    state = PhysicalState.from_speed(5.0, 4.0)
    assert state.speed_mps == 4.0
    assert state.kinetic_energy_j == pytest.approx(0.5 * 5.0 * 16.0)

    # Velocidade negativa e tratada como zero.
    assert PhysicalState.from_speed(10.0, -3.0).speed_mps == 0.0


def test_initial_state_has_zero_cost():
    start = initial_state(1, MASS_KG, 0.0, label_id=7)
    assert start.node_id == 1
    assert start.time_s == 0.0
    assert start.distance_m == 0.0
    assert start.energy_j == 0.0
    assert start.parent is None
    assert start.incoming_arc_id is None
    assert start.label_id == 7
    assert start.speed_mps == 0.0
    assert start.physical_state.kinetic_energy_j == 0.0


def test_advance_state_accumulates_metrics_and_physics():
    start = initial_state(1, MASS_KG, 0.0)
    arc = make_arc()
    result = make_result(
        time_s=10.0,
        distance_m=100.0,
        ascent_m=1.0,
        descent_m=2.0,
        final_speed_mps=6.0,
        mechanical_energy_j=1500.0,
    )

    nxt = advance_state(start, arc, result, mass_kg=MASS_KG, label_id=1)

    assert nxt.node_id == 2
    assert nxt.time_s == pytest.approx(10.0)
    assert nxt.distance_m == pytest.approx(100.0)
    assert nxt.ascent_m == pytest.approx(1.0)
    assert nxt.descent_m == pytest.approx(2.0)
    # energy_j e a energia MECANICA, nao a energia do ciclista.
    assert nxt.energy_j == pytest.approx(1500.0)
    assert nxt.energy_j != pytest.approx(result.rider_energy_j)

    assert nxt.parent is start
    assert nxt.incoming_arc_id == "e0"
    assert nxt.physical_state.speed_mps == 6.0
    assert nxt.physical_state.kinetic_energy_j == pytest.approx(
        kinetic_energy_j(MASS_KG, 6.0)
    )
    assert nxt.active is True


def test_advance_state_chains_and_reconstructs_route():
    start = initial_state(1, MASS_KG, 0.0)
    first = advance_state(
        start, make_arc("e0", target=2), make_result(), mass_kg=MASS_KG, label_id=1
    )
    second = advance_state(
        first, make_arc("e1", target=3), make_result(), mass_kg=MASS_KG, label_id=2
    )

    assert second.time_s == pytest.approx(20.0)
    assert second.distance_m == pytest.approx(200.0)
    assert route_arcs(second) == ["e0", "e1"]
    assert route_arcs(start) == []


def test_search_state_to_dict_and_objective_mapping():
    state = make_state(5, speed_mps=4.0, time_s=12.0, distance_m=80.0)
    payload = state.to_dict()
    assert payload["node_id"] == 5
    assert payload["speed_mps"] == pytest.approx(4.0)
    assert payload["active"] is True

    mapping = state.objective_mapping()
    assert mapping["time_s"] == pytest.approx(12.0)
    assert mapping["distance_m"] == pytest.approx(80.0)


# --------------------------------------------------------------------------
# Buckets de velocidade
# --------------------------------------------------------------------------
def test_speed_bucket_boundaries():
    assert speed_bucket(0.0, 1.0) == 0
    assert speed_bucket(0.99, 1.0) == 0
    assert speed_bucket(1.0, 1.0) == 1
    assert speed_bucket(2.5, 1.0) == 2

    with pytest.raises(ValueError):
        speed_bucket(1.0, 0.0)


# --------------------------------------------------------------------------
# Dominancia com estado fisico
# --------------------------------------------------------------------------
def make_dominance(step_mps=2.0):
    objectives = ObjectiveSet("t", ("time_s", "distance_m"))
    return SpeedBucketDominance(objectives, speed_step_mps=step_mps)

def test_dominance_only_within_same_node_and_bucket():
    dominance = make_dominance(step_mps=2.0)

    a = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    b = make_state(1, speed_mps=1.5, time_s=110.0, distance_m=550.0)
    # Mesmo bucket (0): comparavel.
    assert dominance.key(a) == (1, 0)
    assert dominance.key(b) == (1, 0)
    assert dominance.dominates(a, b) is True
    assert dominance.dominates(b, a) is False

    # Bucket diferente: nao comparavel, mesmo com metricas melhores.
    c = make_state(1, speed_mps=3.0, time_s=90.0, distance_m=400.0)
    assert dominance.key(c) == (1, 1)
    assert dominance.dominates(c, a) is False
    assert dominance.dominates(a, c) is False

    # No diferente: nunca domina.
    d = make_state(2, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    assert dominance.dominates(a, d) is False


def test_dominance_validation():
    objectives = ObjectiveSet("t", ("time_s",))
    with pytest.raises(ValueError):
        SpeedBucketDominance(objectives, speed_step_mps=0.0)


# --------------------------------------------------------------------------
# FrontierManager
# --------------------------------------------------------------------------
def test_frontier_manager_rejects_dominated_with_same_bucket():
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance)

    better = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    worse = make_state(1, speed_mps=1.5, time_s=110.0, distance_m=550.0)

    assert manager.insert(better) is True
    assert manager.insert(worse) is False
    assert worse.active is False
    assert manager.frontier(dominance.key(better)) == (better,)


def test_frontier_manager_keeps_different_buckets():
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance)

    slow = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    fast = make_state(1, speed_mps=3.0, time_s=200.0, distance_m=1000.0)

    assert manager.insert(slow) is True
    # Apesar de pior em tempo/distancia, o bucket diferente mantem a label.
    assert manager.insert(fast) is True
    assert manager.key_count == 2
    assert len(manager.labels_at(1)) == 2


def test_frontier_manager_replaces_dominated_existing():
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance)

    old = make_state(1, speed_mps=1.0, time_s=110.0, distance_m=600.0)
    new = make_state(1, speed_mps=1.5, time_s=100.0, distance_m=500.0)

    manager.insert(old)
    assert manager.insert(new) is True
    assert old.active is False
    assert manager.frontier(dominance.key(new)) == (new,)


def test_frontier_manager_truncates_per_key():
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance, max_labels_per_key=1)

    # Ambos no bucket 0 e nao dominados entre si.
    quick = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=600.0)
    short = make_state(1, speed_mps=1.5, time_s=110.0, distance_m=500.0)

    assert manager.insert(quick) is True
    assert manager.insert(short) is True
    assert manager.total_labels == 1
    assert short.active is False  # pior lexicograficamente (tempo maior)
    assert manager.frontier(dominance.key(quick)) == (quick,)
    # A poda e observavel — nao silenciosa.
    assert manager.frontier_truncated is True
    assert manager.dropped_labels == 1
    assert manager.to_dict()["dropped_labels"] == 1
    assert manager.to_dict()["frontier_truncated"] is True


def test_frontier_manager_dominance_discard_is_not_counted_as_truncation():
    """Descarte por dominancia **nao** conta como poda de fronteira."""
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance, max_labels_per_key=5)

    better = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    worse = make_state(1, speed_mps=1.5, time_s=110.0, distance_m=550.0)

    manager.insert(better)
    assert manager.insert(worse) is False  # dominada, descartada

    assert manager.dropped_labels == 0
    assert manager.frontier_truncated is False


def test_frontier_manager_labels_at_and_active():
    dominance = make_dominance(step_mps=2.0)
    manager = FrontierManager(dominance)

    a = make_state(1, speed_mps=1.0, time_s=100.0, distance_m=500.0)
    b = make_state(2, speed_mps=1.0, time_s=50.0, distance_m=300.0)
    manager.insert(a)
    manager.insert(b)

    assert manager.labels_at(1) == (a,)
    assert manager.active_labels() == (a, b)
    assert manager.labels_at(99) == ()
    assert manager.to_dict()["keys"] == 2


# --------------------------------------------------------------------------
# Perfis tipados
# --------------------------------------------------------------------------
def test_objective_enum_and_search_profile():
    assert Objective.TIME.value == "time_s"
    assert Objective.ENERGY.value == "energy_j"

    fastest = profile_for("Fastest")
    assert fastest.id == "fastest"
    assert fastest.primary is Objective.TIME
    assert fastest.names == ("time_s", "ascent_m")
    assert fastest.objective_set.objectives == ("time_s", "ascent_m")

    assert profile_for("flattest").primary is Objective.ASCENT
    assert profile_for("balanced").to_dict()["primary"] == "time_s"

    with pytest.raises(ValueError):
        profile_for("turbo")
    with pytest.raises(ValueError):
        SearchProfile("x", ())
    with pytest.raises(ValueError):
        SearchProfile("x", (Objective.TIME, Objective.TIME))
