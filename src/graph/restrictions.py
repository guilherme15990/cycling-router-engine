"""Restricoes de acesso observadas no OSM.

Esta camada **preserva** as condicoes; ela nao decide se o arco pode ser
usada em um dado momento. A avaliacao temporal pertence a preparacao da
consulta (o mesmo grafo pode ser utilizavel as 8h e proibido as 18h).

A distincao importante:

* restricao -> pode ou nao pode passar (ex.: ``bicycle=no``);
* preferencia -> pode passar, mas o roteador pode evitar (ex.: superficie ruim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class AccessRule:
    """Condicao de acesso preservada, ainda nao avaliada.

    Exemplo: ``bicycle:conditional=no @ (Mo-Fr 07:00-10:00)`` vira
    ``scope="bicycle"``, ``value="no"`` e ``condition="(Mo-Fr 07:00-10:00)"``.
    """

    scope: str
    value: str
    condition: str | None
    raw: str

    @property
    def is_conditional(self) -> bool:
        return self.condition is not None


@dataclass(frozen=True, slots=True)
class RestrictionSource:
    """Proveniencia de uma restricao derivada de relacao OSM.

    ``relation_id`` e o ID original da relacao; ``tags`` guarda as tags brutas
    (sem interpretacao) e ``version`` identifica a versao da fonte/snapshot.
    """

    relation_id: int | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    version: str | None = None


@dataclass(frozen=True, slots=True)
class TurnRestriction:
    """Restricao de conversao entre dois arcos, preservada do OSM.

    A restricao e avaliada na **transicao** entre arcos, nao ao percorrer um
    arco isolado. O caminho intermediario e dado por ``via_node_id`` (um no) ou
    por ``via_way_ids`` (sequencia de vias de uma restricao ``only_*``).
    """

    id: str
    from_way_id: int
    to_way_id: int
    restriction_type: str
    via_node_id: int | None = None
    via_way_ids: tuple[int, ...] = ()
    condition: str | None = None
    source: RestrictionSource | None = None

    def __post_init__(self) -> None:
        if not self.restriction_type:
            raise ValueError("TurnRestriction exige restriction_type")
        if self.via_node_id is None and not self.via_way_ids:
            raise ValueError("TurnRestriction exige via_node_id ou via_way_ids")

    @property
    def is_conditional(self) -> bool:
        return self.condition is not None


def turn_restriction_from_relation(
    relation: Mapping[str, object],
    version: str | None = None,
) -> TurnRestriction | None:
    """Normaliza uma relacao ``type=restriction`` do OSM em ``TurnRestriction``.

    Nao decide nada: apenas separa ``from``/``via``/``to`` e preserva a tag
    ``restriction`` e a proveniencia. Devolve ``None`` para relacoes que nao
    sejam restricoes ou que nao tenham ``from``/``to``.

    Args:
        relation: Dicionario do elemento OSM com ``id``, ``tags`` e ``members``.
        version: Identificador da versao/snapshot da fonte (proveniencia).

    Returns:
        O ``TurnRestriction`` correspondente, ou ``None`` se nao aplicavel.
    """
    tags = {str(key): str(value) for key, value in dict(relation.get("tags") or {}).items()}
    if tags.get("type") != "restriction":
        return None

    restriction = tags.get("restriction") or tags.get("restriction:bicycle")
    if not restriction:
        return None

    from_way: int | None = None
    to_way: int | None = None
    via_node: int | None = None
    via_ways: list[int] = []

    members = relation.get("members") or ()
    for member in members:
        data = dict(member)  # type: ignore[arg-type]
        role = str(data.get("role") or "")
        ref = data.get("ref")
        kind = str(data.get("type") or "")
        if ref is None:
            continue
        reference = int(ref)
        if role == "from" and kind == "way":
            from_way = reference
        elif role == "to" and kind == "way":
            to_way = reference
        elif role == "via":
            if kind == "node":
                via_node = reference
            elif kind == "way":
                via_ways.append(reference)

    if from_way is None or to_way is None:
        return None
    if via_node is None and not via_ways:
        return None

    relation_id = relation.get("id")
    return TurnRestriction(
        id=f"r{relation_id}" if relation_id is not None else f"r{from_way}:{to_way}",
        from_way_id=from_way,
        to_way_id=to_way,
        restriction_type=restriction,
        via_node_id=via_node,
        via_way_ids=tuple(via_ways),
        condition=tags.get("restriction:conditional"),
        source=RestrictionSource(
            relation_id=int(relation_id) if relation_id is not None else None,
            tags=tags,
            version=version,
        ),
    )


def parse_conditional_restrictions(
    tags: Mapping[str, str],
) -> tuple[AccessRule, ...]:
    """Extrai todas as tags ``*:conditional`` sem interpretar a condicao."""
    restrictions: list[AccessRule] = []

    for key in sorted(tags):
        if not key.endswith(":conditional"):
            continue
        raw = (tags.get(key) or "").strip()
        if not raw:
            continue
        scope = key[: -len(":conditional")]
        value, condition = _split_value_condition(raw)
        restrictions.append(
            AccessRule(
                scope=scope,
                value=value,
                condition=condition,
                raw=raw,
            )
        )

    return tuple(restrictions)


def _split_value_condition(raw: str) -> tuple[str, str | None]:
    """Separa ``valor @ (condicao)``; devolve condicao bruta para avaliacao futura."""
    if "@" not in raw:
        return raw.strip().lower(), None
    value, _, condition = raw.partition("@")
    condition = condition.strip()
    return value.strip().lower(), condition or None
