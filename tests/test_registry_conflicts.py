import pytest

from semlayer.conflicts import apply_pick, apply_split, detect
from semlayer.core import InvalidTransition, Metric, State
from semlayer.registry import Registry


def metric(name, filters=(), usage=1, concept="rev", table="p"):
    m = Metric(name=name, concept=concept, table=table, agg="sum", column="a", filters=list(filters))
    m.evidence.usage = usage
    return m


def test_certify_requires_owner_and_audits(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("a"))
    with pytest.raises(InvalidTransition):
        reg.certify("metric", "a")
    n = reg.certify("metric", "a", owner="o", edits={"description": "changed"})
    assert n.state is State.certified and n.version == 1
    rec = reg.audit()[-1]
    assert rec["action"] == "certify" and rec["edited"] and 0 < rec["edit_ratio"] < 1


def test_certified_name_gets_pending_revision_with_diff(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("a"))
    reg.certify("metric", "a", owner="o")
    assert reg.put_draft(metric("a", filters=["x = 1"])) == "pending"
    assert reg.get("metric", "a").filters == []  # served definition unchanged
    assert "+" in reg.diff("metric", "a") and reg.queue("metric")[0].name == "a"
    n = reg.certify("metric", "a")  # owner carried over
    assert n.version == 2 and n.owner == "o" and n.filters == ["x = 1"]
    assert reg.list("metric", pending=True) == []


def test_reject_and_deprecate(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("a"))
    reg.reject("metric", "a")
    assert reg.get("metric", "a").state is State.deprecated
    reg.put_draft(metric("b"))
    reg.certify("metric", "b", owner="o")
    with pytest.raises(ValueError):
        reg.reject("metric", "b")
    reg.deprecate("metric", "b")


def test_index_is_rebuildable(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("a"))
    reg.db_path.unlink()
    assert [n.name for n in Registry(tmp_path).list("metric")] == ["a"]


def test_conflict_variants_split_and_pick(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("rev_v1", ["s = 1"], usage=5))
    reg.put_draft(metric("rev_v2", [], usage=2))
    (c,) = detect(reg.list("metric"))
    assert c.kind == "variant" and c.options[0]["action"] == "split" and c.usage["rev_v1"] == 5
    apply_split(reg, c, {"rev_v1": "rev_success", "rev_v2": "rev_all"})
    assert detect(reg.list("metric")) == []  # distinct concepts now

    reg2 = Registry(tmp_path / "2")
    reg2.put_draft(metric("rev_v1", ["s = 1"], usage=5))
    reg2.put_draft(metric("rev_v2", [], usage=2))
    (c,) = detect(reg2.list("metric"))
    apply_pick(reg2, c, "rev_v1")
    assert reg2.get("metric", "rev").filters == ["s = 1"]
    assert reg2.get("metric", "rev_v2").state is State.deprecated


def test_duplicate_logic_under_different_names(tmp_path):
    reg = Registry(tmp_path)
    reg.put_draft(metric("a", concept="a", usage=3))
    reg.put_draft(metric("b", concept="b", usage=1))
    (c,) = detect(reg.list("metric"))
    assert c.kind == "duplicate" and c.options == [{"action": "pick", "name": "a"}]
