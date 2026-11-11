"""Linha de comando **offline**: fonte OSM local e elevacao Terrarium local.

Cobrem (ADR-027 §0):

* ``build --pbf`` monta o grafo a partir de um ``.osm.pbf`` **local** (sem
  ingestao online); e roda **inteiro** com a rede desabilitada (``offline()``) —
  prova de que o caminho de execucao nao acessa a internet;
* sem ``--pbf``, o comando falha de forma explicita;
* ``--elevation terrarium`` exige ``--tiles-dir`` e constroi o provedor
  **offline** correto;
* ``fetch-dem`` e a etapa de **preparo de rede** (download de tiles), testada
  com o download substituido por um stub local — nenhum acesso real e feito.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from router.interfaces import cli
from router.interfaces.cli.commands import graph as cli_graph
from _pbf_fixtures import _PbfBuilder
from router.core.data.elevation import TerrariumElevation
from router.core.data.elevation.tiles import TerrariumTileError
from router.offline import offline


def _raw(degree: float) -> int:
    """Grau -> unidade inteira do PBF (granularidade padrao de 1e-7)."""
    return round(degree * 1e7)


def tiny_pbf(tmp_path: Path, name: str = "tiny.osm.pbf") -> Path:
    """Rede minima: 3 nos e duas vias ``residential`` que se encontram no no 2.

    O no 2 e um cruzamento (compartilhado por duas vias), portanto permanece
    como no de roteamento; as coordenadas sao deterministicas e explicitas.
    """
    return (
        _PbfBuilder(tmp_path, name=name)
        .header(
            bbox=(-17.80, -50.93, -17.78, -50.91),
            writing_program="cli-test",
        )
        .data(
            dense=[
                (1, _raw(-17.790), _raw(-50.920), {}),
                (2, _raw(-17.791), _raw(-50.921), {}),
                (3, _raw(-17.792), _raw(-50.922), {}),
            ],
            ways=[
                (100, (1, 2), {"highway": "residential"}),
                (101, (2, 3), {"highway": "residential"}),
            ],
        )
        .write()
    )


# ==========================================================================
# Fonte OSM local + execucao offline
# ==========================================================================
def test_build_without_pbf_fails_explicitly():
    assert cli.main(["build", "--elevation", "none"]) == 2


def test_build_from_pbf_runs_entirely_offline(tmp_path):
    pbf = tiny_pbf(tmp_path)
    out = tmp_path / "graph.json"

    # A rede esta desabilitada: qualquer tentativa de conexao levantaria
    # OfflineViolation. O comando deve terminar com sucesso mesmo assim.
    with offline():
        exit_code = cli.main(
            [
                "build",
                "--pbf",
                str(pbf),
                "--elevation",
                "none",
                "--no-simplify",
                "--out",
                str(out),
            ]
        )

    assert exit_code == 0
    assert out.is_file()

    from router.core.data.graph.serialize import load_json

    graph = load_json(out)
    assert graph.node_count == 3
    assert graph.arc_count >= 1


def test_build_from_pbf_applies_highway_filter(tmp_path):
    # Uma via com highway fora da rede ciclavel nao produz arco.
    pbf = (
        _PbfBuilder(tmp_path, name="motorway.osm.pbf")
        .header(writing_program="cli-test")
        .data(
            dense=[
                (1, _raw(-17.790), _raw(-50.920), {}),
                (2, _raw(-17.791), _raw(-50.921), {}),
            ],
            ways=[(100, (1, 2), {"highway": "motorway"})],
        )
        .write()
    )
    out = tmp_path / "graph.json"

    with offline():
        assert (
            cli.main(
                [
                    "build",
                    "--pbf",
                    str(pbf),
                    "--highways",
                    "residential",
                    "--elevation",
                    "none",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )

    from router.core.data.graph.serialize import load_json

    assert load_json(out).arc_count == 0


def test_build_from_pbf_attaches_turn_restrictions(tmp_path, capsys):
    # Uma relacao type=restriction no PBF vira restricao de conversao no grafo
    # salvo (provisao offline das restricoes que a busca sabe aplicar).
    pbf = (
        _PbfBuilder(tmp_path, name="restriction.osm.pbf")
        .header(writing_program="cli-test")
        .data(
            dense=[
                (1, _raw(-17.790), _raw(-50.920), {}),
                (2, _raw(-17.791), _raw(-50.921), {}),
                (3, _raw(-17.792), _raw(-50.922), {}),
            ],
            ways=[
                (100, (1, 2), {"highway": "residential"}),
                (101, (2, 3), {"highway": "residential"}),
            ],
            relations=[
                (
                    500,
                    [(1, 100, "from"), (0, 2, "via"), (1, 101, "to")],
                    {"type": "restriction", "restriction": "no_left_turn"},
                )
            ],
        )
        .write()
    )
    out = tmp_path / "graph.json"

    with offline():
        code = cli.main(
            [
                "build",
                "--pbf",
                str(pbf),
                "--elevation",
                "none",
                "--no-simplify",
                "--out",
                str(out),
            ]
        )

    assert code == 0
    assert "restricoes de conversao: 1" in capsys.readouterr().out

    from router.core.data.graph.serialize import load_json

    assert list(load_json(out).turn_restrictions) == ["r500"]


# ==========================================================================
# Elevacao Terrarium local (provedor offline)
# ==========================================================================
def test_terrarium_provider_requires_tiles_dir():
    args = SimpleNamespace(
        elevation="terrarium",
        tiles_dir=None,
        tiles_zoom=None,
        no_cache=True,
    )
    with pytest.raises(SystemExit):
        cli_graph._make_provider(args, None)


def test_terrarium_provider_is_offline_and_configured(tmp_path):
    tiles = tmp_path / "dem"
    args = SimpleNamespace(
        elevation="terrarium",
        tiles_dir=str(tiles),
        tiles_zoom=12,
        no_cache=True,
    )
    provider = cli_graph._make_provider(args, None)
    assert isinstance(provider, TerrariumElevation)
    assert provider.tiles_dir == tiles
    assert provider.zoom == 12


def test_build_with_empty_terrarium_does_not_invent_elevation(tmp_path):
    # Tiles ausentes: o provedor devolve None (nunca um valor inventado); o
    # grafo e salvo mesmo com cobertura de elevacao nula.
    pbf = tiny_pbf(tmp_path)
    out = tmp_path / "graph.json"
    empty_tiles = tmp_path / "dem-empty"

    with offline():
        exit_code = cli.main(
            [
                "build",
                "--pbf",
                str(pbf),
                "--elevation",
                "terrarium",
                "--tiles-dir",
                str(empty_tiles),
                "--no-simplify",
                "--no-cache",
                "--out",
                str(out),
            ]
        )

    assert exit_code == 0
    from router.core.data.graph.serialize import load_json, summarize

    summary = summarize(load_json(out))
    assert summary["elevation_coverage"] == pytest.approx(0.0)


# ==========================================================================
# fetch-dem: preparo de rede (com download substituido)
# ==========================================================================
def test_fetch_dem_downloads_to_local_dir(tmp_path, monkeypatch):
    calls = {}

    def fake_fetch(bbox, zoom, dest_dir, *, timeout_s, progress=None):
        calls.update(bbox=bbox, zoom=zoom, dest_dir=Path(dest_dir), timeout=timeout_s)
        tile = Path(dest_dir) / str(zoom) / "10" / "20.png"
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b"\x89PNG\r\n\x1a\n")
        if progress is not None:
            progress(1, 1, tile)
        return [tile]

    monkeypatch.setattr(cli_graph, "fetch_terrarium_tiles", fake_fetch)

    out = tmp_path / "dem"
    exit_code = cli.main(
        [
            "fetch-dem",
            "--bbox",
            "-18.346,-51.73,-17.094,-50.359",
            "--zoom",
            "12",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert calls["bbox"] == pytest.approx((-18.346, -51.73, -17.094, -50.359))
    assert calls["zoom"] == 12
    assert calls["dest_dir"] == out


def test_fetch_dem_reports_tile_error(tmp_path, monkeypatch):
    def failing_fetch(bbox, zoom, dest_dir, *, timeout_s, progress=None):
        raise TerrariumTileError("sem rede")

    monkeypatch.setattr(cli_graph, "fetch_terrarium_tiles", failing_fetch)

    exit_code = cli.main(
        ["fetch-dem", "--bbox", "-18.3,-51.7,-17.1,-50.4", "--out", str(tmp_path)]
    )
    assert exit_code == 1
