"""Conflict detection: same concept with different logic, or different names with identical logic."""
from collections import defaultdict
from dataclasses import dataclass, field

from semlayer.core import Metric, State
from semlayer.registry import Registry


@dataclass
class Conflict:
    concept: str
    kind: str  # variant | duplicate
    variants: list[str]
    diffs: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)


def _diff(a: Metric, b: Metric) -> str:
    parts = []
    if a.table != b.table:
        parts.append(f"table {a.table} -> {b.table}")
    if (a.agg, a.column) != (b.agg, b.column):
        parts.append(f"expr {a.expression} -> {b.expression}")
    fa, fb = set(a.filters), set(b.filters)
    parts += [f"- {x}" for x in sorted(fa - fb)] + [f"+ {x}" for x in sorted(fb - fa)]
    return f"{b.name} vs {a.name}: " + ("; ".join(parts) or "identical logic")


def detect(metrics: list[Metric]) -> list[Conflict]:
    live = [m for m in metrics if m.state is not State.deprecated]
    out: list[Conflict] = []
    by_concept = defaultdict(list)
    for m in live:
        by_concept[m.concept or m.name].append(m)
    for concept, ms in sorted(by_concept.items()):
        if len({m.logic for m in ms}) < 2:
            continue
        ms.sort(key=lambda m: (-m.evidence.usage, m.name))
        tables = [m.table for m in ms]
        split = {m.name: (f"{concept}_{m.table}" if tables.count(m.table) == 1 else m.name)
                 for m in ms}
        out.append(Conflict(
            concept, "variant", [m.name for m in ms], [_diff(ms[0], m) for m in ms[1:]],
            {m.name: m.evidence.usage for m in ms},
            [{"action": "split", "names": split},  # preferred: name each variant explicitly
             {"action": "pick", "name": ms[0].name}],  # fallback: keep the most used
        ))
    by_logic = defaultdict(list)
    for m in live:
        by_logic[m.logic].append(m)
    for ms in by_logic.values():
        if len({m.concept or m.name for m in ms}) > 1:
            ms.sort(key=lambda m: (-m.evidence.usage, m.name))
            out.append(Conflict(
                "/".join(m.name for m in ms), "duplicate", [m.name for m in ms],
                [_diff(ms[0], m) for m in ms[1:]], {m.name: m.evidence.usage for m in ms},
                [{"action": "pick", "name": ms[0].name}],
            ))
    return out


def apply_split(reg: Registry, c: Conflict, names: dict[str, str] | None = None, by="human"):
    names = names or c.options[0]["names"]
    for old, new in names.items():
        if old != new:
            reg.rename("metric", old, new, by=by)
        reg.set_meta("metric", new, by=by, concept=new)  # now distinct concepts: conflict resolved


def apply_pick(reg: Registry, c: Conflict, keep: str, by="human"):
    for name in c.variants:
        if name == keep:
            continue
        m = reg.get("metric", name)
        if m.state is State.draft:
            reg.reject("metric", name, by=by)
        else:
            reg.deprecate("metric", name, by=by)
    target = c.concept if c.kind == "variant" else keep
    if keep != target and reg.get("metric", target) is None:
        reg.rename("metric", keep, target, by=by)
