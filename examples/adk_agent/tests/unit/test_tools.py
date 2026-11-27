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
"""Offline unit tests for certification gating, RBAC, AST SQL safety, and BigQuery MCP."""

import asyncio
import datetime
import json
import os
import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

from app.mcp_server import build_server
from app.semlayer import (
    DEFAULT_DEFINITIONS_DIR,
    BigQueryMCP,
    Config,
    DuckDBWarehouse,
    Metric,
    Refusal,
    Registry,
    Rule,
    Service,
    SimpleCompiler,
    State,
    build_db,
    evaluate,
    make_warehouse,
    parse_bq_payload,
    render_sql,
)

TEST_BQ_PROJECT = "demo-analytics-proj"
TEST_BQ_DATASET = "jaffle_shop"


@dataclass
class OfflineEnv:
    """Isolated offline test fixture with bundled registry copy and seeded DuckDB."""

    tmp: Path
    reg: Registry
    wh: DuckDBWarehouse
    svc: Service


@pytest.fixture
def env(tmp_path: Path) -> OfflineEnv:
    """Create a fresh temporary registry copy and deterministic DuckDB fixture."""
    reg_root = tmp_path / "definitions"
    shutil.copytree(DEFAULT_DEFINITIONS_DIR, reg_root)
    db_path = build_db(tmp_path / "jaffle.duckdb")
    reg = Registry(reg_root, state_dir=tmp_path / "state")
    wh = DuckDBWarehouse(db_path, read_only=True)
    svc = Service(reg, wh)
    try:
        yield OfflineEnv(tmp=tmp_path, reg=reg, wh=wh, svc=svc)
    finally:
        wh.close()


def test_mcp_server_exposes_only_governed_tools(env: OfflineEnv) -> None:
    """Verify the MCP server exposes the 7 governed tools and zero raw SQL tools."""
    srv = build_server(env.svc)
    listed = asyncio.run(srv.list_tools())
    tool_names = {tool.name for tool in getattr(listed, "tools", listed)}
    expected = {
        "list_metrics",
        "describe_metric",
        "query_metric",
        "describe_graph",
        "traverse_graph",
        "report_gap",
        "report_issue",
    }
    assert tool_names == expected
    forbidden = {
        "raw_sql",
        "execute_sql",
        "execute_sql_readonly",
        "run_sql",
        "query",
    }
    assert tool_names.isdisjoint(forbidden)


def test_certification_gate_blocks_draft_and_deprecated_definitions(
    env: OfflineEnv,
) -> None:
    """Draft and deprecated metrics/rules must never be listed or executed."""
    env.reg.put_draft(
        Metric(
            name="draft_margin",
            table="payments",
            agg="sum",
            column="amount",
        )
    )
    listed_names = {item["name"] for item in env.svc.list_metrics()}
    assert "revenue" in listed_names
    assert "draft_margin" not in listed_names
    assert "revenue_v2" not in listed_names  # deprecated in bundled registry

    with pytest.raises(Refusal, match="no certified metric"):
        env.svc.query_metric("draft_margin", as_role="finance")
    with pytest.raises(Refusal, match="no certified metric"):
        env.svc.query_metric("revenue_v2", as_role="finance")

    # Uncertified draft rules grant no access until human certification
    env.reg.put_draft(
        Rule(name="marketing_draft", role="marketing", metric="customer_count")
    )
    with pytest.raises(Refusal, match="access denied"):
        env.svc.query_metric("customer_count", as_role="marketing")

    env.reg.certify("rule", "marketing_draft", owner="security-team")
    allowed = env.svc.query_metric("customer_count", as_role="marketing")
    assert allowed["rows"] == [[60]]


def test_rbac_default_deny_row_filter_and_deny_dims(env: OfflineEnv) -> None:
    """Verify default-deny RBAC, row_filter scoping, deny beats allow, and deny_dims."""
    # Empty role and unknown role fail closed
    with pytest.raises(Refusal, match="access denied"):
        env.svc.query_metric("revenue", as_role="")
    with pytest.raises(Refusal, match="access denied"):
        env.svc.query_metric("revenue", as_role="unknown_role")

    # eu_analyst is denied revenue (default deny), but allowed EU-scoped customer_count
    with pytest.raises(Refusal, match="access denied"):
        env.svc.query_metric("revenue", as_role="eu_analyst")

    all_customers = env.svc.query_metric("customer_count", as_role="finance")[
        "rows"
    ][0][0]
    eu_customers = env.svc.query_metric("customer_count", as_role="eu_analyst")[
        "rows"
    ][0][0]
    expected_eu = env.wh.con.execute(
        "SELECT COUNT(*) FROM customers WHERE region = 'EU'"
    ).fetchone()[0]
    assert all_customers == 60
    assert eu_customers == expected_eu < all_customers

    # Explicit deny beats allow; sensitive dimension `email` is blocked by deny_dims
    rules = [
        Rule(name="r1", role="fin", state=State.certified, owner="sec"),
        Rule(
            name="r2",
            role="fin",
            metric="secret_metric",
            effect="deny",
            state=State.certified,
            owner="sec",
        ),
        Rule(
            name="r3",
            role="fin",
            effect="deny",
            deny_dims=["email"],
            state=State.certified,
            owner="sec",
        ),
    ]
    assert evaluate(rules, "fin", "revenue").allowed
    assert not evaluate(rules, "fin", "secret_metric").allowed
    assert "email" in evaluate(rules, "fin", "revenue").deny_dims

    # Even if a certified metric declares `email` as a dimension, deny_dims blocks it
    env.reg.put_draft(
        Metric(
            name="customer_by_email",
            table="customers",
            agg="count",
            column="*",
            dimensions=["region", "email"],
        )
    )
    env.reg.certify("metric", "customer_by_email", owner="data-team")
    with pytest.raises(Refusal, match="dimensions not permitted"):
        env.svc.query_metric(
            "customer_by_email", dims=["email"], as_role="finance"
        )


def test_graph_traversal_and_multihop_cohort_metric(env: OfflineEnv) -> None:
    """Verify FK entity graph gating and multi-hop cohort metric accuracy."""
    # Row-filtered roles cannot enumerate raw entity keys
    with pytest.raises(Refusal, match="unrestricted"):
        env.svc.traverse_graph("Customer", ["HAS_ORDER"], as_role="eu_analyst")

    # Sensitive / non-certified columns cannot be used as graph filters
    with pytest.raises(Refusal, match="not allowed as a filter"):
        env.svc.traverse_graph(
            "Customer",
            ["HAS_ORDER"],
            {"Customer": {"email": "c1@example.com"}},
            as_role="finance",
        )

    traversal = env.svc.traverse_graph(
        "Customer",
        ["HAS_ORDER"],
        {"Order": {"status": "completed"}},
        as_role="finance",
    )
    assert traversal["count"] > 0
    ans = env.svc.query_metric(
        "revenue", entity_keys=traversal["keys"], as_role="finance"
    )
    gold = env.wh.con.execute(
        "SELECT SUM(amount) FROM payments WHERE status = 'success' "
        "AND customer_id IN ("
        "SELECT customer_id FROM orders WHERE status = 'completed')"
    ).fetchone()[0]
    assert ans["rows"][0][0] == pytest.approx(gold)


@pytest.mark.parametrize(
    "hostile",
    [
        "card' OR '1'='1",
        "card'; DROP TABLE payments; --",
        "card\\' OR TRUE /* comment */",
        "line1\nline2\r\t\\backslash`tick`",
    ],
)
def test_hostile_sql_injection_literals_neutralized_duckdb_and_bigquery(
    env: OfflineEnv, hostile: str
) -> None:
    """Hostile filter strings must stay inert string literals in both dialects."""
    # 1. DuckDB parameterized execution
    res = env.svc.query_metric(
        "revenue", filters={"method": hostile}, as_role="finance"
    )
    assert res["rows"] == [[None]]
    assert (
        env.wh.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 300
    )

    # 2. BigQuery SQLGlot AST literal binding
    compiled = SimpleCompiler(dialect="bigquery").compile(
        env.reg.get("metric", "revenue"),  # type: ignore[arg-type]
        filters={"method": hostile},
    )
    bq_sql = render_sql(
        compiled.sql, compiled.params, TEST_BQ_PROJECT, TEST_BQ_DATASET
    )
    parsed = sqlglot.parse(bq_sql, read="bigquery")
    assert len(parsed) == 1
    assert isinstance(parsed[0], sqlglot.exp.Select)
    literals = [
        lit.this
        for lit in parsed[0].find_all(sqlglot.exp.Literal)
        if lit.is_string
    ]
    assert hostile in literals


@pytest.mark.parametrize(
    "bad_param",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"nested": 1},
        [[1, 2]],
        "bad\x00null",
        datetime.datetime(2026, 1, 1, 12, 0),
        b"raw-bytes",
    ],
)
def test_render_sql_rejects_disallowed_parameters(bad_param: Any) -> None:
    """BigQuery AST renderer must reject unsafe or ambiguous literal types."""
    with pytest.raises(ValueError):
        render_sql(
            "SELECT COUNT(*) FROM orders WHERE status = ?",
            [bad_param],
            TEST_BQ_PROJECT,
            TEST_BQ_DATASET,
        )


@pytest.mark.parametrize(
    "bad_sql",
    [
        "SELECT 1; DROP TABLE orders",
        "DELETE FROM orders WHERE id = 1",
        "CREATE TABLE hacked AS SELECT 1",
        "SELECT * FROM orders\x00",
    ],
)
def test_render_sql_rejects_multi_statement_and_non_select(
    bad_sql: str,
) -> None:
    """BigQuery AST renderer must allow single SELECT statements only."""
    with pytest.raises(ValueError):
        render_sql(bad_sql, [], TEST_BQ_PROJECT, TEST_BQ_DATASET)


def test_render_sql_rejects_placeholder_project_id() -> None:
    """Placeholder project IDs like YOUR_PROJECT_ID must never be rendered."""
    with pytest.raises(ValueError, match="invalid BigQuery project id"):
        render_sql(
            "SELECT COUNT(*) FROM orders",
            [],
            "YOUR_PROJECT_ID",
            TEST_BQ_DATASET,
        )
    with pytest.raises(ValueError, match="configured GCP project"):
        make_warehouse(
            Config(
                warehouse="bigquery",
                bq_project="",
                bq_dataset=TEST_BQ_DATASET,
            )
        )


def test_render_sql_dates_tables_and_empty_unnest() -> None:
    """Verify BigQuery date rewriting, table qualification, and empty key safety."""
    sql = (
        "SELECT method, SUM(amount) AS revenue FROM payments "
        "WHERE (status = 'success') AND method = ? AND paid_at >= ? "
        "AND (order_date >= current_date - 30) AND customer_id IN UNNEST(?) "
        "GROUP BY method ORDER BY method"
    )
    rendered = render_sql(
        sql,
        ["card", datetime.date(2026, 11, 1), [1, 2]],
        TEST_BQ_PROJECT,
        TEST_BQ_DATASET,
    )
    assert f"`{TEST_BQ_PROJECT}`.`{TEST_BQ_DATASET}`.`payments`" in rendered
    assert "DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)" in rendered
    assert "CAST('2026-11-01' AS DATE)" in rendered
    assert "IN UNNEST([1, 2])" in rendered

    empty = render_sql(
        "SELECT SUM(amount) FROM payments WHERE customer_id IN UNNEST(?)",
        [[]],
        TEST_BQ_PROJECT,
        TEST_BQ_DATASET,
    )
    assert "FALSE" in empty and "UNNEST" not in empty


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _translate_bq_to_duckdb(
    bq_sql: str, con: duckdb.DuckDBPyConnection
) -> dict[str, Any]:
    """Execute BigQuery-rendered SQL against the local DuckDB fixture."""
    cleaned = bq_sql.replace(f"`{TEST_BQ_PROJECT}`.`{TEST_BQ_DATASET}`.", "")
    duck_sql = sqlglot.transpile(cleaned, read="bigquery", write="duckdb")[0]
    cur = con.execute(duck_sql)
    fields = []
    for name, col_type, *_ in cur.description or []:
        upper = str(col_type).upper()
        bq_type = (
            "FLOAT64"
            if ("DOUBLE" in upper or "FLOAT" in upper or "DECIMAL" in upper)
            else (
                "INT64"
                if "INT" in upper
                else ("BOOL" if "BOOL" in upper else "STRING")
            )
        )
        fields.append({"name": name, "type": bq_type})
    fetched = cur.fetchall()
    return {
        "jobComplete": True,
        "totalRows": str(len(fetched)),
        "schema": {"fields": fields},
        "rows": [
            {"f": [{"v": None if cell is None else str(cell)} for cell in row]}
            for row in fetched
        ],
    }


@pytest.fixture
def local_bq_mcp_server(env: OfflineEnv):
    """Run a local loopback Streamable-HTTP MCP server with zero external calls."""
    mcp = Server("fake-google-bigquery-mcp")
    state: dict[str, Any] = {
        "dry_run_calls": 0,
        "exec_calls": 0,
        "estimated_bytes": 2048,
        "override_payload": None,
    }

    @mcp.tool()
    def execute_sql(query: str, projectId: str = "") -> dict[str, Any]:
        raise AssertionError(
            "BigQueryMCP must never invoke read-write execute_sql"
        )

    @mcp.tool()
    def execute_sql_readonly(
        query: str, projectId: str = "", dryRun: bool = False
    ) -> str:
        assert projectId == TEST_BQ_PROJECT
        if dryRun:
            state["dry_run_calls"] += 1
            return json.dumps(
                {"totalBytesProcessed": str(state["estimated_bytes"])}
            )
        state["exec_calls"] += 1
        if state["override_payload"] is not None:
            return json.dumps(state["override_payload"])
        return json.dumps(_translate_bq_to_duckdb(query, env.wh.con))

    port = _free_loopback_port()
    app = mcp.streamable_http_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
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


def test_bigquery_mcp_readonly_dry_run_cap_and_payload_safeguards(
    env: OfflineEnv, local_bq_mcp_server: tuple[str, dict[str, Any]]
) -> None:
    """Verify execute_sql_readonly-only execution, dryRun byte cap, and fail-closed parsing."""
    mcp_url, state = local_bq_mcp_server
    wh = BigQueryMCP(
        project=TEST_BQ_PROJECT,
        dataset=TEST_BQ_DATASET,
        mcp_url=mcp_url,
        max_bytes=10_000,
        token_provider=lambda: "offline-test-token",
    )
    svc = Service(env.reg, wh, log=False)

    # 1. Under byte cap: dryRun runs first, then execute_sql_readonly matches DuckDB gold
    ok = svc.query_metric("revenue", as_role="finance")
    gold = env.wh.con.execute(
        "SELECT SUM(amount) FROM payments WHERE status = 'success'"
    ).fetchone()[0]
    assert ok["rows"][0][0] == pytest.approx(gold)
    assert state["dry_run_calls"] == 1
    assert state["exec_calls"] == 1

    # 2. Over byte cap: dryRun refuses BEFORE executing the query
    state["estimated_bytes"] = 50_000
    with pytest.raises(Refusal, match="above cap"):
        svc.query_metric("revenue", as_role="finance")
    assert state["dry_run_calls"] == 2
    assert state["exec_calls"] == 1  # execution was never called!

    # 3. Incomplete / truncated / error payloads fail closed
    state["estimated_bytes"] = 512
    state["override_payload"] = {"jobComplete": False, "rows": []}
    with pytest.raises(Refusal, match="incomplete"):
        svc.query_metric("revenue", as_role="finance")

    with pytest.raises(RuntimeError, match="truncated"):
        parse_bq_payload({"jobComplete": True, "pageToken": "tok", "rows": []})
    with pytest.raises(RuntimeError, match="truncated"):
        parse_bq_payload(
            {
                "jobComplete": True,
                "totalRows": "5",
                "rows": [{"f": [{"v": "1"}]}],
            }
        )
    with pytest.raises(RuntimeError, match="BigQuery error"):
        parse_bq_payload({"errors": [{"message": "permission denied"}]})


def test_stdio_mcp_server_subprocess_roundtrip(env: OfflineEnv) -> None:
    """Verify `python -m app.mcp_server` serves governed queries over stdio."""
    recipe_root = Path(__file__).resolve().parents[2]
    db_file = env.tmp / "stdio_jaffle.duckdb"
    build_db(db_file)
    sub_env = {
        **os.environ,
        "SEMLAYER_WAREHOUSE": "duckdb",
        "SEMLAYER_DUCKDB_PATH": str(db_file),
        "SEMLAYER_STATE_DIR": str(env.tmp / "stdio_state"),
    }

    async def _exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=sub_env,
            cwd=str(recipe_root),
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert "query_metric" in names
            assert "execute_sql" not in names

            allowed = await session.call_tool(
                "query_metric",
                {"metric": "customer_count", "as_role": "eu_analyst"},
            )
            allowed_payload = json.loads(allowed.content[0].text)
            assert allowed_payload["metric"] == "customer_count"
            assert allowed_payload["rows"][0][0] < 60

            denied = await session.call_tool(
                "query_metric",
                {"metric": "revenue", "as_role": "eu_analyst"},
            )
            denied_payload = json.loads(denied.content[0].text)
            assert "access denied" in denied_payload["error"]

    asyncio.run(_exercise())
