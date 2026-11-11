"""Fachada ampla legada do motor (compatibilidade congelada).

A superficie publica recomendada e ``router.contracts`` + ``router.integration``.
Esta fachada existe apenas para nao quebrar consumidores antigos: nenhum
simbolo novo entra aqui (ADR-031, secao 6). O conteudo reexportado vive em
``router.app.legacy`` para que ``interfaces`` nao importe ``router.core.*``.
"""

from __future__ import annotations

from router import __version__
from router.app import legacy as _legacy
from router.app.legacy import *  # noqa: F401,F403
from router.interfaces import integration

__all__ = [*_legacy.__all__, "integration"]
__version__ = __version__
