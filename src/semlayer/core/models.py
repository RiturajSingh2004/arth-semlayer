"""Definitions and the draft -> certified -> deprecated state machine."""
import re
from enum import StrEnum
from typing import ClassVar, Literal

import sqlglot
from pydantic import BaseModel, Field, field_validator
from sqlglot import exp

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(v: str) -> str:
    if not IDENT.match(v):
        raise ValueError(f"invalid identifier: {v!r}")
    return v


class State(StrEnum):
    draft = "draft"
    certified = "certified"
    deprecated = "deprecated"


ALLOWED = {
    State.draft: {State.certified, State.deprecated},
    State.certified: {State.deprecated},
    State.deprecated: set(),
}


class InvalidTransition(Exception):
    pass


class Evidence(BaseModel):
    queries: list[str] = []
    lineage: list[str] = []
    explanation: str = ""
    usage: int = 0
    users: list[str] = []
    suggested_owner: str | None = None
    source: str = ""  # e.g. query_log, schema, manual


class Node(BaseModel):
    kind: ClassVar[str] = "node"
    name: str
    description: str = ""
    state: State = State.draft
    owner: str | None = None
    approvers: list[str] = []
    version: int = 0
    evidence: Evidence = Field(default_factory=Evidence)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return ident(v)


class Metric(Node):
    kind: ClassVar[str] = "metric"
    concept: str = ""
    table: str
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"]
    column: str = "*"
    filters: list[str] = []
    dimensions: list[str] = []
    entity: str | None = None
    entity_key: str | None = None  # column in `table` holding the entity key

    @field_validator("table", "column")
    @classmethod
    def _cols(cls, v):
        return v if v == "*" else ident(v)

    @field_validator("dimensions")
    @classmethod
    def _dims(cls, v):
        return [ident(x) for x in v]

    @property
    def expression(self) -> str:
        c = self.column
        return {"count_distinct": f"COUNT(DISTINCT {c})", "count": f"COUNT({c})"}.get(
            self.agg, f"{self.agg.upper()}({c})"
        )

    @property
    def logic(self) -> tuple:
        return (self.table, self.agg, self.column, tuple(sorted(self.filters)))


class Entity(Node):
    kind: ClassVar[str] = "entity"
    table: str
    key: str


class Edge(Node):
    """src.table.src_key = dst.table.dst_key; `rel` reads src -rel-> dst."""
    kind: ClassVar[str] = "edge"
    src: str
    rel: str
    dst: str
    src_key: str
    dst_key: str


class Rule(Node):
    """Access rule. Default deny; only certified rules apply; deny beats allow.

    effect=allow grants access (optionally limited by row_filter / deny_dims).
    effect=deny with no deny_dims blocks the metric; with deny_dims it only restricts those
    dimensions and grants nothing.
    """
    kind: ClassVar[str] = "rule"
    role: str
    metric: str = "*"
    effect: Literal["allow", "deny"] = "allow"
    row_filter: str | None = None
    deny_dims: list[str] = []

    @field_validator("row_filter")
    @classmethod
    def _row_filter(cls, v):
        if v is None:
            return v
        try:
            tree = sqlglot.condition(v)
        except sqlglot.errors.SqlglotError as e:
            raise ValueError(f"invalid row_filter: {e}") from e
        if tree.find(exp.Select, exp.Subquery, exp.Command):
            raise ValueError("row_filter must be a plain predicate")
        return v


KINDS = {"metric": Metric, "entity": Entity, "edge": Edge, "rule": Rule}


def transition(node: Node, to: State) -> Node:
    if to not in ALLOWED[node.state]:
        raise InvalidTransition(f"{node.kind} {node.name}: {node.state.value} -> {to.value}")
    if to is State.certified and not node.owner:
        raise InvalidTransition(f"{node.kind} {node.name}: owner required before certification")
    node.state = to
    if to is State.certified:
        node.version += 1
    return node
