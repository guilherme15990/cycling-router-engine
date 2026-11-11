"""Fronteira publica nivel B: superficie curada para consumidores externos.

Atalho oficial de importacao (ADR-031, secao 6): o consumidor externo importa
**um** modulo - ``router.integration`` - e, no maximo, os tipos de
``router.contracts``. A implementacao vive em ``router.interfaces.integration``
e este modulo apenas a reexporta, sem logica.
"""

from __future__ import annotations

from router.interfaces.integration import *  # noqa: F401,F403
from router.interfaces.integration import __all__ as _INTERFACE_ALL

__all__ = list(_INTERFACE_ALL)
