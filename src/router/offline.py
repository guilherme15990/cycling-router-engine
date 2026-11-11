"""Guard de rede: mantem a execucao **offline** (ADR-027, requisito §0).

O requisito mandatorio do ADR-027 e que roteamento, simulacao fisica, inferencia
de ML e classificacao rodem **localmente**, sem internet, nuvem ou API externa.
Este modulo fornece um *guard* que **desabilita** conexoes de saida durante sua
vigencia: uma tentativa de acesso a rede falha de forma **explicita**
(``OfflineViolation``) em vez de silenciosa.

Uso principal:

* testes com **rede desabilitada** (lacuna O5) — provam que o caminho de
  execucao nao abre conexao;
* verificacao/embutir do guard no aplicativo, se desejado.

A implementacao usa apenas a stdlib (``socket``) e e reentrante: o aninhamento de
contextos conta a profundidade e so restaura quando o ultimo sai.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator

#: Enderecos considerados locais (permitidos quando ``allow_loopback=True``).
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "", None})


class OfflineViolation(RuntimeError):
    """Tentativa de acesso a rede durante operacao offline (ADR-027 §0)."""


# Guardados no import para restauracao exata.
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection

_depth = 0
_allow_loopback = False


def is_network_disabled() -> bool:
    """True enquanto o guard offline estiver ativo."""
    return _depth > 0


def _is_local(address: object) -> bool:
    if isinstance(address, tuple) and address:
        return address[0] in _LOOPBACK
    if isinstance(address, str):
        return address in _LOOPBACK
    return False


def _guard_connect(self: socket.socket, address: object) -> object:
    if _allow_loopback and _is_local(address):
        return _socket_connect(self, address)  # type: ignore[arg-type]
    raise OfflineViolation(
        "acesso a rede bloqueado: operacao offline (ADR-027 §0); "
        f"destino={address!r}"
    )


def _guard_connect_ex(self: socket.socket, address: object) -> int:
    if _allow_loopback and _is_local(address):
        return _socket_connect_ex(self, address)  # type: ignore[arg-type]
    raise OfflineViolation(
        "acesso a rede bloqueado: operacao offline (ADR-027 §0); "
        f"destino={address!r}"
    )


def _guard_create_connection(address: object, *args: object, **kwargs: object) -> object:
    if _allow_loopback and _is_local(address):
        return _create_connection(address, *args, **kwargs)  # type: ignore[arg-type]
    raise OfflineViolation(
        "acesso a rede bloqueado: operacao offline (ADR-027 §0); "
        f"destino={address!r}"
    )


@contextmanager
def offline(*, allow_loopback: bool = False) -> Iterator[None]:
    """Desabilita conexoes de rede enquanto o contexto estiver ativo.

    Args:
        allow_loopback: se ``True``, permite conexoes para ``localhost``/
        ``127.0.0.1``/``::1`` (uteis para servicos locais); qualquer outro
        destino continua bloqueado.

    Raises:
        OfflineViolation: ao tentar abrir uma conexao bloqueada dentro do bloco.

    Example:
        >>> from offline import offline, is_network_disabled
        >>> with offline():
        ...     assert is_network_disabled()
        >>> is_network_disabled()
        False
    """
    global _depth, _allow_loopback
    first = _depth == 0
    if first:
        _allow_loopback = allow_loopback
        socket.socket.connect = _guard_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _guard_connect_ex  # type: ignore[method-assign]
        socket.create_connection = _guard_create_connection  # type: ignore[assignment]
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0:
            socket.socket.connect = _socket_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = _socket_connect_ex  # type: ignore[method-assign]
            socket.create_connection = _create_connection  # type: ignore[assignment]
            _allow_loopback = False
