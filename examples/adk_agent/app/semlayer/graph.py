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
"""Foreign-key entity graph traversed via governed parameterized JOIN queries."""

from typing import Any

from .models import Edge, Entity, ident


class Graph:
    """Typed entity/relationship graph compiled to parameterized SQL joins."""

    def __init__(self, entities: list[Entity], edges: list[Edge]) -> None:
        self.entities = {entity.name: entity for entity in entities}
        self.edges = edges

    def traverse_sql(
        self,
        start: str,
        path: list[str],
        filters: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str, list[Any]]:
        """Build parameterized SQL returning ``start`` keys reachable along ``path``."""
        if start not in self.entities:
            raise ValueError(f"unknown entity {start}")
        chain = [self.entities[start]]
        current = start
        joins: list[str] = []
        for rel in path:
            edge = next(
                (
                    candidate
                    for candidate in self.edges
                    if candidate.src == current and candidate.rel == rel
                ),
                None,
            )
            if edge is None:
                raise ValueError(f"no relationship {rel} from {current}")
            nxt = self.entities[edge.dst]
            alias_prev = f"e{len(chain) - 1}"
            alias_next = f"e{len(chain)}"
            joins.append(
                f"JOIN {ident(nxt.table)} {alias_next} "
                f"ON {alias_prev}.{ident(edge.src_key)} = "
                f"{alias_next}.{ident(edge.dst_key)}"
            )
            chain.append(nxt)
            current = edge.dst

        where: list[str] = []
        params: list[Any] = []
        for ent_name, conds in (filters or {}).items():
            indices = [
                idx for idx, node in enumerate(chain) if node.name == ent_name
            ]
            if not indices:
                raise ValueError(f"{ent_name} is not on the path")
            for col, val in conds.items():
                where.append(f"e{indices[0]}.{ident(col)} = ?")
                params.append(val)

        start_entity = chain[0]
        sql = (
            f"SELECT DISTINCT e0.{ident(start_entity.key)} "  # noqa: S608 -- identifiers validated via ident()
            f"FROM {ident(start_entity.table)} e0"
        )
        if joins:
            sql += " " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return f"{sql} ORDER BY 1 LIMIT 10000", params

    def traverse(
        self,
        warehouse: Any,
        start: str,
        path: list[str],
        filters: dict[str, dict[str, Any]] | None = None,
    ) -> list[Any]:
        """Execute graph traversal against ``warehouse`` and return entity keys."""
        from .warehouse import ensure_warehouse

        sql, params = self.traverse_sql(start, path, filters)
        return [
            row[0]
            for row in ensure_warehouse(warehouse).query(sql, params).rows
        ]
