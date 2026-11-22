from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class Config(BaseModel):
    db: str = "examples/jaffle_shop/jaffle.duckdb"
    registry: str = "definitions"
    query_log: str = "examples/jaffle_shop/query_log.jsonl"
    docs: str | None = None  # optional dbt manifest.json
    # The LLM is on by default (local Ollama). Any local engine works: provider "ollama", or
    # "openai" for any OpenAI-compatible server (llama.cpp server, LM Studio, vLLM, ...).
    # Provider "none" (or the --no-llm flag) turns it off. SEMLAYER_LLM_PROVIDER / _MODEL / _URL
    # environment variables override these values.
    llm_provider: Literal["ollama", "openai", "none"] = "ollama"
    llm_model: str | None = "qwen3.5:9b"
    llm_url: str | None = None  # default per provider: Ollama :11434, openai-compatible :8080/v1
    llm_api_key_env: str | None = None  # name of the env var holding an API key, if the server needs one

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        p = Path(path or "semlayer.yaml")
        return cls(**(yaml.safe_load(p.read_text()) or {})) if p.exists() else cls()