"""Comandos da CLI (ADR-033, E6).

Cada ``cmd_*`` recebe o ``argparse.Namespace`` do subcomando e devolve o codigo
de saida do processo. A renderizacao textual fica em
``router.interfaces.cli.render``; o parser em ``router.interfaces.cli.parser``.
"""

from __future__ import annotations

from router.interfaces.cli.commands.graph import (
    cmd_build,
    cmd_fetch_dem,
    cmd_geojson,
    cmd_physical,
    cmd_snap,
    cmd_stats,
    cmd_validate,
)
from router.interfaces.cli.commands.plan import cmd_plan

__all__ = [
    "cmd_build",
    "cmd_fetch_dem",
    "cmd_geojson",
    "cmd_physical",
    "cmd_plan",
    "cmd_snap",
    "cmd_stats",
    "cmd_validate",
]
