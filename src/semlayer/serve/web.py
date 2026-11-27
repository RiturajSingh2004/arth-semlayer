"""Registry JSON API + review UI (FastAPI + HTMX, no CSS)."""
from html import escape as e

from fastapi import FastAPI, Form, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from semlayer.conflicts import detect
from semlayer.core import KINDS, InvalidTransition
from semlayer.registry import Registry

from .service import Service

HTMX = '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'


def _summary(n) -> str:
    if n.kind == "metric":
        w = f" WHERE {' AND '.join(n.filters)}" if n.filters else ""
        return f"{n.expression} FROM {n.table}{w}"
    return n.model_dump_json(include={"table", "key", "src", "rel", "dst", "role", "effect", "row_filter"})


def _row(n, msg: str = "", diff_text: str = "") -> str:
    ev, tag = n.evidence, "revision" if n.version else "draft"
    act = f"/certify/{e(n.kind)}/{e(n.name)}"
    rej = f"/reject/{e(n.kind)}/{e(n.name)}"
    desc = f"<div><i>{e(n.description)}</i></div>" if n.description else ""
    diff_html = f"<pre>{e(diff_text)}</pre>" if diff_text else ""
    err_html = f" <strong>{e(msg)}</strong>" if msg else ""
    return (
        f'<tr id="{e(n.kind)}-{e(n.name)}"><td>{e(n.kind)} <b>{e(n.name)}</b> ({tag})</td>'
        f"<td><code>{e(_summary(n))}</code>{desc}<div>{e(ev.explanation)}</div></td>"
        f"<td>{ev.usage}</td>"
        f"<td><details><summary>evidence</summary><pre>{e(chr(10).join(ev.queries))}</pre>"
        f"lineage: {e(', '.join(ev.lineage))}{diff_html}</details></td>"
        f'<td><form method="post" action="{act}" hx-post="{act}" hx-target="closest tr" '
        f'hx-swap="outerHTML"><input name="owner" placeholder="owner" '
        f'value="{e(n.owner or ev.suggested_owner or "")}">'
        f"<button>certify</button></form>"
        f'<form method="post" action="{rej}" '
        f'hx-post="{rej}" hx-target="closest tr" hx-swap="outerHTML">'
        f"<button>reject</button></form>{err_html}</td></tr>"
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
        rows = "".join(
            _row(n, diff_text=reg.diff(k, n.name) if n.version else "")
            for k in ("metric", "entity", "edge", "rule") for n in reg.queue(k)
        )
        empty = '<tr><td colspan=5><i>No pending drafts or revisions in queue.</i></td></tr>'
        conflicts = "".join(
            f"<li><b>{e(c.concept)}</b> ({e(c.kind)}): {e(', '.join(c.variants))}<br>"
            f"{e(' | '.join(c.diffs))}</li>" for c in detect(reg.list("metric")))
        certified = "".join(
            f"<li><b>{e(m['name'])}</b> v{m['version']} — {e(m['description'] or '(no description)')} "
            f"(dims: {e(', '.join(m['dimensions']) or '-')})</li>"
            for m in svc.list_metrics()
        )
        return (
            f"<!doctype html><title>ARTH: SemLayer review</title>{HTMX}"
            f"<h1>ARTH: SemLayer · Review queue</h1>"
            f"<table border=1><tr><th>definition</th><th>logic &amp; description</th><th>usage</th>"
            f"<th>evidence</th><th>action</th></tr>{rows or empty}</table>"
            f"<h2>Conflicts</h2><ul>{conflicts or '<li><i>None</i></li>'}</ul>"
            f"<h2>Certified metrics</h2><ul>{certified or '<li><i>None certified yet</i></li>'}</ul>"
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
            return f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>certified {e(name)}</td></tr>'
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
            return f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>rejected {e(name)}</td></tr>'
        return RedirectResponse("/", 303)

    return app
