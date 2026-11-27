"""Seed the deterministic jaffle_shop demo tables into BigQuery using `bq load` (works without DML/billing).
Run from the project root:
  uv run python scripts/bq_seed.py --project geminisprints26blr-3913 --dataset jaffle_shop
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed", ROOT / "examples/jaffle_shop/seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

SCHEMAS = {
    "customers": "id:INT64,name:STRING,email:STRING,region:STRING,created_at:DATE",
    "orders": "id:INT64,customer_id:INT64,status:STRING,order_date:DATE",
    "payments": "id:INT64,order_id:INT64,customer_id:INT64,method:STRING,status:STRING,amount:FLOAT64,paid_at:DATE",
}


def _bq_bin() -> str:
    found = shutil.which("bq") or shutil.which("bq.cmd")
    if not found:
        raise SystemExit("`bq` CLI not found on PATH; install Google Cloud SDK first.")
    return found


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def export_jsonl(out_dir: Path) -> dict[str, Path]:
    db_path = out_dir / "seed.duckdb"
    _load_seed().build_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    files: dict[str, Path] = {}
    try:
        for table in SCHEMAS:
            cur = con.execute(f"SELECT * FROM {table}")
            cols = [d[0] for d in cur.description]
            dest = out_dir / f"{table}.jsonl"
            lines = []
            for row in cur.fetchall():
                rec = {c: (v.isoformat() if hasattr(v, "isoformat") else v)
                       for c, v in zip(cols, row, strict=True)}
                lines.append(json.dumps(rec))
            dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            files[table] = dest
    finally:
        con.close()
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description="Load jaffle_shop demo tables into BigQuery.")
    ap.add_argument("--project", default="geminisprints26blr-3913")
    ap.add_argument("--dataset", default="jaffle_shop")
    ap.add_argument("--location", default="US")
    args = ap.parse_args()

    bq = _bq_bin()
    _run([bq, f"--location={args.location}", f"--project_id={args.project}",
          "mk", "--dataset", "--force", f"{args.project}:{args.dataset}"])
    with tempfile.TemporaryDirectory() as tmp:
        files = export_jsonl(Path(tmp))
        for table, path in files.items():
            _run([
                bq, f"--location={args.location}", f"--project_id={args.project}",
                "load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
                f"{args.project}:{args.dataset}.{table}", str(path), SCHEMAS[table],
            ])
    print(f"seeded {args.project}:{args.dataset} ({', '.join(SCHEMAS)})")


if __name__ == "__main__":
    main()
