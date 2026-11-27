import datetime
import json
import socket
import threading
import time

import duckdb
import pytest
import sqlglot
import uvicorn
import yaml

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

from semlayer import evaluate
from semlayer.compiler import SimpleCompiler
from semlayer.core import Metric
from semlayer.serve.service import Refusal, Service
from semlayer.warehouse import DuckDBWarehouse
from semlayer.warehouse.bigquery import BigQueryMCP, parse_bq_payload, render_sql

PROJECT = "demo-gcp-project"
DATASET = "jaffle_shop"


def test_render_sql_literals_dates_tables_and_empty_keys():
    sql = (
        "SELECT method, SUM(amount) AS revenue FROM payments "
        "WHERE (status = 'success') AND method = ? AND paid_at >= ? "
        "AND (order_date >= current_date - 30) AND customer_id IN UNNEST(?) "
        "GROUP BY method ORDER BY method"
    )
    out = render_sql(sql, ["card", datetime.date(2026, 11, 1), [1, 2]], PROJECT, DATASET)
    assert f"`{PROJECT}`.`{DATASET}`.`payments`" in out
    assert "DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)" in out
    assert "CAST('2026-11-01' AS DATE)" in out
    assert "IN UNNEST([1, 2])" in out

    # Empty entity-key list avoids BigQuery's untyped `UNNEST([])` error
    empty_sql = "SELECT SUM(amount) FROM payments WHERE customer_id IN UNNEST(?)"
    out_empty = render_sql(empty_sql, [[]], PROJECT, DATASET)
    assert "FALSE" in out_empty and "UNNEST" not in out_empty


@pytest.mark.parametrize("hostile", [
    "card' OR '1'='1",
    "card'; DROP TABLE payments; --",
    "card\\' OR TRUE /* comment */",
    "line1\nline2\r\t\\backslash`tick`",
])
def test_render_sql_neutralises_hostile_strings(hostile):
    out = render_sql("SELECT COUNT(*) FROM orders WHERE status = ?", [hostile], PROJECT, DATASET)
    parsed = sqlglot.parse(out, read="bigquery")
    assert len(parsed) == 1  # never splits into multiple statements
    lits = list(parsed[0].find_all(sqlglot.exp.Literal))
    assert len(lits) == 1 and lits[0].this == hostile


@pytest.mark.parametrize("bad_param", [
    float("nan"),
    float("inf"),
    float("-inf"),
    {"nested": 1},
    [[1, 2]],
    "bad\x00null",
    datetime.datetime(2026, 1, 1, 12, 0),
    b"bytes",
])
def test_render_sql_rejects_disallowed_parameter_types(bad_param):
    with pytest.raises(ValueError):
        render_sql("SELECT * FROM orders WHERE status = ?", [bad_param], PROJECT, DATASET)


@pytest.mark.parametrize("bad_sql", [
    "SELECT 1; DROP TABLE orders",
    "DELETE FROM orders WHERE id = 1",
    "CREATE TABLE x AS SELECT 1",
    "SELECT * FROM orders\x00",
])
def test_render_sql_rejects_non_select_and_multi_statements(bad_sql):
    with pytest.raises(ValueError):
        render_sql(bad_sql, [], PROJECT, DATASET)


def test_parse_bq_payload_safeguards_and_type_casting():
    ok = parse_bq_payload({
        "jobComplete": True,
        "totalRows": "2",
        "schema": {"fields": [
            {"name": "method", "type": "STRING"},
            {"name": "cnt", "type": "INT64"},
            {"name": "rev", "type": "FLOAT64"},
            {"name": "active", "type": "BOOL"},
        ]},
        "rows": [
            {"f": [{"v": "card"}, {"v": "10"}, {"v": "123.45"}, {"v": "true"}]},
            {"f": [{"v": "bank"}, {"v": None}, {"v": "0.5"}, {"v": False}]},
        ],
    })
    assert ok.columns == ["method", "cnt", "rev", "active"]
    assert ok.rows == [["card", 10, 123.45, True], ["bank", None, 0.5, False]]

    with pytest.raises(RuntimeError, match="incomplete"):
        parse_bq_payload({"jobComplete": False, "rows": []})
    with pytest.raises(RuntimeError, match="truncated"):
        parse_bq_payload({"jobComplete": True, "pageToken": "next", "rows": []})
    with pytest.raises(RuntimeError, match="truncated"):
        parse_bq_payload({"jobComplete": True, "totalRows": "5", "rows": [{"f": [{"v": "1"}]}]})
    with pytest.raises(RuntimeError, match="BigQuery error"):
        parse_bq_payload({"errors": [{"message": "access denied"}]})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bq_to_duckdb(bq_sql: str, con: duckdb.DuckDBPyConnection) -> dict:
    """Translate BigQuery-rendered SQL back onto the seeded DuckDB fixture for fake-MCP testing."""
    if "INFORMATION_SCHEMA.COLUMNS" in bq_sql:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='main' ORDER BY table_name, ordinal_position"
        ).fetchall()
        return {
            "jobComplete": True,
            "totalRows": str(len(rows)),
            "schema": {"fields": [
                {"name": "table_name", "type": "STRING"},
                {"name": "column_name", "type": "STRING"},
                {"name": "data_type", "type": "STRING"},
            ]},
            "rows": [{"f": [{"v": t}, {"v": "STRING" if c == "VARCHAR" else c}, {"v": "STRING" if d == "VARCHAR" else d}]}
                     for t, c, d in rows],
        }
    cleaned = bq_sql.replace(f"`{PROJECT}`.`{DATASET}`.", "")
    duck_sql = sqlglot.transpile(cleaned, read="bigquery", write="duckdb")[0]
    cur = con.execute(duck_sql)
    fields = []
    for name, typ, *_ in cur.description:
        t_str = str(typ).upper()
        bq_t = "FLOAT64" if ("DOUBLE" in t_str or "FLOAT" in t_str or "DECIMAL" in t_str) else (
            "INT64" if "INT" in t_str else ("BOOL" if "BOOL" in t_str else "STRING")
        )
        fields.append({"name": name, "type": bq_t})
    fetched = cur.fetchall()
    return {
        "jobComplete": True,
        "totalRows": str(len(fetched)),
        "schema": {"fields": fields},
        "rows": [{"f": [{"v": None if v is None else str(v)} for v in r]} for r in fetched],
    }


@pytest.fixture
def fake_bq_mcp(env):
    """Run a real Streamable-HTTP MCP server backed by env's DuckDB data."""
    mcp = Server("fake-bigquery-mcp")
    state = {"calls": [], "bytes": 1024, "override": None}

    @mcp.tool()
    def execute_sql(query: str, projectId: str = "") -> dict:
        raise AssertionError("BigQueryMCP must never call read-write execute_sql")

    @mcp.tool()
    def execute_sql_readonly(query: str, projectId: str = "") -> str:
        state["calls"].append((projectId, query))
        if state["override"] is not None:
            return json.dumps(state["override"])
        return json.dumps(_bq_to_duckdb(query, env.con))

    port = _free_port()
    app = mcp.streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp", state
    finally:
        server.should_exit = True
        thread.join(timeout=3)


def test_bigquery_mcp_end_to_end_oracle_hostile_and_safeguards(env, fake_bq_mcp, capsys, monkeypatch):
    url, state = fake_bq_mcp
    wh = BigQueryMCP(
        project=PROJECT, dataset=DATASET, mcp_url=url, max_bytes=10_000,
        token_provider=lambda: "fake-oauth-token",
        dry_run_fn=lambda sql: state["bytes"],
    )
    svc = Service(env.reg, wh, log=False)

    # Hostile filter values and entity keys over BigQuery MCP return safe empty results
    r = svc.query_metric("revenue", filters={"method": "card' OR '1'='1; DROP TABLE payments --"},
                         as_role="finance")
    assert r["rows"] == [[None]]
    r_keys = svc.query_metric("revenue", entity_keys=[-999999], as_role="finance")
    assert r_keys["rows"] == [[None]]
    r_empty = svc.query_metric("revenue", entity_keys=[], as_role="finance")
    assert r_empty["rows"] == [[None]]

    # Multi-hop graph traversal + metric query on BigQuery MCP matches DuckDB gold
    g = svc.traverse_graph("Customer", ["HAS_ORDER"], {"Order": {"status": "completed"}}, "finance")
    ans = svc.query_metric("revenue", entity_keys=g["keys"], as_role="finance")
    gold = env.con.execute(
        "SELECT SUM(amount) FROM payments WHERE status='success' AND customer_id IN "
        "(SELECT customer_id FROM orders WHERE status='completed')"
    ).fetchone()[0]
    assert ans["rows"][0][0] == pytest.approx(gold)

    # Full 33-question oracle eval passes 33/33 against BigQueryMCP!
    monkeypatch.setattr(evaluate, "make_warehouse", lambda cfg, read_only=True: wh)
    evaluate.main(env.cfg, env.reg, "layer", True, "eval/questions.yaml")
    out = capsys.readouterr().out
    assert "answer accuracy: 25/25" in out and "abstention precision: 8/8" in out
    assert all(proj == PROJECT for proj, _ in state["calls"])

    # Dry-run byte cap refuses oversized scans before executing
    state["bytes"] = 50_000
    with pytest.raises(Refusal, match="above cap"):
        svc.query_metric("revenue", as_role="finance")

    # Incomplete jobs fail closed
    state["bytes"] = 100
    state["override"] = {"jobComplete": False, "rows": []}
    with pytest.raises(Refusal, match="incomplete"):
        svc.query_metric("revenue", as_role="finance")


def test_eval_stops_immediately_on_llm_quota_error_with_no_score(env, capsys):
    calls = 0

    class QuotaExhaustedLLM:
        def complete(self, prompt: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "OK"  # startup probe succeeds
            if calls == 2:
                return json.dumps({"metric": "revenue"})  # Q1 succeeds
            raise OSError("HTTP 429: quota exceeded")  # Q2 hits daily quota

    qs = [
        {"category": "single", "q": "What is our revenue?", "role": "finance",
         "expect": "answer", "call": None,
         "gold_sql": "SELECT SUM(amount) FROM payments WHERE status='success'"},
        {"category": "abstain", "q": "What is churn?", "role": "finance",
         "expect": "refuse", "call": None, "gold_sql": ""},
    ]
    qfile = env.tmp / "quota_q.yaml"
    qfile.write_text(yaml.safe_dump(qs))
    orig = evaluate.make_llm
    evaluate.make_llm = lambda cfg: QuotaExhaustedLLM()
    try:
        with pytest.raises(SystemExit, match="eval stopped at question 2/2"):
            evaluate.main(env.cfg, env.reg, "layer", False, str(qfile))
    finally:
        evaluate.make_llm = orig
    out = capsys.readouterr().out
    assert "answer accuracy:" not in out  # ADR-029: stopped run reports no score


def test_sensitive_column_values_excluded_from_eval_prompts(tmp_path):
    db = tmp_path / "sens.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE users(id INT, region VARCHAR, email VARCHAR)")
    con.executemany("INSERT INTO users VALUES (?, ?, ?)", [
        (1, "EU", "secret1@corp.test"),
        (2, "US", "secret2@corp.test"),
    ])
    con.close()
    wh = DuckDBWarehouse(db)
    try:
        hinted = evaluate._schema_text(wh, hints=True)
        assert "region in [EU, US]" in hinted
        assert "secret1@corp.test" not in hinted  # low-cardinality sensitive column omitted

        from semlayer.registry import Registry
        reg = Registry(tmp_path / "reg")
        reg.put_draft(Metric(name="user_count", table="users", agg="count", column="*",
                             dimensions=["region", "email"]))
        reg.certify("metric", "user_count", owner="sec")
        svc = Service(reg, wh, log=False)
        vals = evaluate._values(svc, "user_count")
        assert vals == {"region": ["EU", "US"]} and "email" not in vals
    finally:
        wh.close()


def test_compiler_bigquery_dialect_shapes():
    m = Metric(name="rev", table="payments", agg="sum", column="amount",
               dimensions=["method"], entity_key="customer_id")
    c = SimpleCompiler(dialect="bigquery")
    res = c.compile(m, ["method"], None, None, [10, 20])
    assert "customer_id IN UNNEST(?)" in res.sql and res.params == [[10, 20]]
    res_empty = c.compile(m, entity_keys=[])
    assert "FALSE" in res_empty.sql and res_empty.params == []
