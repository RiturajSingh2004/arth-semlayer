import pytest

from semlayer.core import InvalidTransition, Metric, Rule, State, transition


def m(**kw):
    return Metric(name="revenue", table="payments", agg="sum", column="amount", **kw)


def test_certify_needs_owner_and_bumps_version():
    x = m()
    with pytest.raises(InvalidTransition):
        transition(x, State.certified)
    x.owner = "a"
    transition(x, State.certified)
    assert (x.state, x.version) == (State.certified, 1)


def test_invalid_transitions():
    x = m(owner="a")
    transition(x, State.certified)
    with pytest.raises(InvalidTransition):
        transition(x, State.draft)
    transition(x, State.deprecated)
    with pytest.raises(InvalidTransition):
        transition(x, State.certified)


def test_identifiers_validated():
    with pytest.raises(ValueError):
        Metric(name="x", table="payments; drop", agg="sum", column="amount")
    with pytest.raises(ValueError):
        m(dimensions=["a b"])


def test_expression_and_logic():
    assert m().expression == "SUM(amount)"
    assert Metric(name="c", table="t", agg="count_distinct", column="id").expression == "COUNT(DISTINCT id)"
    assert m(filters=["b", "a"]).logic == m(filters=["a", "b"]).logic


@pytest.mark.parametrize("bad", ["1=1; DROP TABLE x", "a IN (SELECT 1)", "((("])
def test_rule_row_filter_must_be_plain_predicate(bad):
    with pytest.raises(ValueError):
        Rule(name="r", role="x", row_filter=bad)
