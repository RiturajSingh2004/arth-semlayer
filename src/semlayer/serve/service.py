"""The only path to data: certified definitions, policy enforced, usage logged."""
import datetime
import decimal
import json
import time

from semlayer.compiler import CompileError, Compiler, SimpleCompiler
from semlayer.core import Metric, Rule
from semlayer.graph import Graph
from semlayer.policy import evaluate, unrestricted
from semlayer.registry import Registry
from semlayer.warehouse import ensure_warehouse

MAX_ROWS = 1000


class Refusal(Exception):
    """Request refused (not found, not certified, denied, invalid)."""


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


class Service:
    def __init__(self, reg: Registry, wh, compiler: Compiler | None = None, log: bool = True):
        self.reg, self.con, self.log = reg, wh, log
        self.wh = ensure_warehouse(wh)
        self.compiler = compiler or SimpleCompiler(dialect=self.wh.dialect)

    # --- helpers ----------------------------------------------------------------------
    def _log(self, event: str, metric: str = "", role: str = "", note: str = "",
             reason: str = "") -> None:
        if not self.log:  # e.g. eval runs must not pollute real usage signals
            return
        with (self.reg.state_dir / "usage.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "event": event, "metric": metric,
                                "role": role, "note": note, "reason": reason}) + "\n")

    def _metric(self, name: str) -> Metric:
        m = self.reg.get("metric", name)
        if m is None or m.state.value != "certified":
            raise Refusal(f"no certified metric '{name}'")
        return m

    # --- tools ------------------------------------------------------------------------
    def list_metrics(self) -> list[dict]:
        return [{"name": m.name, "description": m.description, "dimensions": m.dimensions,
                 "version": m.version, "entity": m.entity} for m in self.reg.certified("metric")]

    def describe_metric(self, name: str) -> dict:
        m = self._metric(name)
        return {"name": m.name, "description": m.description, "definition": m.expression,
                "table": m.table, "filters": m.filters, "dimensions": m.dimensions,
                "entity": m.entity, "owner": m.owner, "version": m.version,
                "lineage": m.evidence.lineage, "explanation": m.evidence.explanation}

    def query_metric(self, metric: str, dims: list[str] | None = None, filters: dict | None = None,
                     as_role: str = "", entity_keys: list | None = None) -> dict:
        m = self._metric(metric)
        rules: list[Rule] = self.reg.certified("rule")
        d = evaluate(rules, as_role, metric)
        if not d.allowed:
            self._log("refused", metric, as_role, d.reason)
            raise Refusal(f"access denied: {d.reason}")
        used = set(dims or []) | set(filters.keys() if isinstance(filters, dict) else [])
        if used & d.deny_dims:
            self._log("refused", metric, as_role, "dims")
            raise Refusal(f"dimensions not permitted for this role: {sorted(used & d.deny_dims)}")
        try:
            c = self.compiler.compile(m, dims, filters, d.row_filter, entity_keys)
            res = self.wh.query(c.sql, c.params, max_rows=MAX_ROWS)
            cols = res.columns
            rows = [[_plain(v) for v in r] for r in res.rows]
        except CompileError as e:
            self._log("error", metric, as_role, str(e))
            raise Refusal(str(e)) from e
        except Exception as e:  # database errors fail closed
            self._log("error", metric, as_role, str(e))
            raise Refusal(f"query failed: {e}") from e
        self._log("query", metric, as_role)
        return {"metric": m.name, "version": m.version, "owner": m.owner,
                "definition": m.expression, "sql": c.sql, "columns": cols, "rows": rows,
                "row_count": len(rows)}

    def _graph_columns(self) -> dict[str, set[str]]:
        """Filterable columns per entity: dimensions that certified metrics expose on its table."""
        ms = self.reg.certified("metric")
        return {e.name: {d for m in ms if m.table == e.table for d in m.dimensions}
                for e in self.reg.certified("entity")}

    def describe_graph(self) -> dict:
        cols = self._graph_columns()
        return {
            "entities": [{"name": e.name, "filterable_columns": sorted(cols[e.name])}
                         for e in self.reg.certified("entity")],
            "relationships": [{"from": e.src, "name": e.rel, "to": e.dst}
                              for e in self.reg.certified("edge")],
            "usage": "traverse_graph(start=<from>, path=[<name>, ...], filters={<Entity>: "
                     "{<column>: <value>}}) returns entity keys; pass them as entity_keys to "
                     "query_metric for a metric whose entity equals start.",
        }

    def traverse_graph(self, start: str, path: list[str], filters: dict | None = None,
                       as_role: str = "") -> dict:
        rules = self.reg.certified("rule")
        if not unrestricted(rules, as_role):  # keys are row-level data: conservative gate
            raise Refusal("graph traversal requires a role with unrestricted access")
        allowed, denied = self._graph_columns(), {
            d for r in rules if r.role == as_role for d in r.deny_dims}
        for ent, conds in (filters or {}).items():
            ok = allowed.get(ent, set()) - denied
            if bad := [c for c in conds if c not in ok]:
                raise Refusal(f"not allowed as a filter on {ent}: {bad}; allowed: {sorted(ok)}")
        g = Graph(self.reg.certified("entity"), self.reg.certified("edge"))
        try:
            keys = g.traverse(self.wh, start, path, filters)
        except ValueError as e:
            raise Refusal(str(e)) from e
        except Exception as e:
            self._log("error", f"graph:{start}", as_role, str(e))
            raise Refusal(f"query failed: {e}") from e
        self._log("query", f"graph:{start}", as_role)
        return {"entity": start, "count": len(keys), "keys": keys}

    def report_gap(self, question: str, as_role: str = "", reason: str = "") -> dict:
        self._log("gap", "", as_role, question, reason)
        return {"recorded": True}

    def report_issue(self, metric: str, note: str, as_role: str = "") -> dict:
        self._log("issue", metric, as_role, note)
        return {"recorded": True}
