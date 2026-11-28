"""BigQuery warehouse over Google's managed MCP server (execute_sql_readonly only)."""
import asyncio
import datetime
import json
import math
import re
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Sequence

import sqlglot
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from sqlglot import exp

from semlayer.core.models import ident

from . import QueryResult

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"
ALLOWED_TOOL = "execute_sql_readonly"
BQ_REGION_RE = re.compile(r"^region-[a-z0-9-]+$")


def _sanitise_owner_hint(user_email: str) -> str:
    """Convert a BigQuery job `user_email` into a privacy-safe local identifier (strip domain)."""
    local = (user_email or "").split("@", 1)[0].strip().lower()
    cleaned = re.sub(r"[^a-z0-9_]+", "_", local).strip("_")
    if not cleaned:
        return "unknown"
    return f"u_{cleaned}" if cleaned[0].isdigit() else cleaned


def normalise_bq_query_tables(sql: str, project: str, dataset: str) -> str:
    """Strip `project.dataset.` or `dataset.` qualifiers so single-table draft parsing matches local schema."""
    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except sqlglot.errors.SqlglotError:
        return sql
    for tbl in tree.find_all(exp.Table):
        cat = (tbl.catalog or "").strip("`")
        db = (tbl.db or "").strip("`")
        if db == dataset and (not cat or cat == project):
            tbl.set("catalog", None)
            tbl.set("db", None)
            tbl.set("this", exp.to_identifier(tbl.name))
    return tree.sql(dialect="duckdb")


def google_access_token(service_account: str | None = None,
                        scopes: Sequence[str] = (CLOUD_SCOPE,)) -> str:
    """Acquire an OAuth2 access token via ADC or service-account impersonation (stdlib urllib)."""
    try:
        import google.auth
        import google.auth.transport
    except ImportError as e:
        raise RuntimeError(
            "google-auth is required for GCP warehouses; install with `uv sync --extra gcp`"
        ) from e

    class _Resp(google.auth.transport.Response):
        def __init__(self, status: int, headers: dict[str, str], data: bytes):
            self._status, self._headers, self._data = status, headers, data

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
        def __call__(self, url: str, method: str = "GET", body: bytes | None = None,
                     headers: dict | None = None, timeout: float | None = None, **kwargs):
            req = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout or 30) as r:
                    return _Resp(r.status, dict(r.headers.items()), r.read())
            except urllib.error.HTTPError as e:
                return _Resp(e.code, dict(e.headers.items()) if e.headers else {},
                             e.read() if e.fp else b"")

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


def _scalar_literal(v) -> exp.Expression:
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, int):
        return exp.Neg(this=exp.Literal.number(-v)) if v < 0 else exp.Literal.number(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"non-finite float not allowed in BigQuery literal: {v!r}")
        return exp.Neg(this=exp.Literal.number(-v)) if v < 0 else exp.Literal.number(v)
    if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        return exp.Cast(this=exp.Literal.string(v.isoformat()), to=exp.DataType.build("DATE"))
    if isinstance(v, str):
        if "\x00" in v:
            raise ValueError("null byte is not allowed in BigQuery string literal")
        return exp.Literal.string(v)
    raise ValueError(f"unsupported parameter type for BigQuery literal: {type(v).__name__}")


def _param_literal(v) -> exp.Expression:
    if isinstance(v, (list, tuple)):
        return exp.Array(expressions=[_scalar_literal(x) for x in v])
    return _scalar_literal(v)


def _replace_empty_list_predicates(ph: exp.Placeholder) -> bool:
    """Rewrite `col IN UNNEST([])` or `list_contains([], col)` to FALSE to avoid untyped `[]`."""
    parent = ph.parent
    if isinstance(parent, exp.Unnest) and isinstance(parent.parent, exp.In):
        parent.parent.replace(exp.Boolean(this=False))
        return True
    if isinstance(parent, (exp.Anonymous, exp.ArrayContains)):
        parent.replace(exp.Boolean(this=False))
        return True
    return False


def render_sql(sql: str, params: Sequence | None, project: str, dataset: str) -> str:
    """Parse DuckDB-dialect SQL, bind literal AST nodes safely, and emit BigQuery GoogleSQL."""
    if "\x00" in sql:
        raise ValueError("null byte is not allowed in SQL")
    ident(dataset)  # dataset must be a clean SQL identifier
    if not project or any(ch in project for ch in ("`", ";", "\n", "\r", "\x00", " ")):
        raise ValueError(f"invalid BigQuery project id: {project!r}")
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError as e:
        raise ValueError(f"invalid SQL: {e}") from e
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select) or stmts[0].find(exp.Command):
        raise ValueError("BigQueryMCP allows single SELECT statements only")
    tree = stmts[0]

    bound = list(params or [])
    placeholders = [n for n in tree.walk(bfs=False) if isinstance(n, exp.Placeholder)]
    if len(placeholders) != len(bound):
        raise ValueError(f"placeholder count mismatch: {len(placeholders)} placeholders, {len(bound)} params")
    for ph, val in zip(placeholders, bound, strict=True):
        if isinstance(val, (list, tuple)) and len(val) == 0 and _replace_empty_list_predicates(ph):
            continue
        ph.replace(_param_literal(val))

    def _rewrite_dates(node: exp.Expression) -> exp.Expression:
        if (isinstance(node, exp.Sub) and isinstance(node.this, exp.CurrentDate)
                and isinstance(node.expression, exp.Literal) and node.expression.is_int):
            return exp.DateSub(this=exp.CurrentDate(), expression=node.expression.copy(), unit=exp.var("DAY"))
        if (isinstance(node, exp.Add) and isinstance(node.this, exp.CurrentDate)
                and isinstance(node.expression, exp.Literal) and node.expression.is_int):
            return exp.DateAdd(this=exp.CurrentDate(), expression=node.expression.copy(), unit=exp.var("DAY"))
        if isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)):
            raw_unit = (node.unit.name if node.unit else "").upper()
            if raw_unit in {"DAY", "WEEK", "ISOWEEK", "MONTH", "QUARTER", "YEAR"}:
                bq_unit = "ISOWEEK" if raw_unit == "WEEK" else raw_unit
                target = node.this.copy()
                if not isinstance(target, exp.Cast):
                    target = exp.Cast(this=target, to=exp.DataType.build("DATE"))
                return exp.DateTrunc(this=target, unit=exp.var(bq_unit))
        return node

    tree = tree.transform(_rewrite_dates)

    cte_names = {c.alias for c in tree.find_all(exp.CTE) if c.alias}
    for tbl in tree.find_all(exp.Table):
        if tbl.name and tbl.name not in cte_names and not tbl.db and not tbl.catalog:
            tbl.set("catalog", exp.Identifier(this=project, quoted=True))
            tbl.set("db", exp.Identifier(this=dataset, quoted=True))
            tbl.set("this", exp.Identifier(this=tbl.name, quoted=True))

    out = tree.sql(dialect="bigquery")
    check = sqlglot.parse(out, read="bigquery")
    if len(check) != 1 or not isinstance(check[0], exp.Select) or list(check[0].find_all(exp.Placeholder)):
        raise ValueError("rendered SQL failed post-parse safety check")
    return out


def _cast_cell(val, bq_type: str):
    if val is None:
        return None
    t = (bq_type or "STRING").upper()
    if t in {"INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "BYTEINT"}:
        return int(val)
    if t in {"FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"}:
        return float(val)
    if t in {"BOOL", "BOOLEAN"}:
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"
    return str(val) if not isinstance(val, (int, float, bool, str)) else val


def parse_bq_payload(payload: dict) -> QueryResult:
    """Validate BigQuery MCP response safeguards (ADR-028) and convert cell types."""
    if errors := (payload.get("errors") or payload.get("error") or payload.get("errorResult")):
        raise RuntimeError(f"BigQuery error: {errors}")
    if payload.get("jobComplete") is False:
        raise RuntimeError("BigQuery query job is incomplete; refusing partial result")
    fields = (payload.get("schema") or {}).get("fields") or []
    cols = [f["name"] for f in fields]
    types = [f.get("type", "STRING") for f in fields]
    raw_rows = payload.get("rows") or []
    if payload.get("pageToken"):
        raise RuntimeError("BigQuery result is paginated/truncated; refusing partial result")
    if (total := payload.get("totalRows")) is not None and int(total) > len(raw_rows):
        raise RuntimeError(f"BigQuery result truncated: returned {len(raw_rows)} of {total} rows")

    rows: list[list] = []
    for r in raw_rows:
        if isinstance(r, dict) and "f" in r and isinstance(r["f"], list):
            cells = [c.get("v") if isinstance(c, dict) else c for c in r["f"]]
            rows.append([_cast_cell(v, types[i] if i < len(types) else "STRING")
                         for i, v in enumerate(cells)])
        elif isinstance(r, dict):
            if not cols:
                cols = list(r.keys())
                types = ["STRING"] * len(cols)
            rows.append([_cast_cell(r.get(c), types[i] if i < len(types) else "STRING")
                         for i, c in enumerate(cols)])
        elif isinstance(r, list):
            rows.append([_cast_cell(v, types[i] if i < len(types) else "STRING")
                         for i, v in enumerate(r)])
    return QueryResult(cols, rows)


class BigQueryMCP:
    """Warehouse backed by Google's BigQuery MCP server via `execute_sql_readonly` only."""

    dialect = "bigquery"

    def __init__(self, project: str, dataset: str, mcp_url: str = BQ_MCP_URL,
                 service_account: str | None = None, max_bytes: int = 100_000_000,
                 location: str = "region-us", job_project: str | None = None,
                 token_provider: Callable[[], str] | None = None,
                 dry_run_fn: Callable[[str], int] | None = None,
                 http_client_factory: Callable[[dict[str, str]], object] | None = None):
        self.project, self.dataset, self.mcp_url = project, dataset, mcp_url
        self.job_project = job_project or project
        self.location = location
        self.service_account, self.max_bytes = service_account, max_bytes
        self._token_provider = token_provider or (
            lambda: google_access_token(service_account=self.service_account)
        )
        self._dry_run_fn = dry_run_fn
        self._http_client_factory = http_client_factory

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        hdrs = {"x-goog-user-project": self.job_project}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        return hdrs

    def _rest_dry_run_bytes(self, bq_sql: str) -> int:
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.job_project}/queries"
        body = json.dumps({"query": bq_sql, "useLegacySql": False, "dryRun": True}).encode()
        headers = {"Content-Type": "application/json", **self._headers()}
        req = urllib.request.Request(url, body, headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace") if e.fp else e.reason
            raise RuntimeError(f"BigQuery dryRun failed ({e.code}): {detail}") from e
        return int(data.get("totalBytesProcessed", 0))

    async def _call_readonly(self, bq_sql: str) -> QueryResult:
        headers = self._headers()
        http_client = (self._http_client_factory(headers) if self._http_client_factory
                       else create_mcp_http_client(headers=headers))
        async with http_client as client:
            async with streamable_http_client(self.mcp_url, http_client=client) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    tools = {t.name: t for t in getattr(listed, "tools", listed)}
                    if ALLOWED_TOOL not in tools:
                        raise RuntimeError(
                            f"MCP server does not expose {ALLOWED_TOOL}; available: {sorted(tools)}"
                        )
                    t_obj = tools[ALLOWED_TOOL]
                    schema_dict = getattr(t_obj, "input_schema", None) or getattr(t_obj, "inputSchema", None) or {}
                    props = schema_dict.get("properties") or {}
                    sql_key = "query" if "query" in props else ("sql" if "sql" in props else "query")
                    proj_key = ("projectId" if "projectId" in props
                                else ("project_id" if "project_id" in props else "project"))
                    dry_key = ("dryRun" if "dryRun" in props
                               else ("dry_run" if "dry_run" in props else None))

                    base_args: dict = {sql_key: bq_sql}
                    if proj_key in props or not props:
                        base_args[proj_key if proj_key in props else "projectId"] = self.job_project
                    if "useLegacySql" in props:
                        base_args["useLegacySql"] = False

                    if self.max_bytes > 0:
                        if self._dry_run_fn is not None:
                            est = self._dry_run_fn(bq_sql)
                        elif dry_key:
                            dr = await session.call_tool(ALLOWED_TOOL, {**base_args, dry_key: True})
                            est = int(_extract_payload(dr).get("totalBytesProcessed", 0))
                        else:
                            est = self._rest_dry_run_bytes(bq_sql)
                        if est > self.max_bytes:
                            raise RuntimeError(
                                f"BigQuery dry run estimated {est} bytes, above cap {self.max_bytes}"
                            )

                    res = await session.call_tool(ALLOWED_TOOL, base_args)
                    return parse_bq_payload(_extract_payload(res))

    def _run_sql(self, bq_sql: str, max_rows: int | None = None) -> QueryResult:
        res = _run_coro(self._call_readonly(bq_sql))
        if max_rows is not None:
            res = QueryResult(res.columns, res.rows[:max_rows])
        return res

    def query(self, sql: str, params: Sequence | None = None,
              max_rows: int | None = None) -> QueryResult:
        bq_sql = render_sql(sql, params, self.project, self.dataset)
        return self._run_sql(bq_sql, max_rows=max_rows)

    def schema(self) -> dict[str, dict[str, str]]:
        ident(self.dataset)
        info_sql = (
            f"SELECT table_name, column_name, data_type "
            f"FROM `{self.project}`.`{self.dataset}`.INFORMATION_SCHEMA.COLUMNS "
            f"ORDER BY table_name, ordinal_position"
        )
        res = self._run_sql(info_sql)
        out: dict[str, dict[str, str]] = defaultdict(dict)
        for t, c, d in res.rows:
            out[str(t)][str(c)] = str(d)
        return dict(out)

    def recent_queries(self, limit: int = 500, days: int = 30) -> list[tuple[str, str]]:
        """Fetch recent SELECT statements from BigQuery INFORMATION_SCHEMA.JOBS_BY_PROJECT (ADR-036)."""
        ident(self.dataset)
        if not self.project or any(ch in self.project for ch in ("`", ";", "\n", "\r", "\x00", " ")):
            raise ValueError(f"invalid BigQuery project id: {self.project!r}")
        if not BQ_REGION_RE.match(self.location):
            raise ValueError(
                f"invalid BigQuery region qualifier {self.location!r} (expected e.g. 'region-us')"
            )
        lim = max(1, min(int(limit), 3000))
        lookback = max(1, min(int(days), 365))
        jobs_sql = (
            f"SELECT user_email, query "
            f"FROM `{self.project}`.`{self.location}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT "
            f"WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback} DAY) "
            f"AND job_type = 'QUERY' AND state = 'DONE' AND error_result IS NULL "
            f"AND statement_type = 'SELECT' "
            f"AND LOWER(query) NOT LIKE '%information_schema%' "
            f"ORDER BY creation_time DESC LIMIT {lim}"
        )
        res = self._run_sql(jobs_sql, max_rows=lim)
        out: list[tuple[str, str]] = []
        for row in res.rows:
            if len(row) >= 2 and row[1]:
                user = _sanitise_owner_hint(str(row[0] or ""))
                norm_sql = normalise_bq_query_tables(str(row[1]).strip().rstrip(";"), self.project, self.dataset)
                out.append((user, norm_sql))
        return out

    def policy_tagged_columns(self) -> dict[str, list[str]]:
        """Return `{table: [column, ...]}` carrying Dataplex / Data Catalog policy tags (ADR-036)."""
        ident(self.dataset)
        if not self.project or any(ch in self.project for ch in ("`", ";", "\n", "\r", "\x00", " ")):
            raise ValueError(f"invalid BigQuery project id: {self.project!r}")
        tags_sql = (
            f"SELECT table_name, field_path "
            f"FROM `{self.project}`.`{self.dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS "
            f"WHERE ARRAY_LENGTH(policy_tags) > 0 "
            f"ORDER BY table_name, field_path"
        )
        try:
            res = self._run_sql(tags_sql)
        except Exception:
            return {}
        out: dict[str, list[str]] = defaultdict(list)
        for t, fp in res.rows:
            col = str(fp).split(".", 1)[0]
            if col not in out[str(t)]:
                out[str(t)].append(col)
        return dict(out)

    def close(self) -> None:
        return None


def _extract_payload(res) -> dict:
    if getattr(res, "isError", False):
        text = " ".join(getattr(c, "text", str(c)) for c in getattr(res, "content", []))
        raise RuntimeError(f"BigQuery MCP error: {text}")
    if getattr(res, "structuredContent", None) and isinstance(res.structuredContent, dict):
        return res.structuredContent
    texts = [getattr(c, "text", "") for c in getattr(res, "content", []) if getattr(c, "text", "")]
    if not texts:
        return {"rows": [], "schema": {"fields": []}}
    raw = "\n".join(texts).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"rows": parsed, "schema": {"fields": []}}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"BigQuery MCP returned non-JSON response: {raw[:240]}") from e


def _unwrap_exc(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _run_coro(coro):
    """Run an async coroutine from sync code, even when an event loop is already running."""
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except BaseExceptionGroup as eg:
        raise _unwrap_exc(eg) from None
