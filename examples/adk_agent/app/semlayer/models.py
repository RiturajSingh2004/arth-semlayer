# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Semantic layer definitions and draft -> certified -> deprecated lifecycle."""

import re
from enum import StrEnum
from typing import ClassVar, Literal

import sqlglot
from pydantic import BaseModel, Field, field_validator
from sqlglot import exp

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(value: str) -> str:
    """Validate that ``value`` is a safe SQL identifier."""
    if not IDENT.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


class State(StrEnum):
    """Certification lifecycle state for a semantic definition."""

    draft = "draft"
    certified = "certified"
    deprecated = "deprecated"


ALLOWED: dict[State, set[State]] = {
    State.draft: {State.certified, State.deprecated},
    State.certified: {State.deprecated},
    State.deprecated: set(),
}


class InvalidTransition(Exception):
    """Raised when a lifecycle transition violates governance rules."""


class Evidence(BaseModel):
    """Provenance and lineage metadata attached to a semantic node."""

    queries: list[str] = Field(default_factory=list)
    lineage: list[str] = Field(default_factory=list)
    explanation: str = ""
    usage: int = 0
    users: list[str] = Field(default_factory=list)
    suggested_owner: str | None = None
    source: str = ""


class Node(BaseModel):
    """Base governed semantic node."""

    kind: ClassVar[str] = "node"
    name: str
    description: str = ""
    state: State = State.draft
    owner: str | None = None
    approvers: list[str] = Field(default_factory=list)
    version: int = 0
    evidence: Evidence = Field(default_factory=Evidence)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return ident(value)


class Metric(Node):
    """Certified metric definition compiled by SimpleCompiler."""

    kind: ClassVar[str] = "metric"
    concept: str = ""
    table: str
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"]
    column: str = "*"
    filters: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    entity: str | None = None
    entity_key: str | None = None

    @field_validator("table", "column")
    @classmethod
    def _validate_cols(cls, value: str) -> str:
        return value if value == "*" else ident(value)

    @field_validator("dimensions")
    @classmethod
    def _validate_dims(cls, values: list[str]) -> list[str]:
        return [ident(item) for item in values]

    @property
    def expression(self) -> str:
        """Return the aggregate SQL expression for this metric."""
        col = self.column
        mapping = {
            "count_distinct": f"COUNT(DISTINCT {col})",
            "count": f"COUNT({col})",
        }
        return mapping.get(self.agg, f"{self.agg.upper()}({col})")


class Entity(Node):
    """Entity node mapped to a physical warehouse table and primary key."""

    kind: ClassVar[str] = "entity"
    table: str
    key: str

    @field_validator("table", "key")
    @classmethod
    def _validate_entity_identifiers(cls, value: str) -> str:
        return ident(value)


class Edge(Node):
    """Foreign-key relationship ``src.table.src_key = dst.table.dst_key``."""

    kind: ClassVar[str] = "edge"
    src: str
    rel: str
    dst: str
    src_key: str
    dst_key: str

    @field_validator("src", "rel", "dst", "src_key", "dst_key")
    @classmethod
    def _validate_edge_identifiers(cls, value: str) -> str:
        return ident(value)


class Rule(Node):
    """Access policy rule. Default deny; certified rules only; deny beats allow."""

    kind: ClassVar[str] = "rule"
    role: str
    metric: str = "*"
    effect: Literal["allow", "deny"] = "allow"
    row_filter: str | None = None
    deny_dims: list[str] = Field(default_factory=list)

    @field_validator("row_filter")
    @classmethod
    def _validate_row_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            tree = sqlglot.condition(value)
        except sqlglot.errors.SqlglotError as exc:
            raise ValueError(f"invalid row_filter: {exc}") from exc
        if tree.find(exp.Select, exp.Subquery, exp.Command):
            raise ValueError("row_filter must be a plain predicate")
        return value


KINDS: dict[str, type[Node]] = {
    "metric": Metric,
    "entity": Entity,
    "edge": Edge,
    "rule": Rule,
}


def transition(node: Node, to: State) -> Node:
    """Advance ``node`` along the draft -> certified -> deprecated state machine."""
    if to not in ALLOWED[node.state]:
        raise InvalidTransition(
            f"{node.kind} {node.name}: {node.state.value} -> {to.value}"
        )
    if to is State.certified and not node.owner:
        raise InvalidTransition(
            f"{node.kind} {node.name}: owner required before certification"
        )
    node.state = to
    if to is State.certified:
        node.version += 1
    return node
