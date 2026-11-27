"""YAML-on-disk registry (source of truth, Git-friendly) + rebuildable SQLite/FTS5 index."""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from semlayer.core import KINDS, InvalidTransition, Metric, Node, State, emit_cloud_log, transition

DIRS = {"metric": "metrics", "entity": "entities", "edge": "edges", "rule": "rules"}


def dump(node: Node) -> str:
    return yaml.safe_dump(node.model_dump(mode="json"), sort_keys=False)


class Registry:
    def __init__(self, root: str | Path = "definitions", commit: bool = False):
        self.root, self.commit = Path(root), commit
        self.state_dir = self.root / "_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.reindex()

    # --- paths / io -------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.state_dir / "index.db"

    def path(self, kind: str, name: str, pending: bool = False) -> Path:
        base = self.root / "_pending" if pending else self.root
        return base / DIRS[kind] / f"{name}.yaml"

    def _read(self, p: Path, kind: str) -> Node:
        return KINDS[kind].model_validate(yaml.safe_load(p.read_text()))

    def _write(self, node: Node, pending: bool = False) -> None:
        p = self.path(node.kind, node.name, pending)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump(node))
        self._index(node, pending)

    # --- index ------------------------------------------------------------------------
    @contextmanager
    def _db(self):
        """Short-lived connection: commit and always close (Windows cannot delete open files)."""
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS objects(kind, name, state, version, owner, pending,"
                " PRIMARY KEY(kind, name, pending))"
            )
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS metrics_fts USING fts5("
                "name, concept, description, dimensions, synonyms)"
            )
            yield db
            db.commit()
        finally:
            db.close()

    def _index(self, node: Node, pending: bool) -> None:
        with self._db() as db:
            db.execute(
                "REPLACE INTO objects VALUES(?,?,?,?,?,?)",
                (node.kind, node.name, node.state.value, node.version, node.owner, int(pending)),
            )
            if isinstance(node, Metric) and not pending:
                db.execute("DELETE FROM metrics_fts WHERE name = ?", (node.name,))
                if node.state is State.certified:
                    db.execute(
                        "INSERT INTO metrics_fts(name, concept, description, dimensions, synonyms)"
                        " VALUES (?,?,?,?,?)",
                        (
                            node.name,
                            node.concept,
                            node.description,
                            " ".join(node.all_dimensions),
                            " ".join(node.synonyms),
                        ),
                    )

    def _unindex(self, kind: str, name: str, pending: bool) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM objects WHERE kind=? AND name=? AND pending=?",
                (kind, name, int(pending)),
            )
            if kind == "metric" and not pending:
                db.execute("DELETE FROM metrics_fts WHERE name = ?", (name,))

    def reindex(self) -> None:
        self.db_path.unlink(missing_ok=True)
        for pending in (False, True):
            for kind in KINDS:
                d = self.path(kind, "x", pending).parent
                for p in sorted(d.glob("*.yaml")) if d.exists() else []:
                    self._index(self._read(p, kind), pending)

    # --- dependencies -----------------------------------------------------------------
    def _check_metric_deps(self, node: Metric, require_certified: bool) -> None:
        if not node.dependencies:
            return
        seen: set[str] = set()

        def _visit(cur: str, deps: list[str]) -> None:
            for dep in deps:
                if dep == node.name:
                    raise ValueError(f"dependency cycle detected involving '{node.name}'")
                if dep in seen:
                    continue
                seen.add(dep)
                target = self.get("metric", dep)
                if require_certified and (target is None or target.state is not State.certified):
                    raise InvalidTransition(
                        f"metric {node.name}: requires certified input metric '{dep}'"
                    )
                if isinstance(target, Metric) and target.dependencies:
                    _visit(dep, target.dependencies)

        _visit(node.name, node.dependencies)

    def dependents_of(self, name: str, state: State | None = State.certified) -> list[Metric]:
        ms = self.list("metric", state) if state else self.list("metric")
        return [m for m in ms if isinstance(m, Metric) and name in m.dependencies]

    # --- queries ----------------------------------------------------------------------
    def get(self, kind: str, name: str, pending: bool = False) -> Node | None:
        p = self.path(kind, name, pending)
        return self._read(p, kind) if p.exists() else None

    def list(self, kind: str, state: State | None = None, pending: bool = False) -> list[Node]:
        q, args = "SELECT name FROM objects WHERE kind=? AND pending=?", [kind, int(pending)]
        if state:
            q, args = q + " AND state=?", args + [state.value]
        with self._db() as db:
            names = [r[0] for r in db.execute(q + " ORDER BY name", args)]
        return [n for n in (self.get(kind, x, pending) for x in names) if n]

    def certified(self, kind: str = "metric") -> list[Node]:
        return self.list(kind, State.certified)

    def search_metrics(self, query: str, k: int = 10) -> list[Metric]:
        """Search certified metrics via SQLite FTS5 BM25 over name, concept, description, dims, synonyms."""
        tokens = [t for t in re.findall(r"[a-z0-9_]+", (query or "").lower()) if t]
        if not tokens or k <= 0:
            return []
        match_expr = " OR ".join(f'"{t}"*' for t in dict.fromkeys(tokens))
        with self._db() as db:
            rows = db.execute(
                "SELECT name FROM metrics_fts WHERE metrics_fts MATCH ?"
                " ORDER BY bm25(metrics_fts, 5.0, 3.0, 2.0, 1.5, 4.0), name LIMIT ?",
                (match_expr, int(k)),
            ).fetchall()
        out: list[Metric] = []
        for (name,) in rows:
            m = self.get("metric", name)
            if isinstance(m, Metric) and m.state is State.certified:
                out.append(m)
        return out

    def queue(self, kind: str = "metric") -> list[Node]:
        """Review queue: drafts and pending revisions, ranked by evidence demand."""
        items = self.list(kind, State.draft) + self.list(kind, State.draft, pending=True)
        return sorted(items, key=lambda n: (-n.evidence.usage, n.name))

    def diff(self, kind: str, name: str) -> str:
        new, old = self.get(kind, name, pending=True), self.get(kind, name)
        if not new or not old:
            return ""
        a, b = dump(old).splitlines(), dump(new).splitlines()
        return "\n".join(difflib.unified_diff(a, b, "certified", "pending", lineterm=""))

    # --- writes -----------------------------------------------------------------------
    def put_draft(self, node: Node) -> str:
        """Store a draft. A name already certified gets a pending revision instead."""
        if isinstance(node, Metric):
            self._check_metric_deps(node, require_certified=False)
        existing = self.get(node.kind, node.name)
        if existing and existing.state is State.certified:
            node.version, node.state = existing.version, State.draft
            self._write(node, pending=True)
            return "pending"
        node.state = State.draft
        self._write(node)
        return "draft"

    def certify(self, kind: str, name: str, owner: str | None = None, by: str = "human",
                edits: dict | None = None) -> Node:
        pending = self.get(kind, name, pending=True) is not None
        node = self.get(kind, name, pending=pending)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        before = dump(node)
        data = node.model_dump()
        data.update(edits or {})
        if owner:
            data["owner"] = owner
        node = KINDS[kind].model_validate(data)
        if pending and not node.owner:
            current = self.get(kind, name)
            node.owner = current.owner if current else None
        if isinstance(node, Metric):
            self._check_metric_deps(node, require_certified=True)
        transition(node, State.certified)
        ratio = difflib.SequenceMatcher(None, before, dump(node)).ratio()
        self._write(node)
        if pending:
            self.path(kind, name, True).unlink()
            self._unindex(kind, name, True)
        self._audit("certify", kind, name, by, version=node.version, edit_ratio=round(ratio, 4),
                    edited=bool(edits))
        return node

    def suspend(self, kind: str, name: str, reason: str, by: str = "canary") -> list[Node]:
        """Suspend a certified definition (never served) and cascade to certified dependents."""
        node = self.get(kind, name)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        transition(node, State.suspended)
        node.suspend_reason = reason
        self._write(node)
        self._audit("suspend", kind, name, by, reason=reason)
        suspended = [node]
        if kind == "metric":
            for dep in self.dependents_of(name, State.certified):
                suspended.extend(
                    self.suspend("metric", dep.name, f"dependency '{name}' suspended", by=by)
                )
        return suspended

    def reject(self, kind: str, name: str, by: str = "human") -> None:
        """Reject a draft / pending revision (drafts are deprecated, pending ones dropped)."""
        if self.get(kind, name, pending=True):
            self.path(kind, name, True).unlink()
            self._unindex(kind, name, True)
        else:
            node = self.get(kind, name)
            if node is None:
                raise KeyError(f"{kind} {name} not found")
            if node.state is State.certified:
                raise ValueError("use deprecate for certified definitions")
            transition(node, State.deprecated)
            self._write(node)
        self._audit("reject", kind, name, by)

    def deprecate(self, kind: str, name: str, by: str = "human") -> Node:
        node = self.get(kind, name)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        if kind == "metric":
            deps = [d.name for d in self.dependents_of(name, State.certified)]
            if deps:
                raise ValueError(
                    f"cannot deprecate '{name}': required by certified metrics {sorted(deps)}"
                )
        transition(node, State.deprecated)
        self._write(node)
        self._audit("deprecate", kind, name, by)
        return node

    def rename(self, kind: str, old: str, new: str, by: str = "human") -> Node:
        node = self.get(kind, old)
        if node is None or self.get(kind, new):
            raise KeyError(f"cannot rename {old} -> {new}")
        node.name = new
        self.path(kind, old).unlink()
        self._unindex(kind, old, False)
        self._write(node)
        self._audit("rename", kind, old, by, to=new)
        return node

    def set_meta(self, kind: str, name: str, by: str = "human", **fields) -> Node:
        """Edit non-logic metadata (e.g. concept) of an existing definition, audited."""
        node = self.get(kind, name)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        node = KINDS[kind].model_validate({**node.model_dump(), **fields})
        self._write(node)
        self._audit("meta", kind, name, by, fields=sorted(fields))
        return node

    # --- audit ------------------------------------------------------------------------
    def _audit(self, action: str, kind: str, name: str, by: str, **extra) -> None:
        rec = {"ts": time.time(), "action": action, "kind": kind, "name": name, "by": by, **extra}
        with (self.state_dir / "audit.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
        sev = "WARNING" if action in {"suspend", "deprecate", "reject"} else "INFO"
        emit_cloud_log(sev, f"registry.{action}: {kind}/{name}", event=f"audit.{action}",
                       kind=kind, name=name, by=by, **extra)
        if self.commit:
            self._git(f"{action} {kind} {name}")

    def audit(self) -> list[dict]:
        p = self.state_dir / "audit.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def _git(self, msg: str) -> None:
        run = lambda *a: subprocess.run(
            ["git", *a], cwd=self.root, capture_output=True, check=False
        )
        run("add", "-A", ".")
        run("commit", "-m", msg, "--", ".")
