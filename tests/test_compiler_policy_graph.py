import pytest

from semlayer.compiler import CompileError, SimpleCompiler
from semlayer.core import Metric, Rule, State
from semlayer.graph import Graph, draft_graph
from semlayer.policy import evaluate, replay, sensitive_columns, suggest_rules

M = Metric(name="rev", table="payments", agg="sum", column="amount", filters=["status = 'ok'"],
           dimensions=["method"], entity_key="customer_id")
C = SimpleCompiler()


def test_compile_shapes_and_params():
    c = C.compile(M, ["method"], None, "region = 'EU'", [1, 2])
    assert c.sql == ("SELECT method, SUM(amount) AS rev FROM payments WHERE (status = 'ok') AND "
                     "(region = 'EU') AND list_contains(?, customer_id) "
                     "GROUP BY method ORDER BY method")
    assert c.params == [[1, 2]]


def test_dim_pinned_by_filter_is_not_grouped():
    c = C.compile(M, ["method"], {"method": "card"})
    assert c.sql == "SELECT SUM(amount) AS rev FROM payments WHERE (status = 'ok') AND method = ?"
    assert c.params == ["card"]


def test_compile_rejects_unlisted_dims_and_unlinked_entity():
    with pytest.raises(CompileError):
        C.compile(M, ["email"])
    with pytest.raises(CompileError):
        C.compile(M, filters={"x; DROP": 1})
    with pytest.raises(CompileError):
        C.compile(M.model_copy(update={"entity_key": None}), entity_keys=[1])


def rule(**kw):
    return Rule(name=kw.pop("name", "r"), state="certified", **kw)


def test_policy_matrix():
    rules = [rule(name="a", role="fin"), rule(name="b", role="eu", metric="cust", row_filter="region='EU'"),
             rule(name="c", role="eu", metric="cust", row_filter="region='US'"),
             rule(name="d", role="fin", metric="secret", effect="deny"),
             rule(name="e", role="fin", effect="deny", deny_dims=["email"])]
    assert evaluate(rules, "fin", "rev").allowed
    assert not evaluate(rules, "fin", "secret").allowed  # deny beats allow
    assert evaluate(rules, "fin", "rev").deny_dims == {"email"}
    d = evaluate(rules, "eu", "cust")
    assert d.allowed and "EU" in d.row_filter and "US" in d.row_filter
    assert not evaluate(rules, "eu", "rev").allowed  # default deny
    assert not evaluate(rules, "nobody", "rev").allowed


def test_only_certified_rules_apply():
    assert not evaluate([Rule(name="r", role="x")], "x", "m").allowed


def test_suggest_and_replay():
    schema = {"customers": {"id": "INT", "email": "VARCHAR", "region": "VARCHAR"}}
    assert sensitive_columns(schema) == {"customers": ["email"]}
    (r,) = suggest_rules(schema, ["fin"])
    assert r.deny_dims == ["email"] and r.effect == "deny" and r.state.value == "draft"
    res = replay([rule(role="fin"), r.model_copy(update={"state": State.certified})],
                 [{"role": "fin", "metric": "m", "dims": ["email"]},
                  {"role": "fin", "metric": "m", "dims": ["region"]}, {"role": "x", "metric": "m"}])
    assert [x["allowed"] for x in res] == [False, True, False]


SCHEMA = {"customers": {"id": "INT"}, "orders": {"id": "INT", "customer_id": "INT"},
          "payments": {"id": "INT", "order_id": "INT", "customer_id": "INT"}}


def test_graph_draft_and_traverse(env):
    nodes = draft_graph(SCHEMA)
    ents = [n for n in nodes if n.kind == "entity"]
    edges = [n for n in nodes if n.kind == "edge"]
    assert {e.name for e in ents} == {"Customer", "Order", "Payment"} and len(edges) == 3
    g = Graph(ents, edges)
    sql, params = g.traverse_sql("Customer", ["HAS_ORDER"], {"Order": {"status": "completed"}})
    assert "JOIN orders e1 ON e0.id = e1.customer_id" in sql and params == ["completed"]
    keys = g.traverse(env.con, "Customer", ["HAS_ORDER"], {"Order": {"status": "completed"}})
    assert keys and keys == sorted(keys)
    with pytest.raises(ValueError):
        g.traverse_sql("Customer", ["NOPE"])
    with pytest.raises(ValueError):
        g.traverse_sql("Customer", ["HAS_ORDER"], {"Payment": {"x": 1}})
