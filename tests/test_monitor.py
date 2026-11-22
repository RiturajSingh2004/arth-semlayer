from semlayer.core import Metric
from semlayer.ingest import Query
from semlayer.monitor import (
    affected,
    feedback_summary,
    pattern_drift,
    propose_revisions,
    record_value,
    schema_drift,
    value_anomaly,
)
from semlayer.registry import Registry

OLD = {"p": {"id": "INT", "amount": "DOUBLE", "status": "VARCHAR"}, "gone": {"x": "INT"}}
NEW = {"p": {"id": "INT", "amount": "VARCHAR", "extra": "INT"}, "fresh": {"y": "INT"}}


def test_schema_drift_kinds_and_severity():
    ch = {(c.kind, c.table, c.column): c for c in schema_drift(OLD, NEW)}
    assert ch[("column_dropped", "p", "status")].breaking
    assert ch[("type_changed", "p", "amount")].breaking
    assert ch[("table_dropped", "gone", "")].breaking
    assert not ch[("column_added", "p", "extra")].breaking
    assert not ch[("table_added", "fresh", "")].breaking


def mk(name="rev", **kw):
    return Metric(name=name, table="p", agg="sum", column="amount", filters=["status = 'ok'"],
                  dimensions=["id"], **kw)


def test_affected_uses_column_filter_and_dimension_refs():
    hit = affected(schema_drift(OLD, NEW), [mk(), Metric(name="n", table="p", agg="count", column="*")])
    assert set(hit) == {"rev"} and len(hit["rev"]) == 2  # amount type + status dropped


def test_propose_revisions_debounced(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(mk())
    reg.certify("metric", "rev", owner="o")
    ch = schema_drift(OLD, NEW)
    assert propose_revisions(reg, ch) == ["rev"]
    assert propose_revisions(reg, ch) == []  # debounce: one open revision per metric
    assert reg.get("metric", "rev").version == 1  # served version untouched
    assert "DRIFT" in reg.get("metric", "rev", pending=True).evidence.explanation


def test_pattern_drift_finds_only_new_logic(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(Metric(name="rev", table="p", agg="sum", column="a"))
    qs = [Query("u", "SELECT SUM(a) AS rev FROM p"), Query("u", "SELECT SUM(a) AS rev FROM p WHERE z = 1")]
    new = pattern_drift(reg, qs)
    assert len(new) == 1 and new[0].filters == ["z = 1"]


def test_value_anomaly_needs_history():
    assert not value_anomaly([1, 2], 100)
    assert value_anomaly([10, 11, 9, 10, 10, 11], 50)
    assert not value_anomaly([10, 11, 9, 10, 10, 11], 10.5)


def test_record_value(tmp_path):
    reg = Registry(tmp_path)
    flags = [record_value(reg, "m", v) for v in [10, 10, 11, 9, 10, 10, 90]]
    assert flags == [False] * 6 + [True]


def test_feedback_min_volume_and_flags():
    ev = [{"event": "query", "metric": "a"}] * 5 + [{"event": "error", "metric": "a"}] * 2
    ev += [{"event": "query", "metric": "b"}] * 2
    ev += [{"event": "gap", "note": " Churn? "}] * 3 + [{"event": "gap", "note": "once"}]
    s = feedback_summary(ev)
    assert s["demand"] == [("a", 5)] and s["flagged"] == ["a"]
    assert s["missing"] == [("churn", 3)] and s["ignored_low_volume"] == 2
    
def test_gap_notes_with_explanations_are_clustered():
    notes = ["What is our churn rate?",
             "What is our churn rate? No certified metric defines churn over a period.",
             "what is our churn rate",
             "Revenue from customers who have a returned order",
             "Revenue from customers who have at least one returned order. Graph failed.",
             "What is our NPS?"]
    s = feedback_summary([{"event": "gap", "note": n} for n in notes], min_volume=2)
    assert dict(s["missing"]) == {"what is our churn rate": 3,
                                  "revenue from customers who have a returned order": 2}