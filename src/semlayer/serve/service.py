"""The only path to data: certified definitions, policy enforced, usage logged."""
from __future__ import annotations

import datetime
import decimal
import json
import time
from collections.abc import Callable

from semlayer.compiler import (
    CompileError,
    Compiler,
    JoinSpec,
    SimpleCompiler,
    normalise_filters,
)
from semlayer.core import Edge, Entity, Metric, Rule, emit_cloud_log
from semlayer.graph import Graph, joinable_entities, resolve_join_path
from semlayer.policy import evaluate, unrestricted
from semlayer.registry import Registry
from semlayer.warehouse import ensure_warehouse

MAX_ROWS = 1000
MAX_GROUPS = 1000
MAX_FILTER_LIST = 500


class Refusal(Exception):
    """Request refused (not found, not certified, denied, invalid)."""


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


class Service:
    def __init__(
        self,
        reg: Registry,
        wh,
        compiler: Compiler | None = None,
        log: bool = True,
        *,
        bound_role: str | None = None,
        timezone: str = "UTC",
        max_rows: int = MAX_ROWS,
        max_groups: int = MAX_GROUPS,
        max_filter_list: int = MAX_FILTER_LIST,
        clock: Callable[[], datetime.date] | None = None,
    ):
        self.reg, self.con, self.log = reg, wh, log
        self.wh = ensure_warehouse(wh)
        self.compiler = compiler or SimpleCompiler(dialect=self.wh.dialect)
        self.bound_role = bound_role.strip() if bound_role and bound_role.strip() else None
        self.timezone = timezone
        self.max_rows = max_rows
        self.max_groups = max_groups
        self.max_filter_list = max_filter_list
        self.clock = clock

    # --- helpers ----------------------------------------------------------------------
    def _log(self, event: str, metric: str = "", role: str = "", note: str = "",
             reason: str = "") -> None:
        if not self.log:  # e.g. eval runs must not pollute real usage signals
            return
        with (self.reg.state_dir / "usage.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "event": event, "metric": metric,
                                "role": role, "note": note, "reason": reason}) + "\n")
        sev = "ERROR" if event == "error" else ("WARNING" if event in {"refused", "issue"} else "INFO")
        emit_cloud_log(sev, f"service.{event}: {metric or '-'}", event=f"usage.{event}",
                       metric=metric, role=role, note=note, reason=reason)

    def _resolve_role(self, as_role: str | None) -> str:
        supplied = (as_role or "").strip()
        if self.bound_role:
            if supplied:
                raise Refusal("role is bound by server; caller-supplied as_role is not accepted")
            return self.bound_role
        return supplied

    def _metric(self, name: str) -> Metric:
        m = self.reg.get("metric", name)
        if m is None or not isinstance(m, Metric) or m.state.value != "certified":
            if isinstance(m, Metric) and m.state.value == "suspended":
                detail = m.suspend_reason or "health check failed"
                raise Refusal(f"no certified metric '{name}' (suspended: {detail})")
            raise Refusal(f"no certified metric '{name}'")
        return m

    def _collect_metric_closure(self, root: Metric) -> dict[str, Metric]:
        """Return certified transitive dependency closure `{name: Metric}` or raise Refusal."""
        out: dict[str, Metric] = {}

        def _walk(cur: Metric, stack: tuple[str, ...] = ()) -> None:
            if cur.name in stack:
                raise Refusal(f"dependency cycle detected: {' -> '.join([*stack, cur.name])}")
            out[cur.name] = cur
            for dep_name in cur.dependencies:
                dep = self._metric(dep_name)
                if dep.name not in out:
                    _walk(dep, (*stack, cur.name))

        _walk(root)
        return out

    def _entity_dim_catalog(self) -> tuple[list[Entity], list[Edge], dict[str, set[str]], dict[str, set[str]]]:
        ents: list[Entity] = self.reg.certified("entity")  # type: ignore[assignment]
        edges: list[Edge] = self.reg.certified("edge")  # type: ignore[assignment]
        ms: list[Metric] = self.reg.certified("metric")  # type: ignore[assignment]
        by_table_dims: dict[str, set[str]] = {}
        by_table_time: dict[str, set[str]] = {}
        for m in ms:
            if m.metric_type == "simple" and m.table:
                by_table_dims.setdefault(m.table, set()).update(m.all_dimensions)
                by_table_time.setdefault(m.table, set()).update(m.all_time_dimensions)
        return ents, edges, by_table_dims, by_table_time

    def _joinable_dims_for(self, m: Metric) -> list[str]:
        if m.metric_type != "simple" or not m.table:
            return []
        ents, edges, by_table_dims, _ = self._entity_dim_catalog()
        out: list[str] = []
        for ent in joinable_entities(m.table, ents, edges):
            for col in sorted(by_table_dims.get(ent.table, set())):
                out.append(f"{ent.name}.{col}")
        return out

    def _resolve_joins(
        self,
        m: Metric,
        refs: list[str],
        role: str,
        rules: list[Rule],
    ) -> dict[str, JoinSpec]:
        dot_refs = [r for r in dict.fromkeys(refs) if "." in r]
        if not dot_refs:
            return {}
        ents, edges, by_table_dims, by_table_time = self._entity_dim_catalog()
        ents_by_name = {e.name: e for e in ents}
        cert_metrics: list[Metric] = self.reg.certified("metric")  # type: ignore[assignment]
        role_denied = {d for r in rules if r.role == role for d in r.deny_dims}

        resolved: dict[str, JoinSpec] = {}
        for ref in dot_refs:
            if ref.count(".") != 1:
                raise Refusal(f"invalid joined dimension {ref!r}; expected '<Entity>.<column>'")
            ent_name, col = ref.split(".", 1)
            try:
                raw_steps = resolve_join_path(m.table, ent_name, ents, edges)
            except ValueError as e:
                raise Refusal(str(e)) from e
            if not raw_steps:
                raise Refusal(f"{ent_name!r} is already {m.name}'s base table; use {col!r} directly")

            target_ent = ents_by_name[ent_name]
            allowed_cols = by_table_dims.get(target_ent.table, set())
            if col not in allowed_cols:
                raise Refusal(
                    f"not allowed on joined entity {ent_name}: {[col]}; allowed: {sorted(allowed_cols)}"
                )
            if col in role_denied or ref in role_denied:
                raise Refusal(f"dimensions not permitted for this role: {[ref]}")

            # Prevent bypassing row-level filters or denied access on the joined entity's table:
            # caller must have unfiltered allow access to `*` or at least one certified metric on target_ent.table.
            target_ms = [tm for tm in cert_metrics if tm.metric_type == "simple" and tm.table == target_ent.table]
            can_read_target = False
            if target_ms:
                for tm in target_ms:
                    td = evaluate(rules, role, tm.name)
                    if td.allowed and not td.row_filter and col not in td.deny_dims:
                        can_read_target = True
                        break
            else:
                td = evaluate(rules, role, "*")
                can_read_target = td.allowed and not td.row_filter
            if not can_read_target:
                raise Refusal(
                    f"access denied for joined entity {ent_name}: role {role!r} lacks unrestricted "
                    f"access to {target_ent.table}"
                )

            sql_steps: list[tuple[str, str, str, str, str]] = []
            prev_alias = "t0"
            for _lt, l_key, r_table, r_key, step_ent in raw_steps:
                r_alias = f"j_{step_ent.lower()}"
                sql_steps.append((prev_alias, l_key, r_table, r_alias, r_key))
                prev_alias = r_alias

            resolved[ref] = JoinSpec(
                field_ref=ref,
                output_alias=f"{ent_name.lower()}_{col}",
                target_alias=prev_alias,
                column=col,
                is_time=col in by_table_time.get(target_ent.table, set()),
                steps=tuple(sql_steps),
            )
        return resolved

    def _validate_limit(self, limit: int | None) -> None:
        if limit is not None and (limit < 1 or limit > self.max_rows):
            raise Refusal(f"limit must be between 1 and {self.max_rows}")

    def _run_bounded(self, sql: str, params: list, is_grouped: bool, limit: int | None):
        if limit is not None:
            fetch_cap = limit
        elif is_grouped:
            fetch_cap = self.max_groups + 1
        else:
            fetch_cap = self.max_rows
        res = self.wh.query(sql, params, max_rows=fetch_cap)
        if limit is None and is_grouped and len(res.rows) > self.max_groups:
            raise Refusal(
                f"result exceeds max_groups ({self.max_groups}); add filters or specify limit"
            )
        return res.columns, [[_plain(v) for v in r] for r in res.rows]

    # --- tools ------------------------------------------------------------------------
    def list_metrics(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        if offset < 0:
            raise Refusal("offset must be >= 0")
        if limit is not None and limit < 1:
            raise Refusal("limit must be >= 1")
        items: list[Metric] = self.reg.certified("metric")  # type: ignore[assignment]
        sliced = items[offset : offset + limit] if limit is not None else items[offset:]
        return [
            {
                "name": m.name,
                "metric_type": m.metric_type,
                "description": m.description,
                "dimensions": m.dimensions,
                "time_dimension": m.time_dimension,
                "synonyms": m.synonyms,
                "version": m.version,
                "entity": m.entity,
            }
            for m in sliced
        ]

    def search_metrics(self, query: str, k: int = 10) -> list[dict]:
        if k < 1 or k > 100:
            raise Refusal("k must be between 1 and 100")
        return [
            {
                "name": m.name,
                "metric_type": m.metric_type,
                "description": m.description,
                "dimensions": m.dimensions,
                "time_dimension": m.time_dimension,
                "synonyms": m.synonyms,
                "version": m.version,
                "entity": m.entity,
            }
            for m in self.reg.search_metrics(query, k=k)
        ]

    def describe_metric(self, name: str) -> dict:
        m = self._metric(name)
        return {
            "name": m.name,
            "metric_type": m.metric_type,
            "description": m.description,
            "definition": m.expression,
            "table": m.table,
            "dependencies": m.dependencies,
            "filters": m.filters,
            "dimensions": m.dimensions,
            "time_dimension": m.time_dimension,
            "time_dimensions": m.all_time_dimensions,
            "joinable_dimensions": self._joinable_dims_for(m),
            "synonyms": m.synonyms,
            "entity": m.entity,
            "owner": m.owner,
            "version": m.version,
            "lineage": m.evidence.lineage,
            "explanation": m.evidence.explanation,
        }

    def query_metric(
        self,
        metric: str,
        dims: list[str] | None = None,
        filters: dict | list | None = None,
        as_role: str = "",
        entity_keys: list | None = None,
        *,
        time_grain: str | None = None,
        time_range=None,
        order_by: str | list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        role = self._resolve_role(as_role)
        self._validate_limit(limit)
        m = self._metric(metric)
        closure = self._collect_metric_closure(m)
        rules: list[Rule] = self.reg.certified("rule")  # type: ignore[assignment]

        try:
            fspecs = normalise_filters(filters)
        except CompileError as e:
            self._log("error", metric, role, str(e))
            raise Refusal(str(e)) from e

        used_fields = list(dims or []) + [f.field for f in fspecs]
        row_filters: dict[str, str | None] = {}
        for target_name, target_m in closure.items():
            d = evaluate(rules, role, target_name)
            if not d.allowed:
                self._log("refused", metric, role, d.reason)
                raise Refusal(f"access denied: {d.reason}")
            used_set = set(used_fields)
            if target_m.time_dimension and (time_grain or time_range):
                used_set.add(target_m.time_dimension)
            if used_set & d.deny_dims:
                self._log("refused", metric, role, "dims")
                raise Refusal(
                    f"dimensions not permitted for this role: {sorted(used_set & d.deny_dims)}"
                )
            row_filters[target_name] = d.row_filter

        is_grouped = bool(dims) or bool(time_grain)
        try:
            if m.metric_type == "simple" and isinstance(self.compiler, SimpleCompiler):
                joins = self._resolve_joins(m, used_fields, role, rules)
                c = self.compiler.compile(
                    m,
                    dims,
                    fspecs,
                    row_filters[m.name],
                    entity_keys,
                    time_grain=time_grain,
                    time_range=time_range,
                    order_by=order_by,
                    limit=limit,
                    joins=joins,
                    max_filter_list=self.max_filter_list,
                    clock=self.clock,
                    timezone=self.timezone,
                )
            elif m.metric_type != "simple" and isinstance(self.compiler, SimpleCompiler):
                joins_by_metric = {
                    leaf.name: self._resolve_joins(leaf, used_fields, role, rules)
                    for leaf in closure.values()
                    if leaf.metric_type == "simple"
                }
                c = self.compiler.compile_derived(
                    m,
                    closure,
                    dims,
                    fspecs,
                    row_filters,
                    entity_keys,
                    time_grain=time_grain,
                    time_range=time_range,
                    order_by=order_by,
                    limit=limit,
                    joins_by_metric=joins_by_metric,
                    max_filter_list=self.max_filter_list,
                    clock=self.clock,
                    timezone=self.timezone,
                )
            else:
                # Custom Compiler protocol implementation (v1 signature)
                c = self.compiler.compile(m, dims, filters, row_filters[m.name], entity_keys)
            cols, rows = self._run_bounded(c.sql, c.params, is_grouped=is_grouped, limit=limit)
        except Refusal:
            raise
        except CompileError as e:
            self._log("error", metric, role, str(e))
            raise Refusal(str(e)) from e
        except Exception as e:  # database errors fail closed
            self._log("error", metric, role, str(e))
            raise Refusal(f"query failed: {e}") from e
        self._log("query", metric, role)
        return {
            "metric": m.name,
            "version": m.version,
            "owner": m.owner,
            "definition": m.expression,
            "sql": c.sql,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
        }

    def query_metrics(
        self,
        metrics: list[str],
        dims: list[str] | None = None,
        filters: dict | list | None = None,
        as_role: str = "",
        entity_keys: list | None = None,
        *,
        time_grain: str | None = None,
        time_range=None,
        order_by: str | list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        role = self._resolve_role(as_role)
        if not metrics:
            raise Refusal("metrics list cannot be empty")
        self._validate_limit(limit)
        if not isinstance(self.compiler, SimpleCompiler):
            raise Refusal("configured compiler does not support multi-metric queries")

        requested = [self._metric(name) for name in dict.fromkeys(metrics)]
        closure: dict[str, Metric] = {}
        for m in requested:
            closure.update(self._collect_metric_closure(m))

        rules: list[Rule] = self.reg.certified("rule")  # type: ignore[assignment]
        try:
            fspecs = normalise_filters(filters)
        except CompileError as e:
            raise Refusal(str(e)) from e

        used_fields = list(dims or []) + [f.field for f in fspecs]
        row_filters: dict[str, str | None] = {}
        for target_name, target_m in closure.items():
            d = evaluate(rules, role, target_name)
            if not d.allowed:
                self._log("refused", target_name, role, d.reason)
                raise Refusal(f"access denied on '{target_name}': {d.reason}")
            used_set = set(used_fields)
            if target_m.time_dimension and (time_grain or time_range):
                used_set.add(target_m.time_dimension)
            if used_set & d.deny_dims:
                self._log("refused", target_name, role, "dims")
                raise Refusal(
                    f"dimensions not permitted for this role on '{target_name}': "
                    f"{sorted(used_set & d.deny_dims)}"
                )
            row_filters[target_name] = d.row_filter

        is_grouped = bool(dims) or bool(time_grain)
        try:
            joins_by_metric = {
                leaf.name: self._resolve_joins(leaf, used_fields, role, rules)
                for leaf in closure.values()
                if leaf.metric_type == "simple"
            }
            c = self.compiler.compile_multi(
                requested,
                closure,
                dims,
                fspecs,
                row_filters,
                entity_keys,
                time_grain=time_grain,
                time_range=time_range,
                order_by=order_by,
                limit=limit,
                joins_by_metric=joins_by_metric,
                max_filter_list=self.max_filter_list,
                clock=self.clock,
                timezone=self.timezone,
            )
            cols, rows = self._run_bounded(c.sql, c.params, is_grouped=is_grouped, limit=limit)
        except Refusal:
            raise
        except CompileError as e:
            for m in requested:
                self._log("error", m.name, role, str(e))
            raise Refusal(str(e)) from e
        except Exception as e:
            for m in requested:
                self._log("error", m.name, role, str(e))
            raise Refusal(f"query failed: {e}") from e

        for m in requested:
            self._log("query", m.name, role)
        return {
            "metrics": [
                {
                    "name": m.name,
                    "version": m.version,
                    "owner": m.owner,
                    "definition": m.expression,
                }
                for m in requested
            ],
            "sql": c.sql,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
        }

    def _graph_columns(self) -> dict[str, set[str]]:
        """Filterable columns per entity: dimensions that certified metrics expose on its table."""
        ms: list[Metric] = self.reg.certified("metric")  # type: ignore[assignment]
        return {
            e.name: {
                d
                for m in ms
                if m.metric_type == "simple" and m.table == e.table
                for d in m.all_dimensions
            }
            for e in self.reg.certified("entity")  # type: ignore[attr-defined]
        }

    def describe_graph(self) -> dict:
        cols = self._graph_columns()
        edges: list[Edge] = self.reg.certified("edge")  # type: ignore[assignment]
        return {
            "entities": [{"name": e.name, "filterable_columns": sorted(cols[e.name])}
                         for e in self.reg.certified("entity")],
            "relationships": [{"from": e.src, "name": e.rel, "to": e.dst} for e in edges],
            "cardinalities": {f"{e.src}.{e.rel}->{e.dst}": e.cardinality for e in edges},
            "usage": "traverse_graph(start=<from>, path=[<name>, ...], filters={<Entity>: "
                     "{<column>: <value>}}) returns entity keys; pass them as entity_keys to "
                     "query_metric for a metric whose entity equals start.",
        }

    def traverse_graph(self, start: str, path: list[str], filters: dict | None = None,
                       as_role: str = "") -> dict:
        role = self._resolve_role(as_role)
        rules: list[Rule] = self.reg.certified("rule")  # type: ignore[assignment]
        if not unrestricted(rules, role):  # keys are row-level data: conservative gate
            raise Refusal("graph traversal requires a role with unrestricted access")
        allowed, denied = self._graph_columns(), {
            d for r in rules if r.role == role for d in r.deny_dims}
        for ent, conds in (filters or {}).items():
            ok = allowed.get(ent, set()) - denied
            if bad := [c for c in conds if c not in ok]:
                raise Refusal(f"not allowed as a filter on {ent}: {bad}; allowed: {sorted(ok)}")
        g = Graph(self.reg.certified("entity"), self.reg.certified("edge"))  # type: ignore[arg-type]
        try:
            keys = g.traverse(self.wh, start, path, filters)
        except ValueError as e:
            raise Refusal(str(e)) from e
        except Exception as e:
            self._log("error", f"graph:{start}", role, str(e))
            raise Refusal(f"query failed: {e}") from e
        self._log("query", f"graph:{start}", role)
        return {"entity": start, "count": len(keys), "keys": keys}

    def report_gap(self, question: str, as_role: str = "", reason: str = "") -> dict:
        role = self._resolve_role(as_role)
        self._log("gap", "", role, question, reason)
        return {"recorded": True}

    def report_issue(self, metric: str, note: str, as_role: str = "") -> dict:
        role = self._resolve_role(as_role)
        self._log("issue", metric, role, note)
        return {"recorded": True}
