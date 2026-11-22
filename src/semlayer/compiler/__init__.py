"""Metric -> SQL. Minimal built-in compiler behind a small interface (see ADR-002)."""
from dataclasses import dataclass, field
from typing import Protocol

from semlayer.core import Metric
from semlayer.core.models import ident


class CompileError(ValueError):
    pass


@dataclass
class Compiled:
    sql: str
    params: list = field(default_factory=list)


class Compiler(Protocol):
    def compile(self, m: Metric, dims=(), filters=None, row_filter=None,
                entity_keys=None) -> Compiled: ...


class SimpleCompiler:
    def compile(self, m: Metric, dims=(), filters=None, row_filter=None,
                entity_keys=None) -> Compiled:
        dims, filters = list(dims or []), dict(filters or {})
        bad = [d for d in [*dims, *filters] if d not in m.dimensions]
        if bad:
            raise CompileError(f"not allowed for {m.name}: {bad}; allowed: {m.dimensions}")
        dims = [d for d in dims if d not in filters]  # a dim pinned by a filter is constant
        where = [f"({f})" for f in m.filters]
        params: list = []
        for col, val in filters.items():
            where.append(f"{ident(col)} = ?")
            params.append(val)
        if row_filter:
            where.append(f"({row_filter})")
        if entity_keys is not None:
            if not m.entity_key:
                raise CompileError(f"{m.name} is not linked to an entity")
            where.append(f"list_contains(?, {m.entity_key})")
            params.append(list(entity_keys))
        cols = ", ".join([*map(ident, dims), f"{m.expression} AS {m.name}"])
        sql = f"SELECT {cols} FROM {m.table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if dims:
            g = ", ".join(map(ident, dims))
            sql += f" GROUP BY {g} ORDER BY {g}"
        return Compiled(sql, params)