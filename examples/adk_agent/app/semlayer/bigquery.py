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
"""BigQuery warehouse over Google's managed MCP server (execute_sql_readonly only)."""

import asyncio
import concurrent.futures
import datetime
import json
import math
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

import sqlglot
from mcp import ClientSession
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from sqlglot import exp

from .config import PLACEHOLDER_PROJECTS
from .models import ident
from .warehouse import QueryResult

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"
ALLOWED_TOOL = "execute_sql_readonly"


def _validate_http_url(url: str) -> None:
    """Allow only HTTPS endpoints or loopback HTTP test servers."""
    if not url.startswith(
        ("https://", "http://127.0.0.1:", "http://localhost:")
    ):
        raise ValueError(f"disallowed endpoint URL scheme: {url!r}")


def google_access_token(
    service_account: str | None = None,
    scopes: Sequence[str] = (CLOUD_SCOPE,),
) -> str:
    """Acquire an OAuth2 access token via ADC or service-account impersonation."""
    import google.auth
    import google.auth.transport

    class _Resp(google.auth.transport.Response):
        def __init__(
            self, status: int, headers: dict[str, str], data: bytes
        ) -> None:
            self._status = status
            self._headers = headers
            self._data = data

        @property
        def status(self) -> int:
            return self._status

        @property
        def headers(self) -> dict[str, str]:
            return self._headers

        @property
        def data(self) -> bytes:
            return self._data

    class _UrllibRequest(google.auth.transport.Request):
        def __call__(
            self,
            url: str,
            method: str = "GET",
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
            **kwargs: Any,
        ) -> _Resp:
            _validate_http_url(url)
            req = urllib.request.Request(  # noqa: S310 -- scheme validated above
                url,
                data=body,
                headers=dict(headers or {}),
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout or 30) as resp:  # noqa: S310
                    return _Resp(
                        resp.status, dict(resp.headers.items()), resp.read()
                    )
            except urllib.error.HTTPError as exc:
                return _Resp(
                    exc.code,
                    dict(exc.headers.items()) if exc.headers else {},
                    exc.read() if exc.fp else b"",
                )

    creds, _ = google.auth.default(scopes=list(scopes))
    transport = _UrllibRequest()
    if service_account:
        from google.auth import impersonated_credentials

        creds = impersonated_credentials.Credentials(
            source_credentials=creds,
            target_principal=service_account,
            target_scopes=list(scopes),
            lifetime=3600,
        )
    if not creds.valid or not creds.token:
        creds.refresh(transport)
    return str(creds.token)


def _scalar_literal(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int):
        return (
            exp.Neg(this=exp.Literal.number(-value))
            if value < 0
            else exp.Literal.number(value)
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"non-finite float not allowed in BigQuery literal: {value!r}"
            )
        return (
            exp.Neg(this=exp.Literal.number(-value))
            if value < 0
            else exp.Literal.number(value)
        )
    if isinstance(value, datetime.date) and not isinstance(
        value, datetime.datetime
    ):
        return exp.Cast(
            this=exp.Literal.string(value.isoformat()),
            to=exp.DataType.build("DATE"),
        )
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("null byte is not allowed in BigQuery literal")
        return exp.Literal.string(value)
    raise ValueError(
        f"unsupported parameter type for BigQuery literal: {type(value).__name__}"
    )


def _param_literal(value: Any) -> exp.Expression:
    if isinstance(value, (list, tuple)):
        return exp.Array(expressions=[_scalar_literal(item) for item in value])
    return _scalar_literal(value)


def _replace_empty_list_predicates(placeholder: exp.Placeholder) -> bool:
    """Rewrite ``col IN UNNEST([])`` or ``list_contains([], col)`` to ``FALSE``."""
    parent = placeholder.parent
    if isinstance(parent, exp.Unnest) and isinstance(parent.parent, exp.In):
        parent.parent.replace(exp.Boolean(this=False))
        return True
    if isinstance(parent, (exp.Anonymous, exp.ArrayContains)):
        parent.replace(exp.Boolean(this=False))
        return True
    return False


def render_sql(
    sql: str,
    params: Sequence[Any] | None,
    project: str,
    dataset: str,
) -> str:
    """Bind AST literal nodes safely and emit single-SELECT BigQuery GoogleSQL."""
    if "\x00" in sql:
        raise ValueError("null byte is not allowed in SQL")
    ident(dataset)
    if (
        not project
        or project in PLACEHOLDER_PROJECTS
        or any(ch in project for ch in ("`", ";", "\n", "\r", "\x00", " "))
    ):
        raise ValueError(f"invalid BigQuery project id: {project!r}")
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError(f"invalid SQL: {exc}") from exc
    if (
        len(stmts) != 1
        or not isinstance(stmts[0], exp.Select)
        or stmts[0].find(exp.Command)
    ):
        raise ValueError("BigQueryMCP allows single SELECT statements only")
    tree = stmts[0]

    bound = list(params or [])
    placeholders = [
        node
        for node in tree.walk(bfs=False)
        if isinstance(node, exp.Placeholder)
    ]
    if len(placeholders) != len(bound):
        raise ValueError(
            f"placeholder count mismatch: {len(placeholders)} placeholders, "
            f"{len(bound)} params"
        )
    for ph, val in zip(placeholders, bound, strict=True):
        if (
            isinstance(val, (list, tuple))
            and len(val) == 0
            and _replace_empty_list_predicates(ph)
        ):
            continue
        ph.replace(_param_literal(val))

    def _rewrite_dates(node: exp.Expression) -> exp.Expression:
        if (
            isinstance(node, exp.Sub)
            and isinstance(node.this, exp.CurrentDate)
            and isinstance(node.expression, exp.Literal)
            and node.expression.is_int
        ):
            return exp.DateSub(
                this=exp.CurrentDate(),
                expression=node.expression.copy(),
                unit=exp.var("DAY"),
            )
        if (
            isinstance(node, exp.Add)
            and isinstance(node.this, exp.CurrentDate)
            and isinstance(node.expression, exp.Literal)
            and node.expression.is_int
        ):
            return exp.DateAdd(
                this=exp.CurrentDate(),
                expression=node.expression.copy(),
                unit=exp.var("DAY"),
            )
        return node

    tree = tree.transform(_rewrite_dates)

    cte_names = {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}
    for tbl in tree.find_all(exp.Table):
        if (
            tbl.name
            and tbl.name not in cte_names
            and not tbl.db
            and not tbl.catalog
        ):
            tbl.set("catalog", exp.Identifier(this=project, quoted=True))
            tbl.set("db", exp.Identifier(this=dataset, quoted=True))
            tbl.set("this", exp.Identifier(this=tbl.name, quoted=True))

    rendered = tree.sql(dialect="bigquery")
    verified = sqlglot.parse(rendered, read="bigquery")
    if (
        len(verified) != 1
        or not isinstance(verified[0], exp.Select)
        or list(verified[0].find_all(exp.Placeholder))
    ):
        raise ValueError("rendered SQL failed post-parse safety check")
    return rendered


def _cast_cell(value: Any, bq_type: str) -> Any:
    if value is None:
        return None
    normalized = (bq_type or "STRING").upper()
    if normalized in {
        "INT64",
        "INTEGER",
        "INT",
        "SMALLINT",
        "BIGINT",
        "BYTEINT",
    }:
        return int(value)
    if normalized in {
        "FLOAT64",
        "FLOAT",
        "NUMERIC",
        "BIGNUMERIC",
        "DECIMAL",
        "BIGDECIMAL",
    }:
        return float(value)
    if normalized in {"BOOL", "BOOLEAN"}:
        if isinstance(value, bool):
            return value
        return str(value).lower() == "true"
    return (
        str(value) if not isinstance(value, (int, float, bool, str)) else value
    )


def parse_bq_payload(payload: dict[str, Any]) -> QueryResult:
    """Validate BigQuery MCP response safeguards and convert cell types."""
    errors = (
        payload.get("errors")
        or payload.get("error")
        or payload.get("errorResult")
    )
    if errors:
        raise RuntimeError(f"BigQuery error: {errors}")
    if payload.get("jobComplete") is False:
        raise RuntimeError(
            "BigQuery query job is incomplete; refusing partial result"
        )
    fields = (payload.get("schema") or {}).get("fields") or []
    cols = [field["name"] for field in fields]
    types = [field.get("type", "STRING") for field in fields]
    raw_rows = payload.get("rows") or []
    if payload.get("pageToken"):
        raise RuntimeError(
            "BigQuery result is paginated/truncated; refusing partial result"
        )
    total = payload.get("totalRows")
    if total is not None and int(total) > len(raw_rows):
        raise RuntimeError(
            f"BigQuery result truncated: returned {len(raw_rows)} of {total} rows"
        )

    rows: list[list[Any]] = []
    for raw_row in raw_rows:
        if (
            isinstance(raw_row, dict)
            and "f" in raw_row
            and isinstance(raw_row["f"], list)
        ):
            cells = [
                cell.get("v") if isinstance(cell, dict) else cell
                for cell in raw_row["f"]
            ]
            rows.append(
                [
                    _cast_cell(
                        val, types[idx] if idx < len(types) else "STRING"
                    )
                    for idx, val in enumerate(cells)
                ]
            )
        elif isinstance(raw_row, dict):
            if not cols:
                cols = list(raw_row.keys())
                types = ["STRING"] * len(cols)
            rows.append(
                [
                    _cast_cell(
                        raw_row.get(col),
                        types[idx] if idx < len(types) else "STRING",
                    )
                    for idx, col in enumerate(cols)
                ]
            )
        elif isinstance(raw_row, list):
            rows.append(
                [
                    _cast_cell(
                        val, types[idx] if idx < len(types) else "STRING"
                    )
                    for idx, val in enumerate(raw_row)
                ]
            )
    return QueryResult(cols, rows)


class BigQueryMCP:
    """Warehouse backed by Google's BigQuery MCP server via execute_sql_readonly."""

    dialect = "bigquery"

    def __init__(
        self,
        project: str,
        dataset: str,
        mcp_url: str = BQ_MCP_URL,
        service_account: str | None = None,
        max_bytes: int = 100_000_000,
        token_provider: Callable[[], str] | None = None,
        dry_run_fn: Callable[[str], int] | None = None,
        http_client_factory: Callable[[dict[str, str]], Any] | None = None,
    ) -> None:
        _validate_http_url(mcp_url)
        self.project = project
        self.dataset = dataset
        self.mcp_url = mcp_url
        self.service_account = service_account
        self.max_bytes = max_bytes
        self._token_provider = token_provider or (
            lambda: google_access_token(service_account=self.service_account)
        )
        self._dry_run_fn = dry_run_fn
        self._http_client_factory = http_client_factory

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        hdrs = {"x-goog-user-project": self.project}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        return hdrs

    def _rest_dry_run_bytes(self, bq_sql: str) -> int:
        url = (
            "https://bigquery.googleapis.com/bigquery/v2/projects/"
            f"{self.project}/queries"
        )
        _validate_http_url(url)
        body = json.dumps(
            {"query": bq_sql, "useLegacySql": False, "dryRun": True}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._headers()}
        req = urllib.request.Request(url, body, headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = (
                exc.read().decode("utf-8", errors="replace")
                if exc.fp
                else exc.reason
            )
            raise RuntimeError(
                f"BigQuery dryRun failed ({exc.code}): {detail}"
            ) from exc
        return int(data.get("totalBytesProcessed", 0))

    async def _call_readonly(self, bq_sql: str) -> QueryResult:
        headers = self._headers()
        http_client = (
            self._http_client_factory(headers)
            if self._http_client_factory
            else create_mcp_http_client(headers=headers)
        )
        async with (
            http_client as client,
            streamable_http_client(self.mcp_url, http_client=client) as streams,
        ):
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                tools = {
                    tool.name: tool for tool in getattr(listed, "tools", listed)
                }
                if ALLOWED_TOOL not in tools:
                    raise RuntimeError(
                        f"MCP server does not expose {ALLOWED_TOOL}; "
                        f"available: {sorted(tools)}"
                    )
                tool_obj = tools[ALLOWED_TOOL]
                schema_dict = (
                    getattr(tool_obj, "input_schema", None)
                    or getattr(tool_obj, "inputSchema", None)
                    or {}
                )
                props = schema_dict.get("properties") or {}
                sql_key = (
                    "query"
                    if "query" in props
                    else ("sql" if "sql" in props else "query")
                )
                proj_key = (
                    "projectId"
                    if "projectId" in props
                    else ("project_id" if "project_id" in props else "project")
                )
                dry_key = (
                    "dryRun"
                    if "dryRun" in props
                    else ("dry_run" if "dry_run" in props else None)
                )

                base_args: dict[str, Any] = {sql_key: bq_sql}
                if proj_key in props or not props:
                    target_key = proj_key if proj_key in props else "projectId"
                    base_args[target_key] = self.project
                if "useLegacySql" in props:
                    base_args["useLegacySql"] = False

                if self.max_bytes > 0:
                    if self._dry_run_fn is not None:
                        estimated = self._dry_run_fn(bq_sql)
                    elif dry_key:
                        dry_res = await session.call_tool(
                            ALLOWED_TOOL, {**base_args, dry_key: True}
                        )
                        estimated = int(
                            _extract_payload(dry_res).get(
                                "totalBytesProcessed", 0
                            )
                        )
                    else:
                        estimated = self._rest_dry_run_bytes(bq_sql)
                    if estimated > self.max_bytes:
                        raise RuntimeError(
                            f"BigQuery dry run estimated {estimated} bytes, "
                            f"above cap {self.max_bytes}"
                        )

                result = await session.call_tool(ALLOWED_TOOL, base_args)
                return parse_bq_payload(_extract_payload(result))

    def _run_sql(self, bq_sql: str, max_rows: int | None = None) -> QueryResult:
        res = _run_coro(self._call_readonly(bq_sql))
        if max_rows is not None:
            res = QueryResult(res.columns, res.rows[:max_rows])
        return res

    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        max_rows: int | None = None,
    ) -> QueryResult:
        """Render safe GoogleSQL and execute via ``execute_sql_readonly``."""
        bq_sql = render_sql(sql, params, self.project, self.dataset)
        return self._run_sql(bq_sql, max_rows=max_rows)

    def schema(self) -> dict[str, dict[str, str]]:
        """Read column metadata via ``INFORMATION_SCHEMA.COLUMNS``."""
        ident(self.dataset)
        if not self.project or any(
            ch in self.project for ch in ("`", ";", "\n", "\r", "\x00", " ")
        ):
            raise ValueError(f"invalid BigQuery project id: {self.project!r}")
        info_sql = (
            "SELECT table_name, column_name, data_type "  # noqa: S608 -- project and dataset validated above
            f"FROM `{self.project}`.`{self.dataset}`.INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY table_name, ordinal_position"
        )
        res = self._run_sql(info_sql)
        out: dict[str, dict[str, str]] = defaultdict(dict)
        for table_name, col_name, data_type in res.rows:
            out[str(table_name)][str(col_name)] = str(data_type)
        return dict(out)

    def close(self) -> None:
        """No persistent socket to close for stateless Streamable HTTP."""
        return None


def _extract_payload(res: Any) -> dict[str, Any]:
    if getattr(res, "isError", False):
        text = " ".join(
            getattr(item, "text", str(item))
            for item in getattr(res, "content", [])
        )
        raise RuntimeError(f"BigQuery MCP error: {text}")
    structured = getattr(res, "structuredContent", None)
    if structured and isinstance(structured, dict):
        return structured
    texts = [
        getattr(item, "text", "")
        for item in getattr(res, "content", [])
        if getattr(item, "text", "")
    ]
    if not texts:
        return {"rows": [], "schema": {"fields": []}}
    raw = "\n".join(texts).strip()
    try:
        parsed = json.loads(raw)
        return (
            parsed
            if isinstance(parsed, dict)
            else {"rows": parsed, "schema": {"fields": []}}
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"BigQuery MCP returned non-JSON response: {raw[:240]}"
        ) from exc


def _unwrap_exc(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _run_coro(coro: Any) -> Any:
    """Run an async coroutine from sync code safely inside or outside a loop."""
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except BaseExceptionGroup as exc_group:
        raise _unwrap_exc(exc_group) from None
