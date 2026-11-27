"""Metric -> SQL via sqlglot ASTs (ADR-002, ADR-032, ADR-033, ADR-034)."""
from __future__ import annotations

import ast
import datetime
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from semlayer.core import Metric
from semlayer.core.models import ident, parse_derived_formula

TIME_GRAINS = ("day", "week", "month", "quarter", "year")
CATEGORICAL_OPS = {"eq", "ne", "in", "is_null"}
TIME_OPS = {"eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "is_null"}
ALL_OPS = TIME_OPS


class CompileError(ValueError):
    pass


@dataclass
class Compiled:
    sql: str
    params: list = field(default_factory=list)


@dataclass(frozen=True)
class JoinSpec:
    """Validated many-to-one join path for `<Entity>.<column>`."""

    field_ref: str  # e.g. "Customer.region"
    output_alias: str  # e.g. "customer_region"
    target_alias: str  # SQL table alias for final joined entity, e.g. "j_customer"
    column: str  # column on target entity table, e.g. "region"
    is_time: bool = False
    # Each step: (left_alias, left_key, right_table, right_alias, right_key)
    steps: tuple[tuple[str, str, str, str, str], ...] = ()


class FilterSpec(BaseModel):
    field: str
    op: Literal["eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "is_null"] = "eq"
    value: object = None


class QuerySpec(BaseModel):
    metrics: list[str]
    dims: list[str] = []
    filters: list[FilterSpec] = []
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    time_range: str | list[str] | tuple[str, str] | dict[str, str] | None = None
    order_by: list[str] = []
    limit: int | None = None


class Compiler(Protocol):
    def compile(self, m: Metric, dims=(), filters=None, row_filter=None,
                entity_keys=None, **kwargs) -> Compiled: ...


def _today_in_tz(clock: Callable[[], datetime.date] | None, timezone: str = "UTC") -> datetime.date:
    if clock is not None:
        return clock()
    try:
        tz = ZoneInfo(timezone)
    except Exception as e:
        raise CompileError(f"invalid timezone {timezone!r}: {e}") from e
    return datetime.datetime.now(tz).date()


def _parse_iso_date(val: object) -> datetime.date:
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.date.fromisoformat(val.strip())
        except ValueError as e:
            raise CompileError(f"invalid ISO date {val!r} (expected YYYY-MM-DD)") from e
    raise CompileError(f"invalid date value {val!r} (expected YYYY-MM-DD)")


def _shift_month(d: datetime.date, delta_months: int) -> datetime.date:
    total = (d.year * 12 + (d.month - 1)) + delta_months
    y, m = divmod(total, 12)
    return datetime.date(y, m + 1, 1)


def resolve_time_range(
    spec: str | Sequence | dict,
    clock: Callable[[], datetime.date] | None = None,
    timezone: str = "UTC",
) -> tuple[datetime.date, datetime.date]:
    """Resolve explicit `[start, end)` or relative time range against `clock`/`timezone`."""
    if isinstance(spec, dict):
        if "start" not in spec or "end" not in spec:
            raise CompileError("time_range dict requires 'start' and 'end'")
        start, end = _parse_iso_date(spec["start"]), _parse_iso_date(spec["end"])
        if start > end:
            raise CompileError(f"time_range start {start} must be <= end {end}")
        return start, end
    if isinstance(spec, (list, tuple)) and not isinstance(spec, (str, bytes)):
        if len(spec) != 2:
            raise CompileError("time_range sequence must be [start, end]")
        start, end = _parse_iso_date(spec[0]), _parse_iso_date(spec[1])
        if start > end:
            raise CompileError(f"time_range start {start} must be <= end {end}")
        return start, end
    if not isinstance(spec, str):
        raise CompileError(f"unsupported time_range: {spec!r}")

    today = _today_in_tz(clock, timezone)
    norm = re.sub(r"[\s-]+", "_", spec.strip().lower())
    if norm == "today":
        return today, today + datetime.timedelta(days=1)
    if norm == "yesterday":
        return today - datetime.timedelta(days=1), today
    if m := re.fullmatch(r"last_(\d+)_days?", norm):
        n = int(m.group(1))
        if n < 1:
            raise CompileError("last_N_days requires N >= 1")
        return today - datetime.timedelta(days=n), today
    if norm == "this_month":
        s = today.replace(day=1)
        return s, _shift_month(s, 1)
    if norm == "last_month":
        e = today.replace(day=1)
        return _shift_month(e, -1), e
    if norm in {"this_quarter", "last_quarter"}:
        q_month = ((today.month - 1) // 3) * 3 + 1
        cur_q = datetime.date(today.year, q_month, 1)
        if norm == "this_quarter":
            return cur_q, _shift_month(cur_q, 3)
        return _shift_month(cur_q, -3), cur_q
    if norm == "this_year":
        return datetime.date(today.year, 1, 1), datetime.date(today.year + 1, 1, 1)
    if norm == "last_year":
        return datetime.date(today.year - 1, 1, 1), datetime.date(today.year, 1, 1)
    raise CompileError(
        f"unknown relative time_range {spec!r}; supported: today, yesterday, last_N_days, "
        "this_month, last_month, this_quarter, last_quarter, this_year, last_year, or [start, end]"
    )


def normalise_filters(raw) -> list[FilterSpec]:
    """Accept v1 dict `{'col': val}`, operator dict `{'col': {'gte': x}}`, or list of FilterSpec."""
    if raw is None:
        return []
    out: list[FilterSpec] = []
    if isinstance(raw, dict):
        for col, val in raw.items():
            if isinstance(val, dict):
                if not val:
                    raise CompileError(f"empty operator dict for filter {col!r}")
                for op, operand in val.items():
                    if op not in ALL_OPS:
                        raise CompileError(f"unsupported filter operator {op!r} on {col!r}")
                    out.append(FilterSpec(field=col, op=op, value=operand))
            else:
                out.append(FilterSpec(field=col, op="eq", value=val))
        return out
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, FilterSpec):
                out.append(item)
            elif isinstance(item, dict):
                try:
                    out.append(FilterSpec.model_validate(item))
                except Exception as e:
                    raise CompileError(f"invalid filter spec {item!r}: {e}") from e
            else:
                raise CompileError(f"invalid filter item: {item!r}")
        return out
    raise CompileError(f"invalid filters: {raw!r}")


def normalise_order_by(order_by) -> list[tuple[str, bool]]:
    """Parse `'-revenue'`, `'revenue desc'`, or list into `[(col, desc_bool), ...]`."""
    if not order_by:
        return []
    items = [order_by] if isinstance(order_by, str) else list(order_by)
    out: list[tuple[str, bool]] = []
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        if s.startswith("-"):
            out.append((s[1:].strip(), True))
        elif s.startswith("+"):
            out.append((s[1:].strip(), False))
        else:
            parts = s.split()
            if len(parts) == 1:
                out.append((parts[0], False))
            elif len(parts) == 2 and parts[1].lower() in {"asc", "desc"}:
                out.append((parts[0], parts[1].lower() == "desc"))
            else:
                raise CompileError(f"invalid order_by entry: {raw!r}")
    return out


def _qualify_condition(cond_sql: str, table_alias: str | None) -> exp.Expression:
    tree = sqlglot.condition(cond_sql, dialect="duckdb")
    if not table_alias:
        return exp.Paren(this=tree)

    def _qual(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table:
            return exp.column(node.name, table=table_alias)
        return node

    return exp.Paren(this=tree.transform(_qual))


def _agg_ast(m: Metric, table_alias: str | None) -> exp.Expression:
    assert m.agg is not None
    col_expr: exp.Expression = (
        exp.Star() if m.column == "*" else exp.column(m.column, table=table_alias)
    )
    if m.agg == "count":
        return exp.Count(this=col_expr)
    if m.agg == "count_distinct":
        return exp.Count(this=exp.Distinct(expressions=[col_expr]))
    cls = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}[m.agg]
    return cls(this=col_expr)


def _filter_ast(
    col_expr: exp.Expression,
    f: FilterSpec,
    is_time: bool,
    params: list,
    max_filter_list: int,
) -> exp.Expression:
    allowed = TIME_OPS if is_time else CATEGORICAL_OPS
    if f.op not in allowed:
        kind = "time" if is_time else "categorical"
        raise CompileError(
            f"operator {f.op!r} is not allowed on {kind} dimension {f.field!r}; "
            f"allowed: {sorted(allowed)}"
        )
    val = f.value
    if is_time and f.op in {"eq", "ne", "gt", "gte", "lt", "lte"} and isinstance(val, str):
        val = _parse_iso_date(val)

    if f.op == "eq":
        if val is None:
            return exp.Is(this=col_expr, expression=exp.Null())
        params.append(val)
        return exp.EQ(this=col_expr, expression=exp.Placeholder())
    if f.op == "ne":
        if val is None:
            return exp.Not(this=exp.Is(this=col_expr, expression=exp.Null()))
        params.append(val)
        return exp.NEQ(this=col_expr, expression=exp.Placeholder())
    if f.op == "is_null":
        if not isinstance(val, bool):
            raise CompileError(f"is_null filter on {f.field!r} requires a boolean value")
        iso = exp.Is(this=col_expr, expression=exp.Null())
        return iso if val else exp.Not(this=iso)
    if f.op == "in":
        if not isinstance(val, (list, tuple)) or isinstance(val, (str, bytes)):
            raise CompileError(f"'in' filter on {f.field!r} requires a list of values")
        if len(val) > max_filter_list:
            raise CompileError(
                f"'in' filter on {f.field!r} has {len(val)} items, exceeding max_filter_list ({max_filter_list})"
            )
        if len(val) == 0:
            return exp.Boolean(this=False)
        phs = []
        for item in val:
            params.append(_parse_iso_date(item) if (is_time and isinstance(item, str)) else item)
            phs.append(exp.Placeholder())
        return exp.In(this=col_expr, expressions=phs)
    if f.op == "between":
        if not isinstance(val, (list, tuple)) or len(val) != 2:
            raise CompileError(f"'between' filter on {f.field!r} requires [low, high]")
        lo = _parse_iso_date(val[0]) if (is_time and isinstance(val[0], str)) else val[0]
        hi = _parse_iso_date(val[1]) if (is_time and isinstance(val[1], str)) else val[1]
        params.extend([lo, hi])
        return exp.Between(this=col_expr, low=exp.Placeholder(), high=exp.Placeholder())

    op_cls = {"gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}[f.op]
    params.append(val)
    return op_cls(this=col_expr, expression=exp.Placeholder())


def _formula_sql_ast(formula: str, metric_exprs: dict[str, exp.Expression]) -> exp.Expression:
    """Convert a validated derived formula into a sqlglot AST with NULLIF safe division."""
    parse_derived_formula(formula)
    py_tree = ast.parse(formula, mode="eval")

    def _conv(node: ast.AST) -> exp.Expression:
        if isinstance(node, ast.Expression):
            return _conv(node.body)
        if isinstance(node, ast.Name):
            return exp.Paren(this=metric_exprs[node.id].copy())
        if isinstance(node, ast.Constant):
            v = node.value
            return exp.Neg(this=exp.Literal.number(-v)) if v < 0 else exp.Literal.number(v)
        if isinstance(node, ast.UnaryOp):
            inner = _conv(node.operand)
            return exp.Neg(this=inner) if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.BinOp):
            left, right = _conv(node.left), _conv(node.right)
            if isinstance(node.op, ast.Add):
                return exp.Paren(this=exp.Add(this=left, expression=right))
            if isinstance(node.op, ast.Sub):
                return exp.Paren(this=exp.Sub(this=left, expression=right))
            if isinstance(node.op, ast.Mult):
                return exp.Paren(this=exp.Mul(this=left, expression=right))
            if isinstance(node.op, ast.Div):
                num = exp.Cast(this=left, to=exp.DataType.build("DOUBLE"))
                den = exp.Nullif(this=right, expression=exp.Literal.number(0))
                return exp.Paren(this=exp.Div(this=num, expression=den))
        raise CompileError(f"unsupported formula AST node: {type(node).__name__}")

    res = _conv(py_tree)
    return res.this if isinstance(res, exp.Paren) else res


class SimpleCompiler:
    def __init__(self, dialect: str = "duckdb"):
        self.dialect = dialect

    def _build_simple_select(
        self,
        m: Metric,
        dims: list[str],
        fspecs: list[FilterSpec],
        row_filter: str | None,
        entity_keys: Sequence | None,
        time_grain: str | None,
        time_range,
        joins: dict[str, JoinSpec],
        max_filter_list: int,
        clock: Callable[[], datetime.date] | None,
        timezone: str,
        params: list,
        include_default_order: bool = True,
    ) -> tuple[exp.Select, list[str]]:
        if m.metric_type != "simple":
            raise CompileError(f"{m.name} is a {m.metric_type} metric; use compile_derived/compile_multi")

        allowed_local = set(m.all_dimensions)
        time_local = set(m.all_time_dimensions)
        use_alias = "t0" if joins else None

        # Validate dimensions and filters against certified local dims + resolved JoinSpecs
        for d in dims:
            if d not in allowed_local and d not in joins:
                raise CompileError(f"not allowed for {m.name}: {[d]}; allowed: {m.all_dimensions}")
        bad_filters = [f.field for f in fspecs if f.field not in allowed_local and f.field not in joins]
        if bad_filters:
            raise CompileError(f"not allowed for {m.name}: {bad_filters}; allowed: {m.all_dimensions}")

        pinned_eq = {f.field for f in fspecs if f.op == "eq" and f.value is not None}
        active_dims = [d for d in dims if d not in pinned_eq]

        # Resolve time column if time_grain or time_range requested
        time_col: str | None = None
        if time_grain is not None or time_range is not None:
            if time_grain is not None and time_grain not in TIME_GRAINS:
                raise CompileError(f"unsupported time_grain {time_grain!r}; allowed: {list(TIME_GRAINS)}")
            time_col = m.time_dimension or (m.all_time_dimensions[0] if len(m.all_time_dimensions) == 1 else None)
            if not time_col:
                raise CompileError(f"{m.name} has no time_dimension configured")

        # Collect required LEFT JOIN steps in deterministic order
        needed_joins: list[tuple[str, str, str, str, str]] = []
        seen_aliases: set[str] = set()
        for ref in [*active_dims, *(f.field for f in fspecs)]:
            if ref in joins:
                for step in joins[ref].steps:
                    if step[3] not in seen_aliases:
                        seen_aliases.add(step[3])
                        needed_joins.append(step)

        def _col_ref(field_name: str) -> tuple[exp.Expression, str, bool]:
            if field_name in joins:
                js = joins[field_name]
                return exp.column(js.column, table=js.target_alias), js.output_alias, js.is_time
            ident(field_name)
            return exp.column(field_name, table=use_alias), field_name, (field_name in time_local)

        select_exprs: list[exp.Expression] = []
        group_exprs: list[exp.Expression] = []
        group_aliases: list[str] = []

        if time_grain is not None and time_col is not None:
            t_col = exp.column(time_col, table=use_alias)
            trunc = exp.Cast(
                this=exp.TimestampTrunc(
                    this=exp.Cast(this=t_col, to=exp.DataType.build("DATE")),
                    unit=exp.Literal.string(time_grain),
                ),
                to=exp.DataType.build("DATE"),
            )
            select_exprs.append(exp.alias_(trunc, "period"))
            group_exprs.append(trunc.copy())
            group_aliases.append("period")

        for d in active_dims:
            c_expr, out_alias, _ = _col_ref(d)
            if out_alias != d or use_alias:
                select_exprs.append(exp.alias_(c_expr, out_alias))
            else:
                select_exprs.append(c_expr)
            group_exprs.append(c_expr.copy())
            group_aliases.append(out_alias)

        select_exprs.append(exp.alias_(_agg_ast(m, use_alias), m.name))

        from_tbl = (
            exp.Table(this=exp.to_identifier(m.table), alias=exp.TableAlias(this=exp.to_identifier(use_alias)))
            if use_alias
            else exp.Table(this=exp.to_identifier(m.table))
        )
        query = exp.select(*select_exprs).from_(from_tbl)

        for l_alias, l_key, r_table, r_alias, r_key in needed_joins:
            on_cond = exp.EQ(
                this=exp.column(l_key, table=l_alias),
                expression=exp.column(r_key, table=r_alias),
            )
            r_tbl = exp.Table(
                this=exp.to_identifier(r_table),
                alias=exp.TableAlias(this=exp.to_identifier(r_alias)),
            )
            query = query.join(r_tbl, on=on_cond, join_type="LEFT")

        where_parts: list[exp.Expression] = [_qualify_condition(f, use_alias) for f in m.filters]

        if time_range is not None and time_col is not None:
            start_d, end_d = resolve_time_range(time_range, clock=clock, timezone=timezone)
            t_ref = exp.column(time_col, table=use_alias)
            params.extend([start_d, end_d])
            where_parts.append(exp.GTE(this=t_ref.copy(), expression=exp.Placeholder()))
            where_parts.append(exp.LT(this=t_ref.copy(), expression=exp.Placeholder()))

        for f in fspecs:
            c_expr, _, is_t = _col_ref(f.field)
            where_parts.append(_filter_ast(c_expr, f, is_t, params, max_filter_list))

        if row_filter:
            where_parts.append(_qualify_condition(row_filter, use_alias))

        if entity_keys is not None:
            if not m.entity_key:
                raise CompileError(f"{m.name} is not linked to an entity")
            keys = list(entity_keys)
            ek_col = exp.column(m.entity_key, table=use_alias)
            if self.dialect == "bigquery":
                if not keys:
                    where_parts.append(exp.Boolean(this=False))
                else:
                    params.append(keys)
                    where_parts.append(exp.In(this=ek_col, field=exp.Unnest(expressions=[exp.Placeholder()])))
            else:
                params.append(keys)
                where_parts.append(exp.Anonymous(this="list_contains", expressions=[exp.Placeholder(), ek_col]))

        if where_parts:
            combined = where_parts[0]
            for w in where_parts[1:]:
                combined = exp.And(this=combined, expression=w)
            query = query.where(combined)

        if group_exprs:
            query = query.group_by(*group_exprs)
            if include_default_order:
                order_refs = [
                    exp.column(a) if (use_alias or a == "period") else g.copy()
                    for g, a in zip(group_exprs, group_aliases, strict=True)
                ]
                query = query.order_by(*order_refs)

        return query, group_aliases

    def _apply_order_and_limit(
        self,
        query: exp.Select,
        order_by,
        limit: int | None,
        valid_order_cols: set[str],
    ) -> exp.Select:
        parsed_order = normalise_order_by(order_by)
        if parsed_order:
            query.set("order", None)
            ordered_exprs = []
            for col, desc in parsed_order:
                if col not in valid_order_cols:
                    raise CompileError(
                        f"cannot order by {col!r}; allowed: {sorted(valid_order_cols)}"
                    )
                ordered_exprs.append(exp.Ordered(this=exp.column(col), desc=desc))
            query = query.order_by(*ordered_exprs)
        if limit is not None:
            if limit < 0:
                raise CompileError("limit must be >= 0")
            query = query.limit(int(limit))
        return query

    def compile(
        self,
        m: Metric,
        dims=(),
        filters=None,
        row_filter: str | None = None,
        entity_keys=None,
        *,
        time_grain: str | None = None,
        time_range=None,
        order_by=None,
        limit: int | None = None,
        joins: dict[str, JoinSpec] | None = None,
        max_filter_list: int = 500,
        clock: Callable[[], datetime.date] | None = None,
        timezone: str = "UTC",
    ) -> Compiled:
        dims_list = list(dims or [])
        fspecs = normalise_filters(filters)
        params: list = []
        joins_map = joins or {}
        has_custom_order = bool(order_by)
        query, group_aliases = self._build_simple_select(
            m=m,
            dims=dims_list,
            fspecs=fspecs,
            row_filter=row_filter,
            entity_keys=entity_keys,
            time_grain=time_grain,
            time_range=time_range,
            joins=joins_map,
            max_filter_list=max_filter_list,
            clock=clock,
            timezone=timezone,
            params=params,
            include_default_order=not has_custom_order,
        )
        valid_cols = set(group_aliases) | {m.name}
        query = self._apply_order_and_limit(query, order_by, limit, valid_cols)
        return Compiled(query.sql(dialect="duckdb").replace("LIST_CONTAINS(", "list_contains("), params)

    def compile_derived(
        self,
        m: Metric,
        base_metrics: dict[str, Metric],
        dims=(),
        filters=None,
        row_filters: dict[str, str | None] | None = None,
        entity_keys=None,
        *,
        time_grain: str | None = None,
        time_range=None,
        order_by=None,
        limit: int | None = None,
        joins_by_metric: dict[str, dict[str, JoinSpec]] | None = None,
        max_filter_list: int = 500,
        clock: Callable[[], datetime.date] | None = None,
        timezone: str = "UTC",
    ) -> Compiled:
        return self.compile_multi(
            metrics=[m],
            base_metrics=base_metrics,
            dims=dims,
            filters=filters,
            row_filters=row_filters,
            entity_keys=entity_keys,
            time_grain=time_grain,
            time_range=time_range,
            order_by=order_by,
            limit=limit,
            joins_by_metric=joins_by_metric,
            max_filter_list=max_filter_list,
            clock=clock,
            timezone=timezone,
        )

    def compile_multi(
        self,
        metrics: Sequence[Metric],
        base_metrics: dict[str, Metric] | None = None,
        dims=(),
        filters=None,
        row_filters: dict[str, str | None] | None = None,
        entity_keys=None,
        *,
        time_grain: str | None = None,
        time_range=None,
        order_by=None,
        limit: int | None = None,
        joins_by_metric: dict[str, dict[str, JoinSpec]] | None = None,
        max_filter_list: int = 500,
        clock: Callable[[], datetime.date] | None = None,
        timezone: str = "UTC",
    ) -> Compiled:
        """Compile one or more metrics (simple, ratio, derived) at a shared grain using per-leaf CTEs."""
        if not metrics:
            raise CompileError("at least one metric is required")
        catalog = {**(base_metrics or {}), **{m.name: m for m in metrics}}
        row_filters = row_filters or {}
        joins_by_metric = joins_by_metric or {}
        dims_list = list(dims or [])
        fspecs = normalise_filters(filters)

        # Collect all leaf simple metrics in deterministic DFS order
        leaves: list[Metric] = []
        leaf_seen: set[str] = set()

        def _collect(m: Metric, stack: tuple[str, ...] = ()) -> None:
            if m.name in stack:
                raise CompileError(f"dependency cycle detected: {' -> '.join([*stack, m.name])}")
            if m.metric_type == "simple":
                if m.name not in leaf_seen:
                    leaf_seen.add(m.name)
                    leaves.append(m)
                return
            for dep_name in m.dependencies:
                dep = catalog.get(dep_name)
                if dep is None:
                    raise CompileError(f"missing input metric {dep_name!r} for {m.name!r}")
                _collect(dep, (*stack, m.name))

        for m in metrics:
            _collect(m)

        params: list = []
        ctes: list[tuple[str, Metric, exp.Select, list[str]]] = []
        shared_group_aliases: list[str] | None = None

        for leaf in leaves:
            cte_alias = f"cte_{leaf.name}"
            sub_q, g_aliases = self._build_simple_select(
                m=leaf,
                dims=dims_list,
                fspecs=fspecs,
                row_filter=row_filters.get(leaf.name),
                entity_keys=entity_keys,
                time_grain=time_grain,
                time_range=time_range,
                joins=joins_by_metric.get(leaf.name, {}),
                max_filter_list=max_filter_list,
                clock=clock,
                timezone=timezone,
                params=params,
                include_default_order=False,
            )
            if shared_group_aliases is None:
                shared_group_aliases = g_aliases
            elif shared_group_aliases != g_aliases:
                raise CompileError(
                    f"grain mismatch across multi-metric CTEs: {shared_group_aliases} vs {g_aliases}"
                )
            ctes.append((cte_alias, leaf, sub_q, g_aliases))

        group_cols = shared_group_aliases or []

        # Map every metric (leaf or composite) to a sqlglot expression over the CTE columns
        expr_cache: dict[str, exp.Expression] = {}

        def _metric_expr(m: Metric) -> exp.Expression:
            if m.name in expr_cache:
                return expr_cache[m.name]
            if m.metric_type == "simple":
                col = exp.column(m.name, table=f"cte_{m.name}")
                if group_cols and m.agg in {"count", "count_distinct"}:
                    col = exp.Coalesce(this=col, expressions=[exp.Literal.number(0)])
                expr_cache[m.name] = col
                return col
            if m.metric_type == "ratio":
                num_m = catalog[m.numerator]  # type: ignore[index]
                den_m = catalog[m.denominator]  # type: ignore[index]
                num = exp.Cast(this=_metric_expr(num_m), to=exp.DataType.build("DOUBLE"))
                den = exp.Nullif(this=_metric_expr(den_m), expression=exp.Literal.number(0))
                res = exp.Div(this=num, expression=den)
                expr_cache[m.name] = res
                return res
            assert m.formula is not None
            dep_map = {d: _metric_expr(catalog[d]) for d in m.dependencies}
            res = _formula_sql_ast(m.formula, dep_map)
            expr_cache[m.name] = res
            return res

        outer_select: list[exp.Expression] = []
        for g in group_cols:
            if len(ctes) == 1:
                outer_select.append(exp.alias_(exp.column(g, table=ctes[0][0]), g))
            else:
                refs = [exp.column(g, table=c[0]) for c in ctes]
                outer_select.append(
                    exp.alias_(exp.Coalesce(this=refs[0], expressions=refs[1:]), g)
                )

        for m in metrics:
            outer_select.append(exp.alias_(_metric_expr(m), m.name))

        outer = exp.select(*outer_select).from_(exp.Table(this=exp.to_identifier(ctes[0][0])))
        for idx in range(1, len(ctes)):
            c_alias = ctes[idx][0]
            if not group_cols:
                outer = outer.join(exp.Table(this=exp.to_identifier(c_alias)), join_type="CROSS")
            else:
                conds = []
                for g in group_cols:
                    prev_refs = [exp.column(g, table=ctes[j][0]) for j in range(idx)]
                    lhs = (
                        prev_refs[0]
                        if len(prev_refs) == 1
                        else exp.Coalesce(this=prev_refs[0], expressions=prev_refs[1:])
                    )
                    rhs = exp.column(g, table=c_alias)
                    conds.append(exp.NullSafeEQ(this=lhs, expression=rhs))
                on_expr = conds[0]
                for extra in conds[1:]:
                    on_expr = exp.And(this=on_expr, expression=extra)
                outer = outer.join(
                    exp.Table(this=exp.to_identifier(c_alias)),
                    on=on_expr,
                    join_type="FULL OUTER",
                )

        for c_alias, _leaf, sub_q, _ga in ctes:
            outer = outer.with_(c_alias, as_=sub_q)

        if group_cols and not order_by:
            outer = outer.order_by(*[exp.column(g) for g in group_cols])

        valid_cols = set(group_cols) | {m.name for m in metrics}
        outer = self._apply_order_and_limit(outer, order_by, limit, valid_cols)
        return Compiled(outer.sql(dialect="duckdb").replace("LIST_CONTAINS(", "list_contains("), params)
