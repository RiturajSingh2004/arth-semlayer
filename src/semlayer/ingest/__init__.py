"""Connectors: schema snapshot, query log, optional dbt manifest docs."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Query:
    user: str
    sql: str


def read_query_log(path: str | Path) -> list[Query]:
    """JSONL ({"user","sql"} per line) or a plain .sql file split on semicolons."""
    p = Path(path)
    if p.suffix == ".jsonl":
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        return [Query(r.get("user", "unknown"), r["sql"].strip().rstrip(";")) for r in rows]
    return [Query("unknown", s.strip()) for s in p.read_text().split(";") if s.strip()]


def schema_snapshot(wh) -> dict[str, dict[str, str]]:
    from semlayer.warehouse import ensure_warehouse

    return ensure_warehouse(wh).schema()


def dbt_docs(manifest: str | Path) -> dict[str, str]:
    """Map `table` and `table.column` to dbt descriptions."""
    nodes = json.loads(Path(manifest).read_text()).get("nodes", {})
    docs: dict[str, str] = {}
    for n in nodes.values():
        if n.get("resource_type") != "model":
            continue
        if n.get("description"):
            docs[n["name"]] = n["description"]
        for c, meta in n.get("columns", {}).items():
            if meta.get("description"):
                docs[f"{n['name']}.{c}"] = meta["description"]
    return docs
