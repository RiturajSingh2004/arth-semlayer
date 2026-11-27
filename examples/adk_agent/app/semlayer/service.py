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
"""Governed semantic service: certified definitions, RBAC enforced, usage logged."""

import datetime
import decimal
import json
import time
from typing import Any, cast

from .compiler import CompileError, Compiler, SimpleCompiler
from .graph import Graph
from .models import Edge, Entity, Metric, Rule
from .policy import evaluate, unrestricted
from .registry import Registry
from .warehouse import ensure_warehouse

MAX_ROWS = 1000


class Refusal(Exception):
    """Raised when a semantic layer request is refused by governance or policy."""


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


class Service:
    """Single governed path to warehouse data for AI agents."""

    def __init__(
        self,
        reg: Registry,
        warehouse: Any,
        compiler: Compiler | None = None,
        log: bool = True,
    ) -> None:
        self.reg = reg
        self.log = log
        self.wh = ensure_warehouse(warehouse)
        self.compiler = compiler or SimpleCompiler(dialect=self.wh.dialect)

    def _log(
        self,
        event: str,
        metric: str = "",
        role: str = "",
        note: str = "",
        reason: str = "",
    ) -> None:
        if not self.log:
            return
        record = {
            "ts": time.time(),
            "event": event,
            "metric": metric,
            "role": role,
            "note": note,
            "reason": reason,
        }
        with (self.reg.state_dir / "usage.jsonl").open(
            "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(record) + "\n")

    def _metric(self, name: str) -> Metric:
        node = self.reg.get("metric", name)
        if node is None or node.state.value != "certified":
            raise Refusal(f"no certified metric '{name}'")
        return cast(Metric, node)

    def list_metrics(self) -> list[dict[str, Any]]:
        """List certified metrics, their allowed dimensions, and versions."""
        metrics = cast(list[Metric], self.reg.certified("metric"))
        return [
            {
                "name": metric.name,
                "description": metric.description,
                "dimensions": metric.dimensions,
                "version": metric.version,
                "entity": metric.entity,
            }
            for metric in metrics
        ]

    def describe_metric(self, name: str) -> dict[str, Any]:
        """Return formula, owner, version, and lineage of one certified metric."""
        metric = self._metric(name)
        return {
            "name": metric.name,
            "description": metric.description,
            "definition": metric.expression,
            "table": metric.table,
            "filters": metric.filters,
            "dimensions": metric.dimensions,
            "entity": metric.entity,
            "owner": metric.owner,
            "version": metric.version,
            "lineage": metric.evidence.lineage,
            "explanation": metric.evidence.explanation,
        }

    def query_metric(
        self,
        metric: str,
        dims: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        as_role: str = "",
        entity_keys: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate RBAC, compile SQL, execute safely, and return governed rows."""
        metric_obj = self._metric(metric)
        rules = cast(list[Rule], self.reg.certified("rule"))
        decision = evaluate(rules, as_role, metric)
        if not decision.allowed:
            self._log("refused", metric, as_role, decision.reason)
            raise Refusal(f"access denied: {decision.reason}")
        used_dims = set(dims or []) | set(
            filters.keys() if isinstance(filters, dict) else []
        )
        blocked_dims = used_dims & decision.deny_dims
        if blocked_dims:
            self._log("refused", metric, as_role, "dims")
            raise Refusal(
                f"dimensions not permitted for this role: {sorted(blocked_dims)}"
            )
        try:
            compiled = self.compiler.compile(
                metric_obj, dims, filters, decision.row_filter, entity_keys
            )
            result = self.wh.query(
                compiled.sql, compiled.params, max_rows=MAX_ROWS
            )
            cols = result.columns
            rows = [[_plain(cell) for cell in row] for row in result.rows]
        except CompileError as exc:
            self._log("error", metric, as_role, str(exc))
            raise Refusal(str(exc)) from exc
        except Exception as exc:  # fail closed on warehouse or dry-run errors
            self._log("error", metric, as_role, str(exc))
            raise Refusal(f"query failed: {exc}") from exc
        self._log("query", metric, as_role)
        return {
            "metric": metric_obj.name,
            "version": metric_obj.version,
            "owner": metric_obj.owner,
            "definition": metric_obj.expression,
            "sql": compiled.sql,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
        }

    def _graph_columns(self) -> dict[str, set[str]]:
        metrics = cast(list[Metric], self.reg.certified("metric"))
        entities = cast(list[Entity], self.reg.certified("entity"))
        return {
            entity.name: {
                dim
                for metric in metrics
                if metric.table == entity.table
                for dim in metric.dimensions
            }
            for entity in entities
        }

    def describe_graph(self) -> dict[str, Any]:
        """List certified entities, filterable columns, and FK relationships."""
        cols = self._graph_columns()
        entities = cast(list[Entity], self.reg.certified("entity"))
        edges = cast(list[Edge], self.reg.certified("edge"))
        return {
            "entities": [
                {
                    "name": entity.name,
                    "filterable_columns": sorted(cols[entity.name]),
                }
                for entity in entities
            ],
            "relationships": [
                {"from": edge.src, "name": edge.rel, "to": edge.dst}
                for edge in edges
            ],
            "usage": (
                "traverse_graph(start=<from>, path=[<name>, ...], "
                "filters={<Entity>: {<column>: <value>}}) returns entity keys; "
                "pass them as entity_keys to query_metric for a metric whose "
                "entity equals start."
            ),
        }

    def traverse_graph(
        self,
        start: str,
        path: list[str],
        filters: dict[str, dict[str, Any]] | None = None,
        as_role: str = "",
    ) -> dict[str, Any]:
        """Traverse certified entity relationships for unrestricted roles."""
        rules = cast(list[Rule], self.reg.certified("rule"))
        if not unrestricted(rules, as_role):
            raise Refusal(
                "graph traversal requires a role with unrestricted access"
            )
        allowed = self._graph_columns()
        denied = {
            dim
            for rule in rules
            if rule.role == as_role
            for dim in rule.deny_dims
        }
        for ent_name, conds in (filters or {}).items():
            ok = allowed.get(ent_name, set()) - denied
            bad = [col for col in conds if col not in ok]
            if bad:
                raise Refusal(
                    f"not allowed as a filter on {ent_name}: {bad}; "
                    f"allowed: {sorted(ok)}"
                )
        entities = cast(list[Entity], self.reg.certified("entity"))
        edges = cast(list[Edge], self.reg.certified("edge"))
        graph = Graph(entities, edges)
        try:
            keys = graph.traverse(self.wh, start, path, filters)
        except ValueError as exc:
            raise Refusal(str(exc)) from exc
        except Exception as exc:
            self._log("error", f"graph:{start}", as_role, str(exc))
            raise Refusal(f"query failed: {exc}") from exc
        self._log("query", f"graph:{start}", as_role)
        return {"entity": start, "count": len(keys), "keys": keys}

    def report_gap(
        self, question: str, as_role: str = "", reason: str = ""
    ) -> dict[str, bool]:
        """Record an abstention when no certified metric answers ``question``."""
        self._log("gap", "", as_role, question, reason)
        return {"recorded": True}

    def report_issue(
        self, metric: str, note: str, as_role: str = ""
    ) -> dict[str, bool]:
        """Record a suspected issue or discrepancy on a certified metric."""
        self._log("issue", metric, as_role, note)
        return {"recorded": True}
