"""One-shot demo: seed data, run the loop, and auto-certify. DEMO ONLY: real use needs humans."""
import importlib.util
import shutil
from pathlib import Path

from semlayer.cli import main
from semlayer.conflicts import apply_pick, apply_split, detect
from semlayer.core import Config, Rule, State
from semlayer.registry import Registry
from semlayer.registry.registry import DIRS

BY = "demo"
MARKER = "demo.marker"


def _seed(cfg: Config):
    p = Path(cfg.db).parent / "seed.py"
    spec = importlib.util.spec_from_file_location("seed", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reset_registry(root: Path, force: bool) -> Registry:
    """Start the demo from a clean registry, but never silently delete someone's real work."""
    has_data = any(list((root / d).glob("*.yaml")) for d in DIRS.values()) or (root / "_pending").exists()
    if has_data and not force and not (root / "_state" / MARKER).exists():
        raise SystemExit(f"{root} already holds definitions that demo did not create. "
                         "Use another registry, or pass --force to wipe it.")
    for sub in [*DIRS.values(), "_pending", "_state"]:  # keep .gitkeep and other files
        shutil.rmtree(root / sub, ignore_errors=True)
    reg = Registry(root)
    (root / "_state" / MARKER).write_text("created by semlayer demo\n")
    return reg


def run(prefix: list[str], cfg: Config, reg: Registry, force: bool = False) -> None:
    print("DEMO: auto-certifying everything. In real use, humans review each draft.\n")
    reg = _reset_registry(Path(cfg.registry), force)  # check first, before touching any data
    seed = _seed(cfg)
    seed.build_db(cfg.db)
    seed.write_log(cfg.query_log)

    main([*prefix, "ingest"])
    main([*prefix, "draft", "--roles", "finance,eu_analyst"])

    conflicts = {c.concept: c for c in detect(reg.list("metric"))}
    print("\nconflicts found:", {k: c.variants for k, c in conflicts.items()})
    c = conflicts["revenue"]
    apply_pick(reg, c, c.options[1]["name"], by=BY)  # keep the most-used revenue definition
    reg.reject("metric", "sum_amount", by=BY)  # same logic as the dropped variant
    apply_split(reg, conflicts["active_customers"], {  # three real meanings: name them
        "active_customers_v1": "active_customers_30d",
        "active_customers_v2": "active_customers_completed",
        "active_customers_v3": "active_customers_paid"}, by=BY)

    links = {"revenue": {"entity": "Customer", "entity_key": "customer_id"},
             "order_count": {"entity": "Order", "entity_key": "id"}}
    for m in reg.queue("metric"):
        reg.certify("metric", m.name, owner=m.evidence.suggested_owner or "data-team", by=BY,
                    edits=links.get(m.name))
    for kind in ("entity", "edge"):
        for n in reg.queue(kind):
            reg.certify(kind, n.name, owner="data-team", by=BY)

    rules = [Rule(name="finance_all", role="finance"),
             Rule(name="eu_analyst_customers", role="eu_analyst", metric="customer_count",
                  row_filter="region = 'EU'"),
             Rule(name="eu_analyst_orders", role="eu_analyst", metric="order_count")]
    for r in rules:
        reg.put_draft(r)
    for r in reg.queue("rule"):
        reg.certify("rule", r.name, owner="security-team", by=BY)

    print("\ncertified:")
    for kind in ("metric", "entity", "edge", "rule"):
        print(f"  {kind}: {[n.name for n in reg.list(kind, State.certified)]}")
    print("\nnext: semlayer eval --oracle   |   semlayer serve   |   semlayer serve --web")
