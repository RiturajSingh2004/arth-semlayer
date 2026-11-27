# GEMINI.md

ARTH: SemLayer is a certification-gated semantic layer with GCP integrations (Gemini API via AI Studio as default LLM, Google's managed BigQuery MCP server as optional cloud warehouse, and local Ollama/DuckDB preserved). Read `ARCHITECTURE.md`, `ADR.md`, and `SECURITY.md` before changing behaviour.

## Rules
- Keep code minimal and idiomatic; no speculative abstractions.
- Python 3.12, `uv`, `ruff`, `pytest`, Pydantic v2 models, `sqlglot`.
- Only free, open-source dependencies (`ADR-009`; `google-auth` lives in optional extra `gcp`). Ask before adding any new dependency.
- Never serve drafts, pending revisions, or deprecated definitions to agents (invariant 1).
- Never expose a raw-SQL tool to agents; `BigQueryMCP` must call only `execute_sql_readonly` and render literals strictly via `sqlglot` AST nodes (`ADR-026`, `ADR-027`, `ADR-028`).
- Never read or print `.env` / API key values into logs or chat; strip sensitive column values (`policy.SENSITIVE`) from eval prompts (`ADR-029`).
- Every behaviour change needs a test; run `uv run ruff check src tests scripts eval examples` and `uv run pytest -q` before finishing.
- CI must pass with no API keys, no GCP credentials, and no network calls (`SEMLAYER_LLM_PROVIDER=none`).
- New architectural decisions go in `ADR.md` as a new numbered entry; amend rather than rewriting history.
- Commit small, one concern per commit.

## Commands
```bash
uv sync --extra gcp
uv run ruff check src tests scripts eval examples
uv run pytest -q
uv run semlayer demo --no-llm
uv run semlayer eval --oracle
uv run python scripts/bq_seed.py --project geminisprints26blr-3913 --dataset jaffle_shop
uv run python scripts/mcp_check.py
```

## Extension points
- `Warehouse` protocol (`src/semlayer/warehouse/`): `DuckDBWarehouse` (local default) and `BigQueryMCP` (`https://bigquery.googleapis.com/mcp`); add new warehouse adapters here.
- `Compiler` protocol (`src/semlayer/compiler/`): `SimpleCompiler`; add a MetricFlow/Cube adapter here.
- `LLM` protocol (`src/semlayer/core/llm.py`): `gemini` (AI Studio `GEMINI_API_KEY` default), `ollama` and `openai` (local secondary options), `none`.
