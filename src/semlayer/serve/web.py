"""Registry JSON API + review UI (FastAPI + HTMX, no CSS)."""
from html import escape as e

from fastapi import FastAPI, Form, Header
from fastapi.responses import HTMLResponse, RedirectResponse

from semlayer.conflicts import detect
from semlayer.core import InvalidTransition
from semlayer.registry import Registry

from .service import Service

HTMX = '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'


def _summary(n) -> str:
    if n.kind == "metric":
        w = f" WHERE {' AND '.join(n.filters)}" if n.filters else ""
        return f"{n.expression} FROM {n.table}{w}"
    return n.model_dump_json(include={"table", "key", "src", "rel", "dst", "role", "effect", "row_filter"})


def _row(n, msg: str = "") -> str:
    ev, tag = n.evidence, "revision" if n.version else "draft"
    act = f"/certify/{n.kind}/{n.name}"
    return (
        f'<tr id="{e(n.kind)}-{e(n.name)}"><td>{e(n.kind)} <b>{e(n.name)}</b> ({tag})</td>'
        f"<td><code>{e(_summary(n))}</code><br>{e(ev.explanation)}</td>"
        f"<td>{ev.usage}</td>"
        f"<td><details><summary>evidence</summary><pre>{e(chr(10).join(ev.queries))}</pre>"
        f"lineage: {e(', '.join(ev.lineage))}</details></td>"
        f'<td><form method="post" action="{act}" hx-post="{act}" hx-target="closest tr" '
        f'hx-swap="outerHTML"><input name="owner" value="{e(n.owner or ev.suggested_owner or "")}">'
        f"<button>certify</button></form>"
        f'<form method="post" action="/reject/{e(n.kind)}/{e(n.name)}" '
        f'hx-post="/reject/{e(n.kind)}/{e(n.name)}" hx-target="closest tr" hx-swap="outerHTML">'
        f"<button>reject</button></form>{e(msg)}</td></tr>"
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
        try:
            n = reg.certify(kind, name, owner=owner or None, by="api")
        except (InvalidTransition, KeyError) as ex:
            return {"error": str(ex)}
        return {"name": n.name, "version": n.version, "state": n.state.value}

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = "".join(_row(n) for k in ("metric", "entity", "edge", "rule") for n in reg.queue(k))
        conflicts = "".join(
            f"<li><b>{e(c.concept)}</b> ({c.kind}): {e(', '.join(c.variants))}<br>"
            f"{e(' | '.join(c.diffs))}</li>" for c in detect(reg.list("metric")))
        return (f"<!doctype html><title>ARTH: SemLayer review</title>{HTMX}<h1>ARTH: SemLayer · Review queue</h1>"
                f"<table border=1><tr><th>definition</th><th>logic</th><th>usage</th><th>evidence</th>"
                f"<th>action</th></tr>{rows}</table><h2>Conflicts</h2><ul>{conflicts}</ul>")

    @app.post("/certify/{kind}/{name}", response_class=HTMLResponse)
    def certify(kind: str, name: str, owner: str = Form(""), hx_request: str | None = Header(None)):
        try:
            reg.certify(kind, name, owner=owner.strip() or None, by="ui")
        except (InvalidTransition, KeyError) as ex:
            n = reg.get(kind, name, pending=bool(reg.get(kind, name, pending=True))) or reg.get(kind, name)
            return _row(n, str(ex)) if hx_request else HTMLResponse(str(ex), 400)
        if hx_request:
            return f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>certified {e(name)}</td></tr>'
        return RedirectResponse("/", 303)

    @app.post("/reject/{kind}/{name}", response_class=HTMLResponse)
    def reject(kind: str, name: str, hx_request: str | None = Header(None)):
        reg.reject(kind, name, by="ui")
        if hx_request:
            return f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>rejected {e(name)}</td></tr>'
        return RedirectResponse("/", 303)

    return app
