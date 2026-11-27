import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


def load_dotenv(*paths: str | Path) -> None:
    """Populate unset environment variables from .env files without extra dependencies."""
    candidates = [Path(p) for p in paths] if paths else [Path(".env"), Path.home() / ".env"]
    for p in candidates:
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
                v = v[1:-1]
            if k and v:
                os.environ.setdefault(k, v)


class Config(BaseModel):
    warehouse: Literal["duckdb", "bigquery"] = "duckdb"
    db: str = "examples/jaffle_shop/jaffle.duckdb"
    bq_project: str = ""  # set in semlayer.yaml, SEMLAYER_BQ_PROJECT, or GOOGLE_CLOUD_PROJECT
    bq_dataset: str = "jaffle_shop"
    bq_mcp_url: str = "https://bigquery.googleapis.com/mcp"
    bq_service_account: str | None = None
    bq_max_bytes: int = 100_000_000  # dry-run byte cap (0 disables dry-run)
    registry: str = "definitions"
    query_log: str = "examples/jaffle_shop/query_log.jsonl"
    docs: str | None = None  # optional dbt manifest.json
    # Governance & runtime limits (ADR-031, ADR-032)
    bound_role: str | None = None
    timezone: str = "UTC"
    max_rows: int = 1000
    max_groups: int = 1000
    max_filter_list: int = 500
    # LLM (on by default). AI Studio Gemini API is the primary provider (GEMINI_API_KEY);
    # local engines ("ollama", "openai") remain secondary options, and "none" (--no-llm) turns it off.
    llm_provider: Literal["gemini", "ollama", "openai", "none"] = "gemini"
    llm_model: str | None = "gemini-3.5-flash-lite"
    llm_url: str | None = None
    llm_api_key_env: str | None = None  # defaults to GEMINI_API_KEY when llm_provider == "gemini"
    llm_max_tokens: int = 512
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = "low"
    llm_rpm: float | None = None  # requests/min pacing (None = 15 for gemini, 0 for local)

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        p = Path(path or "semlayer.yaml")
        return cls(**(yaml.safe_load(p.read_text()) or {})) if p.exists() else cls()
