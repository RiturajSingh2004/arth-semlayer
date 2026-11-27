"""Drift detection and agent feedback aggregation."""
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from semlayer.core import Metric, State
from semlayer.draft import draft_metrics
from semlayer.ingest import Query
from semlayer.registry import Registry


@dataclass
class Change:
    kind: str  # table_dropped | column_dropped | type_changed | table_added | column_added
    table: str
    column: str = ""
    detail: str = ""

    @property
    def breaking(self) -> bool:
        return self.kind in {"table_dropped", "column_dropped", "type_changed"}

    def __str__(self):
        return f"{self.kind} {self.table}.{self.column}".rstrip(".") + (f" ({self.detail})" if self.detail else "")


def schema_drift(old: dict, new: dict) -> list[Change]:
    out = []
    for t in sorted(old.keys() - new.keys()):
        out.append(Change("table_dropped", t))
    for t in sorted(new.keys() - old.keys()):
        out.append(Change("table_added", t))
    for t in sorted(old.keys() & new.keys()):
        for c in sorted(old[t].keys() - new[t].keys()):
            out.append(Change("column_dropped", t, c))
        for c in sorted(new[t].keys() - old[t].keys()):
            out.append(Change("column_added", t, c))
        for c in sorted(old[t].keys() & new[t].keys()):
            if old[t][c] != new[t][c]:
                out.append(Change("type_changed", t, c, f"{old[t][c]} -> {new[t][c]}"))
    return out


def refs(m: Metric) -> set[tuple[str, str]]:
    if m.metric_type != "simple" or not m.table:
        return set()
    cols = set(m.all_dimensions) | ({m.column} - {"*"}) | ({m.entity_key} - {None})
    for f in m.filters:
        cols |= {c.name for c in sqlglot.condition(f).find_all(exp.Column)}
    return {(m.table, c) for c in cols}


def affected(changes: list[Change], metrics: list[Metric]) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = {}
    for m in metrics:
        r = refs(m)
        hit = [c for c in changes if c.breaking and (
            (c.kind == "table_dropped" and c.table == m.table) or (c.table, c.column) in r)]
        if hit:
            out[m.name] = hit
    return out


def propose_revisions(reg: Registry, changes: list[Change]) -> list[str]:
    """Open a pending revision per affected certified metric (debounced: one per metric)."""
    opened = []
    for name, hit in affected(changes, reg.certified("metric")).items():
        if reg.get("metric", name, pending=True):
            continue
        m = reg.get("metric", name).model_copy(deep=True)
        m.evidence.explanation = "DRIFT: " + "; ".join(map(str, hit))
        m.evidence.source = "drift"
        reg.put_draft(m)
        opened.append(name)
    return opened


def pattern_drift(reg: Registry, queries: list[Query], schema: dict | None = None) -> list[Metric]:
    """Query-log variants whose logic matches no existing definition."""
    known = {m.logic for kind in ("metric",) for st in State for m in reg.list(kind, st)}
    known |= {m.logic for m in reg.list("metric", State.draft, pending=True)}
    drafts, _ = draft_metrics(queries, schema)
    return [m for m in drafts if m.logic not in known]


def value_anomaly(history: list[float], latest: float, z: float = 3.0, min_points: int = 5) -> bool:
    if len(history) < min_points:
        return False
    sd = statistics.pstdev(history)
    mean = statistics.fmean(history)
    return abs(latest - mean) > z * sd if sd else latest != mean


def record_value(reg: Registry, metric: str, value: float) -> bool:
    """Append to history; True if the new value is anomalous versus prior points."""
    p = reg.state_dir / "history.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    past = [r["value"] for r in rows if r["metric"] == metric]
    with p.open("a") as f:
        f.write(json.dumps({"metric": metric, "value": value}) + "\n")
    return value_anomaly(past, value)


def read_usage(reg: Registry) -> list[dict]:
    p = reg.state_dir / "usage.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _first_sentence(text: str) -> str:
    return re.split(r"\?|\.\s|\.$", text.strip().lower(), maxsplit=1)[0].strip(" .?!")


def _cluster(notes: list[str], threshold: float = 0.6) -> Counter:
    """Count gap notes by question: first sentence only, near-duplicates (word overlap) merged."""
    reps: list[tuple[str, set]] = []
    counts: Counter = Counter()
    for note in notes:
        text = _first_sentence(note)
        words = set(re.findall(r"[a-z0-9]+", text))
        if not words:
            continue
        for rep, rep_words in reps:
            if len(words & rep_words) / len(words | rep_words) >= threshold:
                counts[rep] += 1
                break
        else:
            reps.append((text, words))
            counts[text] += 1
    return counts


def feedback_summary(events: list[dict], min_volume: int = 3, flag_rate: float = 0.2) -> dict:
    """Aggregate usage. Signals below `min_volume` are ignored (noise guard)."""
    q = Counter(e["metric"] for e in events if e["event"] == "query")
    bad = Counter(e["metric"] for e in events if e["event"] in {"error", "issue"})
    gaps = _cluster([e["note"] for e in events if e["event"] == "gap"])
    demand = sorted(((m, n) for m, n in q.items() if n >= min_volume), key=lambda x: -x[1])
    flagged = [m for m, n in q.items() if n >= min_volume and bad[m] / n > flag_rate]
    missing = sorted(((t, n) for t, n in gaps.items() if n >= min_volume), key=lambda x: -x[1])
    ignored = sum(1 for n in [*q.values(), *gaps.values()] if n < min_volume)
    return {"demand": demand, "flagged": flagged, "missing": missing, "ignored_low_volume": ignored}


def run_canary(reg: Registry, wh, suspend: bool = True, by: str = "canary") -> dict:
    """Probe every certified metric with a LIMIT 0 query; suspend failing ones unless dry-run."""
    from semlayer.compiler import SimpleCompiler
    from semlayer.warehouse import ensure_warehouse

    w = ensure_warehouse(wh)
    comp = SimpleCompiler(dialect=w.dialect)
    passed: list[str] = []
    failed: dict[str, str] = {}
    suspended: list[str] = []
    by_name = {m.name: m for m in reg.certified("metric") if isinstance(m, Metric)}
    ordered = sorted(by_name.items(), key=lambda kv: (kv[1].metric_type != "simple", kv[0]))
    for name, m in ordered:
        cur = reg.get("metric", name)
        if not isinstance(cur, Metric) or cur.state is not State.certified:
            continue  # already cascaded to suspended by an upstream failure
        try:
            if m.metric_type == "simple":
                c = comp.compile(m, limit=0)
            else:
                c = comp.compile_derived(m, by_name, limit=0)
            w.query(c.sql, c.params, max_rows=0)
            passed.append(name)
        except Exception as e:
            reason = str(e)
            failed[name] = reason
            if suspend:
                for s in reg.suspend("metric", name, reason=reason, by=by):
                    if s.name not in suspended:
                        suspended.append(s.name)
    return {"passed": passed, "failed": failed, "suspended": suspended}


def recommend_materialized_views(
    reg: Registry,
    events: list[dict],
    project: str = "YOUR_PROJECT_ID",
    dataset: str = "jaffle_shop",
    min_volume: int = 3,
    refresh_minutes: int = 30,
) -> list[dict]:
    """Generate advisory BigQuery Materialized View DDL for high-demand certified metrics (ADR-038)."""
    from semlayer.compiler import SimpleCompiler
    from semlayer.core.models import ident
    from semlayer.warehouse.bigquery import render_sql

    ident(dataset)
    if not project or any(ch in project for ch in ("`", ";", "\n", "\r", "\x00", " ")):
        raise ValueError(f"invalid BigQuery project id: {project!r}")

    fb = feedback_summary(events, min_volume=min_volume)
    comp = SimpleCompiler(dialect="bigquery")
    recs: list[dict] = []

    for metric_name, count in fb["demand"]:
        m = reg.get("metric", metric_name)
        if not isinstance(m, Metric) or m.state is not State.certified:
            continue
        if m.metric_type != "simple":
            recs.append({
                "metric": m.name,
                "queries": count,
                "eligible": False,
                "reason": f"{m.metric_type} metrics materialize via their underlying simple input metrics",
                "ddl": None,
            })
            continue
        if m.agg == "count_distinct":
            recs.append({
                "metric": m.name,
                "queries": count,
                "eligible": False,
                "reason": "BigQuery incremental MVs do not support exact COUNT(DISTINCT); consider APPROX_COUNT_DISTINCT",
                "ddl": None,
            })
            continue

        group_dims = list(m.dimensions)
        c = comp.compile(
            m,
            dims=group_dims,
            time_grain="day" if m.time_dimension else None,
        )
        # Strip trailing ORDER BY (BigQuery MVs disallow ORDER BY inside the defining SELECT)
        select_bq = render_sql(c.sql, c.params, project, dataset)
        parsed = sqlglot.parse_one(select_bq, read="bigquery")
        parsed.set("order", None)
        clean_select = parsed.sql(dialect="bigquery")
        mv_name = f"mv_semlayer_{m.name}"
        ddl = (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS `{project}`.`{dataset}`.`{mv_name}`\n"
            f"OPTIONS (enable_refresh = true, refresh_interval_minutes = {int(refresh_minutes)})\n"
            f"AS {clean_select};"
        )
        recs.append({
            "metric": m.name,
            "queries": count,
            "eligible": True,
            "view": f"{project}.{dataset}.{mv_name}",
            "ddl": ddl,
        })
    return recs
