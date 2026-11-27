"""Warehouse abstraction: local DuckDB by default, remote BigQuery MCP optional."""
import os
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]


class Warehouse(Protocol):
    dialect: str

    def query(self, sql: str, params: Sequence | None = None,
              max_rows: int | None = None) -> QueryResult: ...

    def schema(self) -> dict[str, dict[str, str]]: ...

    def close(self) -> None: ...


class DuckDBWarehouse:
    dialect = "duckdb"

    def __init__(self, path_or_con: str | Path | duckdb.DuckDBPyConnection, read_only: bool = True):
        if isinstance(path_or_con, (str, Path)):
            self.con = duckdb.connect(str(path_or_con), read_only=read_only)
            self._owns = True
        else:
            self.con = path_or_con
            self._owns = False

    def query(self, sql: str, params: Sequence | None = None,
              max_rows: int | None = None) -> QueryResult:
        cur = self.con.execute(sql, list(params)) if params else self.con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        raw = cur.fetchmany(max_rows) if max_rows is not None else cur.fetchall()
        return QueryResult(cols, [list(r) for r in raw])

    def schema(self) -> dict[str, dict[str, str]]:
        res = self.query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='main' ORDER BY table_name, ordinal_position"
        )
        out: dict[str, dict[str, str]] = defaultdict(dict)
        for t, c, d in res.rows:
            out[t][c] = d
        return dict(out)

    def close(self) -> None:
        if self._owns:
            self.con.close()


def ensure_warehouse(wh_or_con) -> Warehouse:
    if hasattr(wh_or_con, "query") and hasattr(wh_or_con, "schema") and hasattr(wh_or_con, "dialect"):
        return wh_or_con
    return DuckDBWarehouse(wh_or_con)


def make_warehouse(cfg, read_only: bool = True) -> Warehouse:
    kind = os.environ.get("SEMLAYER_WAREHOUSE") or getattr(cfg, "warehouse", "duckdb")
    if kind == "duckdb":
        return DuckDBWarehouse(cfg.db, read_only=read_only)
    if kind == "bigquery":
        from .bigquery import BigQueryMCP

        project = (os.environ.get("SEMLAYER_BQ_PROJECT")
                   or os.environ.get("GOOGLE_CLOUD_PROJECT")
                   or cfg.bq_project)
        dataset = os.environ.get("SEMLAYER_BQ_DATASET") or cfg.bq_dataset
        mcp_url = os.environ.get("SEMLAYER_BQ_MCP_URL") or cfg.bq_mcp_url
        sa = os.environ.get("SEMLAYER_BQ_SERVICE_ACCOUNT") or cfg.bq_service_account
        max_bytes = int(os.environ.get("SEMLAYER_BQ_MAX_BYTES", cfg.bq_max_bytes))
        return BigQueryMCP(
            project=project, dataset=dataset, mcp_url=mcp_url,
            service_account=sa, max_bytes=max_bytes,
        )
    raise ValueError(f"unknown warehouse: {kind!r} (use duckdb or bigquery)")
