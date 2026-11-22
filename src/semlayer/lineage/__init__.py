"""Column-level lineage via sqlglot: which table.column each query reads."""
import sqlglot
from sqlglot import exp


def column_lineage(sql: str, schema: dict[str, dict[str, str]] | None = None,
                   dialect: str = "duckdb") -> list[str]:
    tree = sqlglot.parse_one(sql, dialect=dialect)
    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    tables = sorted(set(aliases.values()))
    out = set()
    for col in tree.find_all(exp.Column):
        if col.table:
            table = aliases.get(col.table, col.table)
        elif len(tables) == 1:
            table = tables[0]
        else:
            owners = [t for t in tables if schema and col.name in schema.get(t, {})]
            table = owners[0] if len(owners) == 1 else "?"
        out.add(f"{table}.{col.name}")
    return sorted(out)
