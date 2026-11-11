"""Leitura de arquivos OpenStreetMap no formato PBF (``.osm.pbf``).

Implementacao em Python puro (somente biblioteca padrao) do subconjunto do
formato PBF necessario para reproduzir o que o cliente Overpass retorna:
apenas as ``way`` s com ``highway`` aceito e os nos por elas referenciados.

O arquivo e lido em duas passagens *streaming*, de modo que o consumo de
memoria fique limitado ao numero de vias selecionadas e seus nos, sem nunca
carregar o arquivo inteiro:

1. percorre os ``PrimitiveBlock`` lendo as ``way`` s e selecionando as que
   possuem ``highway`` aceito (e ``area != yes``), coletando os ids dos nos
   referenciados;
2. percorre novamente o arquivo lendo ``Node``/``DenseNodes`` e mantendo
   apenas os ids necessarios.

Referencias do formato: ``OSM PBF Format`` (wiki.openstreetmap.org) e os
arquivos ``fileformat.proto`` / ``osmformat.proto`` do projeto OSMPBF.

Example:
    >>> from osm.pbf import read_header, read_osm_pbf
    >>> header = read_header("planet.osm.pbf")  # doctest: +SKIP
    >>> data = read_osm_pbf("planet.osm.pbf")  # doctest: +SKIP
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .overpass import DEFAULT_HIGHWAYS
from .parse import OverpassData, RawNode, RawWay

__all__ = [
    "PbfError",
    "PbfHeader",
    "PbfStats",
    "read_header",
    "read_osm_pbf",
]

# Limites impostos pelo formato (fileformat.proto).
_MAX_BLOB_HEADER_SIZE = 64 * 1024
_MAX_BLOB_SIZE = 32 * 1024 * 1024

try:  # zstd e opcional; o blob usual dos extratores publicos usa zlib.
    from compression import zstd as _zstd  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - depende da versao do Python
    _zstd = None


class PbfError(RuntimeError):
    """Falha ao ler um arquivo OpenStreetMap PBF."""


@dataclass(frozen=True, slots=True)
class PbfHeader:
    """Metadados do bloco ``OSMHeader``.

    Attributes:
        bbox: Caixa envolvente em graus no formato ``(sul, oeste, norte, leste)``,
            ou ``None`` quando o arquivo nao a declara.
        writing_program: Programa que gerou o arquivo (tag ``writingprogram``).
        source: Origem declarada dos dados (tag ``source``).
        required_features: Features obrigatorias do ``HeaderBlock``.
        optional_features: Features opcionais do ``HeaderBlock``.
    """

    bbox: tuple[float, float, float, float] | None
    writing_program: str | None
    source: str | None
    required_features: tuple[str, ...] = ()
    optional_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PbfStats:
    """Contadores da leitura (auditoria do que foi percorrido/selecionado).

    ``blobs`` conta apenas os blocos ``OSMData`` percorridos na primeira
    passagem.
    """

    blobs: int
    ways_seen: int
    ways_selected: int
    nodes_seen: int
    nodes_selected: int


# ---------------------------------------------------------------------------
# Varints, zigzag e iteracao de campos protobuf
# ---------------------------------------------------------------------------
def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """Le um varint a partir de ``pos``; retorna ``(valor, nova_posicao)``."""
    result = 0
    shift = 0
    try:
        while True:
            byte = data[pos]
            pos += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result, pos
            shift += 7
    except IndexError as exc:  # pragma: no cover - dado malformado
        raise PbfError("varint truncado") from exc


def _zigzag(value: int) -> int:
    """Decodifica um inteiro ``sint64``/``sint32`` (zigzag)."""
    return (value >> 1) ^ -(value & 1)


def _to_signed(value: int, bits: int) -> int:
    """Interpreta um varint como inteiro de ``bits`` bits em complemento de 2."""
    if value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


def _extend_varints(target: list[int], payload: bytes) -> None:
    """Acrescenta a ``target`` todos os varints de um campo *packed*."""
    pos = 0
    size = len(payload)
    while pos < size:
        value, pos = _varint(payload, pos)
        target.append(value)


def _extend_packed(target: list[int], wire: int, value: object) -> None:
    """Aceita tanto a forma *packed* (wire 2) quanto a repetida (wire 0)."""
    if wire == 2:
        _extend_varints(target, value)  # type: ignore[arg-type]
    elif wire == 0:
        target.append(value)  # type: ignore[arg-type]


def _iter_fields(data: bytes) -> Iterator[tuple[int, int, object]]:
    """Itera ``(field_number, wire_type, value)`` de uma mensagem protobuf.

    Para *wire type* 0, ``value`` e um ``int``; para *wire type* 2, ``bytes``;
    para 1/5, o trecho bruto de 8/4 bytes.
    """
    pos = 0
    size = len(data)
    while pos < size:
        key, pos = _varint(data, pos)
        field = key >> 3
        wire = key & 0x7
        if wire == 0:
            value, pos = _varint(data, pos)
            yield field, wire, value
        elif wire == 2:
            length, pos = _varint(data, pos)
            end = pos + length
            if end > size:  # pragma: no cover - dado malformado
                raise PbfError("campo length-delimited truncado")
            yield field, wire, data[pos:end]
            pos = end
        elif wire == 1:
            yield field, wire, data[pos : pos + 8]
            pos += 8
        elif wire == 5:
            yield field, wire, data[pos : pos + 4]
            pos += 4
        else:  # pragma: no cover - dado malformado
            raise PbfError(f"wire type nao suportado: {wire}")


def _accumulate_zigzag(raws: list[int]) -> list[int]:
    """Deltas ``sint64``: decodifica zigzag de cada varint e acumula."""
    last = 0
    out: list[int] = []
    append = out.append
    for raw in raws:
        last += (raw >> 1) ^ -(raw & 1)
        append(last)
    return out


# ---------------------------------------------------------------------------
# Blob / BlobHeader
# ---------------------------------------------------------------------------
def _read_exact(handle: object, size: int) -> bytes:
    data = handle.read(size)  # type: ignore[attr-defined]
    if len(data) != size:
        raise PbfError("arquivo PBF truncado")
    return data


def _parse_blob_header(data: bytes) -> tuple[str, int]:
    blob_type: str | None = None
    datasize: int | None = None
    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 2:
            blob_type = value.decode("utf-8")  # type: ignore[union-attr]
        elif field == 3 and wire == 0:
            datasize = int(value)  # type: ignore[arg-type]
    if blob_type is None or datasize is None:
        raise PbfError("BlobHeader incompleto")
    return blob_type, datasize


def _decode_blob(data: bytes) -> bytes:
    """Descomprime um ``Blob`` e devolve o payload bruto."""
    raw: bytes | None = None
    zlib_data: bytes | None = None
    zstd_data: bytes | None = None
    raw_size: int | None = None
    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 2:
            raw = value  # type: ignore[assignment]
        elif field == 2 and wire == 0:
            raw_size = int(value)  # type: ignore[arg-type]
        elif field == 3 and wire == 2:
            zlib_data = value  # type: ignore[assignment]
        elif field == 7 and wire == 2:
            zstd_data = value  # type: ignore[assignment]

    if raw is None:
        if zlib_data is not None:
            try:
                raw = zlib.decompress(zlib_data)
            except zlib.error as exc:
                raise PbfError(f"falha ao descomprimir blob zlib: {exc}") from exc
        elif zstd_data is not None and _zstd is not None:
            raw = _zstd.decompress(zstd_data)  # pragma: no cover - opcional
        else:
            raise PbfError("Blob sem payload suportado (raw/zlib/zstd)")

    if raw_size is not None and raw_size != len(raw):
        raise PbfError(
            f"raw_size inconsistente: declarado {raw_size}, obtido {len(raw)}"
        )
    return raw


def _iter_blobs(path: Path) -> Iterator[tuple[str, bytes]]:
    """Gera ``(tipo, payload_descomprimido)`` para cada registro do arquivo."""
    with open(path, "rb") as handle:
        while True:
            size_bytes = handle.read(4)
            if not size_bytes:
                return
            if len(size_bytes) != 4:
                raise PbfError("arquivo PBF truncado no tamanho do BlobHeader")
            (header_size,) = struct.unpack(">I", size_bytes)
            if not 0 < header_size <= _MAX_BLOB_HEADER_SIZE:
                raise PbfError(f"tamanho de BlobHeader invalido: {header_size}")
            header_bytes = _read_exact(handle, header_size)
            blob_type, datasize = _parse_blob_header(header_bytes)
            if not 0 < datasize <= _MAX_BLOB_SIZE:
                raise PbfError(f"tamanho de Blob invalido: {datasize}")
            blob_bytes = _read_exact(handle, datasize)
            yield blob_type, _decode_blob(blob_bytes)


# ---------------------------------------------------------------------------
# HeaderBlock
# ---------------------------------------------------------------------------
def _parse_header_bbox(data: bytes) -> tuple[float, float, float, float]:
    left = right = top = bottom = 0
    for field, wire, value in _iter_fields(data):
        if wire != 0:
            continue
        if field == 1:
            left = _zigzag(int(value))  # type: ignore[arg-type]
        elif field == 2:
            right = _zigzag(int(value))  # type: ignore[arg-type]
        elif field == 3:
            top = _zigzag(int(value))  # type: ignore[arg-type]
        elif field == 4:
            bottom = _zigzag(int(value))  # type: ignore[arg-type]
    # (sul, oeste, norte, leste) == (bottom, left, top, right); nanodegrees.
    return (bottom * 1e-9, left * 1e-9, top * 1e-9, right * 1e-9)


def _parse_header_block(data: bytes) -> PbfHeader:
    bbox: tuple[float, float, float, float] | None = None
    writing_program: str | None = None
    source: str | None = None
    required: list[str] = []
    optional: list[str] = []

    for field, wire, value in _iter_fields(data):
        if wire != 2:
            continue
        if field == 1:
            bbox = _parse_header_bbox(value)  # type: ignore[arg-type]
        elif field == 4:
            writing_program = value.decode("utf-8")  # type: ignore[union-attr]
        elif field == 5:
            required.append(value.decode("utf-8"))  # type: ignore[union-attr]
        elif field == 6:
            optional.append(value.decode("utf-8"))  # type: ignore[union-attr]
        elif field == 16:
            source = value.decode("utf-8")  # type: ignore[union-attr]

    return PbfHeader(
        bbox=bbox,
        writing_program=writing_program,
        source=source,
        required_features=tuple(required),
        optional_features=tuple(optional),
    )


def read_header(path: str | Path) -> PbfHeader:
    """Le o ``OSMHeader`` do arquivo e devolve seus metadados.

    Args:
        path: Caminho do arquivo ``.osm.pbf``.

    Returns:
        O :class:`PbfHeader` com bbox (se houver), programa de escrita e origem.

    Raises:
        PbfError: Se o arquivo nao contiver um bloco ``OSMHeader``.
    """
    for blob_type, data in _iter_blobs(Path(path)):
        if blob_type == "OSMHeader":
            return _parse_header_block(data)
    raise PbfError("arquivo PBF sem bloco OSMHeader")


# ---------------------------------------------------------------------------
# PrimitiveBlock / PrimitiveGroup
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _PrimitiveBlock:
    strings: list[str]
    groups: list[bytes]
    granularity: int
    lat_offset: int
    lon_offset: int


def _parse_string_table(data: bytes) -> list[str]:
    strings: list[str] = []
    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 2:
            strings.append(value.decode("utf-8"))  # type: ignore[union-attr]
    return strings


def _parse_primitive_block(data: bytes) -> _PrimitiveBlock:
    strings: list[str] = []
    groups: list[bytes] = []
    granularity = 100
    lat_offset = 0
    lon_offset = 0

    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 2:
            strings = _parse_string_table(value)  # type: ignore[arg-type]
        elif field == 2 and wire == 2:
            groups.append(value)  # type: ignore[arg-type]
        elif field == 17 and wire == 0:
            granularity = _to_signed(int(value), 32)  # type: ignore[arg-type]
        elif field == 19 and wire == 0:
            lat_offset = _to_signed(int(value), 64)  # type: ignore[arg-type]
        elif field == 20 and wire == 0:
            lon_offset = _to_signed(int(value), 64)  # type: ignore[arg-type]

    return _PrimitiveBlock(strings, groups, granularity, lat_offset, lon_offset)


def _tags_from_keys_vals(
    strings: list[str], keys: list[int], vals: list[int]
) -> dict[str, str]:
    if not keys:
        return {}
    size = len(strings)
    tags: dict[str, str] = {}
    for key_index, value_index in zip(keys, vals):
        if key_index < size and value_index < size:
            tags[strings[key_index]] = strings[value_index]
    return tags


def _parse_way(data: bytes, strings: list[str]) -> RawWay:
    """Decodifica uma mensagem ``Way`` de um ``PrimitiveGroup``.

    Segundo ``osmformat.proto``, ``keys``/``vals`` sao indices na
    ``StringTable`` (``uint32``, *sem* delta); apenas ``refs`` e *delta coded*
    (``sint64``, zigzag).
    """
    way_id = 0
    keys: list[int] = []
    vals: list[int] = []
    refs_raw: list[int] = []

    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 0:
            way_id = int(value)  # type: ignore[arg-type]
        elif field == 2:
            _extend_packed(keys, wire, value)
        elif field == 3:
            _extend_packed(vals, wire, value)
        elif field == 8:
            _extend_packed(refs_raw, wire, value)

    refs = _accumulate_zigzag(refs_raw)
    return RawWay(
        id=way_id,
        nodes=tuple(refs),
        tags=_tags_from_keys_vals(strings, keys, vals),
    )


def _iter_group_ways(group: bytes, strings: list[str]) -> Iterator[RawWay]:
    for field, wire, value in _iter_fields(group):
        if field == 3 and wire == 2:
            yield _parse_way(value, strings)  # type: ignore[arg-type]


def _decode_plain_node(
    data: bytes,
    strings: list[str],
    needed: set[int],
    out: dict[int, RawNode],
    granularity: int,
    lat_offset: int,
    lon_offset: int,
) -> None:
    """Decodifica uma mensagem ``Node`` (formato nao denso)."""
    node_id = 0
    raw_lat = 0
    raw_lon = 0
    keys: list[int] = []
    vals: list[int] = []

    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 0:
            node_id = _zigzag(int(value))  # type: ignore[arg-type]
        elif field == 2:
            _extend_packed(keys, wire, value)
        elif field == 3:
            _extend_packed(vals, wire, value)
        elif field == 8 and wire == 0:
            raw_lat = _zigzag(int(value))  # type: ignore[arg-type]
        elif field == 9 and wire == 0:
            raw_lon = _zigzag(int(value))  # type: ignore[arg-type]

    if node_id not in needed:
        return

    out[node_id] = RawNode(
        id=node_id,
        lat=1e-9 * (lat_offset + granularity * raw_lat),
        lon=1e-9 * (lon_offset + granularity * raw_lon),
        tags=_tags_from_keys_vals(strings, keys, vals),
    )


def _decode_dense_nodes(
    data: bytes,
    strings: list[str],
    needed: set[int],
    out: dict[int, RawNode],
    granularity: int,
    lat_offset: int,
    lon_offset: int,
) -> int:
    """Decodifica um ``DenseNodes``; retorna quantos nos foram vistos."""
    raw_ids: list[int] = []
    raw_lat: list[int] = []
    raw_lon: list[int] = []
    keys_vals: list[int] = []

    for field, wire, value in _iter_fields(data):
        if field == 1:
            _extend_packed(raw_ids, wire, value)
        elif field == 8:
            _extend_packed(raw_lat, wire, value)
        elif field == 9:
            _extend_packed(raw_lon, wire, value)
        elif field == 10:
            _extend_packed(keys_vals, wire, value)

    ids = _accumulate_zigzag(raw_ids)
    lats = _accumulate_zigzag(raw_lat)
    lons = _accumulate_zigzag(raw_lon)

    lat_scale = granularity * 1e-9
    lon_scale = granularity * 1e-9
    lat_off = lat_offset * 1e-9
    lon_off = lon_offset * 1e-9

    total = len(keys_vals)
    cursor = 0
    for index, node_id in enumerate(ids):
        tags: dict[str, str] = {}
        while cursor < total:
            key_index = keys_vals[cursor]
            if key_index == 0:
                cursor += 1
                break
            if cursor + 1 >= total:  # pragma: no cover - dado malformado
                cursor = total
                break
            value_index = keys_vals[cursor + 1]
            cursor += 2
            if key_index < len(strings) and value_index < len(strings):
                tags[strings[key_index]] = strings[value_index]

        if node_id in needed:
            out[node_id] = RawNode(
                id=node_id,
                lat=lat_off + lat_scale * lats[index],
                lon=lon_off + lon_scale * lons[index],
                tags=tags,
            )
    return len(ids)


def _decode_group_nodes(
    group: bytes,
    strings: list[str],
    needed: set[int],
    out: dict[int, RawNode],
    granularity: int,
    lat_offset: int,
    lon_offset: int,
) -> int:
    """Decodifica os nos (densos ou nao) de um ``PrimitiveGroup``."""
    seen = 0
    for field, wire, value in _iter_fields(group):
        if field == 1 and wire == 2:
            _decode_plain_node(
                value, strings, needed, out, granularity, lat_offset, lon_offset  # type: ignore[arg-type]
            )
            seen += 1
        elif field == 2 and wire == 2:
            seen += _decode_dense_nodes(
                value, strings, needed, out, granularity, lat_offset, lon_offset  # type: ignore[arg-type]
            )
    return seen


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------
def read_osm_pbf(
    path: str | Path,
    *,
    highways: tuple[str, ...] | None = None,
    with_stats: bool = False,
) -> OverpassData | tuple[OverpassData, PbfStats]:
    """Le um ``.osm.pbf`` e monta um :class:`OverpassData`.

    Reproduz a semantica de :meth:`OverpassClient.fetch`:

    * mantem toda ``way`` cujo ``highway`` esteja em ``highways`` (por padrao
      :data:`DEFAULT_HIGHWAYS`) e cujo ``area`` nao seja ``yes``;
    * mantem exatamente os nos referenciados por essas vias, com suas tags
      (necessarias depois para ``ele=*``);
    * preserva o id original e a lista ordenada completa de refs de cada via.

    Args:
        path: Caminho do arquivo ``.osm.pbf``.
        highways: Valores aceitos de ``highway=*``. ``None`` usa o mesmo
            conjunto do cliente Overpass (:data:`DEFAULT_HIGHWAYS`).
        with_stats: Quando ``True``, devolve ``(data, stats)``.

    Returns:
        O :class:`OverpassData` (ou ``(data, PbfStats)`` se ``with_stats``).
    """
    allowed = frozenset(DEFAULT_HIGHWAYS if highways is None else highways)
    source = Path(path)

    # ----- Passagem 1: seleciona as vias e coleta os nos referenciados -----
    ways: list[RawWay] = []
    needed: set[int] = set()
    blobs = 0
    ways_seen = 0

    for blob_type, blob in _iter_blobs(source):
        if blob_type != "OSMData":
            continue
        blobs += 1
        block = _parse_primitive_block(blob)
        for group in block.groups:
            for way in _iter_group_ways(group, block.strings):
                ways_seen += 1
                tags = way.tags
                highway = tags.get("highway")
                if highway is None or highway not in allowed:
                    continue
                if tags.get("area") == "yes":
                    continue
                ways.append(way)
                needed.update(way.nodes)

    # ----- Passagem 2: le os nos e mantem apenas os necessarios ------------
    nodes: dict[int, RawNode] = {}
    nodes_seen = 0

    for blob_type, blob in _iter_blobs(source):
        if blob_type != "OSMData":
            continue
        block = _parse_primitive_block(blob)
        for group in block.groups:
            nodes_seen += _decode_group_nodes(
                group,
                block.strings,
                needed,
                nodes,
                block.granularity,
                block.lat_offset,
                block.lon_offset,
            )

    data = OverpassData(nodes=nodes, ways=ways)
    if with_stats:
        return data, PbfStats(
            blobs=blobs,
            ways_seen=ways_seen,
            ways_selected=len(ways),
            nodes_seen=nodes_seen,
            nodes_selected=len(nodes),
        )
    return data
