"""Lightweight entity graph: typed entities/edges, drafted from schema, traversed with joins."""
import re
from collections import deque

from semlayer.core import Edge, Entity, Evidence
from semlayer.core.models import ident

REVERSE_CARDINALITY = {
    "one_to_many": "many_to_one",
    "many_to_one": "one_to_many",
    "one_to_one": "one_to_one",
    "many_to_many": "many_to_many",
}
SAFE_JOIN_CARDINALITIES = {"many_to_one", "one_to_one"}


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
                cardinality="one_to_many",
                evidence=Evidence(source="schema", lineage=[f"{ref}.{src.key}", f"{t}.{col}"],
                                  explanation=f"{t}.{col} references {ref}.{src.key}")))
    return nodes


def _directed_steps(entities: dict[str, Entity], edges: list[Edge]) -> dict[str, list[tuple]]:
    """Adjacency list of (next_entity, left_key, right_key, cardinality, edge_name)."""
    adj: dict[str, list[tuple]] = {name: [] for name in entities}
    for e in edges:
        if e.src not in entities or e.dst not in entities:
            continue
        adj[e.src].append((e.dst, e.src_key, e.dst_key, e.cardinality, e.name))
        rev_card = REVERSE_CARDINALITY[e.cardinality]
        adj[e.dst].append((e.src, e.dst_key, e.src_key, rev_card, f"~{e.name}"))
    return adj


def resolve_join_path(
    start_table: str,
    target_entity: str,
    entities: list[Entity],
    edges: list[Edge],
) -> list[tuple[str, str, str, str, str]]:
    """Find unique shortest many-to-one/one-to-one join path from `start_table` to `target_entity`.

    Returns steps of `(left_table, left_key, right_table, right_key, right_entity_name)`.
    Raises ValueError with an explicit fan-out or ambiguity message when unsafe or missing.
    """
    ents = {e.name: e for e in entities}
    src_ent = next((e for e in entities if e.table == start_table), None)
    if src_ent is None:
        raise ValueError(f"table '{start_table}' has no certified entity for joining")
    if target_entity not in ents:
        raise ValueError(f"unknown certified entity '{target_entity}'")
    if src_ent.name == target_entity:
        return []

    adj = _directed_steps(ents, edges)

    # Shortest-path BFS over safe (many_to_one / one_to_one) edges, tracking ambiguity.
    q: deque[tuple[str, list[tuple[str, str, str, str, str]]]] = deque([(src_ent.name, [])])
    best_depth: dict[str, int] = {src_ent.name: 0}
    found: list[list[tuple[str, str, str, str, str]]] = []
    target_len: int | None = None

    while q:
        cur, path = q.popleft()
        if target_len is not None and len(path) >= target_len:
            continue
        for nxt, l_key, r_key, card, _ename in adj.get(cur, ()):
            if card not in SAFE_JOIN_CARDINALITIES:
                continue
            if any(step[4] == nxt for step in path) or nxt == src_ent.name:
                continue
            step = (ents[cur].table, l_key, ents[nxt].table, r_key, nxt)
            new_path = [*path, step]
            if nxt == target_entity:
                target_len = len(new_path)
                found.append(new_path)
                continue
            prev = best_depth.get(nxt)
            if prev is None or len(new_path) <= prev:
                best_depth[nxt] = len(new_path)
                q.append((nxt, new_path))

    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        # Deduplicate identical table/key join chains before declaring ambiguity
        uniq = {tuple(p): p for p in found}
        if len(uniq) == 1:
            return next(iter(uniq.values()))
        raise ValueError(
            f"ambiguous join path from {src_ent.name} to {target_entity}: "
            f"{len(uniq)} equal-length many-to-one paths exist"
        )

    # Check whether an unsafe (one_to_many / many_to_many) path exists to explain fan-out refusal.
    seen = {src_ent.name}
    uq = deque([src_ent.name])
    while uq:
        cur = uq.popleft()
        for nxt, _lk, _rk, _card, _en in adj.get(cur, ()):
            if nxt == target_entity:
                raise ValueError(
                    f"cannot join {target_entity} onto {src_ent.name}: path is one-to-many or "
                    f"many-to-many (fan-out would inflate totals); use traverse_graph instead"
                )
            if nxt not in seen:
                seen.add(nxt)
                uq.append(nxt)

    raise ValueError(f"no certified relationship path from {src_ent.name} to {target_entity}")


def joinable_entities(start_table: str, entities: list[Entity], edges: list[Edge]) -> list[Entity]:
    """Entities reachable from `start_table` via a unique safe many-to-one/one-to-one path."""
    out: list[Entity] = []
    for e in entities:
        if e.table == start_table:
            continue
        try:
            if resolve_join_path(start_table, e.name, entities, edges):
                out.append(e)
        except ValueError:
            continue
    return out


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

    def traverse(self, wh, start: str, path: list[str], filters: dict | None = None) -> list:
        from semlayer.warehouse import ensure_warehouse

        sql, params = self.traverse_sql(start, path, filters)
        return [r[0] for r in ensure_warehouse(wh).query(sql, params).rows]
