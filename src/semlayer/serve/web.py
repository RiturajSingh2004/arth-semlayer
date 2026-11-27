"""Registry JSON API + Ryoku-themed technical Governance & Review UI (FastAPI + HTMX, zero build)."""
from datetime import UTC, datetime
from html import escape as e

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from semlayer.compiler import SimpleCompiler
from semlayer.conflicts import apply_pick, apply_split, detect
from semlayer.core import KINDS, InvalidTransition
from semlayer.monitor import feedback_summary, read_usage
from semlayer.registry import Registry
from semlayer.warehouse.bigquery import render_sql

from .service import Refusal, Service

HTMX = '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'

STYLE = """<style>
:root {
  --bg-canvas: #0d0c0b;
  --bg-sidebar: #0a0908;
  --bg-surface: #141211;
  --bg-elevated: #1b1817;
  --bg-hover: #221e1c;
  --bg-accent-dim: #291913;
  --border: #221f1d;
  --border-strong: #302b28;
  --text-primary: #f5f4f2;
  --text-secondary: #a8a29e;
  --text-muted: #736c67;
  --accent: #e8652b;
  --accent-bright: #f2743d;
  --ok: #34d399;
  --ok-bg: rgba(52, 211, 153, 0.12);
  --warn: #fbbf24;
  --warn-bg: rgba(251, 191, 36, 0.12);
  --err: #f87171;
  --err-bg: rgba(248, 113, 113, 0.12);
  --sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", "Cascadia Code", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--bg-canvas); color: var(--text-primary); font-family: var(--sans); font-size: 14px; line-height: 1.55; }
body { margin: 0; background: var(--bg-canvas); color: var(--text-primary); }
a { color: inherit; text-decoration: none; }
code, pre, .mono { font-family: var(--mono); font-size: 12.5px; }
pre { margin: 6px 0 0; padding: 10px 12px; background: #090808; border: 1px solid var(--border); border-radius: 6px; overflow-x: auto; color: #e7e5e4; line-height: 1.45; }

/* Topbar */
.topbar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 28px; height: 60px; background: rgba(13, 12, 11, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); }
.brand-wrap { display: flex; align-items: center; gap: 28px; height: 100%; }
.brand { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; color: var(--text-primary); display: flex; align-items: center; gap: 10px; }
.brand-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); }
.top-tab { display: inline-flex; align-items: center; height: 100%; font-size: 13px; font-weight: 600; color: var(--text-primary); border-bottom: 2px solid var(--accent); padding: 0 2px; }
.top-meta { display: flex; align-items: center; gap: 12px; }
.search-pill { display: flex; align-items: center; gap: 10px; background: var(--bg-elevated); border: 1px solid var(--border-strong); border-radius: 999px; padding: 7px 14px; width: 340px; max-width: 42vw; transition: border-color 0.15s; }
.search-pill:focus-within { border-color: var(--accent); }
.search-pill input { flex: 1; background: transparent; border: none; outline: none; color: var(--text-primary); font-size: 13px; font-family: var(--sans); }
.search-pill input::placeholder { color: var(--text-muted); }
.kbd { font-family: var(--mono); font-size: 11px; color: var(--text-secondary); background: #100e0d; border: 1px solid var(--border-strong); border-radius: 4px; padding: 1px 6px; }

/* Shell */
.shell { display: grid; grid-template-columns: 260px minmax(0, 1fr); min-height: calc(100vh - 60px); }
.sidebar { background: var(--bg-sidebar); border-right: 1px solid var(--border); padding: 22px 16px 40px; position: sticky; top: 60px; height: calc(100vh - 60px); overflow-y: auto; }
.nav-group { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.nav-group:last-child { border-bottom: none; }
.nav-title { font-size: 11.5px; font-weight: 700; color: var(--text-primary); letter-spacing: 0.02em; margin: 0 10px 8px; }
.nav-link { display: flex; align-items: center; justify-content: space-between; padding: 7px 11px; border-radius: 7px; color: var(--text-secondary); font-size: 13px; margin-bottom: 2px; transition: background 0.12s, color 0.12s; }
.nav-link:hover { background: var(--bg-surface); color: var(--text-primary); }
.nav-link.active { background: var(--bg-accent-dim); color: var(--accent-bright); font-weight: 500; }
.nav-count { font-family: var(--mono); font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--bg-elevated); color: var(--text-secondary); border: 1px solid var(--border); }
.nav-link.active .nav-count { background: rgba(232, 101, 43, 0.2); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.35); }

/* Main content */
.content { padding: 34px 44px 72px; max-width: 1280px; }
.eyebrow { color: var(--accent); font-size: 12.5px; font-weight: 600; letter-spacing: 0.01em; margin-bottom: 6px; }
h1 { font-size: 30px; font-weight: 700; letter-spacing: -0.025em; line-height: 1.2; margin: 0 0 10px; color: var(--text-primary); }
.lead { font-size: 15px; color: var(--text-secondary); max-width: 820px; margin: 0 0 24px; line-height: 1.6; }
h2 { font-size: 20px; font-weight: 700; letter-spacing: -0.015em; margin: 38px 0 6px; color: var(--text-primary); display: flex; align-items: center; gap: 10px; }
.sec-sub { color: var(--text-secondary); font-size: 13.5px; margin: 0 0 16px; }

/* Stat strip */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); gap: 12px; margin-bottom: 28px; }
.stat { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 9px; padding: 13px 15px; }
.stat-k { font-size: 11.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.stat-v { font-size: 22px; font-weight: 700; color: var(--text-primary); margin-top: 3px; font-family: var(--mono); }
.stat-v.accent { color: var(--accent-bright); }
.stat-sub { font-size: 11.5px; color: var(--text-secondary); margin-top: 2px; }

/* Badges & Pills */
.badge { display: inline-flex; align-items: center; gap: 5px; font-family: var(--mono); font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 5px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); white-space: nowrap; }
.badge-orange { background: var(--bg-accent-dim); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.35); }
.badge-ok { background: var(--ok-bg); color: var(--ok); border-color: rgba(52, 211, 153, 0.3); }
.badge-warn { background: var(--warn-bg); color: var(--warn); border-color: rgba(251, 191, 36, 0.3); }
.badge-err { background: var(--err-bg); color: var(--err); border-color: rgba(248, 113, 113, 0.3); }

/* Tables */
.table-wrap { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th { font-size: 12px; font-weight: 600; color: var(--text-primary); padding: 11px 14px; border-bottom: 1px solid var(--border-strong); background: #110f0e; white-space: nowrap; }
td { padding: 13px 14px; border-bottom: 1px solid var(--border); vertical-align: top; color: var(--text-secondary); font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(255, 255, 255, 0.015); }
.def-name { color: var(--text-primary); font-weight: 600; font-size: 13.5px; }
.sql-chip { display: inline-block; padding: 3px 7px; background: #090808; border: 1px solid var(--border-strong); border-radius: 5px; color: #f3f4f6; font-family: var(--mono); font-size: 12px; word-break: break-word; }
.desc-text { color: var(--text-primary); margin-top: 6px; line-height: 1.45; }
.meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; font-size: 11.5px; color: var(--text-muted); }

/* Details & Diffs */
details { margin-top: 4px; }
summary { cursor: pointer; color: var(--accent-bright); font-size: 12px; font-weight: 500; user-select: none; }
summary:hover { text-decoration: underline; }
.diff-add { color: var(--ok); }
.diff-del { color: var(--err); }

/* Forms, Inputs & Buttons */
input, select { background: #0b0a09; border: 1px solid var(--border-strong); color: var(--text-primary); border-radius: 6px; padding: 6px 9px; font-size: 12.5px; font-family: var(--sans); outline: none; }
input:focus, select:focus { border-color: var(--accent); }
button { cursor: pointer; font-family: var(--sans); font-size: 12.5px; font-weight: 600; border-radius: 6px; padding: 6px 11px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary); transition: background 0.12s, border-color 0.12s; }
button:hover { background: var(--bg-hover); border-color: #423b37; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #0d0c0b; font-weight: 700; }
.btn-primary:hover { background: var(--accent-bright); border-color: var(--accent-bright); }
.btn-danger { background: transparent; border-color: rgba(248, 113, 113, 0.35); color: var(--err); }
.btn-danger:hover { background: var(--err-bg); border-color: var(--err); }
.act-stack { display: flex; flex-direction: column; gap: 6px; min-width: 195px; }
.act-inline { display: flex; gap: 6px; align-items: center; }
.act-inline input { width: 115px; flex: 1; }
.err-msg { display: block; margin-top: 4px; color: var(--err); font-size: 12px; font-weight: 600; }

/* Cards & Grids */
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; }
.card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.card-title { font-weight: 700; color: var(--text-primary); font-size: 14.5px; }
.split-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0; }
.playground-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; }
.form-row { display: grid; grid-template-columns: 110px 1fr; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12.5px; color: var(--text-secondary); }
.empty-note { color: var(--text-muted); font-style: italic; padding: 6px 0; }

@media (max-width: 960px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  .content { padding: 22px 18px 48px; }
}
</style>"""

SCRIPT = """<script>
document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("global-search");
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      if (input) { input.focus(); input.select(); }
    }
  });
  if (input) {
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll("[data-filterable]").forEach((el) => {
        el.style.display = (!q || el.textContent.toLowerCase().includes(q)) ? "" : "none";
      });
    });
  }
  const links = document.querySelectorAll(".nav-link");
  links.forEach((lnk) => {
    lnk.addEventListener("click", () => {
      links.forEach((x) => x.classList.remove("active"));
      lnk.classList.add("active");
    });
  });
});
</script>"""


def _summary(n) -> str:
    if n.kind == "metric":
        w = f" WHERE {' AND '.join(n.filters)}" if n.filters else ""
        return f"{n.expression} FROM {n.table}{w}"
    if n.kind == "entity":
        return f"TABLE {n.table} (PK: {n.key})"
    if n.kind == "edge":
        return f"{n.src}.{n.src_key} -[{n.rel}]-> {n.dst}.{n.dst_key}"
    if n.kind == "rule":
        rf = f" WHERE {n.row_filter}" if n.row_filter else ""
        dd = f" deny_dims={n.deny_dims}" if n.deny_dims else ""
        return f"{n.effect.upper()} role={n.role} metric={n.metric}{rf}{dd}"
    return n.model_dump_json()


def _format_diff(diff_text: str) -> str:
    if not diff_text:
        return ""
    lines = []
    for ln in diff_text.splitlines():
        esc = e(ln)
        if ln.startswith("+") and not ln.startswith("+++"):
            lines.append(f'<span class="diff-add">{esc}</span>')
        elif ln.startswith("-") and not ln.startswith("---"):
            lines.append(f'<span class="diff-del">{esc}</span>')
        else:
            lines.append(esc)
    return f"<pre>{chr(10).join(lines)}</pre>"


def _row(n, msg: str = "", diff_text: str = "") -> str:
    ev, tag = n.evidence, "revision" if n.version else "draft"
    tag_cls = "badge-warn" if n.version else "badge-orange"
    act = f"/certify/{e(n.kind)}/{e(n.name)}"
    rej = f"/reject/{e(n.kind)}/{e(n.name)}"
    desc = f'<div class="desc-text"><i>{e(n.description)}</i></div>' if n.description else ""
    expl = f'<div class="meta-row"><span>{e(ev.explanation)}</span></div>' if ev.explanation else ""
    meta_bits = []
    if n.kind == "metric":
        if n.dimensions:
            meta_bits.append(f"dims: {', '.join(n.dimensions)}")
        if n.entity:
            meta_bits.append(f"entity: {n.entity} ({n.entity_key})")
    if ev.source:
        meta_bits.append(f"source: {ev.source}")
    if ev.users:
        meta_bits.append(f"users: {', '.join(ev.users)}")
    meta_html = f'<div class="meta-row">{"".join(f"<span class=badge>{e(b)}</span>" for b in meta_bits)}</div>' if meta_bits else ""
    q_block = f"<pre>{e(chr(10).join(ev.queries))}</pre>" if ev.queries else ""
    lin = e(", ".join(ev.lineage)) if ev.lineage else "-"
    diff_html = _format_diff(diff_text)
    err_html = f'<strong class="err-msg">{e(msg)}</strong>' if msg else ""
    ver_note = f" v{n.version}→v{n.version + 1}" if n.version else ""
    return (
        f'<tr id="{e(n.kind)}-{e(n.name)}" data-filterable>'
        f'<td><span class="badge">{e(n.kind)}</span> <b class="def-name">{e(n.name)}</b> '
        f'<span class="badge {tag_cls}">({tag}){e(ver_note)}</span></td>'
        f'<td><code class="sql-chip">{e(_summary(n))}</code>{desc}{expl}{meta_html}</td>'
        f'<td class="mono">{ev.usage}</td>'
        f'<td><details><summary>inspect lineage &amp; SQL ({len(ev.queries)})</summary>'
        f'{q_block}<div class="meta-row"><span>lineage: <code>{lin}</code></span></div>{diff_html}</details></td>'
        f'<td><div class="act-stack">'
        f'<form class="act-inline" method="post" action="{act}" hx-post="{act}" '
        f'hx-target="closest tr" hx-swap="outerHTML">'
        f'<input name="owner" placeholder="owner (required)" value="{e(n.owner or ev.suggested_owner or "")}">'
        f'<button class="btn-primary" type="submit">certify</button></form>'
        f'<form method="post" action="{rej}" hx-post="{rej}" hx-target="closest tr" hx-swap="outerHTML">'
        f'<button class="btn-danger" type="submit">reject</button></form>'
        f"{err_html}</div></td></tr>"
    )


def _conflict_card(c) -> str:
    variants_str = ", ".join(c.variants)
    diffs_html = "".join(f"<div><code>{e(d)}</code></div>" for d in c.diffs)
    picks = "".join(
        f'<form method="post" action="/conflicts/pick" style="display:inline-block;margin-right:6px;">'
        f'<input type="hidden" name="winner" value="{e(v)}">'
        f'<button type="submit">pick <code>{e(v)}</code> ({c.usage.get(v, 0)})</button></form>'
        for v in c.variants
    )
    split_html = ""
    if c.kind == "variant":
        inputs = "".join(
            f'<div class="act-inline"><code style="min-width:110px">{e(v)}</code>'
            f'<input name="rename_{e(v)}" value="{e(v)}" placeholder="distinct_name"></div>'
            for v in c.variants
        )
        split_html = (
            f'<form method="post" action="/conflicts/split" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">'
            f'<input type="hidden" name="concept" value="{e(c.concept)}">'
            f'<div class="meta-row" style="margin-bottom:6px"><span>Or split variants into distinct certified concepts:</span></div>'
            f'<div class="split-grid">{inputs}</div>'
            f'<button class="btn-primary" type="submit">split variants</button></form>'
        )
    return (
        f'<div class="card" data-filterable>'
        f'<div class="card-head"><span class="card-title"><b>{e(c.concept)}</b> ({e(c.kind)}): '
        f'{e(variants_str)}</span><span class="badge badge-warn">{e(c.kind)}</span></div>'
        f'<div style="margin-bottom:10px">{diffs_html}</div>'
        f"<div>{picks}</div>{split_html}</div>"
    )


def _compiled_preview(m, dialect: str, project: str = "geminisprints26blr-3913",
                      dataset: str = "jaffle_shop") -> tuple[str, str]:
    duck_c = SimpleCompiler(dialect="duckdb").compile(m, m.dimensions[:1])
    bq_c = SimpleCompiler(dialect="bigquery").compile(m, m.dimensions[:1])
    try:
        bq_sql = render_sql(bq_c.sql, bq_c.params, project, dataset)
    except Exception:
        bq_sql = bq_c.sql
    return duck_c.sql, bq_sql


def _render_query_result(res: dict | None, err: str = "") -> str:
    if err:
        return f'<div class="card" style="border-color:rgba(248,113,113,0.4)"><span class="badge badge-err">REFUSED</span> <strong style="color:var(--err);margin-left:6px">{e(err)}</strong></div>'
    if not res:
        return ""
    if "keys" in res:
        preview = ", ".join(map(str, res["keys"][:40])) + (" ..." if len(res["keys"]) > 40 else "")
        return (
            f'<div class="card"><div class="card-head"><span class="card-title">Graph traversal · '
            f'<code>{e(res["entity"])}</code></span><span class="badge badge-ok">{res["count"]} keys</span></div>'
            f'<pre>[{e(preview)}]</pre></div>'
        )
    cols_th = "".join(f"<th>{e(str(c))}</th>" for c in res["columns"])
    rows_tr = "".join(
        "<tr>" + "".join(f'<td class="mono">{e(str(v))}</td>' for v in r) + "</tr>"
        for r in res["rows"][:25]
    ) or f'<tr><td colspan="{max(1, len(res["columns"]))}"><i>0 rows</i></td></tr>'
    return (
        f'<div class="card"><div class="card-head">'
        f'<span class="card-title"><code>{e(res["metric"])}</code> '
        f'<span class="badge badge-ok">v{res["version"]}</span> '
        f'<span class="badge">owner: {e(res["owner"] or "-")}</span></span>'
        f'<span class="mono" style="color:var(--text-muted)">{res["row_count"]} row(s)</span></div>'
        f'<pre>{e(res["sql"])}</pre>'
        f'<div class="table-wrap" style="margin-top:10px"><table><tr>{cols_th}</tr>{rows_tr}</table></div></div>'
    )


def create_app(reg: Registry, svc: Service) -> FastAPI:
    app = FastAPI(title="semlayer")

    @app.get("/api/metrics")
    def metrics():
        return svc.list_metrics()

    @app.get("/api/queue")
    def queue():
        return [{"kind": n.kind, "name": n.name, "usage": n.evidence.usage,
                 "revision": bool(n.version)} for k in ("metric", "entity", "edge", "rule")
                for n in reg.queue(k)]

    @app.post("/api/certify/{kind}/{name}")
    def api_certify(kind: str, name: str, owner: str = ""):
        if kind not in KINDS:
            return JSONResponse({"error": f"unknown kind: {kind}"}, status_code=400)
        try:
            n = reg.certify(kind, name, owner=owner or None, by="api")
        except (InvalidTransition, KeyError, ValueError) as ex:
            return JSONResponse({"error": str(ex)}, status_code=400)
        return {"name": n.name, "version": n.version, "state": n.state.value}

    @app.get("/", response_class=HTMLResponse)
    def index():
        q_items = [(k, n) for k in ("metric", "entity", "edge", "rule") for n in reg.queue(k)]
        rows = "".join(
            _row(n, diff_text=reg.diff(k, n.name) if n.version else "")
            for k, n in q_items
        )
        empty = '<tr><td colspan=5><i>No pending drafts or revisions in queue.</i></td></tr>'

        detected = detect(reg.list("metric"))
        conflicts_html = (
            f'<div class="card-grid">{"".join(_conflict_card(c) for c in detected)}</div>'
            if detected else '<p class="empty-note">None — no variant or duplicate metric logic detected.</p>'
        )

        cert_metrics = reg.certified("metric")
        cert_entities = reg.certified("entity")
        cert_edges = reg.certified("edge")
        cert_rules = reg.certified("rule")
        wh_dialect = getattr(svc.wh, "dialect", "duckdb")
        bq_proj = getattr(svc.wh, "project", "geminisprints26blr-3913")
        bq_ds = getattr(svc.wh, "dataset", "jaffle_shop")

        cert_rows = []
        for m in cert_metrics:
            duck_sql, bq_sql = _compiled_preview(m, wh_dialect, bq_proj, bq_ds)
            cert_rows.append(
                f'<tr data-filterable>'
                f'<td><b class="def-name">{e(m.name)}</b> <span class="badge badge-ok">v{m.version}</span>'
                f'<div class="meta-row"><span>owner: <code>{e(m.owner or "-")}</code></span></div></td>'
                f'<td><code class="sql-chip">{e(_summary(m))}</code>'
                f'<div class="desc-text">{e(m.description or "(no description)")}</div></td>'
                f'<td><code>{e(", ".join(m.dimensions) or "-")}</code>'
                f'<div class="meta-row"><span>entity: <code>{e(m.entity or "-")}</code></span></div></td>'
                f'<td><details><summary>compiled SQL (DuckDB &amp; BigQuery)</summary>'
                f'<div class="meta-row"><span>DuckDB (parameterised):</span></div><pre>{e(duck_sql)}</pre>'
                f'<div class="meta-row"><span>BigQuery MCP (sqlglot AST):</span></div><pre>{e(bq_sql)}</pre>'
                f'<div class="meta-row"><span>lineage: <code>{e(", ".join(m.evidence.lineage) or "-")}</code></span></div>'
                f"</details></td></tr>"
            )
        cert_section = (
            f'<div class="table-wrap"><table><tr><th>metric</th><th>expression &amp; description</th>'
            f'<th>dimensions / entity</th><th>dialect SQL &amp; lineage</th></tr>{"".join(cert_rows)}</table></div>'
            if cert_rows else '<p class="empty-note">None certified yet</p>'
        )

        ent_rows = "".join(
            f'<tr data-filterable><td><span class="badge">entity</span> <b class="def-name">{e(en.name)}</b> '
            f'<span class="badge badge-ok">v{en.version}</span></td>'
            f'<td><code>{e(en.table)}</code></td><td><code>{e(en.key)}</code></td>'
            f'<td><code>{e(en.owner or "-")}</code></td></tr>'
            for en in cert_entities
        )
        edge_rows = "".join(
            f'<tr data-filterable><td><span class="badge badge-orange">{e(ed.rel)}</span></td>'
            f'<td><code>{e(ed.src)}.{e(ed.src_key)}</code> → <code>{e(ed.dst)}.{e(ed.dst_key)}</code></td>'
            f'<td><code>{e(", ".join(ed.evidence.lineage) or "-")}</code></td>'
            f'<td><span class="badge badge-ok">v{ed.version}</span></td></tr>'
            for ed in cert_edges
        )

        rule_rows = "".join(
            f'<tr data-filterable><td><b class="def-name">{e(r.name)}</b> <span class="badge badge-ok">v{r.version}</span></td>'
            f'<td><code>{e(r.role)}</code></td><td><code>{e(r.metric)}</code></td>'
            f'<td><span class="badge {"badge-ok" if r.effect == "allow" else "badge-err"}">{e(r.effect.upper())}</span></td>'
            f'<td><code>{e(r.row_filter or "-")}</code></td>'
            f'<td><code>{e(", ".join(r.deny_dims) or "-")}</code></td></tr>'
            for r in cert_rules
        ) or '<tr><td colspan=6><i>No certified rules — default deny blocks all queries.</i></td></tr>'

        fb = feedback_summary(read_usage(reg), min_volume=1)
        demand_str = ", ".join(f"{m} ({n})" for m, n in fb["demand"][:6]) or "none yet"
        gaps_str = ", ".join(f'"{g}" ({n})' for g, n in fb["missing"][:5]) or "none reported"
        flagged_str = ", ".join(fb["flagged"]) or "none"

        audits = list(reversed(reg.audit()[-12:]))
        audit_rows = "".join(
            f'<tr data-filterable>'
            f'<td class="mono">{datetime.fromtimestamp(a["ts"], UTC).strftime("%Y-%m-%d %H:%M:%S")} UTC</td>'
            f'<td><span class="badge">{e(a["action"])}</span></td>'
            f'<td><code>{e(a["kind"])}/{e(a["name"])}</code></td>'
            f'<td><code>{e(a.get("by", "-"))}</code></td>'
            f'<td class="mono">{e(str(a.get("version", "-")))} (edit_ratio={e(str(a.get("edit_ratio", "-")))})</td></tr>'
            for a in audits
        ) or '<tr><td colspan=5><i>No audit events recorded yet.</i></td></tr>'

        metric_opts = "".join(f'<option value="{e(m.name)}">{e(m.name)}</option>' for m in cert_metrics)
        entity_opts = "".join(f'<option value="{e(en.name)}">{e(en.name)}</option>' for en in cert_entities)
        known_roles = sorted({r.role for r in cert_rules} | {"finance", "eu_analyst", "guest"})
        role_opts = "".join(f'<option value="{e(rl)}">{e(rl)}</option>' for rl in known_roles)

        wh_badge = "BigQuery MCP (HTTPS)" if wh_dialect == "bigquery" else "DuckDB (Local Read-Only)"

        return (
            f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>ARTH: SemLayer review</title>{STYLE}{HTMX}{SCRIPT}</head><body>"
            f"<header class='topbar'>"
            f"<div class='brand-wrap'>"
            f"<a href='#' class='brand'><span class='brand-dot'></span>ARTH · SemLayer</a>"
            f"<span class='top-tab'>Governance Console</span>"
            f"</div>"
            f"<div class='top-meta'>"
            f"<div class='search-pill'><span style='color:var(--text-muted)'>⌕</span>"
            f"<input id='global-search' type='search' placeholder='Filter metrics, SQL, lineage, rules...'>"
            f"<span class='kbd'>Ctrl K</span></div>"
            f"<span class='badge badge-orange'>{e(wh_badge)}</span>"
            f"</div></header>"
            f"<div class='shell'>"
            f"<aside class='sidebar'>"
            f"<div class='nav-group'><div class='nav-title'>Governance</div>"
            f"<a class='nav-link active' href='#sec-queue'><span>Review queue</span><span class='nav-count'>{len(q_items)}</span></a>"
            f"<a class='nav-link' href='#sec-conflicts'><span>Conflicts (Split / Pick)</span><span class='nav-count'>{len(detected)}</span></a>"
            f"<a class='nav-link' href='#sec-feedback'><span>Drift &amp; agent signals</span></a></div>"
            f"<div class='nav-group'><div class='nav-title'>Certified Catalog</div>"
            f"<a class='nav-link' href='#sec-metrics'><span>Certified metrics</span><span class='nav-count'>{len(cert_metrics)}</span></a>"
            f"<a class='nav-link' href='#sec-graph'><span>Entity graph &amp; edges</span><span class='nav-count'>{len(cert_entities) + len(cert_edges)}</span></a>"
            f"<a class='nav-link' href='#sec-policy'><span>RBAC &amp; row-filter rules</span><span class='nav-count'>{len(cert_rules)}</span></a></div>"
            f"<div class='nav-group'><div class='nav-title'>Verification &amp; Ops</div>"
            f"<a class='nav-link' href='#sec-playground'><span>Governed query runner</span></a>"
            f"<a class='nav-link' href='#sec-audit'><span>Audit log (JSONL)</span><span class='nav-count'>{len(audits)}</span></a>"
            f"<a class='nav-link' href='#sec-mcp'><span>MCP &amp; REST surface</span></a></div>"
            f"</aside>"
            f"<main class='content'>"
            f"<div class='eyebrow'>Certification-Gated Semantic Layer</div>"
            f"<h1>ARTH: SemLayer · Review queue</h1>"
            f"<p class='lead'>LLMs and query-log statistics propose candidate definitions; humans certify ownership, "
            f"filters, and RBAC rules here before any metric, entity edge, or policy is exposed to agents over MCP.</p>"
            f"<div class='stats'>"
            f"<div class='stat'><div class='stat-k'>Pending review</div><div class='stat-v accent'>{len(q_items)}</div><div class='stat-sub'>drafts &amp; drift revisions</div></div>"
            f"<div class='stat'><div class='stat-k'>Open conflicts</div><div class='stat-v'>{len(detected)}</div><div class='stat-sub'>variants &amp; duplicates</div></div>"
            f"<div class='stat'><div class='stat-k'>Certified metrics</div><div class='stat-v'>{len(cert_metrics)}</div><div class='stat-sub'>served over MCP</div></div>"
            f"<div class='stat'><div class='stat-k'>Graph topology</div><div class='stat-v'>{len(cert_entities)} / {len(cert_edges)}</div><div class='stat-sub'>entities / FK edges</div></div>"
            f"<div class='stat'><div class='stat-k'>Active rules</div><div class='stat-v'>{len(cert_rules)}</div><div class='stat-sub'>default-deny policy</div></div>"
            f"<div class='stat'><div class='stat-k'>Warehouse dialect</div><div class='stat-v'>{e(wh_dialect)}</div><div class='stat-sub'>execute_sql_readonly</div></div>"
            f"</div>"
            f"<section id='sec-queue'>"
            f"<div class='table-wrap'><table><tr><th>definition</th><th>logic &amp; description</th><th>usage</th>"
            f"<th>evidence &amp; diff</th><th>action</th></tr>{rows or empty}</table></div></section>"
            f"<section id='sec-conflicts'><h2>Conflicts</h2>"
            f"<p class='sec-sub'>Structural variant clusters (same concept name, different SQL filter/agg) and duplicate logic under distinct aliases.</p>"
            f"{conflicts_html}</section>"
            f"<section id='sec-metrics'><h2>Certified metrics</h2>"
            f"<p class='sec-sub'>Live governed definitions exposed to agents via <code>list_metrics</code>, <code>describe_metric</code>, and <code>query_metric</code>.</p>"
            f"{cert_section}</section>"
            f"<section id='sec-graph'><h2>Entity graph &amp; foreign-key edges</h2>"
            f"<p class='sec-sub'>Certified join topology powering multi-hop <code>describe_graph</code> → <code>traverse_graph</code> → <code>entity_keys</code> queries.</p>"
            f"<div class='card-grid'>"
            f"<div class='table-wrap'><table><tr><th>entity</th><th>table</th><th>PK</th><th>owner</th></tr>"
            f"{ent_rows or '<tr><td colspan=4><i>No certified entities</i></td></tr>'}</table></div>"
            f"<div class='table-wrap'><table><tr><th>relationship</th><th>join condition</th><th>lineage</th><th>ver</th></tr>"
            f"{edge_rows or '<tr><td colspan=4><i>No certified edges</i></td></tr>'}</table></div>"
            f"</div></section>"
            f"<section id='sec-policy'><h2>Access policy rules (default deny)</h2>"
            f"<p class='sec-sub'>Only certified rules apply; explicit <code>DENY</code> overrides <code>ALLOW</code>; restricted dimensions cannot be grouped or filtered.</p>"
            f"<div class='table-wrap'><table><tr><th>rule</th><th>role</th><th>metric</th><th>effect</th><th>row_filter</th><th>deny_dims</th></tr>"
            f"{rule_rows}</table></div></section>"
            f"<section id='sec-playground'><h2>Interactive governed query &amp; graph runner</h2>"
            f"<p class='sec-sub'>Test policy enforcement, row filters, and compiled SQL through <code>Service</code> (never raw SQL).</p>"
            f"<div class='playground-grid'>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Run <code>query_metric</code></span><span class='badge badge-orange'>MCP tool</span></div>"
            f"<form hx-post='/ui/query' hx-target='#runner-output' hx-swap='innerHTML' method='post' action='/ui/query'>"
            f"<div class='form-row'><label>Metric</label><select name='metric'>{metric_opts or '<option value=\"\">(certify a metric first)</option>'}</select></div>"
            f"<div class='form-row'><label>As role</label><select name='as_role'>{role_opts}</select></div>"
            f"<div class='form-row'><label>Dimensions</label><input name='dims' placeholder='e.g. method, region'></div>"
            f"<div class='form-row'><label>Filters</label><input name='filters' placeholder='e.g. method=card, status=completed'></div>"
            f"<div class='form-row'><label>Entity keys</label><input name='entity_keys' placeholder='optional, e.g. 4, 5, 6'></div>"
            f"<button class='btn-primary' type='submit'>execute governed metric</button></form></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Run <code>traverse_graph</code></span><span class='badge badge-orange'>MCP tool</span></div>"
            f"<form hx-post='/ui/traverse' hx-target='#runner-output' hx-swap='innerHTML' method='post' action='/ui/traverse'>"
            f"<div class='form-row'><label>Start entity</label><select name='start'>{entity_opts or '<option value=\"Customer\">Customer</option>'}</select></div>"
            f"<div class='form-row'><label>Rel path</label><input name='path' placeholder='e.g. HAS_ORDER'></div>"
            f"<div class='form-row'><label>As role</label><select name='as_role'>{role_opts}</select></div>"
            f"<div class='form-row'><label>Entity filter</label><input name='filters' placeholder='e.g. Order.status=returned'></div>"
            f"<button class='btn-primary' type='submit'>traverse entity keys</button></form></div>"
            f"</div><div id='runner-output' style='margin-top:14px'></div></section>"
            f"<section id='sec-feedback'><h2>Drift &amp; agent feedback signals</h2>"
            f"<div class='card-grid'>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Top metric demand</span><span class='badge'>usage.jsonl</span></div><code>{e(demand_str)}</code></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Clustered coverage gaps</span><span class='badge badge-warn'>report_gap</span></div><code>{e(gaps_str)}</code></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Flagged metrics (&gt;20% error/issue)</span><span class='badge badge-err'>health</span></div><code>{e(flagged_str)}</code></div>"
            f"</div></section>"
            f"<section id='sec-audit'><h2>Audit trail</h2>"
            f"<p class='sec-sub'>Append-only governance log from <code>definitions/_state/audit.jsonl</code> tracking reviewer identity, version bumps, and edit ratios.</p>"
            f"<div class='table-wrap'><table><tr><th>timestamp</th><th>action</th><th>target</th><th>actor</th><th>version &amp; edit ratio</th></tr>"
            f"{audit_rows}</table></div></section>"
            f"<section id='sec-mcp'><h2>Agent MCP &amp; HTTP API reference</h2>"
            f"<div class='table-wrap'><table><tr><th>Surface</th><th>Tools / Endpoints</th></tr>"
            f"<tr><td><b class='def-name'>MCP Server (stdio)</b></td><td><code>list_metrics</code> · <code>describe_metric</code> · <code>query_metric</code> · <code>describe_graph</code> · <code>traverse_graph</code> · <code>report_gap</code> · <code>report_issue</code> (no raw SQL tool)</td></tr>"
            f"<tr><td><b class='def-name'>JSON REST API</b></td><td><code>GET /api/metrics</code> · <code>GET /api/queue</code> · <code>POST /api/certify/{{kind}}/{{name}}?owner=...</code></td></tr>"
            f"<tr><td><b class='def-name'>Warehouse Backend</b></td><td><code>DuckDBWarehouse</code> (bound parameters) · <code>BigQueryMCP</code> (<code>https://bigquery.googleapis.com/mcp</code> → <code>execute_sql_readonly</code> + <code>dryRun</code> + <code>sqlglot</code> AST literals)</td></tr>"
            f"</table></div></section>"
            f"</main></div></body></html>"
        )

    @app.post("/certify/{kind}/{name}", response_class=HTMLResponse)
    def certify(kind: str, name: str, owner: str = Form(""), hx_request: str | None = Header(None)):
        if kind not in KINDS:
            return HTMLResponse(f"unknown kind: {e(kind)}", status_code=400)
        try:
            reg.certify(kind, name, owner=owner.strip() or None, by="ui")
        except (InvalidTransition, KeyError, ValueError) as ex:
            n = reg.get(kind, name, pending=bool(reg.get(kind, name, pending=True))) or reg.get(kind, name)
            if hx_request and n is not None:
                diff_text = reg.diff(kind, name) if n.version else ""
                return _row(n, str(ex), diff_text=diff_text)
            code = 404 if isinstance(ex, KeyError) else 400
            if hx_request:
                return HTMLResponse(
                    f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>{e(str(ex))}</td></tr>',
                    status_code=code,
                )
            return HTMLResponse(e(str(ex)), status_code=code)
        if hx_request:
            return (f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>'
                    f'<span class="badge badge-ok">certified {e(name)}</span></td></tr>')
        return RedirectResponse("/", 303)

    @app.post("/reject/{kind}/{name}", response_class=HTMLResponse)
    def reject(kind: str, name: str, hx_request: str | None = Header(None)):
        if kind not in KINDS:
            return HTMLResponse(f"unknown kind: {e(kind)}", status_code=400)
        try:
            reg.reject(kind, name, by="ui")
        except (InvalidTransition, KeyError, ValueError) as ex:
            code = 404 if isinstance(ex, KeyError) else 400
            if hx_request:
                return HTMLResponse(
                    f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>{e(str(ex))}</td></tr>',
                    status_code=code,
                )
            return HTMLResponse(e(str(ex)), status_code=code)
        if hx_request:
            return (f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>'
                    f'<span class="badge badge-err">rejected {e(name)}</span></td></tr>')
        return RedirectResponse("/", 303)

    @app.post("/conflicts/pick", response_class=HTMLResponse)
    def ui_conflict_pick(winner: str = Form("")):
        cs = detect(reg.list("metric"))
        c = next((x for x in cs if winner in x.variants), None)
        if c is None:
            return HTMLResponse(f"no conflict containing {e(winner)}", status_code=400)
        apply_pick(reg, c, winner, by="ui")
        return RedirectResponse("/#sec-conflicts", 303)

    @app.post("/conflicts/split", response_class=HTMLResponse)
    async def ui_conflict_split(request: Request):
        form = await request.form()
        concept = str(form.get("concept", ""))
        cs = detect(reg.list("metric"))
        c = next((x for x in cs if x.concept == concept and x.kind == "variant"), None)
        if c is None:
            return HTMLResponse(f"no variant conflict for {e(concept)}", status_code=400)
        mapping = {}
        for v in c.variants:
            target = str(form.get(f"rename_{v}", "")).strip()
            if target:
                mapping[v] = target
        try:
            apply_split(reg, c, mapping or None, by="ui")
        except (KeyError, ValueError) as ex:
            return HTMLResponse(e(str(ex)), status_code=400)
        return RedirectResponse("/#sec-queue", 303)

    @app.post("/ui/query", response_class=HTMLResponse)
    def ui_query(metric: str = Form(""), as_role: str = Form(""), dims: str = Form(""),
                 filters: str = Form(""), entity_keys: str = Form("")):
        dim_list = [x.strip() for x in dims.split(",") if x.strip()] or None
        flt_dict: dict = {}
        for item in [x.strip() for x in filters.split(",") if x.strip()]:
            if "=" not in item:
                return _render_query_result(None, f"invalid filter '{item}' (use col=value)")
            k, _, v = item.partition("=")
            flt_dict[k.strip()] = v.strip()
        keys_list = None
        if entity_keys.strip():
            keys_list = []
            for raw in [x.strip() for x in entity_keys.split(",") if x.strip()]:
                keys_list.append(int(raw) if raw.lstrip("-").isdigit() else raw)
        try:
            res = svc.query_metric(metric.strip(), dim_list, flt_dict or None,
                                   as_role=as_role.strip(), entity_keys=keys_list)
            return _render_query_result(res)
        except Refusal as ex:
            return _render_query_result(None, str(ex))

    @app.post("/ui/traverse", response_class=HTMLResponse)
    def ui_traverse(start: str = Form(""), path: str = Form(""), as_role: str = Form(""),
                    filters: str = Form("")):
        rel_list = [x.strip() for x in path.split(",") if x.strip()]
        flt_dict: dict[str, dict[str, str]] = {}
        for item in [x.strip() for x in filters.split(",") if x.strip()]:
            if "=" not in item or "." not in item.partition("=")[0]:
                return _render_query_result(None, f"invalid entity filter '{item}' (use Entity.col=value)")
            lhs, _, val = item.partition("=")
            ent, _, col = lhs.partition(".")
            flt_dict.setdefault(ent.strip(), {})[col.strip()] = val.strip()
        try:
            res = svc.traverse_graph(start.strip(), rel_list, flt_dict or None, as_role=as_role.strip())
            return _render_query_result(res)
        except Refusal as ex:
            return _render_query_result(None, str(ex))

    return app
