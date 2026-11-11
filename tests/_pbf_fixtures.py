"""Codificador PBF minimo compartilhado pelos testes (apenas leitura).

Este modulo contem um pequeno codificador ``.osm.pbf`` (varints, campos
length-delimited, ``BlobHeader``/``Blob`` com zlib, ``StringTable``,
``DenseNodes`` e ``Way``) para que os testes sejam **totalmente hermeticos**:
sem depender do arquivo real, da rede ou de qualquer biblioteca externa.

E usado tanto pelos testes do leitor (``test_osm_pbf``) quanto pelos testes da
linha de comando offline (``test_cli_offline``). Nada aqui faz parte do motor.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# Codificador PBF minimo (apenas o subconjunto lido pelo leitor)
# ---------------------------------------------------------------------------
def _uvarint(value: int) -> bytes:
    if value < 0:
        raise ValueError("uvarint nao aceita valores negativos")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _key(field: int, wire: int) -> bytes:
    return _uvarint((field << 3) | wire)


def _fvarint(field: int, value: int) -> bytes:
    return _key(field, 0) + _uvarint(value)


def _fsint(field: int, value: int, bits: int = 64) -> bytes:
    """Campo ``int32``/``int64`` em complemento de 2 (aceita negativos)."""
    return _fvarint(field, value & ((1 << bits) - 1))


def _fbytes(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _uvarint(len(payload)) + payload


def _packed(field: int, values) -> bytes:
    return _fbytes(field, b"".join(_uvarint(v) for v in values))


def _deltas(values) -> list[int]:
    out: list[int] = []
    previous = 0
    for value in values:
        out.append(value - previous)
        previous = value
    return out


class _Strings:
    """StringTable incremental com o indice 0 reservado para a string vazia."""

    def __init__(self) -> None:
        self.table: list[str] = [""]
        self.index: dict[str, int] = {"": 0}

    def add(self, value: str) -> int:
        if value not in self.index:
            self.index[value] = len(self.table)
            self.table.append(value)
        return self.index[value]


def _encode_string_table(strings: list[str]) -> bytes:
    return b"".join(_fbytes(1, value.encode("utf-8")) for value in strings)


def _encode_dense_nodes(nodes, strings: _Strings) -> bytes:
    """``nodes`` = sequencia de ``(id, lat_raw, lon_raw, tags)``."""
    ids = [node[0] for node in nodes]
    lats = [node[1] for node in nodes]
    lons = [node[2] for node in nodes]

    keys_vals: list[int] = []
    for _, _, _, tags in nodes:
        for key, value in tags.items():
            keys_vals.append(strings.add(key))
            keys_vals.append(strings.add(value))
        keys_vals.append(0)

    body = b""
    body += _packed(1, [_zigzag(delta) for delta in _deltas(ids)])
    body += _packed(8, [_zigzag(delta) for delta in _deltas(lats)])
    body += _packed(9, [_zigzag(delta) for delta in _deltas(lons)])
    body += _packed(10, keys_vals)
    return body


def _encode_way(way_id, refs, tags, strings: _Strings, packed_refs: bool = True) -> bytes:
    keys = [strings.add(key) for key in tags]
    vals = [strings.add(value) for value in tags.values()]

    body = _fvarint(1, way_id)  # id int64 (nao zigzag)
    if keys:
        body += _packed(2, keys)
        body += _packed(3, vals)
    if packed_refs:
        body += _packed(8, [_zigzag(delta) for delta in _deltas(refs)])
    else:
        for delta in _deltas(refs):
            body += _fvarint(8, _zigzag(delta))  # forma nao empacotada
    return body


def _encode_node(node_id, lat_raw, lon_raw, tags, strings: _Strings) -> bytes:
    keys = [strings.add(key) for key in tags]
    vals = [strings.add(value) for value in tags.values()]

    body = _fvarint(1, _zigzag(node_id))
    if keys:
        body += _packed(2, keys)
        body += _packed(3, vals)
    body += _fvarint(8, _zigzag(lat_raw))
    body += _fvarint(9, _zigzag(lon_raw))
    return body


def _group(plain=(), dense=None, ways=()) -> bytes:
    body = b""
    for node in plain:
        body += _fbytes(1, node)
    if dense is not None:
        body += _fbytes(2, dense)
    for way in ways:
        body += _fbytes(3, way)
    return body


def _primitive_block(
    strings: _Strings, groups, granularity: int = 100, lat_offset: int = 0, lon_offset: int = 0
) -> bytes:
    body = _fbytes(1, _encode_string_table(strings.table))
    for group in groups:
        body += _fbytes(2, group)
    body += _fsint(17, granularity, 32)
    body += _fsint(19, lat_offset, 64)
    body += _fsint(20, lon_offset, 64)
    return body


def _blob(payload: bytes, compress: bool = True) -> bytes:
    if compress:
        return _fvarint(2, len(payload)) + _fbytes(3, zlib.compress(payload))
    return _fbytes(1, payload)


def _record(blob_type: str, payload: bytes, compress: bool = True) -> bytes:
    blob = _blob(payload, compress=compress)
    header = _fbytes(1, blob_type.encode("utf-8")) + _fvarint(3, len(blob))
    return struct.pack(">I", len(header)) + header + blob


def _header_block(bbox=None, writing_program=None, source=None, required=(), optional=()) -> bytes:
    body = b""
    if bbox is not None:
        south, west, north, east = bbox
        hb = (
            _fvarint(1, _zigzag(round(west * 1e9)))
            + _fvarint(2, _zigzag(round(east * 1e9)))
            + _fvarint(3, _zigzag(round(north * 1e9)))
            + _fvarint(4, _zigzag(round(south * 1e9)))
        )
        body += _fbytes(1, hb)
    if writing_program is not None:
        body += _fbytes(4, writing_program.encode("utf-8"))
    for feature in required:
        body += _fbytes(5, feature.encode("utf-8"))
    for feature in optional:
        body += _fbytes(6, feature.encode("utf-8"))
    if source is not None:
        body += _fbytes(16, source.encode("utf-8"))
    return body


def _deg(raw: int, granularity: int = 100, offset: int = 0) -> float:
    return 1e-9 * (offset + granularity * raw)


class _PbfBuilder:
    """Monta um arquivo ``.osm.pbf`` em memoria e o grava sob demanda."""

    def __init__(self, tmp_path: Path, name: str = "tiny.osm.pbf") -> None:
        self.path = Path(tmp_path) / name
        self.strings = _Strings()
        self._records: list[bytes] = []

    def header(self, **kwargs) -> "_PbfBuilder":
        self._records.append(_record("OSMHeader", _header_block(**kwargs)))
        return self

    def data(
        self,
        dense=(),
        plain=(),
        ways=(),
        granularity: int = 100,
        lat_offset: int = 0,
        lon_offset: int = 0,
        compress: bool = True,
        separate_groups: bool = False,
    ) -> "_PbfBuilder":
        dense_payload = _encode_dense_nodes(dense, self.strings) if dense else None
        encoded_ways = [
            _encode_way(
                way[0],
                way[1],
                way[2],
                self.strings,
                packed_refs=way[3] if len(way) > 3 else True,
            )
            for way in ways
        ]
        encoded_plain = [_encode_node(*node, self.strings) for node in plain]

        if separate_groups:
            groups = []
            if encoded_plain or dense_payload is not None:
                groups.append(_group(plain=encoded_plain, dense=dense_payload))
            if encoded_ways:
                groups.append(_group(ways=encoded_ways))
        else:
            groups = [_group(plain=encoded_plain, dense=dense_payload, ways=encoded_ways)]

        block = _primitive_block(
            self.strings, groups, granularity, lat_offset, lon_offset
        )
        self._records.append(_record("OSMData", block, compress=compress))
        return self

    def write(self) -> Path:
        self.path.write_bytes(b"".join(self._records))
        return self.path
