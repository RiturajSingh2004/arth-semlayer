import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from semlayer.cli import main
from semlayer.core import Metric, State
from semlayer.monitor import feedback_summary, read_usage
from semlayer.serve.mcp_server import build_server
from semlayer.serve.service import Refusal
from semlayer.serve.web import create_app


def q(env, metric, role="finance", **kw):
    return env.svc.query_metric(metric, as_role=role, **kw)


def test_serves_certified_only(env):
    env.reg.put_draft(Metric(name="secret_draft", table="payments", agg="sum", column="amount"))
    with pytest.raises(Refusal):
        q(env, "secret_draft")
    with pytest.raises(Refusal):
        q(env, "revenue_v2")  # deprecated
    assert "secret_draft" not in [m["name"] for m in env.svc.list_metrics()]


def test_answer_carries_version_owner_and_matches_gold(env):
    r = q(env, "revenue")
    gold = env.con.execute("SELECT SUM(amount) FROM payments WHERE status='success'").fetchone()[0]
    assert r["rows"][0][0] == pytest.approx(gold) and r["version"] == 1 and r["owner"]


def test_policy_row_filter_and_default_deny(env):
    total = q(env, "customer_count")["rows"][0][0]
    eu = q(env, "customer_count", "eu_analyst")["rows"][0][0]
    assert eu == env.con.execute("SELECT COUNT(*) FROM customers WHERE region='EU'").fetchone()[0] < total
    with pytest.raises(Refusal, match="denied"):
        q(env, "revenue", "eu_analyst")
    with pytest.raises(Refusal):
        q(env, "revenue", "")


def test_dims_outside_definition_refused(env):
    with pytest.raises(Refusal):
        q(env, "customer_count", dims=["email"])
    with pytest.raises(Refusal):
        q(env, "revenue", filters={"method": "card; DROP TABLE payments"} | {"bogus": 1})


def test_injection_in_filter_value_is_parameterised(env):
    r = q(env, "revenue", filters={"method": "card' OR '1'='1"})
    assert r["rows"][0][0] is None  # no rows match; nothing leaked
    assert env.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 300


def test_graph_gate_and_multihop(env):
    with pytest.raises(Refusal, match="unrestricted"):
        env.svc.traverse_graph("Customer", ["HAS_ORDER"], as_role="eu_analyst")
    g = env.svc.traverse_graph("Customer", ["HAS_ORDER"], {"Order": {"status": "completed"}}, "finance")
    r = q(env, "revenue", entity_keys=g["keys"])
    gold = env.con.execute("SELECT SUM(amount) FROM payments WHERE status='success' AND customer_id IN "
                           "(SELECT customer_id FROM orders WHERE status='completed')").fetchone()[0]
    assert r["rows"][0][0] == pytest.approx(gold)

def test_describe_graph_lists_names_only_certified_columns(env):
    g = env.svc.describe_graph()
    assert {"from": "Customer", "name": "HAS_ORDER", "to": "Order"} in g["relationships"]
    cols = {e["name"]: e["filterable_columns"] for e in g["entities"]}
    assert cols["Order"] == ["status"] and "email" not in cols["Customer"]


def test_graph_filters_limited_to_certified_dimensions(env):
    for bad in ({"Customer": {"email": "c1@example.com"}}, {"Order": {"id": 1}}):
        with pytest.raises(Refusal, match="not allowed as a filter"):
            env.svc.traverse_graph("Customer", ["HAS_ORDER"], bad, "finance")
    ok = env.svc.traverse_graph("Customer", ["HAS_ORDER"], {"Order": {"status": "returned"}}, "finance")
    assert ok["count"] > 0

def test_usage_logged_and_feedback(env):
    for _ in range(3):
        q(env, "revenue")
    env.svc.report_gap("churn rate?")
    ev = read_usage(env.reg)
    assert sum(e["event"] == "query" for e in ev) >= 3 and ev[-1]["event"] == "gap"
    assert "role" in ev[0] and "user" not in ev[0]  # roles only, no identities
    assert ("revenue", 3) in feedback_summary(ev)["demand"]


def test_mcp_tools_and_refusal_shape(env):
    srv = build_server(env.svc)
    tools = asyncio.run(srv.list_tools())
    names = {t.name for t in getattr(tools, "tools", tools)}
    assert {"list_metrics", "query_metric", "traverse_graph", "describe_metric", "describe_graph"} <= names
    assert "raw_sql" not in names and "execute_sql" not in names


def test_web_review_flow(env):
    env.reg.put_draft(Metric(name="new_one", table="orders", agg="count", column="*",
                             evidence={"usage": 4, "suggested_owner": "zed"}))
    c = TestClient(create_app(env.reg, env.svc))
    assert "new_one" in c.get("/").text and c.get("/api/queue").json()[0]["name"] == "new_one"
    bad = c.post("/certify/metric/new_one", data={"owner": ""}, headers={"HX-Request": "true"})
    assert "owner required" in bad.text and env.reg.get("metric", "new_one").state is State.draft
    ok = c.post("/certify/metric/new_one", data={"owner": "zed"}, headers={"HX-Request": "true"})
    assert "certified" in ok.text and env.reg.get("metric", "new_one").owner == "zed"
    assert "new_one" in [m["name"] for m in c.get("/api/metrics").json()]


def test_cli_edit_creates_pending_revision_then_certify(env, capsys):
    a = ["--config", str(env.cfgfile)]
    main([*a, "edit", "order_count", "description=Orders placed"])
    assert env.reg.get("metric", "order_count").description != "Orders placed"  # not live yet
    main([*a, "certify", "order_count"])
    assert env.reg.get("metric", "order_count").description == "Orders placed"
    assert env.reg.get("metric", "order_count").version == 2


def test_cli_drift_and_review(env, capsys):
    a = ["--config", str(env.cfgfile)]
    main([*a, "drift"])
    main([*a, "review"])
    out = capsys.readouterr().out
    assert "opened revisions: none" in out


def test_eval_oracle_is_clean_and_llm_routing_works(env, capsys):
    from semlayer import evaluate
    evaluate.main(env.cfg, env.reg, "layer", True, "eval/questions.yaml")
    out = capsys.readouterr().out
    assert "answer accuracy: 25/25" in out and "abstention precision: 8/8" in out

    class Router:  # stands in for an LLM: always picks revenue
        def complete(self, p):
            return json.dumps({"metric": "revenue"})
    q0 = [{"category": "x", "q": "revenue?", "role": "finance", "expect": "answer", "call": None,
           "gold_sql": "SELECT SUM(amount) FROM payments WHERE status='success'"}]
    import yaml
    p = env.tmp / "q.yaml"
    p.write_text(yaml.safe_dump(q0))
    orig = evaluate.make_llm
    evaluate.make_llm = lambda cfg: Router()
    try:
        evaluate.main(env.cfg, env.reg, "layer", False, str(p))
    finally:
        evaluate.make_llm = orig
    assert "answer accuracy: 1/1" in capsys.readouterr().out

def test_cli_rule_add_then_certify_and_reindex(env, capsys):
    a = ["--config", str(env.cfgfile)]
    main([*a, "rule-add", "analyst_orders", "--role", "analyst", "--metric", "order_count",
          "--row-filter", "status = 'completed'"])
    assert env.reg.get("rule", "analyst_orders").state is State.draft
    with pytest.raises(Refusal):
        env.svc.query_metric("order_count", as_role="analyst")  # drafts grant nothing
    main([*a, "certify", "analyst_orders", "--kind", "rule", "--owner", "sec"])
    r = env.svc.query_metric("order_count", as_role="analyst")
    gold = env.con.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
    assert r["rows"][0][0] == gold
    with pytest.raises(SystemExit, match="invalid rule"):
        main([*a, "rule-add", "bad", "--role", "x", "--row-filter", "1=1; DROP TABLE orders"])
    assert env.reg.get("rule", "bad") is None
    env.reg.db_path.unlink()
    main([*a, "reindex"])
    assert "indexed:" in capsys.readouterr().out
    assert env.reg.get("rule", "analyst_orders") is not None


def test_baseline_hints_schema_and_arm(env, capsys):
    from semlayer import evaluate
    plain, hinted = evaluate._schema_text(env.con), evaluate._schema_text(env.con, True)
    assert "status in" not in plain and "region in [APAC, EU, US]" in hinted
    assert "email in" not in hinted  # high-cardinality columns get no value list

    class Sql:
        def complete(self, p):
            return "SELECT SUM(amount) FROM payments WHERE status = 'success'"
    import yaml
    q = env.tmp / "q.yaml"
    q.write_text(yaml.safe_dump([{"category": "x", "q": "revenue?", "role": "finance",
                                  "expect": "answer", "call": None,
                                  "gold_sql": "SELECT SUM(amount) FROM payments WHERE status = 'success'"}]))
    env.cfg.llm_model = "fake"
    orig = evaluate.make_llm
    evaluate.make_llm = lambda *a: Sql()
    try:
        evaluate.main(env.cfg, env.reg, "baseline-hints", False, str(q))
    finally:
        evaluate.make_llm = orig
    assert "answer accuracy: 1/1" in capsys.readouterr().out

def test_eval_fails_fast_when_llm_is_unreachable(env):
    from semlayer import evaluate

    class Down:
        def complete(self, p):
            raise OSError("connection refused")
    env.cfg.llm_model = "fake"
    orig = evaluate.make_llm
    evaluate.make_llm = lambda cfg: Down()
    try:
        with pytest.raises(SystemExit, match="LLM unreachable"):
            evaluate.main(env.cfg, env.reg, "layer", False, "eval/questions.yaml")
    finally:
        evaluate.make_llm = orig

def test_demo_is_idempotent_and_protects_real_registries(env, capsys):
    a = ["--config", str(env.cfgfile)]
    env.con.close()  # demo rebuilds the database file, so nothing may hold it open
    main([*a, "demo"])  # second run over a demo-made registry: allowed, rebuilt from scratch
    assert len(env.reg.certified("metric")) == 8
    (env.reg.state_dir / "demo.marker").unlink()  # now it looks like someone's own registry
    with pytest.raises(SystemExit, match="did not create"):
        main([*a, "demo"])
    assert len(env.reg.certified("metric")) == 8  # nothing was deleted
    main([*a, "demo", "--force"])
    assert (env.reg.state_dir / "demo.marker").exists()


