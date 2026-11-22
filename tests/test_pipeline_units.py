import json

from semlayer.draft import draft_metrics
from semlayer.ingest import Query, dbt_docs, read_query_log
from semlayer.lineage import column_lineage

SCHEMA = {"orders": {"id": "INT", "customer_id": "INT", "status": "VARCHAR"},
          "customers": {"id": "INT", "region": "VARCHAR"}}


def q(sql, user="a"):
    return Query(user, sql)


def test_lineage_resolves_aliases_and_unqualified():
    sql = "SELECT c.region, COUNT(o.id) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE status = 'x'"
    out = column_lineage(sql, SCHEMA)
    assert "customers.region" in out and "orders.id" in out and "orders.status" in out


def test_draft_variants_filters_dims_owner():
    qs = [q("SELECT SUM(amount) AS revenue FROM p WHERE b = 1 AND a = 2", "x")] * 3 + [
        q("SELECT SUM(amount) AS revenue FROM p", "y"),
        q("SELECT m, SUM(amount) AS revenue FROM p WHERE a = 2 AND b = 1 GROUP BY m", "x")]
    ms, skipped = draft_metrics(qs)
    assert skipped == 0
    by = {m.name: m for m in ms}
    assert set(by) == {"revenue_v1", "revenue_v2"}
    assert by["revenue_v1"].filters == ["a = 2", "b = 1"]  # normalised + sorted
    assert by["revenue_v1"].dimensions == ["m"] and by["revenue_v1"].evidence.usage == 4
    assert by["revenue_v1"].evidence.suggested_owner == "x"
    assert by["revenue_v2"].filters == []


def test_draft_skips_joins_junk_and_unsupported():
    ms, skipped = draft_metrics([
        q("SELECT SUM(a.x) AS s FROM a JOIN b ON a.id=b.id"), q("not sql at all"),
        q("SELECT SUM(a*b) AS s FROM t"), q("SELECT x FROM t")])
    assert ms == [] and skipped == 4


def test_draft_default_names_and_count_forms():
    ms, _ = draft_metrics([q("SELECT COUNT(*) FROM t"), q("SELECT COUNT(DISTINCT u) FROM t"),
                           q("SELECT COUNT(u) FROM t")])
    assert {m.name for m in ms} == {"count_all", "count_distinct_u", "count_u"}


class FakeLLM:
    def complete(self, prompt):
        return "LLM TEXT"


def test_llm_wording_used_when_available():
    ms, _ = draft_metrics([q("SELECT SUM(a) AS s FROM t")], llm=FakeLLM())
    assert ms[0].description == "LLM TEXT" and ms[0].evidence.explanation == "LLM TEXT"


def test_ingest_readers(tmp_path):
    (tmp_path / "l.sql").write_text("select 1; select 2;")
    assert len(read_query_log(tmp_path / "l.sql")) == 2
    (tmp_path / "l.jsonl").write_text(json.dumps({"user": "u", "sql": "select 1;"}) + "\n")
    assert read_query_log(tmp_path / "l.jsonl")[0].user == "u"
    man = {"nodes": {"m": {"resource_type": "model", "name": "orders", "description": "d",
                           "columns": {"id": {"description": "pk"}}}}}
    (tmp_path / "m.json").write_text(json.dumps(man))
    assert dbt_docs(tmp_path / "m.json") == {"orders": "d", "orders.id": "pk"}

def test_unreachable_llm_falls_back_to_heuristics():
    class Down:
        calls = 0

        def complete(self, prompt):
            Down.calls += 1
            raise OSError("connection refused")
    ms, _ = draft_metrics([q("SELECT SUM(a) AS s FROM t WHERE x = 1"), q("SELECT COUNT(*) AS c FROM t")],
                          llm=Down())
    assert Down.calls == 1  # no further attempts after the first failure
    assert all("on t" in m.description for m in ms) and len(ms) == 2
