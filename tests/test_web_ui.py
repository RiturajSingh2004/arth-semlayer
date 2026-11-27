import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
import yaml
from fastapi.testclient import TestClient

from semlayer.core import Config, Metric, State
from semlayer.registry import Registry
from semlayer.serve.service import Service
from semlayer.serve.web import create_app
from semlayer.warehouse import make_warehouse


@pytest.fixture
def web_env(tmp_path):
    cfg = Config(
        db=str(tmp_path / "missing.duckdb"),  # intentionally does not exist yet!
        registry=str(tmp_path / "defs"),
        query_log=str(tmp_path / "log.jsonl"),
        llm_provider="none",
    )
    reg = Registry(cfg.registry)
    wh = make_warehouse(cfg, read_only=True)
    svc = Service(reg, wh)
    return cfg, reg, svc, TestClient(create_app(reg, svc), follow_redirects=False)


def test_web_starts_without_duckdb_file_and_renders_empty_state(web_env):
    _, _, _, client = web_env
    res = client.get("/")
    assert res.status_code == 200
    assert "No pending drafts or revisions in queue." in res.text
    assert "None certified yet" in res.text
    assert client.get("/api/queue").json() == []
    assert client.get("/api/metrics").json() == []


def test_web_displays_descriptions_filters_diffs_conflicts_and_escapes_xss(web_env):
    _, reg, _, client = web_env
    # Seed two variants (conflict) + XSS attempt in description/explanation
    m1 = Metric(
        name="rev_v1", concept="rev", table="payments", agg="sum", column="amount",
        filters=["status = 'success'"],
        description='<script>alert("xss")</script> Safe revenue',
        evidence={"usage": 10, "suggested_owner": "alice", "queries": ["SELECT 1 < 2"],
                  "lineage": ["payments.amount"], "explanation": "<b>bold</b>"},
    )
    m2 = Metric(name="rev_v2", concept="rev", table="payments", agg="sum", column="amount",
                evidence={"usage": 2, "suggested_owner": "bob"})
    reg.put_draft(m1)
    reg.put_draft(m2)

    html = client.get("/").text
    # Ranked by usage: rev_v1 appears before rev_v2
    assert html.index("rev_v1") < html.index("rev_v2")
    # Filter shown alongside description
    assert "WHERE status = &#x27;success&#x27;" in html
    # XSS payload is escaped, never rendered as raw <script>
    assert '<script>alert("xss")</script>' not in html
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html
    # Conflict section populated
    assert "rev_v1, rev_v2" in html

    # Certify rev_v1 via non-HTMX form POST -> 303 redirect to /
    r_redir = client.post("/certify/metric/rev_v1", data={"owner": "alice"})
    assert r_redir.status_code == 303 and r_redir.headers["location"] == "/"
    assert reg.get("metric", "rev_v1").state is State.certified

    # Open a pending revision on rev_v1 and verify unified diff renders in UI
    rev_update = m1.model_copy(update={"filters": ["status = 'paid'"]})
    assert reg.put_draft(rev_update) == "pending"
    html_rev = client.get("/").text
    assert "(revision)" in html_rev
    assert "--- certified" in html_rev and "+++ pending" in html_rev

    # Reject the pending revision via HTMX -> live v1 stays certified
    r_rej = client.post("/reject/metric/rev_v1", headers={"HX-Request": "true"})
    assert r_rej.status_code == 200 and "rejected rev_v1" in r_rej.text
    assert reg.get("metric", "rev_v1").state is State.certified
    assert reg.get("metric", "rev_v1", pending=True) is None


def test_web_error_paths_never_500_on_missing_invalid_or_double_actions(web_env):
    _, reg, _, client = web_env
    reg.put_draft(Metric(name="m1", table="orders", agg="count", column="*"))

    # Unknown kind -> 400 (never 500 KeyError)
    assert client.post("/certify/bogus/m1", data={"owner": "a"}).status_code == 400
    assert client.post("/reject/bogus/m1").status_code == 400
    assert client.post("/api/certify/bogus/m1").status_code == 400

    # Nonexistent item with and without HX-Request -> 404 (never 500 AttributeError)
    assert client.post("/certify/metric/ghost", data={"owner": "a"},
                       headers={"HX-Request": "true"}).status_code == 404
    assert client.post("/certify/metric/ghost", data={"owner": "a"}).status_code == 404
    assert client.post("/reject/metric/ghost", headers={"HX-Request": "true"}).status_code == 404

    # Double reject & rejecting an already-certified metric -> 400 (never 500)
    assert client.post("/reject/metric/m1", headers={"HX-Request": "true"}).status_code == 200
    assert client.post("/reject/metric/m1", headers={"HX-Request": "true"}).status_code == 400

    reg.put_draft(Metric(name="m2", table="orders", agg="count", column="*"))
    assert client.post("/api/certify/metric/m2", params={"owner": "sec"}).status_code == 200
    assert client.post("/reject/metric/m2", headers={"HX-Request": "true"}).status_code == 400


def test_live_cli_serve_web_over_http(tmp_path):
    """Start `semlayer serve --web` as a real subprocess and exercise HTTP endpoints."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    cfg = Config(
        db=str(tmp_path / "no_db_yet.duckdb"),
        registry=str(tmp_path / "defs"),
        query_log=str(tmp_path / "log.jsonl"),
        llm_provider="none",
    )
    cfg_path = tmp_path / "semlayer.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump()))
    reg = Registry(cfg.registry)
    reg.put_draft(Metric(name="live_orders", table="orders", agg="count", column="*",
                         description="Total order rows", evidence={"usage": 5, "suggested_owner": "ops"}))

    proc = subprocess.Popen(
        [sys.executable, "-m", "semlayer.cli", "--config", str(cfg_path),
         "serve", "--web", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/queue", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("semlayer serve --web did not become ready in 10s")

        with urllib.request.urlopen(f"{base}/") as r:
            page = r.read().decode("utf-8")
        assert "ARTH: SemLayer · Review queue" in page and "live_orders" in page

        form = urllib.parse.urlencode({"owner": "ops"}).encode()
        req = urllib.request.Request(f"{base}/certify/metric/live_orders", data=form,
                                     headers={"HX-Request": "true"})
        with urllib.request.urlopen(req) as r:
            frag = r.read().decode("utf-8")
        assert "certified live_orders" in frag

        with urllib.request.urlopen(f"{base}/api/metrics") as r:
            assert '"live_orders"' in r.read().decode("utf-8")

        bad_req = urllib.request.Request(f"{base}/reject/metric/live_orders", data=b"",
                                         headers={"HX-Request": "true"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad_req)
        assert exc.value.code == 400
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_web_conflict_resolution_and_governed_query_runner(env):
    c = TestClient(create_app(env.reg, env.svc), follow_redirects=False)
    # Seed a variant conflict and resolve it from the UI via /conflicts/split and /conflicts/pick
    env.reg.put_draft(Metric(name="gmv_v1", concept="gmv", table="payments", agg="sum",
                             column="amount", filters=["status = 'success'"], evidence={"usage": 5}))
    env.reg.put_draft(Metric(name="gmv_v2", concept="gmv", table="payments", agg="sum",
                             column="amount", evidence={"usage": 1}))
    r_split = c.post("/conflicts/split", data={
        "concept": "gmv", "rename_gmv_v1": "gmv_net", "rename_gmv_v2": "gmv_gross"
    })
    assert r_split.status_code == 303
    assert env.reg.get("metric", "gmv_net") is not None and env.reg.get("metric", "gmv_gross") is not None

    # Duplicate conflict resolved via /conflicts/pick
    env.reg.put_draft(Metric(name="dup_a", concept="dup_a", table="orders", agg="max", column="id",
                             evidence={"usage": 4}))
    env.reg.put_draft(Metric(name="dup_b", concept="dup_b", table="orders", agg="max", column="id",
                             evidence={"usage": 1}))
    r_pick = c.post("/conflicts/pick", data={"winner": "dup_a"})
    assert r_pick.status_code == 303
    assert env.reg.get("metric", "dup_b").state is State.deprecated

    # Interactive governed query & graph runner respects policy and returns compiled SQL + rows
    ok_q = c.post("/ui/query", data={"metric": "revenue", "as_role": "finance", "filters": "method=card"})
    assert ok_q.status_code == 200 and "v1" in ok_q.text and "SELECT SUM(amount)" in ok_q.text
    denied_q = c.post("/ui/query", data={"metric": "revenue", "as_role": "eu_analyst"})
    assert "REFUSED" in denied_q.text and "access denied" in denied_q.text

    ok_g = c.post("/ui/traverse", data={"start": "Customer", "path": "HAS_ORDER",
                                        "as_role": "finance", "filters": "Order.status=returned"})
    assert ok_g.status_code == 200 and "26 keys" in ok_g.text
    denied_g = c.post("/ui/traverse", data={"start": "Customer", "path": "HAS_ORDER", "as_role": "eu_analyst"})
    assert "REFUSED" in denied_g.text and "unrestricted" in denied_g.text

