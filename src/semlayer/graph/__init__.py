"""Lightweight entity graph: typed entities/edges, drafted from schema, traversed with joins."""
import re

from semlayer.core import Edge, Entity, Evidence
from semlayer.core.models import ident


def _entity_name(table: str) -> str:
    base = re.sub(r"ies$", "y", table) if table.endswith("ies") else table.rstrip("s")
    return base.title().replace("_", "")


def _key(cols: dict) -> str:
    return "id" if "id" in cols else next((c for c in cols if c.endswith("_id")), next(iter(cols)))


def draft_graph(schema: dict[str, dict[str, str]]) -> list:
    ents = {t: Entity(name=_entity_name(t), table=t, key=_key(c),
                      evidence=Evidence(source="schema", explanation=f"table {t}"))
            for t, c in schema.items()}
    nodes: list = list(ents.values())
    for t, cols in schema.items():
        for col in cols:
            if not col.endswith("_id") or col == ents[t].key:
                continue
            x = col[:-3]
            ref = next((r for r in (x, x + "s", x[:-1] + "ies" if x.endswith("y") else "") if r in ents), None)
            if not ref or ref == t:
                continue
            src, dst = ents[ref], ents[t]
            nodes.append(Edge(
                name=f"{src.name}_has_{dst.name}_via_{col}".lower(), src=src.name,
                rel=f"HAS_{dst.name.upper()}", dst=dst.name, src_key=src.key, dst_key=col,
                evidence=Evidence(source="schema", lineage=[f"{ref}.{src.key}", f"{t}.{col}"],
                                  explanation=f"{t}.{col} references {ref}.{src.key}")))
    return nodes


class Graph:
    def __init__(self, entities: list[Entity], edges: list[Edge]):
        self.entities = {e.name: e for e in entities}
        self.edges = edges

    def traverse_sql(self, start: str, path: list[str], filters: dict | None = None):
        """Keys of `start` entities reachable along `path` (relationship names), filtered by
        equality on entities along the path: filters={"Order": {"status": "completed"}}."""
        if start not in self.entities:
            raise ValueError(f"unknown entity {start}")
        chain, cur = [self.entities[start]], start
        joins = []
        for rel in path:
            e = next((e for e in self.edges if e.src == cur and e.rel == rel), None)
            if e is None:
                raise ValueError(f"no relationship {rel} from {cur}")
            nxt = self.entities[e.dst]
            joins.append(
                f"JOIN {ident(nxt.table)} e{len(chain)} ON e{len(chain) - 1}.{ident(e.src_key)}"
                f" = e{len(chain)}.{ident(e.dst_key)}"
            )
            chain.append(nxt)
            cur = e.dst
        where, params = [], []
        for ent, conds in (filters or {}).items():
            idx = [i for i, c in enumerate(chain) if c.name == ent]
            if not idx:
                raise ValueError(f"{ent} is not on the path")
            for col, val in conds.items():
                where.append(f"e{idx[0]}.{ident(col)} = ?")
                params.append(val)
        s = chain[0]
        sql = f"SELECT DISTINCT e0.{ident(s.key)} FROM {ident(s.table)} e0 " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return sql + " ORDER BY 1 LIMIT 10000", params

    def traverse(self, con, start: str, path: list[str], filters: dict | None = None) -> list:
        sql, params = self.traverse_sql(start, path, filters)
        return [r[0] for r in con.execute(sql, params).fetchall()]
