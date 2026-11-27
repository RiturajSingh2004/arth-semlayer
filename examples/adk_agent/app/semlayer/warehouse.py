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
"""Warehouse abstraction: offline DuckDB fallback and managed BigQuery MCP."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb

from .config import Config
from .seed import build_db


@dataclass
class QueryResult:
    """Tabular query result returned by a warehouse backend."""

    columns: list[str]
    rows: list[list[Any]]


class Warehouse(Protocol):
    """Protocol implemented by DuckDBWarehouse and BigQueryMCP."""

    dialect: str

    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        max_rows: int | None = None,
    ) -> QueryResult: ...

    def schema(self) -> dict[str, dict[str, str]]: ...

    def close(self) -> None: ...


class DuckDBWarehouse:
    """Local DuckDB warehouse for deterministic offline execution and tests."""

    dialect = "duckdb"

    def __init__(
        self,
        path_or_con: str | Path | duckdb.DuckDBPyConnection,
        read_only: bool = True,
    ) -> None:
        if isinstance(path_or_con, (str, Path)):
            self._path: Path | None = Path(path_or_con)
            self._read_only = read_only
            self._con: duckdb.DuckDBPyConnection | None = (
                duckdb.connect(str(self._path), read_only=read_only)
                if (not read_only or self._path.exists())
                else None
            )
            self._owns = True
        else:
            self._path = None
            self._read_only = read_only
            self._con = path_or_con
            self._owns = False

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """Lazily open the underlying DuckDB connection."""
        if self._con is None:
            self._con = duckdb.connect(
                str(self._path), read_only=self._read_only
            )
        return self._con

    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        max_rows: int | None = None,
    ) -> QueryResult:
        """Execute a parameterized query against DuckDB."""
        cur = (
            self.con.execute(sql, list(params))
            if params
            else self.con.execute(sql)
        )
        cols = [desc[0] for desc in (cur.description or [])]
        raw = (
            cur.fetchmany(max_rows) if max_rows is not None else cur.fetchall()
        )
        return QueryResult(cols, [list(row) for row in raw])

    def schema(self) -> dict[str, dict[str, str]]:
        """Inspect table and column types in the ``main`` DuckDB schema."""
        res = self.query(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema='main' ORDER BY table_name, ordinal_position"
        )
        out: dict[str, dict[str, str]] = defaultdict(dict)
        for table_name, col_name, data_type in res.rows:
            out[str(table_name)][str(col_name)] = str(data_type)
        return dict(out)

    def close(self) -> None:
        """Close owned DuckDB connections."""
        if self._owns and self._con is not None:
            self._con.close()
            self._con = None


def ensure_warehouse(wh_or_con: Any) -> Warehouse:
    """Wrap a raw DuckDB connection or path if not already a ``Warehouse``."""
    if (
        hasattr(wh_or_con, "query")
        and hasattr(wh_or_con, "schema")
        and hasattr(wh_or_con, "dialect")
    ):
        return wh_or_con
    return DuckDBWarehouse(wh_or_con)


def make_warehouse(
    cfg: Config | None = None, read_only: bool = True
) -> Warehouse:
    """Instantiate the configured warehouse backend (DuckDB or BigQuery MCP)."""
    resolved = cfg or Config.from_env()
    if resolved.warehouse == "duckdb":
        db_file = Path(resolved.db_path)
        if not db_file.exists():
            build_db(db_file)
        return DuckDBWarehouse(db_file, read_only=read_only)
    if resolved.warehouse == "bigquery":
        from .bigquery import BigQueryMCP

        if not resolved.bq_project:
            raise ValueError(
                "BigQuery warehouse requires a configured GCP project: "
                "set SEMLAYER_BQ_PROJECT or GOOGLE_CLOUD_PROJECT to a real "
                "project ID (not YOUR_PROJECT_ID)."
            )
        return BigQueryMCP(
            project=resolved.bq_project,
            dataset=resolved.bq_dataset,
            mcp_url=resolved.bq_mcp_url,
            service_account=resolved.bq_service_account,
            max_bytes=resolved.bq_max_bytes,
        )
    raise ValueError(
        f"unknown warehouse: {resolved.warehouse!r} (use duckdb or bigquery)"
    )
