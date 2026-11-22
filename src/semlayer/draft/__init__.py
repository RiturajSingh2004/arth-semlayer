"""Draft engine: statistics over the query log (+ optional LLM wording) -> metric drafts."""
import sys
from collections import Counter, defaultdict

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from semlayer.core import LLM, Evidence, Metric, NullLLM
from semlayer.core.models import IDENT
from semlayer.ingest import Query
from semlayer.lineage import column_lineage

SIMPLE = {exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}


def _conjuncts(e: exp.Expression) -> list[exp.Expression]:
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return _conjuncts(e.left) + _conjuncts(e.right) if isinstance(e, exp.And) else [e]


def _agg(node: exp.Expression) -> tuple[str, str] | None:
    """(agg, column) for a supported aggregate, else None."""
    for cls, name in SIMPLE.items():
        if isinstance(node, cls) and isinstance(node.this, exp.Column):
            return name, node.this.name
    if isinstance(node, exp.Count):
        arg = node.this
        if isinstance(arg, exp.Star):
            return "count", "*"
        if isinstance(arg, exp.Distinct) and len(arg.expressions) == 1:
            if isinstance(arg.expressions[0], exp.Column):
                return "count_distinct", arg.expressions[0].name
        if isinstance(arg, exp.Column):
            return "count", arg.name
    return None


def _parse(q: Query) -> list[dict]:
    """Single-table aggregate queries only; anything else is skipped."""
    try:
        tree = sqlglot.parse_one(q.sql, dialect="duckdb")
    except SqlglotError:
        return []
    if not isinstance(tree, exp.Select) or tree.find(exp.Join) or tree.find(exp.Subquery):
        return []
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        return []
    where = tree.args.get("where")
    filters = sorted(c.sql(dialect="duckdb") for c in _conjuncts(where.this)) if where else []
    group = tree.args.get("group")
    dims = [e.name for e in group.expressions if isinstance(e, exp.Column)] if group else []
    out = []
    for p in tree.expressions:
        inner = p.this if isinstance(p, exp.Alias) else p
        agg = _agg(inner)
        if not agg:
            continue
        alias = p.alias if isinstance(p, exp.Alias) else ""
        concept = alias if IDENT.match(alias or "") else f"{agg[0]}_{'all' if agg[1] == '*' else agg[1]}"
        out.append({"concept": concept, "table": tables[0].name, "agg": agg[0], "column": agg[1],
                    "filters": filters, "dims": dims, "q": q})
    return out

class _Guard:
    """Wraps an LLM; after the first connection failure, falls back to heuristic wording."""

    def __init__(self, llm: LLM):
        self.llm, self.ok = llm, True

    def complete(self, prompt: str) -> str:
        if not self.ok:
            return ""
        try:
            return self.llm.complete(prompt)
        except OSError as e:
            self.ok = False
            print(f"warning: LLM unavailable ({e}); using heuristic descriptions. Start your LLM "
                  "server and pull the model (see README), or set llm_model: null.", file=sys.stderr)
            return ""

def draft_metrics(queries: list[Query], schema: dict | None = None, llm: LLM | None = None,
                  docs: dict[str, str] | None = None) -> tuple[list[Metric], int]:
    """Returns (drafts, skipped_query_count)."""
    llm, docs = _Guard(llm or NullLLM()), docs or {}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    skipped = 0
    for q in queries:
        found = _parse(q)
        skipped += not found
        for f in found:
            groups[(f["concept"], f["table"], f["agg"], f["column"], tuple(f["filters"]))].append(f)
    by_concept: dict[str, list[list[dict]]] = defaultdict(list)
    for key, items in groups.items():
        by_concept[key[0]].append(items)
    drafts = []
    for concept, variants in sorted(by_concept.items()):
        variants.sort(key=lambda v: -len(v))
        for i, items in enumerate(variants, 1):
            first = items[0]
            users = Counter(f["q"].user for f in items)
            m = Metric(
                name=concept if len(variants) == 1 else f"{concept}_v{i}", concept=concept,
                table=first["table"], agg=first["agg"], column=first["column"],
                filters=first["filters"], dimensions=sorted({d for f in items for d in f["dims"]}),
            )
            lineage = column_lineage(first["q"].sql, schema)
            m.evidence = Evidence(
                queries=sorted({f["q"].sql for f in items})[:5], lineage=lineage,
                usage=len(items), users=sorted(users), source="query_log",
                suggested_owner=users.most_common(1)[0][0],
                explanation=_explain(m, docs, llm),
            )
            m.description = _describe(m, docs, llm)
            drafts.append(m)
    return drafts, skipped


def _explain(m: Metric, docs: dict, llm: LLM) -> str:
    base = f"{m.expression} over {m.table}" + (f" where {' AND '.join(m.filters)}" if m.filters else "")
    out = llm.complete(f"Explain in one plain sentence what this business metric computes: {base}.")
    return out or base


def _describe(m: Metric, docs: dict, llm: LLM) -> str:
    hint = docs.get(f"{m.table}.{m.column}") or docs.get(m.table) or ""
    where = f" where {' AND '.join(m.filters)}" if m.filters else ""
    out = llm.complete(
        f"Give a one-sentence business description for the metric '{m.concept}' = "
        f"{m.expression} on {m.table}{where}. Mention the filter conditions if there are any. "
        f"Context: {hint}"
    )
    return out or hint or f"{m.concept.replace('_', ' ')} ({m.expression} on {m.table}{where})"