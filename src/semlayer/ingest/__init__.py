"""Connectors: schema snapshot, local/BigQuery query logs, policy tags, optional dbt docs."""
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


def read_bq_jobs(wh, limit: int = 500, days: int = 30) -> list[Query]:
    """Read historical SELECT statements from BigQuery INFORMATION_SCHEMA.JOBS_BY_PROJECT."""
    from semlayer.warehouse import ensure_warehouse

    w = ensure_warehouse(wh)
    fn = getattr(w, "recent_queries", None)
    if fn is None:
        raise ValueError(f"warehouse {w.dialect!r} does not support BigQuery job-history ingestion")
    return [Query(user, sql) for user, sql in fn(limit=limit, days=days)]


def schema_snapshot(wh) -> dict[str, dict[str, str]]:
    from semlayer.warehouse import ensure_warehouse

    return ensure_warehouse(wh).schema()


def policy_tags_snapshot(wh) -> dict[str, list[str]]:
    """Return `{table: [column, ...]}` with Dataplex / BigQuery policy tags when supported."""
    from semlayer.warehouse import ensure_warehouse

    w = ensure_warehouse(wh)
    fn = getattr(w, "policy_tagged_columns", None)
    return fn() if callable(fn) else {}


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
