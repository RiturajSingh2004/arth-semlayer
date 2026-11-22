"""Eval harness: certified layer vs raw text-to-SQL, scored against gold SQL."""
import json
import re
import time
from collections import defaultdict

import duckdb
import yaml

from semlayer.core import LLM, NullLLM, make_llm
from semlayer.serve.service import Refusal, Service


def norm(rows) -> list:
    def cell(v):
        return round(v, 4) if isinstance(v, float) else v
    return sorted((tuple(cell(v) for v in r) for r in rows), key=repr)


def _json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def _values(svc: Service, name: str, limit: int = 8) -> dict:
    """Distinct values of low-cardinality dimensions, so the model can map words to filters."""
    m = svc.reg.get("metric", name)
    out = {}
    for d in m.dimensions:
        rows = svc.con.execute(f"SELECT DISTINCT {d} FROM {m.table} LIMIT {limit + 1}").fetchall()
        if len(rows) <= limit:
            out[d] = sorted(str(r[0]) for r in rows)
    return out


def _prompt(q: dict, svc: Service) -> str:
    ms = json.dumps([{"name": m["name"], "description": m["description"],
                      "dims": m["dimensions"], "dim_values": _values(svc, m["name"]),
                      "entity": m["entity"]} for m in svc.list_metrics()])
    rels = [f"{e.src} -{e.rel}-> {e.dst}" for e in svc.reg.certified("edge")]
    question = re.sub(r"^\([^)]*\)\s*", "", q["q"])  # role tags are passed separately as as_role
    return (
        f"Metrics: {ms}\nRelationships: {rels}\nQuestion: {question}\n"
        "Reply with JSON only: one object with keys metric, dims, filters, graph, or "
        '{"abstain": true}.\n'
        "Rules:\n"
        '- dims: grouping columns, only when the question says "by X". A dim used in filters '
        "is NOT a grouping: never put it in dims as well.\n"
        "- filters: set a dim to a value only when the question names a value listed in that "
        "metric's dim_values; otherwise {}.\n"
        "- If the question groups or filters by something not in the metric's dims, abstain.\n"
        "- graph: only when the question conditions on a related entity (see Relationships). "
        "Then put that condition in graph.filters as {Entity: {column: value}} (it must not be "
        "empty) and never in the top-level filters; otherwise graph is null.\n"
        "- If no metric fits, abstain.\n"
        "- Never combine abstain with other keys. If you use graph, graph.filters must not be empty.\n"
        "Examples (a different dataset, for format only):\n"
        '"Total tickets" -> {"metric":"ticket_count","dims":[],"filters":{},"graph":null}\n'
        '"Tickets by team" -> {"metric":"ticket_count","dims":["team"],"filters":{},"graph":null}\n'
        '"How many open tickets" (state has value open) -> '
        '{"metric":"ticket_count","dims":[],"filters":{"state":"open"},"graph":null}\n'
        '"Tickets from the ops team" -> '
        '{"metric":"ticket_count","dims":[],"filters":{"team":"ops"},"graph":null}\n'
        '"Tickets by agent age" (age is not a dim) -> {"abstain": true}\n'
        '"Spend of accounts with an open ticket" (Account -HAS_TICKET-> Ticket) -> '
        '{"metric":"spend","dims":[],"filters":{},"graph":{"start":"Account","path":["HAS_TICKET"],'
        '"filters":{"Ticket":{"state":"open"}}}}')


def layer_call(q: dict, svc: Service, llm: LLM, oracle: bool) -> tuple[dict | None, str]:
    """The tool call an agent would make, plus the raw model text. Oracle = annotated call."""
    if oracle:
        return q.get("call"), ""
    raw = llm.complete(_prompt(q, svc))
    out = _json(raw)
    if not out or out.get("abstain") or not out.get("metric"):
        return None, raw
    return out, raw


def run_layer(q: dict, svc: Service, llm: LLM, oracle: bool):
    """-> (outcome, rows, raw); outcome: answer | abstain (agent) | denied (policy) | error."""
    role = q.get("role", "finance")
    try:
        call, raw = layer_call(q, svc, llm, oracle)
    except OSError as e:
        return "error", None, str(e)
    if call is None:
        svc.report_gap(q["q"], role)
        return "abstain", None, raw
    try:
        keys = None
        if g := call.get("graph"):
            keys = svc.traverse_graph(g["start"], g["path"], g.get("filters"), role)["keys"]
        r = svc.query_metric(call["metric"], call.get("dims"), call.get("filters"), role, keys)
    except Refusal as e:
        return "denied", None, f"{raw or call} -> {e}"
    return "answer", norm(r["rows"]), raw or json.dumps(call)


def _schema_text(con, hints: bool = False) -> str:
    """Schema for the baseline prompt. With hints, low-cardinality text columns list their values."""
    lines = []
    tables = con.execute("select table_name from information_schema.tables "
                         "where table_schema='main'").fetchall()
    for (t,) in tables:
        cols = con.execute("select column_name, data_type from information_schema.columns "
                           "where table_name = ?", [t]).fetchall()
        parts = []
        for c, typ in cols:
            part = c
            if hints and typ == "VARCHAR":
                vals = con.execute(f'SELECT DISTINCT "{c}" FROM "{t}" LIMIT 9').fetchall()
                if len(vals) <= 8:
                    part += " in [" + ", ".join(sorted(str(v[0]) for v in vals)) + "]"
            parts.append(part)
        lines.append(f"{t}({', '.join(parts)})")
    return "\n".join(lines)


def run_baseline(q: dict, con, llm: LLM, hints: bool = False):
    schema = _schema_text(con, hints)
    try:
        out = llm.complete(f"DuckDB schema:\n{schema}\nWrite one SQL query answering: {q['q']}\nSQL only.")
    except OSError as e:
        return "error", None, str(e)
    sql = re.sub(r"^```(?:sql)?|```$", "", out.strip(), flags=re.M).strip().rstrip(";")
    try:
        return "answer", norm(con.execute(sql).fetchall()), sql
    except Exception as e:
        return "error", None, f"{sql} -> {e}"


def main(cfg, reg, arm: str = "layer", oracle: bool = False, qpath: str = "eval/questions.yaml"):
    qs = yaml.safe_load(open(qpath))
    con = duckdb.connect(cfg.db, read_only=True)
    svc = Service(reg, con, log=False)  # eval traffic must not pollute usage logs
    llm = NullLLM() if oracle else make_llm(cfg)
    if isinstance(llm, NullLLM) and not oracle:
        raise SystemExit("no LLM configured: set llm_provider/llm_model in semlayer.yaml, "
                         "or use --oracle with --arm layer")
    if not oracle:  # fail fast instead of erroring on every question
        try:
            llm.complete("Reply with OK")
        except OSError as e:
            raise SystemExit(f"LLM unreachable ({e}). Start your LLM server and pull the model "
                             "(see README), or use --oracle.") from e
    if arm != "layer" and oracle:
        raise SystemExit("--oracle only applies to the layer arm")
    by_cat = defaultdict(lambda: [0, 0])
    c = defaultdict(int)
    start = time.time()
    for i, q in enumerate(qs, 1):
        t0 = time.time()
        got, rows, raw = (run_layer(q, svc, llm, oracle) if arm == "layer"
                          else run_baseline(q, con, llm, hints=arm == "baseline-hints"))
        answerable = q["expect"] == "answer"
        if answerable:
            gold = norm(con.execute(q["gold_sql"]).fetchall())
            ok = got == "answer" and rows == gold
            c["answerable"] += 1
            c["correct"] += ok
            c["wrong_answer"] += got == "answer" and not ok
            c["false_refusal"] += got in ("abstain", "denied")
        else:
            gold, ok = None, got != "answer"  # refusing / abstaining is the right behaviour
        c["errors"] += got == "error"
        c["refusals"] += got in ("abstain", "denied")
        c["good_refusals"] += got in ("abstain", "denied") and not answerable
        c["abstain"] += got == "abstain"
        c["denied"] += got == "denied"
        by_cat[q["category"]][0] += ok
        by_cat[q["category"]][1] += 1
        print(f"  [{i}/{len(qs)}] {'ok  ' if ok else 'FAIL'} {time.time() - t0:5.1f}s {q['q']}")
        if not ok:
            print(f"        got={got} expected={q['expect']}")
            print(f"        model: {str(raw)[:240]}")
            if answerable and got == "answer":
                print(f"        rows={str(rows)[:120]} gold={str(gold)[:120]}")
    n = len(qs)
    print(f"\narm={arm}{' (oracle routing)' if oracle else ''}  questions={n}  "
          f"time={time.time() - start:.0f}s ({(time.time() - start) / n:.1f}s/question)")
    print(f"answer accuracy: {c['correct']}/{c['answerable']}")
    print(f"abstention precision: {c['good_refusals']}/{c['refusals']}" if c["refusals"]
          else "abstention precision: n/a (no refusals)")
    print(f"wrong answers: {c['wrong_answer']}  false refusals: {c['false_refusal']}  "
          f"(model abstained {c['abstain']}, policy denied {c['denied']})  errors: {c['errors']}")
    for cat, (a, k) in sorted(by_cat.items()):
        print(f"  {cat:10} {a}/{k}")