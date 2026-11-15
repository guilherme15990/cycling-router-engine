"""Debug adapters exercise the public toolkit, including category preparation."""
import importlib
from pathlib import Path
import sys

import pytest

from router.integration import MobilityEngine, save_graph
from test_mobility_toolkit import payload

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "debug"))
debug = importlib.import_module("mobility_debug")


def settings(tmp_path):
    return dict(physics_step=5., require_elevation=False, wind_mps=0., air_density=1.225,
                gravity=9.80665, speed_cap_kmh=79.2, initial_kmh=0., snap_radius=100.,
                pbf_path=str(tmp_path / "source.osm.pbf"), dem_dir=str(tmp_path / "dem"))


def test_category_change_rebuilds_correct_network_and_reuses_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(debug, "ROOT", tmp_path)
    graph = MobilityEngine.from_osm(payload()).prepare(vehicle="bicycle")
    path = tmp_path / "bicycle.json"
    save_graph(graph, path)
    (tmp_path / "source.osm.pbf").write_bytes(b"local-source-signature")
    calls = []
    def from_pbf(source, **kwargs):
        calls.append(source)
        return MobilityEngine.from_osm(payload())
    monkeypatch.setattr(MobilityEngine, "from_pbf", from_pbf)
    request = dict(origin=(0, 0), destination=(0, .002), vehicle="motorcar", propulsion_type="electric")
    first = debug.execute(request, settings(tmp_path), path)
    second = debug.execute(dict(request, propulsion_type="combustion"), settings(tmp_path), path)
    assert len(calls) == 1
    assert first.reached and second.reached
    assert first.diagnostics["access_mode"] == second.diagnostics["access_mode"] == "motorcar"
    assert first.routes[0].metrics.distance_m < 300  # Motorway, not the bicycle detour.
    assert first.mobility["propulsion"] == "electric"
    assert second.mobility["propulsion"] == "combustion"
    assert debug.read_plan(first.to_dict()) == first


def test_missing_pbf_cannot_relabel_a_bicycle_graph_as_motorcar(tmp_path):
    graph = MobilityEngine.from_osm(payload()).prepare(vehicle="bicycle")
    path = tmp_path / "bicycle.json"
    save_graph(graph, path)
    with pytest.raises(ValueError, match="PBF"):
        debug.execute(dict(origin=(0, 0), destination=(0, .002), vehicle="motorcar"), settings(tmp_path), path)


def test_debug_refinements_keep_unit_conversion_and_provenance():
    profile = debug.profile_for_request(dict(vehicle="motorcar", propulsion_type="combustion",
                                            refinements={"power_cv": 100, "rider_mass_kg": None}))
    assert profile.nominal_power_w == pytest.approx(73549.875)
    assert dict(profile.parameter_sources)["nominal_power_w"] == "provided"
