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
"""Deterministic metric-to-SQL compiler with parameterized filter binding."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import Metric, ident


class CompileError(ValueError):
    """Raised when a metric query requests disallowed dimensions or entities."""


@dataclass
class Compiled:
    """Parameterized SQL statement produced by ``SimpleCompiler``."""

    sql: str
    params: list[Any] = field(default_factory=list)


class Compiler(Protocol):
    """Protocol for compiling certified metrics into parameterized SQL."""

    def compile(
        self,
        metric: Metric,
        dims: Iterable[str] = (),
        filters: Mapping[str, Any] | None = None,
        row_filter: str | None = None,
        entity_keys: Iterable[Any] | None = None,
    ) -> Compiled: ...


class SimpleCompiler:
    """Minimal governed compiler supporting DuckDB and BigQuery dialects."""

    def __init__(self, dialect: str = "duckdb") -> None:
        self.dialect = dialect

    def compile(
        self,
        metric: Metric,
        dims: Iterable[str] = (),
        filters: Mapping[str, Any] | None = None,
        row_filter: str | None = None,
        entity_keys: Iterable[Any] | None = None,
    ) -> Compiled:
        """Compile ``metric`` into a parameterized ``SELECT`` statement."""
        dim_list = list(dims or [])
        filter_map = dict(filters or {})
        bad = [
            col
            for col in [*dim_list, *filter_map]
            if col not in metric.dimensions
        ]
        if bad:
            raise CompileError(
                f"not allowed for {metric.name}: {bad}; "
                f"allowed: {metric.dimensions}"
            )
        # Dimensions pinned by an equality filter are constant and need no GROUP BY.
        grouped_dims = [col for col in dim_list if col not in filter_map]
        where = [f"({predicate})" for predicate in metric.filters]
        params: list[Any] = []
        for col, val in filter_map.items():
            where.append(f"{ident(col)} = ?")
            params.append(val)
        if row_filter:
            where.append(f"({row_filter})")
        if entity_keys is not None:
            if not metric.entity_key:
                raise CompileError(f"{metric.name} is not linked to an entity")
            keys = list(entity_keys)
            if self.dialect == "bigquery":
                if not keys:
                    where.append("FALSE")
                else:
                    where.append(f"{ident(metric.entity_key)} IN UNNEST(?)")
                    params.append(keys)
            else:
                where.append(f"list_contains(?, {ident(metric.entity_key)})")
                params.append(keys)
        cols = ", ".join(
            [*map(ident, grouped_dims), f"{metric.expression} AS {metric.name}"]
        )
        sql = f"SELECT {cols} FROM {ident(metric.table)}"  # noqa: S608 -- identifiers validated via ident()
        if where:
            sql += " WHERE " + " AND ".join(where)
        if grouped_dims:
            group_expr = ", ".join(map(ident, grouped_dims))
            sql += f" GROUP BY {group_expr} ORDER BY {group_expr}"
        return Compiled(sql, params)
