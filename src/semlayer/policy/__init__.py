"""Access policy: default deny, certified rules only, deny beats allow."""
import re
from dataclasses import dataclass, field

from semlayer.core import Rule, State

SENSITIVE = re.compile(r"email|ssn|phone|salary|password|dob|birth|address|passport|card_?num", re.IGNORECASE)


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    row_filter: str | None = None
    deny_dims: set[str] = field(default_factory=set)


def evaluate(rules: list[Rule], role: str, metric: str) -> Decision:
    hit = [r for r in rules if r.state is State.certified and r.role == role
           and r.metric in ("*", metric)]
    deny_dims = {d for r in hit for d in r.deny_dims}
    if any(r.effect == "deny" and not r.deny_dims for r in hit):
        return Decision(False, "denied by rule", deny_dims=deny_dims)
    allows = [r for r in hit if r.effect == "allow"]
    if not allows:
        return Decision(False, f"no allow rule for role '{role}'", deny_dims=deny_dims)
    if any(r.row_filter is None for r in allows):
        return Decision(True, row_filter=None, deny_dims=deny_dims)
    rf = " OR ".join(sorted({f"({r.row_filter})" for r in allows}))
    return Decision(True, row_filter=rf, deny_dims=deny_dims)


def unrestricted(rules: list[Rule], role: str) -> bool:
    """True if the role has an allow-all (metric '*') rule with no row filter and no deny rule."""
    live = [r for r in rules if r.state is State.certified and r.role == role]
    if any(r.effect == "deny" and not r.deny_dims for r in live):
        return False
    return any(r.effect == "allow" and r.metric == "*" and r.row_filter is None for r in live)


def sensitive_columns(schema: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    out = {t: [c for c in cols if SENSITIVE.search(c)] for t, cols in schema.items()}
    return {t: c for t, c in out.items() if c}


def suggest_rules(schema: dict, roles: list[str]) -> list[Rule]:
    """Draft dimension restrictions for sensitive columns. Humans must certify; grants are never drafted."""
    cols = sorted({c for v in sensitive_columns(schema).values() for c in v})
    if not cols:
        return []
    return [
        Rule(name=f"{r}_restrict_sensitive", role=r, effect="deny", deny_dims=cols,
             description="Suggested: sensitive columns are not usable as dimensions.")
        for r in roles
    ]


def replay(rules: list[Rule], history: list[dict]) -> list[dict]:
    """Re-run past requests ({role, metric, dims}) against rules to test them before certifying."""
    out = []
    for h in history:
        d = evaluate(rules, h["role"], h["metric"])
        blocked = sorted(set(h.get("dims", [])) & d.deny_dims)
        out.append({**h, "allowed": d.allowed and not blocked, "blocked_dims": blocked,
                    "reason": d.reason, "row_filter": d.row_filter})
    return out
