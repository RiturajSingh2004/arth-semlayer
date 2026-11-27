"""Tests for Lean v2 features (ADR-031..ADR-035):
- Bound role strict mode, resource caps, suspended state & canary
- AST QuerySpec, time grains (incl. ISO week, month-end, leap day), relative/explicit time ranges
- Filter operators (eq, ne, in, gt, gte, lt, lte, between, is_null) & order_by / limit
- Ratio & derived metrics (NULLIF division, dependency cycles, RBAC on inputs) & query_metrics CTEs
- Many-to-one dimension joins across certified edges & one-to-many fan-out refusal
- SQLite FTS5 search_metrics & paginated list_metrics
- BigQuery MCP AST rendering compatibility across all new SQL constructs
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from semlayer.cli import main as cli_main
from semlayer.compiler import SimpleCompiler, resolve_time_range
from semlayer.core import Edge, Entity, InvalidTransition, Metric, Rule, State
from semlayer.monitor import run_canary
from semlayer.registry import Registry
from semlayer.serve.mcp_server import build_server
from semlayer.serve.service import Refusal, Service
from semlayer.warehouse import DuckDBWarehouse
from semlayer.warehouse.bigquery import render_sql


@pytest.fixture()
def env_v2(tmp_path):
    db_path = tmp_path / "v2.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers(id INTEGER, region VARCHAR, email VARCHAR, created_at DATE)")
    con.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, status VARCHAR, order_date DATE)")
    con.execute(
        "CREATE TABLE payments(id INTEGER, order_id INTEGER, customer_id INTEGER, "
        "method VARCHAR, status VARCHAR, amount DOUBLE, paid_at DATE)"
    )
    con.executemany(
        "INSERT INTO customers VALUES (?,?,?,?)",
        [
            (1, "EU", "a@eu.example", dt.date(2024, 1, 10)),
            (2, "US", "b@us.example", dt.date(2024, 2, 29)),  # leap day!
            (3, "EU", None, dt.date(2024, 3, 15)),
        ],
    )
    con.executemany(
        "INSERT INTO orders VALUES (?,?,?,?)",
        [
            (10, 1, "completed", dt.date(2024, 1, 31)),  # month-end Wednesday
            (11, 1, "returned", dt.date(2024, 2, 4)),    # ISO week 2024-W05 Sunday -> Monday 2024-01-29!
            (12, 2, "completed", dt.date(2024, 2, 29)),  # leap day Thursday -> Monday 2024-02-26!
            (13, 3, "completed", dt.date(2024, 3, 1)),   # next month Friday
        ],
    )
    con.executemany(
        "INSERT INTO payments VALUES (?,?,?,?,?,?,?)",
        [
            (100, 10, 1, "card", "success", 120.0, dt.date(2024, 1, 31)),
            (101, 11, 1, "bank", "success", 80.0, dt.date(2024, 2, 4)),
            (102, 12, 2, "card", "success", 300.0, dt.date(2024, 2, 29)),
            (103, 13, 3, "gift", "failed", 50.0, dt.date(2024, 3, 1)),
        ],
    )
    con.close()

    reg = Registry(tmp_path / "defs")

    # Entities & certified edges (Customer 1 -> N Order, Customer 1 -> N Payment)
    for ent in [
        Entity(name="Customer", table="customers", key="id"),
        Entity(name="Order", table="orders", key="id"),
        Entity(name="Payment", table="payments", key="id"),
    ]:
        reg.put_draft(ent)
        reg.certify("entity", ent.name, owner="data")

    for edge in [
        Edge(
            name="customer_has_order",
            src="Customer",
            rel="HAS_ORDER",
            dst="Order",
            src_key="id",
            dst_key="customer_id",
            cardinality="one_to_many",
        ),
        Edge(
            name="customer_has_payment",
            src="Customer",
            rel="HAS_PAYMENT",
            dst="Payment",
            src_key="id",
            dst_key="customer_id",
            cardinality="one_to_many",
        ),
    ]:
        reg.put_draft(edge)
        reg.certify("edge", edge.name, owner="data")

    # Base certified metrics
    for m in [
        Metric(
            name="revenue",
            concept="revenue",
            description="Total successful payment amount",
            table="payments",
            agg="sum",
            column="amount",
            filters=["status = 'success'"],
            dimensions=["method"],
            time_dimension="paid_at",
            synonyms=["sales", "gross_sales", "turnover"],
            entity="Customer",
            entity_key="customer_id",
        ),
        Metric(
            name="order_count",
            concept="order_count",
            description="Total number of customer orders",
            table="orders",
            agg="count",
            column="*",
            dimensions=["status"],
            time_dimension="order_date",
            synonyms=["orders", "volume"],
            entity="Customer",
            entity_key="customer_id",
        ),
        Metric(
            name="customer_count",
            concept="customer_count",
            description="Registered customers by region",
            table="customers",
            agg="count",
            column="*",
            dimensions=["region", "email"],
            time_dimension="created_at",
            synonyms=["users", "accounts"],
            entity="Customer",
            entity_key="id",
        ),
    ]:
        reg.put_draft(m)
        reg.certify("metric", m.name, owner="alice")

    # Rules
    for r in [
        Rule(name="finance_all", role="finance", metric="*", deny_dims=["email"]),
        Rule(
            name="eu_customers",
            role="eu_analyst",
            metric="customer_count",
            row_filter="region = 'EU'",
            deny_dims=["email"],
        ),
        Rule(name="eu_orders", role="eu_analyst", metric="order_count"),
    ]:
        reg.put_draft(r)
        reg.certify("rule", r.name, owner="sec")

    wh = DuckDBWarehouse(db_path, read_only=True)
    yield tmp_path, db_path, reg, wh
    wh.close()


# --- ADR-031: Bound Role, Resource Caps & Canary / Suspended -------------------------


def test_bound_role_strict_mode_blocks_spoofing(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh, bound_role="eu_analyst")
    # Omitting as_role uses bound_role ("eu_analyst")
    res = svc.query_metric("customer_count")
    assert res["rows"] == [[2]]
    # Prompt-injected caller supplying as_role="finance" (or even matching role) is rejected
    with pytest.raises(Refusal, match="role is bound by server"):
        svc.query_metric("revenue", as_role="finance")
    with pytest.raises(Refusal, match="role is bound by server"):
        svc.query_metric("customer_count", as_role="eu_analyst")
    # Default-deny still holds under the bound role
    with pytest.raises(Refusal, match="access denied"):
        svc.query_metric("revenue")


def test_resource_caps_max_rows_groups_and_filter_list(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh, max_rows=2, max_groups=1, max_filter_list=2)

    with pytest.raises(Refusal, match="limit must be between 1 and 2"):
        svc.query_metric("order_count", as_role="finance", limit=5)

    # 2 distinct payment methods ('card', 'bank') exceeds max_groups=1 when un-limited
    with pytest.raises(Refusal, match="max_groups"):
        svc.query_metric("revenue", dims=["method"], as_role="finance")

    # Explicit limit=1 within max_rows succeeds
    top1 = svc.query_metric("revenue", dims=["method"], order_by="-revenue", limit=1, as_role="finance")
    assert top1["rows"] == [["card", 420.0]]

    # Filter 'in' list above max_filter_list=2 is refused
    with pytest.raises(Refusal, match="max_filter_list"):
        svc.query_metric(
            "revenue",
            filters={"method": {"in": ["card", "bank", "gift"]}},
            as_role="finance",
        )


def test_canary_suspends_broken_metric_and_cascades_to_dependents(env_v2):
    _, db_path, reg, wh = env_v2
    aov = Metric(
        name="aov",
        metric_type="ratio",
        numerator="revenue",
        denominator="order_count",
    )
    reg.put_draft(aov)
    reg.certify("metric", "aov", owner="alice")

    # Healthy canary passes all certified metrics
    ok = run_canary(reg, wh, suspend=True)
    assert ok["failed"] == {}
    assert "aov" in ok["passed"]

    # Simulate upstream breaking schema change: drop `amount` column from `payments`
    wh.close()
    wcon = duckdb.connect(str(db_path))
    wcon.execute("ALTER TABLE payments DROP COLUMN amount")
    wcon.close()

    wh2 = DuckDBWarehouse(db_path, read_only=True)
    try:
        dry = run_canary(reg, wh2, suspend=False)
        assert "revenue" in dry["failed"]
        assert reg.get("metric", "revenue").state is State.certified  # dry-run did not mutate

        live = run_canary(reg, wh2, suspend=True)
        assert "revenue" in live["failed"]
        # Both `revenue` and dependent `aov` transition to `suspended`!
        assert set(live["suspended"]) == {"revenue", "aov"}
        assert reg.get("metric", "revenue").state is State.suspended
        assert reg.get("metric", "aov").state is State.suspended
        assert "dependency 'revenue' suspended" in reg.get("metric", "aov").suspend_reason

        svc = Service(reg, wh2)
        listed_names = [m["name"] for m in svc.list_metrics()]
        assert "revenue" not in listed_names
        assert "aov" not in listed_names
        with pytest.raises(Refusal, match="suspended"):
            svc.query_metric("revenue", as_role="finance")
    finally:
        wh2.close()


# --- ADR-032: Time Grains, Relative/Explicit Ranges, Filter Ops, Order & Limit -------


def test_time_grains_iso_week_leap_day_and_ranges(env_v2):
    _, _, reg, wh = env_v2
    frozen_clock = lambda: dt.date(2024, 3, 1)
    svc = Service(reg, wh, clock=frozen_clock)

    # Monthly revenue across Jan-Feb 2024 (includes Jan 31 month-end and Feb 29 leap day)
    monthly = svc.query_metric("revenue", time_grain="month", as_role="finance")
    assert monthly["columns"] == ["period", "revenue"]
    assert monthly["rows"] == [["2024-01-01", 120.0], ["2024-02-01", 380.0]]

    # ISO Week truncation: Sunday 2024-02-04 truncates to Monday 2024-01-29 (same week as Wed 2024-01-31!)
    weekly = svc.query_metric("revenue", time_grain="week", as_role="finance")
    assert weekly["rows"] == [["2024-01-29", 200.0], ["2024-02-26", 300.0]]

    # Relative `last_month` with frozen clock 2024-03-01 -> [2024-02-01, 2024-03-01) (captures leap day 2024-02-29, excludes 2024-03-01)
    feb = svc.query_metric("order_count", time_range="last_month", as_role="finance")
    assert feb["rows"] == [[2]]

    # Half-open explicit [start, end) excludes end boundary
    jan_only = svc.query_metric(
        "order_count",
        time_range={"start": "2024-01-31", "end": "2024-02-29"},
        as_role="finance",
    )
    assert jan_only["rows"] == [[2]]  # 2024-01-31 and 2024-02-04; 2024-02-29 excluded

    # Relative resolver unit checks (quarter, year, last_7_days)
    assert resolve_time_range("last_7_days", clock=frozen_clock) == (
        dt.date(2024, 2, 23),
        dt.date(2024, 3, 1),
    )
    assert resolve_time_range("this_quarter", clock=frozen_clock) == (
        dt.date(2024, 1, 1),
        dt.date(2024, 4, 1),
    )


def test_filter_operators_and_type_allowlist(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh)

    # `in` + `ne` on categorical
    r_in = svc.query_metric(
        "revenue",
        dims=["method"],
        filters={"method": {"in": ["card", "bank"]}},
        order_by="revenue asc",
        as_role="finance",
    )
    assert r_in["rows"] == [["bank", 80.0], ["card", 420.0]]

    r_ne = svc.query_metric("revenue", filters={"method": {"ne": "card"}}, as_role="finance")
    assert r_ne["rows"] == [[80.0]]

    # Empty `in` compiles safely to FALSE -> 0 count
    r_empty = svc.query_metric("order_count", filters={"status": {"in": []}}, as_role="finance")
    assert r_empty["rows"] == [[0]]

    # `between` and `gte`/`lt` on time dimension
    r_between = svc.query_metric(
        "order_count",
        filters=[{"field": "order_date", "op": "between", "value": ["2024-02-01", "2024-02-29"]}],
        as_role="finance",
    )
    assert r_between["rows"] == [[2]]

    # Range operator on categorical dimension is refused!
    with pytest.raises(Refusal, match="not allowed on categorical dimension"):
        svc.query_metric("revenue", filters={"method": {"gt": "a"}}, as_role="finance")

    # `is_null` on allowed categorical dimension (using a non-denied dim)
    r_not_null = svc.query_metric(
        "customer_count",
        filters={"region": {"is_null": False}},
        as_role="finance",
    )
    assert r_not_null["rows"] == [[3]]


# --- ADR-033: Ratio & Derived Metrics, Dependency Gating & Multi-Metric CTEs ---------


def test_ratio_derived_metrics_dependencies_and_divide_by_zero(env_v2):
    _, _, reg, wh = env_v2

    # Disallow SQL/code injection in derived formula
    with pytest.raises(ValueError, match="disallowed construct"):
        Metric(name="bad_derived", metric_type="derived", formula="__import__('os').system('id')")

    # Cannot certify a ratio metric whose input is still a draft
    draft_input = Metric(name="draft_m", table="orders", agg="count", column="*")
    reg.put_draft(draft_input)
    bad_ratio = Metric(
        name="bad_ratio",
        metric_type="ratio",
        numerator="revenue",
        denominator="draft_m",
    )
    reg.put_draft(bad_ratio)
    with pytest.raises(InvalidTransition, match="requires certified input metric 'draft_m'"):
        reg.certify("metric", "bad_ratio", owner="alice")

    # Certify AOV (ratio) and net_per_order (derived referencing AOV!)
    aov = Metric(
        name="aov",
        metric_type="ratio",
        description="Average order value",
        numerator="revenue",
        denominator="order_count",
    )
    scaled = Metric(
        name="aov_cents",
        metric_type="derived",
        description="AOV in cents",
        formula="aov * 100",
    )
    reg.put_draft(aov)
    reg.certify("metric", "aov", owner="alice")
    reg.put_draft(scaled)
    reg.certify("metric", "aov_cents", owner="alice")

    # Cycle rejection on revision
    with pytest.raises(ValueError, match="cycle"):
        reg.put_draft(Metric(name="aov", metric_type="derived", formula="aov_cents / 100"))

    # Deprecating `revenue` while `aov` is certified is blocked
    with pytest.raises(ValueError, match="required by certified metrics"):
        reg.deprecate("metric", "revenue")

    svc = Service(reg, wh)
    # Total revenue = 500.0, order_count = 4 -> AOV = 125.0, aov_cents = 12500.0
    res_aov = svc.query_metric("aov", as_role="finance")
    assert res_aov["rows"] == [[125.0]]
    res_cents = svc.query_metric("aov_cents", as_role="finance")
    assert res_cents["rows"] == [[12500.0]]

    # Divide-by-zero returns None (NULLIF) instead of crashing when 0 orders match time_range
    zero_div = svc.query_metric(
        "aov",
        time_range=["2020-01-01", "2020-01-02"],
        as_role="finance",
    )
    assert zero_div["rows"] == [[None]]

    # Indirect RBAC bypass check: eu_analyst is allowed `order_count` and even if granted `aov`,
    # lacks `revenue` -> refused!
    r_aov = Rule(name="eu_aov", role="eu_analyst", metric="aov")
    reg.put_draft(r_aov)
    reg.certify("rule", "eu_aov", owner="sec")
    with pytest.raises(Refusal, match="access denied"):
        svc.query_metric("aov", as_role="eu_analyst")


def test_query_metrics_cte_no_fanout_multiplication(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh)

    # Multi-metric query across `payments` and `orders` by month (`period`)
    res = svc.query_metrics(
        ["revenue", "order_count"],
        time_grain="month",
        order_by="period asc",
        as_role="finance",
    )
    assert res["columns"] == ["period", "revenue", "order_count"]
    # Jan: rev 120, orders 1; Feb: rev 380, orders 2; Mar: rev None (only failed payment), orders 1!
    assert res["rows"] == [
        ["2024-01-01", 120.0, 1],
        ["2024-02-01", 380.0, 2],
        ["2024-03-01", None, 1],
    ]
    # Sum of monthly orders == total orders (4), sum of monthly revenue == total revenue (500.0)
    assert sum(r[2] for r in res["rows"]) == 4
    assert sum(r[1] or 0.0 for r in res["rows"]) == 500.0


# --- ADR-034: Many-to-One Joins & One-to-Many Fan-Out Refusal ------------------------


def test_many_to_one_join_preserves_totals_and_blocks_fanout(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh)

    desc = svc.describe_metric("revenue")
    assert "Customer.region" in desc["joinable_dimensions"]

    # Slice `revenue` (`payments`) by `Customer.region` (`customers`) via many-to-one reverse FK!
    by_reg = svc.query_metric("revenue", dims=["Customer.region"], as_role="finance")
    assert by_reg["columns"] == ["customer_region", "revenue"]
    assert by_reg["rows"] == [["EU", 200.0], ["US", 300.0]]
    # Total preservation invariant: group sum == ungrouped revenue (500.0)
    total = svc.query_metric("revenue", as_role="finance")["rows"][0][0]
    assert sum(r[1] for r in by_reg["rows"]) == total

    # Denied dimension (`email`) on joined entity is blocked!
    with pytest.raises(Refusal, match="not permitted"):
        svc.query_metric("revenue", dims=["Customer.email"], as_role="finance")

    # Restricted role (`eu_analyst` has row_filter `region = 'EU'` on `customers`) cannot join
    # `Customer.region` onto unrestricted `order_count` to leak non-EU order counts!
    with pytest.raises(Refusal, match="lacks unrestricted access"):
        svc.query_metric("order_count", dims=["Customer.region"], as_role="eu_analyst")

    # ONE-TO-MANY FAN-OUT TRAP: slicing `customer_count` (`customers`) by `Order.status` (`orders`)
    # would inflate customer counts (customer 1 has 2 orders!) -> MUST BE REFUSED!
    with pytest.raises(Refusal, match="fan-out would inflate totals"):
        svc.query_metric("customer_count", dims=["Order.status"], as_role="finance")


# --- ADR-035: SQLite FTS5 Search, Pagination & MCP Surface ---------------------------


def test_fts5_search_pagination_and_mcp_surface(env_v2):
    _, _, reg, wh = env_v2
    svc = Service(reg, wh)

    # Synonym match ("turnover" -> `revenue`) with punctuation / FTS boolean keywords in query
    hits = svc.search_metrics("What is our gross turnover or sales?", k=5)
    assert hits and hits[0]["name"] == "revenue"

    # Pagination
    page1 = svc.list_metrics(limit=2, offset=0)
    page2 = svc.list_metrics(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1
    assert {m["name"] for m in page1 + page2} == {"customer_count", "order_count", "revenue"}

    # MCP server still exposes zero raw-SQL tools
    mcp = build_server(svc)
    tool_names = set(mcp._tool_manager._tools.keys())
    assert {"search_metrics", "query_metrics", "query_metric", "list_metrics"} <= tool_names
    assert not any("sql" in t.lower() for t in tool_names)


# --- BigQuery Dialect AST Compatibility Across All New SQL Constructs ----------------


def test_bigquery_ast_rendering_for_time_grains_joins_and_ctes(env_v2):
    _, _, reg, wh = env_v2
    comp = SimpleCompiler(dialect="bigquery")
    rev = reg.get("metric", "revenue")
    ord_m = reg.get("metric", "order_count")
    aov = Metric(name="aov", metric_type="ratio", numerator="revenue", denominator="order_count")

    # 1. ISO week + range filter + hostile string filter -> BigQuery SQL
    c1 = comp.compile(
        rev,
        dims=["method"],
        filters={"method": {"in": ["card' OR 1=1--", "bank"]}},
        time_grain="week",
        time_range=["2024-01-01", "2024-03-01"],
        order_by="-revenue",
        limit=5,
    )
    bq1 = render_sql(c1.sql, c1.params, "my-proj", "jaffle_shop")
    assert "ISOWEEK" in bq1
    assert "`my-proj`.`jaffle_shop`.`payments`" in bq1
    assert "LIMIT 5" in bq1

    # 2. Multi-metric / ratio CTE with FULL OUTER JOIN IS NOT DISTINCT FROM -> BigQuery SQL
    c2 = comp.compile_multi(
        [rev, ord_m, aov],
        {"revenue": rev, "order_count": ord_m, "aov": aov},
        time_grain="month",
    )
    bq2 = render_sql(c2.sql, c2.params, "my-proj", "jaffle_shop")
    assert "IS NOT DISTINCT FROM" in bq2
    assert "NULLIF" in bq2
    assert "`my-proj`.`jaffle_shop`.`orders`" in bq2


def test_cli_metric_add_and_canary_dry_run(env_v2, tmp_path):
    root, db_path, reg, _ = env_v2
    cfg_file = root / "semlayer.yaml"
    cfg_file.write_text(f"db: {db_path.as_posix()}\nregistry: {(root / 'defs').as_posix()}\n")

    cli_main(
        [
            "--config",
            str(cfg_file),
            "--no-llm",
            "metric-add",
            "aov_cli",
            "--type",
            "ratio",
            "--numerator",
            "revenue",
            "--denominator",
            "order_count",
            "--synonyms",
            "basket_size,avg_ticket",
        ]
    )
    cli_main(["--config", str(cfg_file), "--no-llm", "certify", "aov_cli", "--owner", "alice"])
    cli_main(["--config", str(cfg_file), "--no-llm", "canary", "--dry-run"])
    assert reg.get("metric", "aov_cli").state is State.certified
