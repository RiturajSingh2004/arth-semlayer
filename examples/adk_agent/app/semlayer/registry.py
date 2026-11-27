# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""YAML-on-disk semantic registry with rebuildable SQLite index."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from .config import DEFAULT_DEFINITIONS_DIR
from .models import KINDS, Node, State, transition

DIRS: dict[str, str] = {
    "metric": "metrics",
    "entity": "entities",
    "edge": "edges",
    "rule": "rules",
}


def dump_node(node: Node) -> str:
    """Serialize a semantic node to deterministic YAML."""
    return yaml.safe_dump(node.model_dump(mode="json"), sort_keys=False)


class Registry:
    """Governed definition store backed by YAML files and a SQLite lookup index."""

    def __init__(
        self,
        root: str | Path = DEFAULT_DEFINITIONS_DIR,
        state_dir: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        if state_dir:
            self.state_dir = Path(state_dir)
        elif self.root.resolve() == DEFAULT_DEFINITIONS_DIR.resolve():
            # Keep bundled package directory clean during local runs and Docker.
            self.state_dir = (
                Path(tempfile.gettempdir())
                / "certified-bigquery-mcp"
                / "_state"
            )
        else:
            self.state_dir = self.root / "_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if (
            not self.db_path.exists()
            or self.root.resolve() == DEFAULT_DEFINITIONS_DIR.resolve()
        ):
            self.reindex()

    @property
    def db_path(self) -> Path:
        """Path to the SQLite metadata index."""
        return self.state_dir / "index.db"

    def path(self, kind: str, name: str, pending: bool = False) -> Path:
        """Return the YAML file path for a semantic definition."""
        base = self.root / "_pending" if pending else self.root
        return base / DIRS[kind] / f"{name}.yaml"

    def _read(self, file_path: Path, kind: str) -> Node:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        return KINDS[kind].model_validate(raw)

    def _write(self, node: Node, pending: bool = False) -> None:
        file_path = self.path(node.kind, node.name, pending)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(dump_node(node), encoding="utf-8")
        self._index(node, pending)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS objects("
                "kind TEXT, name TEXT, state TEXT, version INTEGER, "
                "owner TEXT, pending INTEGER, "
                "PRIMARY KEY(kind, name, pending))"
            )
            yield db
            db.commit()
        finally:
            db.close()

    def _index(self, node: Node, pending: bool) -> None:
        with self._db() as db:
            db.execute(
                "REPLACE INTO objects VALUES(?,?,?,?,?,?)",
                (
                    node.kind,
                    node.name,
                    node.state.value,
                    node.version,
                    node.owner,
                    int(pending),
                ),
            )

    def _unindex(self, kind: str, name: str, pending: bool) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM objects WHERE kind=? AND name=? AND pending=?",
                (kind, name, int(pending)),
            )

    def reindex(self) -> None:
        """Rebuild the SQLite index from YAML files on disk."""
        self.db_path.unlink(missing_ok=True)
        for pending in (False, True):
            for kind in KINDS:
                folder = self.path(kind, "x", pending).parent
                files = sorted(folder.glob("*.yaml")) if folder.exists() else []
                for file_path in files:
                    self._index(self._read(file_path, kind), pending)

    def get(self, kind: str, name: str, pending: bool = False) -> Node | None:
        """Fetch a single definition by kind and name."""
        file_path = self.path(kind, name, pending)
        return self._read(file_path, kind) if file_path.exists() else None

    def list(
        self,
        kind: str,
        state: State | None = None,
        pending: bool = False,
    ) -> list[Node]:
        """List definitions matching ``kind`` and optional ``state``."""
        query = "SELECT name FROM objects WHERE kind=? AND pending=?"
        args: list[str | int] = [kind, int(pending)]
        if state is not None:
            query += " AND state=?"
            args.append(state.value)
        with self._db() as db:
            names = [
                row[0] for row in db.execute(f"{query} ORDER BY name", args)
            ]
        nodes = [self.get(kind, name, pending) for name in names]
        return [node for node in nodes if node is not None]

    def certified(self, kind: str = "metric") -> list[Node]:
        """Return only human-certified definitions of ``kind``."""
        return self.list(kind, State.certified)

    def put_draft(self, node: Node) -> str:
        """Store a draft definition (or pending revision if already certified)."""
        existing = self.get(node.kind, node.name)
        if existing and existing.state is State.certified:
            node.version = existing.version
            node.state = State.draft
            self._write(node, pending=True)
            return "pending"
        node.state = State.draft
        self._write(node)
        return "draft"

    def certify(
        self,
        kind: str,
        name: str,
        owner: str | None = None,
        by: str = "human",
        edits: dict | None = None,
    ) -> Node:
        """Promote a draft or pending revision to ``certified``."""
        pending = self.get(kind, name, pending=True) is not None
        node = self.get(kind, name, pending=pending)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        data = node.model_dump()
        data.update(edits or {})
        if owner:
            data["owner"] = owner
        validated = KINDS[kind].model_validate(data)
        if pending and not validated.owner:
            current = self.get(kind, name)
            validated.owner = current.owner if current else None
        transition(validated, State.certified)
        self._write(validated)
        if pending:
            self.path(kind, name, True).unlink()
            self._unindex(kind, name, True)
        self._audit("certify", kind, name, by, version=validated.version)
        return validated

    def deprecate(self, kind: str, name: str, by: str = "human") -> Node:
        """Transition a definition to ``deprecated`` so it is no longer served."""
        node = self.get(kind, name)
        if node is None:
            raise KeyError(f"{kind} {name} not found")
        transition(node, State.deprecated)
        self._write(node)
        self._audit("deprecate", kind, name, by)
        return node

    def _audit(
        self, action: str, kind: str, name: str, by: str, **extra: object
    ) -> None:
        record = {
            "ts": time.time(),
            "action": action,
            "kind": kind,
            "name": name,
            "by": by,
            **extra,
        }
        with (self.state_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
