"""semlayer CLI: ingest | draft | review | certify | reject | edit | conflicts | serve | drift ..."""
import argparse
import json
import os
import sys

from semlayer.conflicts import apply_pick, apply_split, detect
from semlayer.core import KINDS, Config, Metric, Rule, State, load_dotenv, make_llm
from semlayer.draft import draft_metrics
from semlayer.graph import draft_graph
from semlayer.ingest import (
    Query,
    dbt_docs,
    policy_tags_snapshot,
    read_bq_jobs,
    read_query_log,
    schema_snapshot,
)
from semlayer.monitor import (
    feedback_summary,
    pattern_drift,
    propose_revisions,
    read_usage,
    recommend_materialized_views,
    record_value,
    run_canary,
    schema_drift,
)
from semlayer.policy import replay, suggest_rules
from semlayer.registry import Registry
from semlayer.warehouse import make_warehouse

LISTY = {"dimensions", "time_dimensions", "synonyms", "filters", "approvers", "deny_dims"}


def _wh(cfg: Config, read_only: bool = True):
    return make_warehouse(cfg, read_only=read_only)

def _kv(items: list[str]) -> dict:
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        out[k] = None if v.lower() == "none" else ([x for x in v.split(",") if x] if k in LISTY else v)
    return out


# --- commands -------------------------------------------------------------------------
def cmd_ingest(a, cfg, reg):
    wh = _wh(cfg)
    try:
        schema = schema_snapshot(wh)
        policy_tags = policy_tags_snapshot(wh)
        if a.from_bq_jobs or cfg.query_log == "bigquery":
            queries = read_bq_jobs(wh, limit=a.bq_limit, days=a.bq_days)
        else:
            queries = read_query_log(cfg.query_log)
    finally:
        wh.close()
    docs = dbt_docs(cfg.docs) if cfg.docs else {}
    (reg.state_dir / "ingest.json").write_text(json.dumps({
        "schema": schema,
        "docs": docs,
        "policy_tags": policy_tags,
        "queries": [q.__dict__ for q in queries],
    }))
    tagged_n = sum(len(v) for v in policy_tags.values())
    print(f"ingested {len(schema)} tables, {len(queries)} queries, {len(docs)} doc entries, "
          f"{tagged_n} policy-tagged columns")


def _ingested(reg):
    p = reg.state_dir / "ingest.json"
    if not p.exists():
        sys.exit("run `semlayer ingest` first")
    d = json.loads(p.read_text())
    return d["schema"], d["docs"], [Query(**q) for q in d["queries"]], d.get("policy_tags", {})


def cmd_draft(a, cfg, reg):
    schema, docs, queries, policy_tags = _ingested(reg)
    metrics, skipped = draft_metrics(queries, schema, make_llm(cfg), docs)
    existing = {m.logic for st in (State.draft, State.certified) for m in reg.list("metric", st)}
    made = {"draft": 0, "pending": 0, "skipped": 0}
    for m in metrics:
        if m.logic in existing:
            made["skipped"] += 1
            continue
        n = 1
        while reg.get("metric", m.name):
            n += 1
            m.name = f"{m.concept}_v{n}"
        made[reg.put_draft(m)] += 1
    roles = [r for r in a.roles.split(",") if r] if a.roles else []
    nodes = draft_graph(schema) + suggest_rules(schema, roles, policy_tags=policy_tags)
    for n in nodes:
        if reg.get(n.kind, n.name) is None:
            reg.put_draft(n)
            made["draft"] += 1
    print(f"drafted: {made}; unusable queries skipped: {skipped}")


def cmd_review(a, cfg, reg):
    if a.name:
        kind = a.kind or "metric"
        n = reg.get(kind, a.name, pending=bool(reg.get(kind, a.name, pending=True)))
        if n is None:
            sys.exit("not found")
        print(json.dumps(n.model_dump(mode="json"), indent=2))
        if d := reg.diff(kind, a.name):
            print(d)
        return
    for k in [a.kind] if a.kind else KINDS:
        for n in reg.queue(k):
            tag = "revision" if n.version else "draft"
            print(f"{k:7} {n.name:40} {tag:8} usage={n.evidence.usage:<3} "
                  f"suggested_owner={n.evidence.suggested_owner or '-'}")


def cmd_certify(a, cfg, reg):
    owner = a.owner
    if a.accept_suggested and not owner:
        n = reg.get(a.kind, a.name, pending=bool(reg.get(a.kind, a.name, pending=True)))
        owner = n.evidence.suggested_owner if n else None
    n = reg.certify(a.kind, a.name, owner=owner, by=a.by, edits=_kv(a.set or []))
    print(f"certified {a.kind} {n.name} v{n.version} owner={n.owner}")


def cmd_reject(a, cfg, reg):
    reg.reject(a.kind, a.name, by=a.by)
    print(f"rejected {a.kind} {a.name}")


def cmd_edit(a, cfg, reg):
    n = reg.get(a.kind, a.name)
    if n is None:
        sys.exit("not found")
    data = {**n.model_dump(), **_kv(a.fields)}
    print(reg.put_draft(KINDS[a.kind].model_validate(data)), a.name)


def cmd_conflicts(a, cfg, reg):
    cs = detect(reg.list("metric"))
    if a.split:
        c = next((c for c in cs if c.concept == a.split and c.kind == "variant"), None)
        if not c:
            sys.exit("no such conflict")
        apply_split(reg, c, _kv(a.names or []) or None, by=a.by)
        return print("split applied")
    if a.pick:
        c = next((c for c in cs if a.pick in c.variants), None)
        if not c:
            sys.exit("no such conflict")
        apply_pick(reg, c, a.pick, by=a.by)
        return print("pick applied")
    for c in cs:
        print(f"[{c.kind}] {c.concept}: usage={c.usage}")
        for d in c.diffs:
            print("   ", d)
        for o in c.options:
            print("    option:", o)
    if not cs:
        print("no conflicts")

def cmd_rule_add(a, cfg, reg):
    try:
        rule = Rule(name=a.name, role=a.role, metric=a.metric, effect=a.effect,
                    row_filter=a.row_filter, deny_dims=[d for d in a.deny_dims.split(",") if d])
    except ValueError as e:
        sys.exit(f"invalid rule: {e}")
    print(reg.put_draft(rule), a.name)  # certify it like any other definition


def cmd_metric_add(a, cfg, reg):
    try:
        m = Metric(
            name=a.name,
            metric_type=a.type,
            concept=a.concept or a.name,
            description=a.description,
            table=a.table,
            agg=a.agg,
            column=a.column,
            numerator=a.numerator,
            denominator=a.denominator,
            formula=a.formula,
            dimensions=[d for d in a.dimensions.split(",") if d],
            time_dimension=a.time_dimension or None,
            synonyms=[s for s in a.synonyms.split(",") if s],
        )
    except ValueError as e:
        sys.exit(f"invalid metric: {e}")
    try:
        tag = reg.put_draft(m)
    except ValueError as e:
        sys.exit(f"invalid metric: {e}")
    print(tag, a.name)


def cmd_canary(a, cfg, reg):
    wh = _wh(cfg)
    try:
        res = run_canary(reg, wh, suspend=not a.dry_run, by=a.by)
    finally:
        wh.close()
    print(json.dumps(res, indent=2))
    if res["failed"]:
        sys.exit(1)


def cmd_reindex(a, cfg, reg):
    reg.reindex()
    print("indexed:", {k: len(reg.list(k)) for k in KINDS})

def cmd_serve(a, cfg, reg):
    from semlayer.serve.service import Service

    bound_role = a.role or os.environ.get("SEMLAYER_BOUND_ROLE") or cfg.bound_role
    svc = Service(
        reg,
        _wh(cfg),
        bound_role=bound_role,
        timezone=cfg.timezone,
        max_rows=cfg.max_rows,
        max_groups=cfg.max_groups,
        max_filter_list=cfg.max_filter_list,
    )
    if a.web:
        import uvicorn

        from semlayer.serve.web import create_app
        uvicorn.run(create_app(reg, svc), host=a.host, port=a.port)
    else:
        from semlayer.serve.mcp_server import build_server
        build_server(svc).run()


def cmd_drift(a, cfg, reg):
    schema, _, _, _ = _ingested(reg)
    wh = _wh(cfg)
    try:
        changes = schema_drift(schema, schema_snapshot(wh))
        for c in changes:
            print(("BREAKING " if c.breaking else "digest   ") + str(c))
        print("opened revisions:", propose_revisions(reg, changes) or "none")
        new = pattern_drift(reg, read_query_log(cfg.query_log), schema)
        for m in new:
            print("new query variant:", m.concept, m.logic)
            reg.put_draft(m) if reg.get("metric", m.name) is None else None
        if a.values:
            for m in reg.certified("metric"):
                sql = f"SELECT {m.expression} FROM {m.table}" + (
                    " WHERE " + " AND ".join(f"({f})" for f in m.filters) if m.filters else "")
                rows = wh.query(sql).rows
                v = rows[0][0] if rows else None
                if v is not None and record_value(reg, m.name, float(v)):
                    print("ANOMALY", m.name, v)
    finally:
        wh.close()


def cmd_feedback(a, cfg, reg):
    events = read_usage(reg)
    out = feedback_summary(events, a.min_volume)
    if a.recommend_mv:
        proj = a.project or os.environ.get("SEMLAYER_BQ_PROJECT") or cfg.bq_project or "YOUR_PROJECT_ID"
        ds = a.dataset or os.environ.get("SEMLAYER_BQ_DATASET") or cfg.bq_dataset or "jaffle_shop"
        out["materialized_views"] = recommend_materialized_views(
            reg, events, project=proj, dataset=ds, min_volume=a.min_volume
        )
    print(json.dumps(out, indent=2))


def cmd_policy(a, cfg, reg):
    rules = [r.model_copy(update={"state": State.certified}) for r in reg.list("rule")
             if r.state is not State.deprecated]
    hist = [{"role": e["role"], "metric": e["metric"], "dims": []} for e in read_usage(reg)
            if e["event"] in {"query", "refused"} and e["role"]]
    for r in replay(rules, hist):
        print(r["role"], r["metric"], "ALLOW" if r["allowed"] else "DENY", r["reason"], r["row_filter"] or "")


def cmd_eval(a, cfg, reg):
    from semlayer.evaluate import main as run
    run(cfg, reg, a.arm, a.oracle, a.questions)


def cmd_demo(a, cfg, reg):
    from semlayer.demo import run
    prefix = (["--config", a.config] if a.config else []) + (["--commit"] if a.commit else [])
    run(prefix, cfg, reg, force=a.force)


def main(argv=None):
    p = argparse.ArgumentParser("semlayer")
    p.add_argument("--config", default=None)
    p.add_argument("--commit", action="store_true", help="git-commit registry changes")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM (heuristic descriptions only)")
    p.add_argument("--log-json", action="store_true", help="emit Cloud Logging structured JSON to stderr")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, **flags):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        for x in args:
            s.add_argument(x)
        for k, v in flags.items():
            s.add_argument(f"--{k.replace('_', '-')}", **v)
        return s

    add("ingest", cmd_ingest,
        from_bq_jobs=dict(action="store_true", help="ingest SELECT history from BigQuery INFORMATION_SCHEMA.JOBS_BY_PROJECT"),
        bq_limit=dict(type=int, default=500),
        bq_days=dict(type=int, default=30))
    add("draft", cmd_draft, roles=dict(default="", help="comma-separated roles for sensitive-column suggestions"))
    kind = dict(default="metric", choices=list(KINDS))
    by = dict(default="human")
    s = add("review", cmd_review, kind=dict(default=None, choices=list(KINDS)))
    s.add_argument("name", nargs="?")
    s = add("certify", cmd_certify, "name", kind=kind, owner=dict(default=None), by=by,
            accept_suggested=dict(action="store_true"), set=dict(nargs="*"))
    add("reject", cmd_reject, "name", kind=kind, by=by)
    s = add("edit", cmd_edit, "name", kind=kind)
    s.add_argument("fields", nargs="*")
    add("conflicts", cmd_conflicts, split=dict(default=None), pick=dict(default=None),
        names=dict(nargs="*"), by=by)
    add("rule-add", cmd_rule_add, "name", role=dict(required=True), metric=dict(default="*"),
        effect=dict(default="allow", choices=["allow", "deny"]), row_filter=dict(default=None),
        deny_dims=dict(help="comma-separated", default=""))
    add("metric-add", cmd_metric_add, "name",
        type=dict(default="ratio", choices=["simple", "ratio", "derived"]),
        concept=dict(default=""), description=dict(default=""),
        table=dict(default=""), agg=dict(default=None, choices=["sum", "count", "count_distinct", "avg", "min", "max"]),
        column=dict(default="*"), numerator=dict(default=None), denominator=dict(default=None),
        formula=dict(default=None), dimensions=dict(default=""), time_dimension=dict(default=""),
        synonyms=dict(default=""))
    add("canary", cmd_canary, dry_run=dict(action="store_true"), by=dict(default="canary"))
    add("reindex", cmd_reindex)
    add("serve", cmd_serve, web=dict(action="store_true"), port=dict(type=int, default=8000),
        host=dict(default="127.0.0.1"), role=dict(default=None, help="bind caller role in strict mode"))
    add("drift", cmd_drift, values=dict(action="store_true"))
    add("feedback", cmd_feedback, min_volume=dict(type=int, default=3),
        recommend_mv=dict(action="store_true", help="emit BigQuery Materialized View DDL recommendations"),
        project=dict(default=""), dataset=dict(default=""))
    add("policy-replay", cmd_policy)
    add("eval", cmd_eval, arm=dict(default="layer", choices=["layer", "baseline", "baseline-hints"]),
        oracle=dict(action="store_true"), questions=dict(default="eval/questions.yaml"))
    add("demo", cmd_demo, force=dict(action="store_true", help="wipe a registry that demo did not create"))
    a = p.parse_args(argv)
    load_dotenv()
    if a.no_llm:
        os.environ["SEMLAYER_LLM_PROVIDER"] = "none"
    cfg = Config.load(a.config)
    if a.log_json or cfg.log_json:
        os.environ["SEMLAYER_LOG_JSON"] = "1"
    reg = Registry(cfg.registry, commit=a.commit)
    a.fn(a, cfg, reg)


if __name__ == "__main__":
    main()
