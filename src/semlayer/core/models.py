"""Definitions and the draft -> certified <-> suspended -> deprecated state machine."""
import ast
import math
import re
from enum import StrEnum
from typing import ClassVar, Literal

import sqlglot
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlglot import exp

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(v: str) -> str:
    if not IDENT.match(v):
        raise ValueError(f"invalid identifier: {v!r}")
    return v


def parse_derived_formula(formula: str) -> list[str]:
    """Validate a restricted arithmetic formula over metric names and numeric constants."""
    if not formula or not formula.strip():
        raise ValueError("derived formula cannot be empty")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid derived formula syntax: {e}") from e

    refs: list[str] = []

    def _walk(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            _walk(node.body)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            _walk(node.left)
            _walk(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            _walk(node.operand)
        elif isinstance(node, ast.Name):
            ident(node.id)
            if node.id not in refs:
                refs.append(node.id)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
            and math.isfinite(node.value)
        ):
            return
        else:
            raise ValueError(f"disallowed construct in derived formula: {type(node).__name__}")

    _walk(tree)
    if not refs:
        raise ValueError("derived formula must reference at least one metric")
    return refs


class State(StrEnum):
    draft = "draft"
    certified = "certified"
    suspended = "suspended"
    deprecated = "deprecated"


ALLOWED = {
    State.draft: {State.certified, State.deprecated},
    State.certified: {State.suspended, State.deprecated},
    State.suspended: {State.certified, State.deprecated},
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
    suspend_reason: str = ""
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
    metric_type: Literal["simple", "ratio", "derived"] = "simple"
    concept: str = ""
    table: str = ""
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"] | None = None
    column: str = "*"
    numerator: str | None = None
    denominator: str | None = None
    formula: str | None = None
    filters: list[str] = []
    dimensions: list[str] = []
    time_dimension: str | None = None
    time_dimensions: list[str] = []
    synonyms: list[str] = []
    entity: str | None = None
    entity_key: str | None = None  # column in `table` holding the entity key

    @field_validator("dimensions", "time_dimensions")
    @classmethod
    def _dims(cls, v):
        return [ident(x) for x in v]

    @field_validator("time_dimension")
    @classmethod
    def _time_dim(cls, v):
        return ident(v) if v else None

    @model_validator(mode="after")
    def _validate_metric_type(self) -> "Metric":
        if self.metric_type == "simple":
            if not self.table:
                raise ValueError("simple metric requires 'table'")
            ident(self.table)
            if self.agg is None:
                raise ValueError("simple metric requires 'agg'")
            if self.column != "*":
                ident(self.column)
        elif self.metric_type == "ratio":
            if not self.numerator or not self.denominator:
                raise ValueError("ratio metric requires 'numerator' and 'denominator'")
            ident(self.numerator)
            ident(self.denominator)
            if self.name in {self.numerator, self.denominator}:
                raise ValueError(f"metric {self.name!r} cannot reference itself")
        elif self.metric_type == "derived":
            if not self.formula:
                raise ValueError("derived metric requires 'formula'")
            refs = parse_derived_formula(self.formula)
            if self.name in refs:
                raise ValueError(f"metric {self.name!r} cannot reference itself")
        return self

    @property
    def dependencies(self) -> list[str]:
        if self.metric_type == "ratio":
            out = [self.numerator] if self.numerator else []
            if self.denominator and self.denominator not in out:
                out.append(self.denominator)
            return out
        if self.metric_type == "derived" and self.formula:
            return parse_derived_formula(self.formula)
        return []

    @property
    def all_time_dimensions(self) -> list[str]:
        out = [self.time_dimension] if self.time_dimension else []
        for d in self.time_dimensions:
            if d not in out:
                out.append(d)
        return out

    @property
    def all_dimensions(self) -> list[str]:
        out = list(self.dimensions)
        for d in self.all_time_dimensions:
            if d not in out:
                out.append(d)
        return out

    @property
    def expression(self) -> str:
        if self.metric_type == "ratio":
            return f"{self.numerator} / NULLIF({self.denominator}, 0)"
        if self.metric_type == "derived":
            return self.formula or ""
        c = self.column
        assert self.agg is not None
        return {"count_distinct": f"COUNT(DISTINCT {c})", "count": f"COUNT({c})"}.get(
            self.agg, f"{self.agg.upper()}({c})"
        )

    @property
    def logic(self) -> tuple:
        if self.metric_type == "ratio":
            return ("ratio", self.numerator, self.denominator)
        if self.metric_type == "derived":
            return ("derived", (self.formula or "").strip())
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
    cardinality: Literal["one_to_many", "many_to_one", "one_to_one", "many_to_many"] = "one_to_many"


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
        node.suspend_reason = ""
    return node
