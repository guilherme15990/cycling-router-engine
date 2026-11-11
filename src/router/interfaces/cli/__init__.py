"""Linha de comando do motor de roteamento.

Exemplos:

    roteamento build --pbf dados.osm.pbf --elevation terrarium --tiles-dir data/dem
    roteamento fetch-dem --bbox -18.35,-51.73,-17.09,-50.36 --zoom 12 --out data/dem
    roteamento stats centro.json
    roteamento geojson centro.json centro.geojson

O caminho **offline** usa um arquivo ``.osm.pbf`` local (``build --pbf``) e
tiles de elevacao Terrarium locais (``build --elevation terrarium``). O comando
``fetch-dem`` e a unica etapa que acessa a rede (preparo dos tiles).

Este modulo e a **orquestracao** (ADR-033, E6): o parser vive em
``router.interfaces.cli.parser``, a renderizacao em
``router.interfaces.cli.render`` e os comandos em
``router.interfaces.cli.commands``.
"""

from __future__ import annotations

from router.interfaces.cli.parser import build_parser
from router.interfaces.cli.render import configure_stdout

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
