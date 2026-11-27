"""Offline tests for Tier 1 & Tier 2 GCP integrations (ADR-036..ADR-038):
- BigQuery INFORMATION_SCHEMA.JOBS_BY_PROJECT ingestion, owner email privacy stripping, and table qualification stripping
- Dataplex / BigQuery policy tags -> automatic `deny_dims` rule drafting
- Google Cloud Logging structured JSON stderr telemetry (preserving stdout cleanly)
- BigQuery Materialized View DDL advisor (`recommend_materialized_views` & `semlayer feedback --recommend-mv`)
"""
from __future__ import annotations

import json

import pytest

from semlayer.cli import main as cli_main
from semlayer.core import Metric, Rule
from semlayer.ingest import policy_tags_snapshot, read_bq_jobs
from semlayer.monitor import recommend_materialized_views
from semlayer.policy import suggest_rules
from semlayer.registry import Registry
from semlayer.serve.service import Refusal, Service
from semlayer.warehouse import DuckDBWarehouse, QueryResult
from semlayer.warehouse.bigquery import (
    BigQueryMCP,
    _sanitise_owner_hint,
    normalise_bq_query_tables,
)


def test_bq_jobs_ingestion_sanitises_owner_and_normalises_tables(monkeypatch):
    captured_sqls: list[str] = []

    def fake_run_sql(self, bq_sql: str, max_rows: int | None = None) -> QueryResult:
        captured_sqls.append(bq_sql)
        if "JOBS_BY_PROJECT" in bq_sql:
            return QueryResult(
                ["user_email", "query"],
                [
                    [
                        "Alice.Finance@company.com",
                        "SELECT method, SUM(amount) AS revenue FROM `my-proj`.`jaffle_shop`.`payments` WHERE status = 'success' GROUP BY method;",
                    ],
                    [
                        "99_bot@service.gserviceaccount.com",
                        "SELECT COUNT(*) AS order_count FROM `jaffle_shop`.`orders`",
                    ],
                ],
            )
        if "COLUMN_FIELD_PATHS" in bq_sql:
            return QueryResult(
                ["table_name", "field_path"],
                [
                    ["customers", "tax_id"],
                    ["customers", "billing.iban"],
                ],
            )
        return QueryResult(
            ["table_name", "column_name", "data_type"],
            [
                ["customers", "id", "INT64"],
                ["customers", "region", "STRING"],
                ["customers", "tax_id", "STRING"],
                ["customers", "billing", "STRUCT"],
                ["orders", "id", "INT64"],
                ["payments", "amount", "FLOAT64"],
                ["payments", "method", "STRING"],
                ["payments", "status", "STRING"],
            ],
        )

    monkeypatch.setattr(BigQueryMCP, "_run_sql", fake_run_sql)
    bq = BigQueryMCP(project="my-proj", dataset="jaffle_shop", location="region-us", token_provider=lambda: "tok")

    queries = read_bq_jobs(bq, limit=50, days=14)
    assert len(queries) == 2
    # Domain stripped! Raw email address is never stored in Query.user
    assert queries[0].user == "alice_finance"
    assert "@company.com" not in queries[0].user
    assert queries[1].user == "u_99_bot"
    # Qualified `my-proj`.`jaffle_shop`.`payments` normalised to `payments` for single-table draft engine
    assert "FROM payments" in queries[0].sql
    assert "my-proj" not in queries[0].sql

    # Policy tags extracted cleanly
    tags = policy_tags_snapshot(bq)
    assert tags == {"customers": ["tax_id", "billing"]}

    # Invalid region qualifier rejected before building SQL
    bad_bq = BigQueryMCP(project="my-proj", dataset="jaffle_shop", location="us; DROP TABLE x", token_provider=lambda: "tok")
    with pytest.raises(ValueError, match="invalid BigQuery region qualifier"):
        bad_bq.recent_queries()

    assert _sanitise_owner_hint("") == "unknown"
    assert "FROM payments" in normalise_bq_query_tables(
        "SELECT SUM(amount) FROM `my-proj`.`jaffle_shop`.`payments`", "my-proj", "jaffle_shop"
    )


def test_dataplex_policy_tags_merge_into_suggested_deny_dims():
    schema = {
        "customers": {"id": "INT64", "email": "STRING", "tax_id": "STRING", "region": "STRING"},
    }
    policy_tags = {"customers": ["tax_id"]}
    rules = suggest_rules(schema, ["finance", "eu_analyst"], policy_tags=policy_tags)
    assert len(rules) == 2
    # Both regex `email` AND Dataplex policy-tagged `tax_id` are restricted, while `region` stays open!
    assert rules[0].deny_dims == ["email", "tax_id"]
    assert rules[0].evidence.source == "dataplex_policy_tags+schema"
    assert "customers.tax_id" in rules[0].evidence.lineage


def test_cloud_logging_json_writes_to_stderr_only(tmp_path, monkeypatch, capsys):
    import duckdb

    monkeypatch.setenv("SEMLAYER_LOG_JSON", "1")
    db_path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE orders(id INT, status VARCHAR)")
    con.execute("INSERT INTO orders VALUES (1, 'completed')")
    con.close()

    reg = Registry(tmp_path / "defs")
    m = Metric(name="order_count", table="orders", agg="count", column="*", dimensions=["status"])
    reg.put_draft(m)
    reg.certify("metric", "order_count", owner="alice")
    r = Rule(name="fin_all", role="finance", metric="*")
    reg.put_draft(r)
    reg.certify("rule", "fin_all", owner="sec")

    wh = DuckDBWarehouse(db_path, read_only=True)
    try:
        svc = Service(reg, wh)
        svc.query_metric("order_count", as_role="finance")
        with pytest.raises(Refusal):
            svc.query_metric("order_count", as_role="intruder")
    finally:
        wh.close()

    captured = capsys.readouterr()
    # Stdout MUST stay completely empty so MCP stdio JSON-RPC framing is never corrupted!
    assert captured.out == ""
    lines = [json.loads(line) for line in captured.err.strip().splitlines()]
    events = {item["event"]: item for item in lines}
    assert events["audit.certify"]["severity"] == "INFO"
    assert events["usage.query"]["severity"] == "INFO"
    assert events["usage.refused"]["severity"] == "WARNING"
    assert events["usage.refused"]["role"] == "intruder"


def test_bigquery_materialized_view_advisor_and_cli_feedback(tmp_path):
    import duckdb

    db_path = tmp_path / "mv.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE payments(id INT, customer_id INT, method VARCHAR, status VARCHAR, amount DOUBLE, paid_at DATE)")
    con.close()

    reg = Registry(tmp_path / "defs")
    rev = Metric(
        name="revenue",
        table="payments",
        agg="sum",
        column="amount",
        filters=["status = 'success'"],
        dimensions=["method"],
        time_dimension="paid_at",
    )
    active = Metric(
        name="active_buyers",
        table="payments",
        agg="count_distinct",
        column="customer_id",
    )
    reg.put_draft(rev)
    reg.certify("metric", "revenue", owner="alice")
    reg.put_draft(active)
    reg.certify("metric", "active_buyers", owner="alice")

    # Simulate 4 queries to `revenue` and 3 queries to `active_buyers`
    events = (
        [{"event": "query", "metric": "revenue", "role": "finance"}] * 4
        + [{"event": "query", "metric": "active_buyers", "role": "finance"}] * 3
    )
    (reg.state_dir / "usage.jsonl").write_text("\n".join(json.dumps(x) for x in events) + "\n")

    recs = recommend_materialized_views(reg, events, project="demo-gcp", dataset="jaffle_shop", min_volume=3)
    by_metric = {r["metric"]: r for r in recs}

    assert by_metric["revenue"]["eligible"] is True
    ddl = by_metric["revenue"]["ddl"]
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS `demo-gcp`.`jaffle_shop`.`mv_semlayer_revenue`" in ddl
    assert "`demo-gcp`.`jaffle_shop`.`payments`" in ddl
    assert "ORDER BY" not in ddl  # BigQuery MVs forbid ORDER BY in defining SELECT

    # COUNT(DISTINCT) is flagged cleanly as ineligible for incremental BigQuery MV
    assert by_metric["active_buyers"]["eligible"] is False
    assert "APPROX_COUNT_DISTINCT" in by_metric["active_buyers"]["reason"]

    # CLI `semlayer feedback --recommend-mv` outputs valid JSON
    cfg_file = tmp_path / "semlayer.yaml"
    cfg_file.write_text(f"db: {db_path.as_posix()}\nregistry: {(tmp_path / 'defs').as_posix()}\n")
    cli_main([
        "--config", str(cfg_file), "--no-llm",
        "feedback", "--recommend-mv", "--project", "demo-gcp", "--dataset", "jaffle_shop",
    ])
